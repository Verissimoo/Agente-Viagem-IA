"""Intake — extrai slots e conversa com o vendedor até ter tudo.

Estratégia híbrida: o `intent_parser` regex/heurístico já existente faz uma
primeira passada barata; o LLM completa o que faltou e formula a próxima
pergunta de forma natural.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import date
from typing import Any, Dict, List, Optional

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from backend.app.ai.agents.llm import get_cached_chat_model
from backend.app.ai.agents.prompts import intake_system_prompt
from backend.app.ai.agents.state import ChatState, IntakeSlots
from backend.app.nlp.intent_parser import parse_intent_ptbr
from backend.app.providers.buscamilhas.iata_resolver import resolve_city_to_iatas


# Sufixos de país que o vendedor adiciona ("Lisboa Portugal") e que
# o resolver não conhece. Limpamos antes de tentar resolver.
_COUNTRY_SUFFIXES = [
    "portugal", "brasil", "brazil", "argentina", "espanha", "spain",
    "franca", "frança", "france", "italia", "italy", "alemanha", "germany",
    "eua", "estados unidos", "usa", "united states", "inglaterra",
    "reino unido", "uk", "united kingdom", "chile", "uruguai", "uruguay",
    "mexico", "méxico", "japao", "japão", "japan", "china",
    "africa do sul", "south africa", "australia", "canada", "canadá",
]

# Override local de cidades brasileiras que faltam na tabela oficial do
# provider buscamilhas. Evita que o LLM invente códigos errados (ex.: dizer
# que Chapecó é CPM quando na verdade é XAP — CPM é Compiègne na França).
_BR_CITY_OVERRIDES: Dict[str, List[str]] = {
    "chapeco": ["XAP"], "chapecó": ["XAP"],
    "joinville": ["JOI"],
    "florianopolis": ["FLN"], "florianópolis": ["FLN"], "floripa": ["FLN"],
    "curitiba": ["CWB"],
    "maringa": ["MGF"], "maringá": ["MGF"],
    "londrina": ["LDB"],
    "cascavel": ["CAC"],
    "foz do iguacu": ["IGU"], "foz do iguaçu": ["IGU"], "foz": ["IGU"],
    "caxias do sul": ["CXJ"], "caxias": ["CXJ"],
    "passo fundo": ["PFB"],
    "pelotas": ["PET"],
    "santa maria": ["RIA"],
    "bauru": ["JTC"],
    "presidente prudente": ["PPB"], "ribeirao preto": ["RAO"], "ribeirão preto": ["RAO"],
    "sao jose do rio preto": ["SJP"], "são josé do rio preto": ["SJP"],
    "campo grande": ["CGR"],
    "cuiaba": ["CGB"], "cuiabá": ["CGB"],
    "goiania": ["GYN"], "goiânia": ["GYN"],
    "palmas": ["PMW"],
    "manaus": ["MAO"],
    "boa vista": ["BVB"],
    "porto velho": ["PVH"],
    "macapa": ["MCP"], "macapá": ["MCP"],
    "rio branco": ["RBR"],
    "belem": ["BEL"], "belém": ["BEL"],
    "sao luis": ["SLZ"], "são luís": ["SLZ"], "são luis": ["SLZ"],
    "teresina": ["THE"],
    "fortaleza": ["FOR"],
    "natal": ["NAT"],
    "joao pessoa": ["JPA"], "joão pessoa": ["JPA"],
    "recife": ["REC"],
    "maceio": ["MCZ"], "maceió": ["MCZ"],
    "aracaju": ["AJU"],
    "salvador": ["SSA"],
    "ilheus": ["IOS"], "ilhéus": ["IOS"],
    "porto seguro": ["BPS"],
    "vitoria": ["VIX"], "vitória": ["VIX"],
    "belo horizonte": ["CNF", "PLU"], "bh": ["CNF"], "confins": ["CNF"],
    "juiz de fora": ["JDF"],
    "uberlandia": ["UDI"], "uberlândia": ["UDI"],
    "campinas": ["VCP"], "viracopos": ["VCP"],
    "navegantes": ["NVT"],
}


def _resolve_city_smart(query: str) -> List[str]:
    """Resolve cidade → IATA tentando variações + override local de cidades BR."""
    if not query:
        return []

    # 0) Override local primeiro (cidades BR que faltam na tabela oficial)
    q_low = query.lower().strip().rstrip(",.").strip()
    if q_low in _BR_CITY_OVERRIDES:
        return _BR_CITY_OVERRIDES[q_low]

    # 1) Resolver oficial direto
    direct = resolve_city_to_iatas(query)
    if direct:
        return direct

    # 2) Tira sufixo de país e tenta de novo (oficial + override)
    for suffix in _COUNTRY_SUFFIXES:
        if q_low.endswith(" " + suffix) or q_low.endswith("," + suffix) or q_low.endswith(", " + suffix):
            stripped = q_low.rsplit(suffix, 1)[0].rstrip(", ").strip()
            if stripped in _BR_CITY_OVERRIDES:
                return _BR_CITY_OVERRIDES[stripped]
            res = resolve_city_to_iatas(stripped)
            if res:
                return res

    # 3) Remove "lixo" comum ("qualquer", "data", etc) — pega só palavras de cidade
    junk = {"qualquer", "data", "lugar", "aeroporto", "cidade", "voo", "voos"}
    words = [w for w in q_low.split() if w not in junk]
    cleaned = " ".join(words).strip()
    if cleaned and cleaned != q_low:
        if cleaned in _BR_CITY_OVERRIDES:
            return _BR_CITY_OVERRIDES[cleaned]
        res = resolve_city_to_iatas(cleaned)
        if res:
            return res

    # 4) Última tentativa: primeira palavra
    first = q_low.split()[0] if q_low.split() else ""
    if first and first != q_low:
        if first in _BR_CITY_OVERRIDES:
            return _BR_CITY_OVERRIDES[first]
        res = resolve_city_to_iatas(first)
        if res:
            return res

    return []

logger = logging.getLogger(__name__)


_REQUIRED_FIELDS = ("origin_iata", "destination_iata", "date_start", "adults")


# Loop guard — após N tentativas do intake sem ir pro orchestrator,
# força a busca com o que tem (evita travar em loop de confirmação).
_MAX_INTAKE_ATTEMPTS = 3


def _extract_route_fallback(text: str) -> tuple[Optional[str], Optional[str]]:
    """Captura rota em formatos que o parser nativo não pega:
       'Brasilia <> Chapecó', 'BSB-XAP', 'GRU → LIS', 'rio x sp', 'BSB para POA'.
       Retorna (origin, destination) — pode ser cidade ou IATA, deixa o resolver lidar.
    """
    import re as _re

    # Tokens não-rota a ignorar
    junk_re = _re.compile(
        r"\b(qualquer|data|entre|dias?|viagem|adultos?|crian[cç]as?|"
        r"bagagem|despachada|sem|com|para|de|do|da|janeiro|fevereiro|março|"
        r"abril|maio|junho|julho|agosto|setembro|outubro|novembro|dezembro)\b",
        _re.IGNORECASE,
    )

    # Padrão 1: SEPARADORES típicos <>, ->, →, x, -, /, |
    # Ex.: "Brasilia <> Chapecó", "BSB - XAP", "rio → sp"
    sep_re = _re.compile(
        r"([A-ZÀ-Üa-zà-ü]{3,}(?:\s+[A-ZÀ-Üa-zà-ü]+){0,2})"   # origem (até 3 palavras)
        r"\s*(?:<>|<-->|<->|->|—>|→|\bx\b|/|\|)\s*"
        r"([A-ZÀ-Üa-zà-ü]{3,}(?:\s+[A-ZÀ-Üa-zà-ü]+){0,2})"   # destino
    )
    m = sep_re.search(text)
    if m:
        return (m.group(1).strip(), m.group(2).strip())

    # Padrão 2: "CIDADE para/até CIDADE" — backup caso parser nativo não pegue
    m = _re.search(
        r"\b([A-ZÀ-Üa-zà-ü]{3,}(?:\s+[A-ZÀ-Üa-zà-ü]+){0,2})\s+(?:para|ate|até|p/|pro|pra)\s+"
        r"([A-ZÀ-Üa-zà-ü]{3,}(?:\s+[A-ZÀ-Üa-zà-ü]+){0,2})\b",
        text, flags=_re.IGNORECASE,
    )
    if m:
        return (m.group(1).strip(), m.group(2).strip())

    return (None, None)


def _extract_date_range(text: str) -> Optional[tuple[date, date]]:
    """Detecta range explícito tipo 'entre 1 e 13 de junho' / '1 a 13 de junho' /
    '01/06 a 13/06'. Retorna (date_start, date_end) ou None.
    """
    import re as _re
    from datetime import date as _date
    today = _date.today()
    lower = text.lower()
    _MONTHS_RE = (
        "janeiro|fevereiro|março|marco|abril|maio|junho|julho|agosto|"
        "setembro|outubro|novembro|dezembro"
    )
    month_map = {
        "janeiro": 1, "fevereiro": 2, "março": 3, "marco": 3, "abril": 4,
        "maio": 5, "junho": 6, "julho": 7, "agosto": 8,
        "setembro": 9, "outubro": 10, "novembro": 11, "dezembro": 12,
    }

    def _mk(d1: int, mo1: int, d2: int, mo2: int) -> Optional[tuple[date, date]]:
        year = today.year
        try:
            if _date(year, mo2, d2) < today:
                year += 1
            start, end = _date(year, mo1, d1), _date(year, mo2, d2)
            return (start, end) if end >= start else (end, start)
        except ValueError:
            return None

    # Padrão: "DD de MES a/ao/até DD de MES" (cada data com seu mês) —
    # ex.: "15 de setembro ao dia 20 de setembro" / "10 de jun a 2 de jul".
    m = _re.search(
        rf"(?:entre\s+)?(\d{{1,2}})\s+(?:de\s+|se\s+)?({_MONTHS_RE})\s+"
        rf"(?:a|ao|at[ée]|e|-)\s*(?:o\s+)?(?:dia\s+)?(\d{{1,2}})\s+(?:de\s+|se\s+)?({_MONTHS_RE})",
        lower,
    )
    if m:
        r = _mk(int(m.group(1)), month_map[m.group(2)], int(m.group(3)), month_map[m.group(4)])
        if r:
            return r

    # Padrão: "X a Y de MES" ou "entre X e Y de MES" (mês único)
    m = _re.search(
        rf"(?:entre\s+)?(\d{{1,2}})\s*(?:a|até|e|-)\s*(\d{{1,2}})\s+(?:de\s+|se\s+)?({_MONTHS_RE})",
        lower,
    )
    if m:
        d1, d2 = int(m.group(1)), int(m.group(2))
        month = month_map.get(m.group(3))
        if month:
            return _mk(min(d1, d2), month, max(d1, d2), month)

    # Padrão: "DD/MM a DD/MM" ou "DD-MM a DD-MM"
    m = _re.search(
        r"\b(\d{1,2})[\/\-](\d{1,2})\s*(?:a|até|-)\s*(\d{1,2})[\/\-](\d{1,2})\b",
        lower,
    )
    if m:
        d1, mo1, d2, mo2 = (int(g) for g in m.groups())
        year = today.year
        try:
            start = _date(year, mo1, d1)
            end = _date(year, mo2, d2)
            if end < today:
                start = _date(year + 1, mo1, d1)
                end = _date(year + 1, mo2, d2)
            return (start, end)
        except ValueError:
            return None

    return None


def _all_date_ranges(text: str) -> list[tuple[int, date, date]]:
    """Acha TODOS os ranges de data no texto, com a posição onde começam.
    Retorna lista de (pos, start_date, end_date), ordenada por posição.
    """
    import re as _re
    from datetime import date as _date
    today = _date.today()
    lower = text.lower()
    month_map = {
        "janeiro": 1, "fevereiro": 2, "março": 3, "marco": 3, "abril": 4,
        "maio": 5, "junho": 6, "julho": 7, "agosto": 8,
        "setembro": 9, "outubro": 10, "novembro": 11, "dezembro": 12,
    }
    found: list[tuple[int, date, date]] = []

    # "X a/e Y [de|se] MES"
    for m in _re.finditer(
        r"(\d{1,2})\s*(?:a|até|e|-)\s*(\d{1,2})\s+(?:de\s+|se\s+)?"
        r"(janeiro|fevereiro|março|marco|abril|maio|junho|julho|agosto|"
        r"setembro|outubro|novembro|dezembro)",
        lower,
    ):
        d1, d2, month = int(m.group(1)), int(m.group(2)), month_map.get(m.group(3))
        if not month:
            continue
        year = today.year
        try:
            if _date(year, month, max(d1, d2)) < today:
                year += 1
            found.append((m.start(), _date(year, month, min(d1, d2)), _date(year, month, max(d1, d2))))
        except ValueError:
            continue

    # "DD/MM a DD/MM"
    for m in _re.finditer(
        r"\b(\d{1,2})[\/\-](\d{1,2})\s*(?:a|até|-)\s*(\d{1,2})[\/\-](\d{1,2})\b",
        lower,
    ):
        d1, mo1, d2, mo2 = (int(g) for g in m.groups())
        year = today.year
        try:
            start, end = _date(year, mo1, d1), _date(year, mo2, d2)
            if end < today:
                start, end = _date(year + 1, mo1, d1), _date(year + 1, mo2, d2)
            found.append((m.start(), start, end))
        except ValueError:
            continue

    found.sort(key=lambda x: x[0])
    return found


def _extract_depart_return_windows(
    text: str,
) -> tuple[Optional[tuple[date, date]], Optional[tuple[date, date]]]:
    """Quando o vendedor dá DUAS janelas — ida e volta separadas
    ('ir entre 10 e 12, voltando entre 25 e 26') — atribui o primeiro range à
    ida e o segundo à volta. Retorna (janela_ida, janela_volta), cada uma
    podendo ser None.
    """
    import re as _re
    ranges = _all_date_ranges(text)
    if len(ranges) >= 2:
        dep = (ranges[0][1], ranges[0][2])
        ret = (ranges[1][1], ranges[1][2])
        return dep, ret

    # Só 1 range explícito: se há marcador de volta + "X a Y" cru depois dele,
    # herda mês/ano da ida ("ir 10-12 de outubro, voltar entre 20 e 22").
    if len(ranges) == 1:
        dep = (ranges[0][1], ranges[0][2])
        kw = _re.search(r"\b(voltando|voltar|de\s+volta|retorn\w+|regress\w+)\b", text.lower())
        if kw:
            tail = text[kw.start():].lower()
            bare = _re.search(r"(\d{1,2})\s*(?:a|até|e|-)\s*(\d{1,2})", tail)
            if bare:
                d1, d2 = int(bare.group(1)), int(bare.group(2))
                ref = dep[1]
                try:
                    return dep, (
                        date(ref.year, ref.month, min(d1, d2)),
                        date(ref.year, ref.month, max(d1, d2)),
                    )
                except ValueError:
                    return dep, None
        return dep, None

    return None, None


_NUM_WORDS = {
    "um": 1, "uma": 1, "dois": 2, "duas": 2, "três": 3, "tres": 3,
    "quatro": 4, "cinco": 5, "seis": 6, "sete": 7, "oito": 8,
    "nove": 9, "dez": 10, "onze": 11, "doze": 12, "treze": 13,
    "quatorze": 14, "catorze": 14, "quinze": 15,
}


def _extract_trip_duration(text: str) -> Optional[int]:
    """Detecta duração da viagem: 'viagem de 3 dias', '5 noites', 'fim de semana', 'uma semana'.

    Retorna número de dias ou None.
    """
    import re as _re
    lower = text.lower()

    # "fim de semana" → 2 dias (sex-dom = 3, mas usualmente 2 noites)
    if _re.search(r"\bfim\s+de\s+semana\b", lower):
        return 2

    # "X semanas" → X*7
    m = _re.search(r"\b(\d+|" + "|".join(_NUM_WORDS) + r")\s+semanas?\b", lower)
    if m:
        n = _NUM_WORDS.get(m.group(1), 0) or int(m.group(1) or 0)
        if n:
            return n * 7

    # "X dias" / "X noites" — só conta se há contexto de duração de viagem,
    # evita falso match com "X dias antes" (que é flex)
    duration_patterns = [
        r"viagem\s+de\s+(\d+|" + "|".join(_NUM_WORDS) + r")\s+(dias?|noites?)",
        r"ficar\s+(?:por\s+)?(\d+|" + "|".join(_NUM_WORDS) + r")\s+(dias?|noites?)",
        r"(\d+|" + "|".join(_NUM_WORDS) + r")\s+(dias?|noites?)\s+(?:de\s+)?(?:viagem|l[aá]|fora|hospedad)",
        r"apenas\s+(?:de\s+)?(\d+|" + "|".join(_NUM_WORDS) + r")\s+(dias?|noites?)",
    ]
    for pat in duration_patterns:
        m = _re.search(pat, lower)
        if m:
            raw = m.group(1)
            n = _NUM_WORDS.get(raw, 0) or int(raw)
            if n > 0:
                return n
    return None


_MONTH_ABBR = {
    "jan": 1, "fev": 2, "mar": 3, "abr": 4, "mai": 5, "jun": 6,
    "jul": 7, "ago": 8, "set": 9, "out": 10, "nov": 11, "dez": 12,
    # Variantes com til/acento/3+ letras
    "jane": 1, "feve": 2, "marc": 3, "abri": 4, "maio": 5, "junh": 6,
    "julh": 7, "agos": 8, "sete": 9, "outu": 10, "nove": 11, "deze": 12,
}


def _extract_date_fallback(text: str) -> Optional[date]:
    """Pega data em formatos que o parser oficial não cobre.

    Suporta:
      - mês por extenso abreviado: "10/out", "15/jan/2027", "5/dez"
      - separadores variados (parser nativo só pega /): "15;09", "15.09", "15-09"
      - DD/MM puro: "15/09" → 15 de setembro (próxima ocorrência)
    """
    import re as _re
    from datetime import date as _date
    today = _date.today()
    lower = text.lower()

    # 1) Mês por extenso abreviado: "10/out", "15-out-2027"
    m = _re.search(r"\b(\d{1,2})[\/\-\.;](\w{3,9})(?:[\/\-\.;](\d{2,4}))?\b", lower)
    if m and m.group(2).isalpha():
        month_key = m.group(2)[:3]
        month = _MONTH_ABBR.get(month_key)
        if month:
            day = int(m.group(1))
            year_raw = m.group(3)
            if year_raw:
                year = int(year_raw)
                if year < 100:
                    year += 2000
            else:
                year = today.year
                try:
                    if _date(year, month, day) < today:
                        year += 1
                except ValueError:
                    return None
            try:
                return _date(year, month, day)
            except ValueError:
                return None

    # 2) DD<sep>MM[<sep>YYYY] com separadores múltiplos (; . , - / | espaço)
    m = _re.search(
        r"\b(\d{1,2})[\/\-\.;,|\s](\d{1,2})(?:[\/\-\.;,|\s](\d{2,4}))?\b",
        lower,
    )
    if m:
        day = int(m.group(1))
        month = int(m.group(2))
        if not (1 <= day <= 31 and 1 <= month <= 12):
            return None
        year_raw = m.group(3)
        if year_raw:
            year = int(year_raw)
            if year < 100:
                year += 2000
        else:
            year = today.year
            try:
                if _date(year, month, day) < today:
                    year += 1
            except ValueError:
                return None
        try:
            return _date(year, month, day)
        except ValueError:
            return None

    return None


def _extract_children_info(text: str) -> Dict[str, Any]:
    """Extrai número de crianças e suas idades da mensagem do vendedor.

    Heurísticas:
      - "2 crianças", "1 criança", "duas crianças" → children
      - "1 bebê", "2 bebês", "infante" → infants
      - "de 3 anos", "3 anos as duas", "uma de 5 e uma de 7" → ages
    """
    import re as _re
    out: Dict[str, Any] = {}
    lower = text.lower()

    # número de crianças
    children_patterns = [
        (r"\b(\d+)\s+crian[cç]as?\b", lambda m: int(m.group(1))),
        (r"\buma?\s+crian[cç]a\b", lambda m: 1),
        (r"\bduas?\s+crian[cç]as\b", lambda m: 2),
        (r"\btr[eê]s\s+crian[cç]as\b", lambda m: 3),
    ]
    for pat, fn in children_patterns:
        m = _re.search(pat, lower)
        if m:
            out["children"] = fn(m)
            break

    # bebês / infants
    infant_patterns = [
        (r"\b(\d+)\s+beb[êe]s?\b", lambda m: int(m.group(1))),
        (r"\buma?\s+beb[êe]\b", lambda m: 1),
        (r"\b(\d+)\s+infante?s?\b", lambda m: int(m.group(1))),
    ]
    for pat, fn in infant_patterns:
        m = _re.search(pat, lower)
        if m:
            out["infants"] = fn(m)
            break

    # idades: "3 anos", "5 e 7 anos", "as duas com 3 anos"
    ages: List[int] = []
    for m in _re.finditer(r"\b(\d{1,2})\s*anos?\b", lower):
        try:
            age = int(m.group(1))
            if 0 <= age <= 17:
                ages.append(age)
        except ValueError:
            continue
    # Se mencionou "as duas/as tres/ambas/ambos com X anos", replica
    if ages and len(ages) == 1:
        if _re.search(r"\b(as duas|ambas|os dois|ambos|cada uma|cada um)\b", lower):
            ages = [ages[0]] * (out.get("children", 2))
        elif _re.search(r"\bas tr[eê]s\b", lower):
            ages = [ages[0]] * 3
    if ages:
        out["children_ages"] = ages
        # Reclassifica em children vs infants pelas idades, se temos contagem
        if "children" not in out and "infants" not in out:
            out["children"] = sum(1 for a in ages if a >= 2)
            inf = sum(1 for a in ages if a < 2)
            if inf:
                out["infants"] = inf

    return out


def is_new_search(intent: Dict[str, Any]) -> bool:
    """A mensagem define uma rota completa (origem + destino)? Então é um novo
    pedido de cotação — não deve herdar datas/flex de buscas anteriores."""
    return bool(intent.get("origin_iata") and intent.get("destination_iata"))


# Sinais textuais de uma NOVA cotação (rota nova), mesmo que o IATA ainda não
# resolva. Usado pelo router/routes pra LIMPAR resultados do turno anterior
# antes do grafo — senão o router vê presented_offers e vai pra refinement
# (repetindo o resultado velho). Lê melhor errar pro lado de "é nova busca"
# (re-pesquisa) que reaproveitar resultado errado.
_NEW_QUOTE_RE = re.compile(
    r"\b[a-zà-ú]{3,}\s+(?:para|pra)\s+[a-zà-ú]{3,}"          # "X para/pra Y"
    r"|[a-z]{2,4}\s*(?:->|→)\s*[a-z]{2,4}"                    # "X -> Y"
    r"|\b(?:voo|passagem|cota[çc][aã]o)\s+(?:de|para|pra)\b"  # "voo de / passagem para"
    r"|\bsaindo de\b|\bcom destino a\b",
    re.IGNORECASE,
)


def looks_like_new_quote(text: str) -> bool:
    """Heurística leve (regex) — a mensagem parece abrir uma cotação nova?"""
    return bool(_NEW_QUOTE_RE.search(text or ""))


# Slots de BUSCA que devem ser zerados quando uma nova rota é informada, pra
# evitar que um thread fique "envenenado" com datas/janelas/flex de buscas
# antigas. Passageiros/cabine (adults, children, cabin) NÃO são zerados —
# costumam valer pro mesmo atendimento.
_SEARCH_SCOPED_SLOTS = (
    "date_start", "date_end", "date_return", "return_from", "return_to",
    "flex_mode", "flex_days", "trip_duration_days", "trip_type",
    "direct_only", "baggage_checked",
    # Confirmação internacional: uma busca NOVA não pode herdar o "aguardando
    # confirmação", as datas de radar nem o pacote de confirmação anterior
    # (thread poisoning).
    "intl_awaiting_confirmation", "intl_radar_dates", "intl_confirmation",
    "origin_iatas", "destination_iatas",
)


def _merge_parsed_intent(slots: IntakeSlots, intent: Dict[str, Any]) -> IntakeSlots:
    """Mescla o que o intent_parser extraiu com o que já temos. Não sobrescreve confirmado."""
    new_slots: IntakeSlots = dict(slots)  # type: ignore[assignment]

    # NOVA BUSCA: rota completa na mensagem → zera datas/flex/janelas anteriores
    # e sobrescreve a rota. Sem isso, "voo X→Y dia 20 só ida" depois de um teste
    # de flex herdaria as janelas velhas e buscaria a rota/datas erradas.
    if is_new_search(intent):
        for k in _SEARCH_SCOPED_SLOTS:
            new_slots.pop(k, None)  # type: ignore[misc]
        new_slots["origin_iata"] = intent["origin_iata"]
        new_slots["destination_iata"] = intent["destination_iata"]
        if intent.get("origin_city"):
            new_slots["origin_city"] = intent["origin_city"]
        if intent.get("destination_city"):
            new_slots["destination_city"] = intent["destination_city"]

    def _set_if_empty(key: str, value: Any) -> None:
        if value is not None and not new_slots.get(key):
            new_slots[key] = value  # type: ignore[literal-required]

    _set_if_empty("origin_city", intent.get("origin_city"))
    _set_if_empty("origin_iata", intent.get("origin_iata"))
    _set_if_empty("destination_city", intent.get("destination_city"))
    _set_if_empty("destination_iata", intent.get("destination_iata"))

    ds = intent.get("date_start")
    if isinstance(ds, date) and not new_slots.get("date_start"):
        new_slots["date_start"] = ds.isoformat()

    dr = intent.get("date_return")
    if isinstance(dr, date) and not new_slots.get("date_return"):
        new_slots["date_return"] = dr.isoformat()
        new_slots["trip_type"] = "roundtrip"
    elif not new_slots.get("trip_type"):
        new_slots["trip_type"] = "oneway"

    if intent.get("adults") and not new_slots.get("adults"):
        new_slots["adults"] = int(intent["adults"])

    cabin = intent.get("cabin")
    if cabin and not new_slots.get("cabin"):
        new_slots["cabin"] = cabin if isinstance(cabin, str) else cabin.value

    if intent.get("direct_only") is not None and not new_slots.get("direct_only"):
        new_slots["direct_only"] = bool(intent["direct_only"])

    # Bagagem despachada: None = vendedor não mencionou; só grava quando explícito.
    if intent.get("baggage_checked") is not None and new_slots.get("baggage_checked") is None:
        new_slots["baggage_checked"] = bool(intent["baggage_checked"])

    # Resolve cidade → IATA quando temos só nome — com fallback de variações
    if new_slots.get("origin_city") and not new_slots.get("origin_iata"):
        iatas = _resolve_city_smart(new_slots["origin_city"])
        if iatas:
            new_slots["origin_iata"] = iatas[0]
    if new_slots.get("destination_city") and not new_slots.get("destination_iata"):
        iatas = _resolve_city_smart(new_slots["destination_city"])
        if iatas:
            new_slots["destination_iata"] = iatas[0]

    return new_slots


def _missing_required(slots: IntakeSlots) -> Optional[str]:
    for f in _REQUIRED_FIELDS:
        if not slots.get(f):
            return f
    # Roundtrip precisa de volta — mas uma JANELA de volta (return_from/to) ou
    # uma DURAÇÃO de viagem (trip_duration_days dentro de um range) já bastam:
    # o orchestrator deriva as datas de volta a partir delas.
    if slots.get("trip_type") == "roundtrip" and not slots.get("date_return"):
        has_return_window = bool(slots.get("return_from") and slots.get("return_to"))
        has_duration = bool(slots.get("trip_duration_days") and slots.get("date_end"))
        if not (has_return_window or has_duration):
            return "date_return"
    return None


def intake_node(state: ChatState) -> ChatState:
    """Nó de intake.

    1. Pega a última mensagem do usuário.
    2. Roda parser regex pra extrair o que der.
    3. Mescla com slots existentes.
    4. Se completou, pergunta confirmação. Se já confirmou na mensagem ("sim", "pode"),
       marca `intake_complete=True` e rota pro Orchestrator.
    5. Se faltam slots, gera pergunta via LLM e devolve mensagem AI.
    """
    slots: IntakeSlots = state.get("slots") or {}  # type: ignore[assignment]
    messages = state.get("messages", [])
    errors = list(state.get("errors", []))

    last_user_msg = ""
    for msg in reversed(messages):
        if isinstance(msg, HumanMessage):
            last_user_msg = msg.content if isinstance(msg.content, str) else str(msg.content)
            break

    # Passada barata regex/heurística
    new_search = False
    if last_user_msg:
        try:
            parsed = parse_intent_ptbr(last_user_msg)
            parsed_dict = parsed.model_dump() if hasattr(parsed, "model_dump") else dict(parsed)
            new_search = is_new_search(parsed_dict)
            slots = _merge_parsed_intent(slots, parsed_dict)
        except Exception as e:
            errors.append(f"intent_parser falhou: {e}")

    # Histórico inteiro do usuário (todas as mensagens humanas concatenadas) —
    # usamos pra capturar info espalhada em respostas a perguntas anteriores.
    history_text = " ".join(
        m.content if isinstance(m, HumanMessage) and isinstance(m.content, str) else ""
        for m in messages
    )

    # Em NOVA busca, os extratores de data/janela/duração olham SÓ a mensagem
    # atual — senão frases de flex de buscas antigas no histórico voltam a ser
    # aplicadas (thread "envenenado").
    extraction_text = last_user_msg if new_search else history_text

    # Crianças/bebês (parser oficial não cobre).
    if history_text:
        kid_info = _extract_children_info(history_text)
        for k, v in kid_info.items():
            if v is not None and not slots.get(k):
                slots[k] = v  # type: ignore[literal-required]

    # Datas em formato abreviado tipo "10/out" (parser não cobre).
    if extraction_text and not slots.get("date_start"):
        d = _extract_date_fallback(extraction_text)
        if d:
            slots["date_start"] = d.isoformat()

    # Rota em formatos que o parser não pega ("A <> B", "A - B", "A → B").
    if history_text and (not slots.get("origin_iata") or not slots.get("destination_iata")):
        origin_raw, dest_raw = _extract_route_fallback(history_text)
        if origin_raw and not slots.get("origin_iata"):
            iatas = _resolve_city_smart(origin_raw)
            if iatas:
                slots["origin_iata"] = iatas[0]
                slots.setdefault("origin_city", origin_raw)
        if dest_raw and not slots.get("destination_iata"):
            iatas = _resolve_city_smart(dest_raw)
            if iatas:
                slots["destination_iata"] = iatas[0]
                slots.setdefault("destination_city", dest_raw)

    # Duas janelas (ida + volta separadas): "ir entre 10 e 12, voltando entre 25 e 26".
    if extraction_text and not (slots.get("return_from") and slots.get("return_to")):
        dep_w, ret_w = _extract_depart_return_windows(extraction_text)
        if ret_w:
            slots["trip_type"] = "roundtrip"
            slots["flex_mode"] = "range"
            if dep_w:
                slots["date_start"] = dep_w[0].isoformat()
                slots["date_end"] = dep_w[1].isoformat()
            slots["return_from"] = ret_w[0].isoformat()
            slots["return_to"] = ret_w[1].isoformat()

    # Range "1 a 13 de junho" / "01/06 a 13/06" — vira flex_mode=range (só ida).
    if extraction_text and not (slots.get("return_from") and slots.get("return_to")):
        rng = _extract_date_range(extraction_text)
        if rng:
            ds, de = rng
            slots.setdefault("flex_mode", "range")
            if slots.get("flex_mode") in (None, "none", ""):
                slots["flex_mode"] = "range"
            if not slots.get("date_start") or slots["date_start"] > ds.isoformat():
                slots["date_start"] = ds.isoformat()
            slots["date_end"] = de.isoformat()

    # Duração da viagem ("viagem de 3 dias", "fim de semana")
    if extraction_text and not slots.get("trip_duration_days"):
        dur = _extract_trip_duration(extraction_text)
        if dur:
            slots["trip_duration_days"] = dur
            # Se tem duração, é roundtrip implícito
            if slots.get("trip_type") in (None, "oneway"):
                slots["trip_type"] = "roundtrip"

    # ─── INTERPRETAÇÃO POR LLM (primária; o regex acima é fallback) ────
    # A LLM vê a conversa inteira e devolve os blocos de filtro estruturados
    # (rota, janela de ida, janela de volta, flex, mala, direto, horário). Em
    # frases naturais ela é bem mais robusta que o regex. Quando resolve rota +
    # datas com confiança, SOBRESCREVE os slots de busca; se cair, ficam os do
    # regex. IATA/datas são validados de forma determinística no `to_slots`.
    if history_text:
        try:
            from backend.app.ai.agents.interpreter import interpret, to_slots
            raw = interpret(history_text, today=date.today())
            if raw:
                llm = to_slots(raw, today=date.today())
                if llm.get("origin_iata") and llm.get("destination_iata"):
                    # Interpretação COMPLETA da conversa → limpa janelas/flex
                    # antigos (evita herdar de turnos passados) e aplica a da LLM.
                    for k in ("date_end", "return_from", "return_to", "trip_duration_days"):
                        slots.pop(k, None)  # type: ignore[misc]
                    slots.update(llm)  # type: ignore[typeddict-item]
                else:
                    # Interpretação PARCIAL: aproveita o que veio mesmo sem a rota
                    # completa. Aplica o lado de rota que resolveu, os NOMES de
                    # cidade preservados, e os campos não-rota (pax, datas, flex).
                    _ROUTE_SIDE = ("origin_iata", "origin_iatas", "origin_city",
                                   "destination_iata", "destination_iatas", "destination_city")
                    _NON_ROUTE = ("date_start", "date_end", "date_return", "return_from",
                                  "return_to", "trip_type", "trip_duration_days", "flex_mode",
                                  "adults", "children", "infants", "baggage_checked",
                                  "direct_only", "cabin", "time_preference", "notes")
                    for k in (*_ROUTE_SIDE, *_NON_ROUTE):
                        if llm.get(k) is not None:
                            slots[k] = llm[k]  # type: ignore[literal-required]
        except Exception as e:
            errors.append(f"llm_interpreter: {e}")

    # Se tava aguardando a direção da flex, tenta extrair da resposta.
    if state.get("awaiting_field") == "flex_direction" and last_user_msg:
        msg = last_user_msg.lower()
        if any(w in msg for w in ("ambos", "ambas", "antes e depois", "antes ou depois", "tanto faz", "qualquer", "os dois", "ida e volta")):
            slots["flex_mode"] = "plusminus"
        elif any(w in msg for w in ("antes", "antecip", "mais cedo", "pra trás", "anterior")):
            slots["flex_mode"] = "minus"
        elif any(w in msg for w in ("depois", "posterga", "mais tarde", "pra frente", "para frente", "posterior")):
            slots["flex_mode"] = "plus"

    has_all_required = _missing_required(slots) is None

    # Gates extras (todos precisam estar OK pra ir pro orchestrator):
    flex_needs_direction = _flex_lacks_direction(slots, last_user_msg)
    children_need_ages = _children_lack_ages(slots)

    attempts = int(state.get("intake_attempts", 0) or 0)

    if has_all_required and not flex_needs_direction and not children_need_ages:
        # Tudo pronto → busca direto sem re-confirmação.
        return {
            **state,
            "slots": slots,
            "intake_complete": True,
            "awaiting_field": None,
            "intake_attempts": 0,
            "next_node": "orchestrator",
            "errors": errors,
        }

    # Loop guard agressivo: após N tentativas, força avanço.
    # - Se tem essentials: vai pro orchestrator mesmo se gates menores falharem.
    # - Se ainda falta info crítica: devolve mensagem direta de erro + reset.
    if attempts >= _MAX_INTAKE_ATTEMPTS:
        if has_all_required:
            logger.warning(
                "intake loop guard: %d tentativas → orchestrator forçado "
                "(flex_dir=%s, children_ages=%s)",
                attempts, flex_needs_direction, children_need_ages,
            )
            return {
                **state, "slots": slots,
                "intake_complete": True, "awaiting_field": None,
                "intake_attempts": 0,
                "next_node": "orchestrator", "errors": errors,
            }
        # Sem essentials após N tries — pede REFRAMING explícito ao usuário
        missing_label = _missing_required(slots) or "alguma informação"
        logger.warning(
            "intake loop guard: %d tentativas SEM essentials (falta %s) → mensagem de reset",
            attempts, missing_label,
        )
        # B2: se falta só o IATA de UMA cidade cujo NOME a gente preservou (não
        # resolveu na base), pergunta o aeroporto DESSA cidade — melhor que o
        # reset genérico. Usa o nome guardado pela LLM (BUG 2) / regex.
        unresolved_city = None
        if missing_label == "origin_iata" and slots.get("origin_city") and not slots.get("origin_iata"):
            unresolved_city = slots["origin_city"]
        elif missing_label == "destination_iata" and slots.get("destination_city") and not slots.get("destination_iata"):
            unresolved_city = slots["destination_city"]
        if unresolved_city:
            ask = (
                f"Não encontrei o aeroporto de **{unresolved_city}** na minha base. "
                f"Confirma a cidade/país, ou me passa o código IATA de 3 letras "
                f"(ex.: Marselha é MRS)."
            )
            return {
                **state, "slots": slots,
                "intake_complete": False, "awaiting_field": None,
                "intake_attempts": 0,
                "messages": [AIMessage(content=ask)],
                "next_node": "end", "errors": errors,
            }

        readable_missing = {
            "origin_iata": "aeroporto de origem",
            "destination_iata": "aeroporto de destino",
            "date_start": "data de ida",
            "date_return": "data de volta",
            "adults": "número de passageiros adultos",
        }.get(missing_label, missing_label)
        reset_text = (
            f"Não consegui identificar **{readable_missing}** depois de algumas "
            f"trocas — vamos recomeçar pra agilizar. Manda numa frase só: "
            f"origem, destino, data e número de passageiros. "
            f"Ex.: *\"GRU para LIS, 15/jun, 2 adultos\"*."
        )
        return {
            **state, "slots": slots,
            "intake_complete": False, "awaiting_field": None,
            "intake_attempts": 0,
            "messages": [AIMessage(content=reset_text)],
            "next_node": "end", "errors": errors,
        }

    # Prioridade das perguntas (do mais importante pro mais geral):
    # idade de criança (afeta preço) > direção da flex > campo faltante genérico.
    # Usamos templates determinísticos por padrão — evita LLM criar loops de
    # confirmações redundantes ("você quer X mesmo?" sobre algo já confirmado).
    if children_need_ages:
        next_msg_text = _ask_children_ages(slots)
        new_awaiting = "children_ages"
    elif flex_needs_direction:
        next_msg_text = _ask_flex_direction(slots)
        new_awaiting = "flex_direction"
    else:
        next_msg_text = _template_question(slots)
        new_awaiting = _missing_required(slots)

    return {
        **state,
        "slots": slots,
        "intake_complete": False,
        "awaiting_field": new_awaiting,
        "intake_attempts": attempts + 1,
        "messages": [AIMessage(content=next_msg_text)],
        "next_node": "end",
        "errors": errors,
    }


def _children_lack_ages(slots: IntakeSlots) -> bool:
    """DESATIVADO (decisão de negócio): nunca perguntamos idade. A política é só
    bebê-de-colo (`infants`, ~10%/gratuito) vs criança-com-assento (`children`,
    tarifa cheia) — a palavra do vendedor já distingue. A cotação NÃO trava
    pedindo idade. Ver pricing.py."""
    return False


def _ask_children_ages(slots: IntakeSlots) -> str:
    children = int(slots.get("children", 0) or 0)
    infants = int(slots.get("infants", 0) or 0)
    ages = slots.get("children_ages") or []
    missing = (children + infants) - len(ages)

    pax_parts = []
    if children:
        pax_parts.append(f"{children} criança{'s' if children != 1 else ''}")
    if infants:
        pax_parts.append(f"{infants} bebê{'s' if infants != 1 else ''}")
    pax_label = " e ".join(pax_parts)

    if missing == 1 and (children + infants) == 1:
        return (
            f"Antes de cotar — qual a idade da criança? "
            f"Isso muda bastante o preço: até 2 anos costuma ser bebê "
            f"(gratuito ou ~10% da tarifa), 2-11 anos paga ~75%, "
            f"e a partir de 12 é tarifa adulta."
        )
    return (
        f"Antes de cotar, preciso da idade de cada um dos {pax_label} — "
        f"isso muda direto o preço (bebê até 2 anos costuma ser gratuito, "
        f"criança 2-11 anos paga ~75%, 12+ tarifa adulta). "
        f"Pode me passar a idade de cada um?"
    )


def _flex_lacks_direction(slots: IntakeSlots, last_msg: str) -> bool:
    """Detecta se há flex_days > 0 mas a direção (mais/menos/ambos) é desconhecida.

    `flex_mode` no slot deveria refletir: "none", "plusminus" (ambos),
    "plus" (só pra frente), "minus" (só pra trás).
    Se o parser deixou flex_mode vazio E o vendedor mencionou flexibilidade
    sem qualificar, perguntamos.
    """
    flex_days = int(slots.get("flex_days") or 0)
    flex_mode = (slots.get("flex_mode") or "").lower()
    if flex_days <= 0:
        return False
    if flex_mode in ("plusminus", "plus", "minus", "range"):
        return False
    # Mencionou número de dias mas sem direção clara.
    # Aceita também palavras na mensagem que já indicam direção.
    msg = last_msg.lower()
    if any(w in msg for w in ("antes ou depois", "antes e depois", "para mais ou menos", "± ", "+/-", "ambos", "qualquer direção")):
        return False
    if any(w in msg for w in ("antes", "antecip", "mais cedo")) and "depois" not in msg:
        return False  # só pra trás — já claro
    if any(w in msg for w in ("depois", "posterga", "mais tarde", "para frente", "pra frente")) and "antes" not in msg:
        return False  # só pra frente — já claro
    return True


def _ask_flex_direction(slots: IntakeSlots) -> str:
    days = slots.get("flex_days") or 0
    return (
        f"Você mencionou flexibilidade de {days} dias — pra ter certeza, "
        f"é pra **antes** da data, **depois**, ou **ambos** (antes e depois)?"
    )


def _ask_next(slots: IntakeSlots, messages: list) -> str:
    """Chama o LLM com slots atuais pra formular a próxima pergunta."""
    try:
        model = get_cached_chat_model(primary=False)
    except Exception as e:
        logger.warning("LLM indisponível em intake (%s) — caindo pra template", e)
        return _template_question(slots)

    slots_text = json.dumps(slots, ensure_ascii=False, indent=2)
    sys = SystemMessage(content=intake_system_prompt())
    user = HumanMessage(content=(
        f"SLOTS ATUAIS:\n```json\n{slots_text}\n```\n\n"
        f"FALTAM: {_missing_required(slots) or 'nada — pedir confirmação'}.\n\n"
        "Formule a próxima fala em PT-BR. Uma fala só, natural, sem markdown."
    ))
    try:
        resp = model.invoke([sys] + messages + [user])
        content = resp.content if isinstance(resp.content, str) else str(resp.content)
        return content.strip()
    except Exception as e:
        logger.warning("LLM falhou no intake (%s) — template", e)
        return _template_question(slots)


def _template_question(slots: IntakeSlots) -> str:
    missing = _missing_required(slots)
    if missing == "origin_iata":
        return "De onde o cliente quer sair? Pode me dar a cidade ou o código do aeroporto."
    if missing == "destination_iata":
        return "E pra onde ele quer ir?"
    if missing == "date_start":
        return "Qual a data de ida?"
    if missing == "date_return":
        return "Qual a data de volta?"
    if missing == "adults":
        return "Quantos passageiros adultos?"
    if missing is None:
        return (
            f"Vou cotar: {slots.get('origin_iata')} → {slots.get('destination_iata')}, "
            f"ida {slots.get('date_start')}"
            + (f", volta {slots.get('date_return')}" if slots.get('date_return') else "")
            + f", {slots.get('adults', 1)} adulto(s). Pode buscar?"
        )
    return "Pode me passar mais detalhes da viagem?"
