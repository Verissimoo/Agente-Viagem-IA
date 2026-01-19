import re
from datetime import date, datetime

MONTHS_PT = {
    "JANEIRO": 1, "FEVEREIRO": 2, "MARCO": 3, "MARÇO": 3, "ABRIL": 4, "MAIO": 5, "JUNHO": 6,
    "JULHO": 7, "AGOSTO": 8, "SETEMBRO": 9, "OUTUBRO": 10, "NOVEMBRO": 11, "DEZEMBRO": 12
}


def _infer_year_for_month(month: int) -> int:
    today = date.today()
    year = today.year
    if month < today.month:
        return year + 1
    return year


def _parse_day_month(text: str) -> date | None:
    t = text.upper()

    m = re.search(r"\b(\d{1,2})\s*/\s*(\d{1,2})\b", t)
    if m:
        d = int(m.group(1))
        mo = int(m.group(2))
        y = _infer_year_for_month(mo)
        return date(y, mo, d)

    m = re.search(r"\b(\d{1,2})\s+DE\s+([A-ZÇÃÓ]+)\b", t)
    if m:
        d = int(m.group(1))
        mo_name = m.group(2)
        mo = MONTHS_PT.get(mo_name)
        if not mo:
            return None
        y = _infer_year_for_month(mo)
        return date(y, mo, d)

    return None


def _parse_date_range(text: str) -> tuple[date, date] | None:
    t = text.upper()

    m = re.search(r"\bDO\s+DIA\s+(\d{1,2})\s+AO\s+DIA\s+(\d{1,2})\s+DE\s+([A-ZÇÃÓ]+)\b", t)
    if m:
        d1 = int(m.group(1))
        d2 = int(m.group(2))
        mo_name = m.group(3)
        mo = MONTHS_PT.get(mo_name)
        if not mo:
            return None
        y = _infer_year_for_month(mo)
        start = date(y, mo, d1)
        end = date(y, mo, d2)
        if end < start:
            start, end = end, start
        return start, end

    return None


def _extract_places(text: str) -> tuple[str, str]:
    """
    Captura 'de X para Y' aceitando nomes com espaços:
    Ex: 'de Brasília para Rio de Janeiro somente ida dia 10/3...'
    """
    t = text.strip()

    # Para quando encontrar gatilhos típicos do resto da frase
    stop_words = r"(?:\s+(?:SOMENTE|APENAS|IDA|VOLTA|DIA|DATA|COM|SEM|TENDO|FLEXIBILIDADE|ENTRE)\b|,|$)"

    m = re.search(
        rf"de\s+(.+?)\s+para\s+(.+?){stop_words}",
        t,
        flags=re.IGNORECASE,
    )
    if m:
        origin = m.group(1).strip()
        destination = m.group(2).strip()
        return origin, destination

    m = re.search(
        rf"(.+?)\s*[-–>]+\s*(.+?){stop_words}",
        t,
        flags=re.IGNORECASE,
    )
    if m:
        return m.group(1).strip(), m.group(2).strip()

    raise ValueError("Não encontrei origem/destino. Ex: 'de Brasília para São Paulo'.")


def parse_prompt_pt(user_msg: str) -> dict:
    origin_place, destination_place = _extract_places(user_msg)

    range_ = _parse_date_range(user_msg)
    single = _parse_day_month(user_msg)

    if range_:
        date_start, date_end = range_
    elif single:
        date_start = date_end = single
    else:
        raise ValueError("Não encontrei data. Ex: 'dia 10/3' ou 'do dia 5 ao dia 15 de março'.")

    baggage_checked = True
    if re.search(r"\bSEM\s+MALA\s+DESPACHADA\b|\bSEM\s+DESPACHAR\b|\bSOMENTE\s+MALA\s+DE\s+MAO\b", user_msg, flags=re.IGNORECASE):
        baggage_checked = False

    return {
        "origin_place": origin_place,
        "destination_place": destination_place,
        "date_start": date_start,
        "date_end": date_end,
        "baggage_checked": baggage_checked,
        "adults": 1,
        "cabin": "e",
        "sort_mode": "price_a",
    }

