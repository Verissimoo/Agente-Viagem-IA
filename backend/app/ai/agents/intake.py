"""Intake — extrai slots e conversa com o vendedor até ter tudo.

Estratégia híbrida: o `intent_parser` regex/heurístico já existente faz uma
primeira passada barata; o LLM completa o que faltou e formula a próxima
pergunta de forma natural.
"""
from __future__ import annotations

import json
import logging
from datetime import date
from typing import Any, Dict, Optional

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

    # Padrão: "X a Y de MES" ou "entre X e Y de MES"
    m = _re.search(
        r"(?:entre\s+)?(\d{1,2})\s*(?:a|até|e|-)\s*(\d{1,2})\s+de\s+"
        r"(janeiro|fevereiro|março|marco|abril|maio|junho|julho|agosto|"
        r"setembro|outubro|novembro|dezembro)",
        lower,
    )
    if m:
        d1, d2 = int(m.group(1)), int(m.group(2))
        month_map = {
            "janeiro": 1, "fevereiro": 2, "março": 3, "marco": 3, "abril": 4,
            "maio": 5, "junho": 6, "julho": 7, "agosto": 8,
            "setembro": 9, "outubro": 10, "novembro": 11, "dezembro": 12,
        }
        month = month_map.get(m.group(3))
        if month:
            year = today.year
            try:
                candidate_end = _date(year, month, d2)
                if candidate_end < today:
                    year += 1
                start = _date(year, month, min(d1, d2))
                end = _date(year, month, max(d1, d2))
                return (start, end)
            except ValueError:
                return None

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


def _merge_parsed_intent(slots: IntakeSlots, intent: Dict[str, Any]) -> IntakeSlots:
    """Mescla o que o intent_parser extraiu com o que já temos. Não sobrescreve confirmado."""
    new_slots: IntakeSlots = dict(slots)  # type: ignore[assignment]

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
    if slots.get("trip_type") == "roundtrip" and not slots.get("date_return"):
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
    if last_user_msg:
        try:
            parsed = parse_intent_ptbr(last_user_msg)
            parsed_dict = parsed.model_dump() if hasattr(parsed, "model_dump") else dict(parsed)
            slots = _merge_parsed_intent(slots, parsed_dict)
        except Exception as e:
            errors.append(f"intent_parser falhou: {e}")

    # Histórico inteiro do usuário (todas as mensagens humanas concatenadas) —
    # usamos pra capturar info espalhada em respostas a perguntas anteriores.
    history_text = " ".join(
        m.content if isinstance(m, HumanMessage) and isinstance(m.content, str) else ""
        for m in messages
    )

    # Crianças/bebês (parser oficial não cobre).
    if history_text:
        kid_info = _extract_children_info(history_text)
        for k, v in kid_info.items():
            if v is not None and not slots.get(k):
                slots[k] = v  # type: ignore[literal-required]

    # Datas em formato abreviado tipo "10/out" (parser não cobre).
    if history_text and not slots.get("date_start"):
        d = _extract_date_fallback(history_text)
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

    # Range "1 a 13 de junho" / "01/06 a 13/06" — vira flex_mode=range
    if history_text:
        rng = _extract_date_range(history_text)
        if rng:
            ds, de = rng
            slots.setdefault("flex_mode", "range")
            if slots.get("flex_mode") in (None, "none", ""):
                slots["flex_mode"] = "range"
            if not slots.get("date_start") or slots["date_start"] > ds.isoformat():
                slots["date_start"] = ds.isoformat()
            slots["date_end"] = de.isoformat()

    # Duração da viagem ("viagem de 3 dias", "fim de semana")
    if history_text and not slots.get("trip_duration_days"):
        dur = _extract_trip_duration(history_text)
        if dur:
            slots["trip_duration_days"] = dur
            # Se tem duração, é roundtrip implícito
            if slots.get("trip_type") in (None, "oneway"):
                slots["trip_type"] = "roundtrip"

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
    """Detecta se há crianças/bebês mas sem todas as idades — não pode
    cotar sem isso porque a tarifa varia bastante por faixa etária."""
    children = int(slots.get("children", 0) or 0)
    infants = int(slots.get("infants", 0) or 0)
    total_minors = children + infants
    if total_minors == 0:
        return False
    ages = slots.get("children_ages") or []
    return len(ages) < total_minors


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
