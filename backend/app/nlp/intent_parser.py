import re
import json
import os
from datetime import date, timedelta
from typing import Optional, Dict, Any
# Regex/heuristics only. LLM is reserved for backend.app.ai.summarizer.
from backend.app.domain.models import ParsedIntent, TripType, CabinClass
from backend.app.providers.buscamilhas.iata_resolver import resolve_city_to_iatas, normalize_city_key

IATA_STOPWORDS = {"UMA", "IDA", "VOL", "PRA", "COM", "TEM", "SAO", "DIA", "PRO", "VOU", "ELA", "ELE", "EST", "NOS", "POR"}

def clean_text_ptbr(text: str) -> str:
    """
    Remove frases de preenchimento iniciais e normaliza o texto.
    Ex: 'Quero uma passagem de Brasília para Lisboa' -> 'de Brasília para Lisboa'
    """
    if not text:
        return ""
    
    t = text.lower().strip()
    # Normalizar pontuação básica para espaços
    t = re.sub(r"[,.;!?]", " ", t)
    
    # Expressões de preenchimento para remover (somente no início)
    filler_prefixes = [
        r"^quero\s+(?:uma\s+|um\s+)?(?:passagem|voo|viagem|cotacao|cotar)?(?:\s+de\s+|\s+para\s+|\s+pra\s+)?",
        r"^preciso\s+(?:de\s+)?(?:uma\s+|um\s+)?(?:passagem|voo|viagem)?",
        r"^gostaria\s+(?:de\s+)?(?:uma\s+|um\s+)?(?:passagem|voo|viagem)?",
        r"^me\s+ve\s+(?:uma\s+|um\s+)?(?:passagem|voo|viagem)?",
        r"^(?:uma\s+|um\s+)?(?:passagem|voo|viagem|cotacao)\s+(?:de\s+|para\s+|pra\s+)?",
        r"^busque\s+(?:de\s+|para\s+|pra\s+)?",
        r"^procure\s+(?:de\s+|para\s+|pra\s+)?",
    ]
    
    for pattern in filler_prefixes:
        new_t = re.sub(pattern, "", t).strip()
        # Se removeu algo, mas sobrou "de " ou "para " no início que foi removido acidentalmente, 
        # ou se o texto mudou, precisamos ter cuidado para manter os delimitadores.
        # Na verdade, o ideal é remover o máximo e depois o regex de extração (Part B) cuida do resto.
        if new_t != t:
            # Se o texto original começava com "de " ou "para ", garanta que não perdemos o delimitador
            # se ele for necessário para o regex. Entretanto, o regex posterior aceita sem "de".
            t = new_t
            break

    # Se o texto ficou "brasília para lisboa...", mantemos.
    return t

_MONTHS_PTBR = {
    "janeiro": 1, "jan": 1,
    "fevereiro": 2, "fev": 2,
    "marco": 3, "marc": 3, "mar": 3,
    "abril": 4, "abr": 4,
    "maio": 5, "mai": 5,
    "junho": 6, "jun": 6,
    "julho": 7, "jul": 7,
    "agosto": 8, "ago": 8,
    "setembro": 9, "set": 9,
    "outubro": 10, "out": 10,
    "novembro": 11, "nov": 11,
    "dezembro": 12, "dez": 12,
}


def _strip_accents(s: str) -> str:
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


def _extract_dates(text: str) -> tuple[Optional[date], Optional[date]]:
    """Extracts up to two dates from free text in PT-BR.

    Supported formats:
      - Numeric:   `21/05/2026`, `21-5-26`, `21/5`
      - PT-BR:     `21 de maio`, `21 maio 2026`, `dia 21 de maio`
    Year defaults to the current year if not given; if the resulting date
    is in the past, rolls forward to next year.
    """
    today = date.today()
    text_norm = _strip_accents(text.lower())

    dates: list[date] = []

    # Numeric dd/mm[/yy[yy]]
    for m in re.finditer(r"\b(\d{1,2})[/-](\d{1,2})(?:[/-](\d{2,4}))?\b", text_norm):
        try:
            d = int(m.group(1))
            mo = int(m.group(2))
            y_raw = m.group(3)
            if y_raw:
                y = int(y_raw)
                if y < 100:
                    y += 2000
            else:
                y = today.year
            cand = date(y, mo, d)
            if not y_raw and cand < today:
                cand = date(y + 1, mo, d)
            dates.append(cand)
        except (ValueError, OverflowError):
            continue

    # PT-BR: "21 de maio [de 2026]" / "21 maio" / "dia 21 de maio"
    months_pattern = "|".join(sorted(_MONTHS_PTBR.keys(), key=len, reverse=True))
    pt_re = re.compile(
        rf"\b(?:dia\s+)?(\d{{1,2}})\s+(?:de\s+)?({months_pattern})(?:\s+(?:de\s+)?(\d{{4}}))?\b"
    )
    for m in pt_re.finditer(text_norm):
        try:
            d = int(m.group(1))
            mo = _MONTHS_PTBR[m.group(2)]
            y_raw = m.group(3)
            y = int(y_raw) if y_raw else today.year
            cand = date(y, mo, d)
            if not y_raw and cand < today:
                cand = date(y + 1, mo, d)
            dates.append(cand)
        except (ValueError, OverflowError, KeyError):
            continue

    # Deduplicate, sort: first date = departure, second = return.
    dates = sorted(set(dates))
    depart = dates[0] if dates else None
    return_dt = dates[1] if len(dates) > 1 else None
    return depart, return_dt

