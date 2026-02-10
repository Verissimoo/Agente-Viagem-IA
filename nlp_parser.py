from __future__ import annotations
import re
from datetime import date, datetime
from typing import Optional


_PT_MONTHS = {
    "janeiro": 1, "jan": 1,
    "fevereiro": 2, "fev": 2,
    "marco": 3, "março": 3, "mar": 3,
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


def _normalize_text(s: str) -> str:
    return (s or "").strip()


def _guess_year(d: date) -> int:
    """Se não vier ano, tenta usar ano atual; se a data já passou muito, empurra pro próximo ano."""
    today = date.today()
    y = today.year
    candidate = date(y, d.month, d.day)
    # se já passou (mais de 30 dias), joga para o próximo ano
    if candidate < date(today.year, today.month, today.day) and (today - candidate).days > 30:
        y += 1
    return y


def _parse_date_fragment(fragment: str) -> Optional[date]:
    """
    Aceita:
      - 10/3
      - 10/03/2026
      - 10-03-2026
      - 10 de março (ano opcional)
      - dia 10/3
    """
    if not fragment:
        return None
    frag = fragment.strip().lower()

    # dd/mm(/yyyy)
    m = re.search(r"(\d{1,2})\s*[\/\-]\s*(\d{1,2})(?:\s*[\/\-]\s*(\d{2,4}))?", frag)
    if m:
        dd = int(m.group(1))
        mm = int(m.group(2))
        yy = m.group(3)
        if yy:
            y = int(yy)
            if y < 100:
                y += 2000
        else:
            y = _guess_year(date(2000, mm, dd))
        return date(y, mm, dd)

    # "10 de março (2026?)"
    m = re.search(r"(\d{1,2})\s*(?:de\s*)?([a-zçãõáéíóú]+)\s*(\d{4})?", frag)
    if m:
        dd = int(m.group(1))
        mon = m.group(2).strip()
        yy = m.group(3)
        mm = _PT_MONTHS.get(mon)
        if not mm:
            return None
        if yy:
            y = int(yy)
        else:
            y = _guess_year(date(2000, mm, dd))
        return date(y, mm, dd)

    return None


def _extract_city_pair(text: str) -> tuple[str, str]:
    """
    Tenta pegar origem/destino em PT:
      - "de Brasília para São Paulo"
      - "de Brasilia pra Sao Paulo"
    """
    t = text

    # de X para Y
    m = re.search(
        r"\bde\s+(.+?)\s+(?:para|pra)\s+(.+?)(?:,|\.|\b(?:somente|só)\s+ida\b|\bida\b|\bvolta\b|\bcom\b|\bsem\b|\bdia\b|$)",
        t,
        flags=re.IGNORECASE,
    )
    if m:
        origin = m.group(1).strip()
        dest = m.group(2).strip()
        return origin, dest

    # fallback bem simples
    raise ValueError("Não consegui identificar origem e destino. Use: 'de X para Y'.")


def parse_prompt_pt(text: str) -> dict:
    """
    Saída padrão usada pelo seu serviço:
      origin_place, destination_place
      date_start, date_end
      adults, cabin, baggage_checked
    Agora adiciona:
      trip_type: 'oneway' | 'roundtrip'
      return_start, return_end (se roundtrip)
    """
    text = _normalize_text(text)
    if not text:
        raise ValueError("Prompt vazio.")

    origin_place, destination_place = _extract_city_pair(text)

    # adultos
    adults = 1
    m = re.search(r"\b(\d{1,2})\s*(adultos?|pessoas|passageiros?)\b", text, flags=re.IGNORECASE)
    if m:
        adults = max(1, int(m.group(1)))

    # cabine
    cabin = "e"
    if re.search(r"\bexecutiva\b|\bbusiness\b", text, flags=re.IGNORECASE):
        cabin = "b"

    # bagagem despachada
    baggage_checked = False
    if re.search(r"\bmala(s)?\s+despachad(a|as)\b", text, flags=re.IGNORECASE):
        # "sem mala despachada" => False
        if re.search(r"\bsem\s+mala(s)?\s+despachad(a|as)\b", text, flags=re.IGNORECASE) or re.search(
            r"\bn[aã]o\s+.*mala(s)?\s+despachad", text, flags=re.IGNORECASE
        ):
            baggage_checked = False
        else:
            baggage_checked = True

    # datas: pega ida e (se existir) volta
    # ida: procura "ida dia ..." ou apenas "dia ..."
    ida_date = None

    m = re.search(r"\bida\b.*?\bdia\b\s*([^\.,\n]+)", text, flags=re.IGNORECASE)
    if m:
        ida_date = _parse_date_fragment(m.group(1))

    if ida_date is None:
        m = re.search(r"\bdia\b\s*([0-9]{1,2}\s*[\/\-]\s*[0-9]{1,2}(?:\s*[\/\-]\s*[0-9]{2,4})?)", text, flags=re.IGNORECASE)
        if m:
            ida_date = _parse_date_fragment(m.group(1))

    # suporte ao modelo antigo de flex "do dia X ao dia Y..." (oneway)
    date_start = ida_date
    date_end = ida_date
    flex = re.search(r"\bdo\s+dia\s+(.+?)\s+ao\s+dia\s+(.+?)(?:,|\.|$)", text, flags=re.IGNORECASE)
    if flex:
        ds = _parse_date_fragment(flex.group(1))
        de = _parse_date_fragment(flex.group(2))
        if ds and de:
            date_start, date_end = ds, de

    if not date_start:
        raise ValueError("Não consegui identificar a data de ida. Ex.: 'ida dia 10/3'.")

    # volta: procura "volta dia ..." ou "retorno dia ..."
    return_start = None
    return_end = None

    m = re.search(r"\b(volta|retorno)\b.*?\bdia\b\s*([^\.,\n]+)", text, flags=re.IGNORECASE)
    if m:
        return_start = _parse_date_fragment(m.group(2))
        return_end = return_start

    # se pedir ida e volta mas sem data de volta -> erro (por enquanto)
    wants_roundtrip = bool(re.search(r"\bida\s+e\s+volta\b|\bvolta\b|\bretorno\b", text, flags=re.IGNORECASE))
    if wants_roundtrip and return_start is None:
        # só considera RT se houver data de volta
        wants_roundtrip = False

    trip_type = "roundtrip" if wants_roundtrip else "oneway"

    return {
        "origin_place": origin_place,
        "destination_place": destination_place,
        "date_start": date_start,
        "date_end": date_end,
        "return_start": return_start,
        "return_end": return_end,
        "trip_type": trip_type,
        "adults": adults,
        "cabin": cabin,
        "baggage_checked": baggage_checked,
    }