def parse_intent_regex(text: str) -> ParsedIntent:
    """Fallback usando Regex e heurísticas básicas"""
    text_clean = clean_text_ptbr(text)
    text_lower = text_clean.lower()

    intent = ParsedIntent()

    # 1a. Detecta "flexibilidade até dia X" / "com flexibilidade até X" e
    # REMOVE essa data do texto antes da extração geral — evita que ela seja
    # confundida com data de volta na busca principal.
    flex_until_pattern = (
        r"(?:com\s+)?flex(?:ibilidade|[ií]vel)?\s+at[ée]\s+(?:o\s+)?(?:dia\s+)?"
        r"(\d{1,2}(?:[/-]\d{1,2}(?:[/-]\d{2,4})?)?)"
    )
    flex_until_match = re.search(flex_until_pattern, text_lower)
    flex_until_raw: Optional[str] = None
    text_for_dates = text_lower
    if flex_until_match:
        flex_until_raw = flex_until_match.group(1)
        text_for_dates = re.sub(flex_until_pattern, " ", text_lower)

    # 1b. Datas (do texto limpo da janela "até X")
    intent.date_start, intent.date_return = _extract_dates(text_for_dates)

    # 2. Trip Type
    is_oneway_explicit = any(k in text_lower for k in [
        "apenas ida", "somente ida", "só ida", "so ida", "one way", "oneway",
    ])
    if any(k in text_lower for k in ["ida e volta", "volta dia", "retorno", "roundtrip"]) and not is_oneway_explicit:
        intent.trip_type = TripType.ROUNDTRIP
    else:
        intent.trip_type = TripType.ONEWAY
        # "Apenas ida" → não usar 2a data como volta (vira janela de flex)
        if is_oneway_explicit:
            intent.date_return = None
        
    # 3. Origem / Destino (Regex com prioridade e lookahead)
    # Padrão 1: de <orig> para <dest> (com lookahead para descartar datas/flex)
    # Padrão 2: <orig> para <dest>
    # Padrão 3: <orig> -> <dest>
    
    _STOP = r"dia|em|na|no|ida|volta|data|flex|±|com|para\s+o|as|às|apenas|somente"
    patterns = [
        # de X para Y
        rf"\bde\s+(?P<orig>.+?)\s+(?:para|pra|to)\s+(?P<dest>.+?)(?=\s+(?:{_STOP})\b|\s+\d+|$)",
        # X para Y (sem o 'de' inicial)
        rf"^(?P<orig>.+?)\s+(?:para|pra|to)\s+(?P<dest>.+?)(?=\s+(?:{_STOP})\b|\s+\d+|$)",
        # X -> Y
        rf"(?P<orig>.+?)\s*(?:->|=>)\s*(?P<dest>.+?)(?=\s+(?:{_STOP})\b|\s+\d+|$)",
    ]
    
    extracted = False
    for p in patterns:
        match = re.search(p, text_lower)
        if match:
            origin_raw = match.group("orig").strip()
            dest_raw = match.group("dest").strip()
            
            # Resolver cidades
            origin_iatas = resolve_city_to_iatas(origin_raw)
            dest_iatas = resolve_city_to_iatas(dest_raw)
            
            # Anti-IATA Falso (Stopwords)
            def filter_iatas(codes):
                return [c for c in codes if c.upper() not in IATA_STOPWORDS]

            origin_iatas = filter_iatas(origin_iatas)
            dest_iatas = filter_iatas(dest_iatas)

            intent.origin_city = origin_raw.title()
            intent.origin_iata = origin_iatas[0] if origin_iatas else None
            
            intent.destination_city = dest_raw.title()
            intent.destination_iata = dest_iatas[0] if dest_iatas else None
            
            # Se resolveu ambos os IATAs, aumenta a confiança
            if intent.origin_iata and intent.destination_iata:
                intent.confidence = 0.85
            else:
                intent.confidence = 0.6
                
            extracted = True
            break
    
    # 4. Voo Direto
    direct_patterns = [
        r"voo\s+direto", r"\bdireto\b", r"sem\s+escala", r"sem\s+escalas",
        r"sem\s+conexão", r"sem\s+conexões", r"não\s+quero\s+conecta", r"nao\s+quero\s+conecta"
    ]
    if any(re.search(p, text_lower) for p in direct_patterns):
        intent.direct_only = True
        
    # 5. Cabine
    if "executiva" in text_lower or "business" in text_lower:
        intent.cabin = CabinClass.BUSINESS
    elif "primeira" in text_lower or "first" in text_lower:
        intent.cabin = CabinClass.FIRST
        
    # 6. Flexibilidade
    # Helper compartilhado para converter strings parciais de data
    def _parse_partial_date(s: str, base_ref: Optional[date] = None) -> Optional[date]:
        if s.isdigit():
            day = int(s)
            ref = base_ref or intent.date_start or date.today()
            try: return date(ref.year, ref.month, day)
            except: return None
        m = re.match(r"(\d{1,2})[/-](\d{1,2})(?:[/-](\d{2,4}))?", s)
        if m:
            d, month, y = m.groups()
            d, month = int(d), int(month)
            if y:
                y = int(y)
                if y < 100: y += 2000
            else:
                ref = base_ref or intent.date_start or date.today()
                y = ref.year
            try: return date(y, month, d)
            except: return None
        return None

    # 6.1 Datas Próximas (±3 dias)
    flex_prox_patterns = [
        r"datas?\s+próximas?", r"datas?\s+proximas?", r"dias?\s+próximas?", r"dias?\s+proximas?",
        r"se\s+tiver\s+o\s+melhor\s+preço", r"melhor\s+preço\s+em\s+datas\s+próximas"
    ]
    if any(re.search(p, text_lower) for p in flex_prox_patterns):
        intent.flex_mode = "plusminus"
        intent.flex_days = 3

    # 6.2 Flexibilidade por ±N dias (padrão antigo melhorado)
    flex_match = re.search(r"(?:flex(?:[ií]vel|ibilidade)?|±|mais\s+ou\s+menos)\s*(?:de\s+)?(\d+)\s*dia", text_lower) or \
                 re.search(r"(\d+)\s*dia(?:s)?\s*(?:de\s*)?flex", text_lower)
    if flex_match:
        intent.flex_mode = "plusminus"
        intent.flex_days = int(flex_match.group(1))

    # 6.3 Intervalo Explícito (Range)
    # Ex: "do dia 5 ao dia 15", "entre 05/03 e 15/03", "de 10/10 a 20/10"
    range_pattern = r"(?:do\s+dia\s+|entre\s+|de\s+)(\d{1,2}(?:[/-]\d{1,2}(?:[/-]\d{2,4})?)?)\s+(?:ao\s+dia\s+|a\s+|e\s+)(\d{1,2}(?:[/-]\d{1,2}(?:[/-]\d{2,4})?)?)"
    range_match = re.search(range_pattern, text_for_dates)
    if range_match:
        from_str = range_match.group(1)
        to_str = range_match.group(2)
        dt_from = _parse_partial_date(from_str)
        dt_to = _parse_partial_date(to_str, base_ref=dt_from)
        if dt_from and dt_to:
            intent.flex_mode = "range"
            intent.depart_date_from = dt_from
            intent.depart_date_to = dt_to

    # 6.4 "flexibilidade até dia X" — captura range a partir de date_start
    if flex_until_raw and intent.date_start:
        dt_to = _parse_partial_date(flex_until_raw, base_ref=intent.date_start)
        if dt_to and dt_to >= intent.date_start:
            intent.flex_mode = "range"
            intent.depart_date_from = intent.date_start
            intent.depart_date_to = dt_to

    # 6.5 "com flexibilidade" sem detalhar — default ±3 dias
    if intent.flex_mode == "none":
        has_generic_flex = re.search(r"\bcom\s+flex(?:ibilidade|[ií]vel)?\b", text_lower) \
                          or re.search(r"\bflex(?:ibilidade|[ií]vel)\b(?!\s+(?:at[ée]|de|±|mais|do|para|entre))", text_lower)
        if has_generic_flex:
            intent.flex_mode = "plusminus"
            intent.flex_days = 3

    # 7. Volta Flexível
    if "volta flex" in text_lower or "retorno flex" in text_lower:
        intent.flex_return = True
        
    # 8. Adultos
    adult_match = re.search(r"(\d+)\s*(?:adulto|pessoa|passageiro)", text_lower)
    if adult_match:
        intent.adults = int(adult_match.group(1))
    elif any(k in text_lower for k in ["eu e minha esposa", "eu e meu marido", "casal", "eu e mais 1"]):
        intent.adults = 2
    else:
        fam_match = re.search(r"fam[ií]lia\s+de\s+(\d+)", text_lower)
        if fam_match:
            intent.adults = int(fam_match.group(1))
            
    # Limitar entre 1 e 9
    intent.adults = max(1, min(9, getattr(intent, "adults", 1) or 1))
        
    if not extracted:
        intent.confidence = 0.2
        intent.notes = "Extraído via REGEx (Fallback - Baixa Confiança)"
    else:
        intent.notes = "Extraído via REGEx (Fallback - Refinado)"
    
    return intent

def parse_intent_ptbr(text: str) -> ParsedIntent:
    """Public entry point for intent parsing — regex/heuristics only."""
    return parse_intent_regex(text)
