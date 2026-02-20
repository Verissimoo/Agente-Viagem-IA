from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Dict, Optional


def _parse_date_pt(s: str) -> Optional[str]:
    """
    Aceita:
    - 30/03/2026
    - 30/03
    - 2026-03-30
    Retorna ISO YYYY-MM-DD
    """
    if not s:
        return None
    s = s.strip()

    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        return s

    m = re.fullmatch(r"(\d{1,2})/(\d{1,2})(?:/(\d{4}))?", s)
    if not m:
        return None

    d = int(m.group(1))
    mo = int(m.group(2))
    y = int(m.group(3)) if m.group(3) else datetime.now().year
    try:
        return date(y, mo, d).isoformat()
    except Exception:
        return None


def parse_prompt_pt(text: str) -> Dict[str, Any]:
    """
    Extrai:
      - origin_place, destination_place
      - date_start (ida)
      - return_start (volta opcional)
      - trip_type: oneway/roundtrip
    """
    t = (text or "").strip()
    if not t:
        raise ValueError("Prompt vazio.")

    low = t.lower()

    # origem/destino: "de X para Y"
    m = re.search(r"\bde\s+(.+?)\s+para\s+(.+?)(?:\s+(?:somente|ida|volta|dia|em)\b|$)", low)
    if not m:
        # fallback simples "X para Y"
        m = re.search(r"^(.+?)\s+para\s+(.+?)(?:\s+(?:somente|ida|volta|dia|em)\b|$)", low)

    if not m:
        raise ValueError("Não consegui extrair origem e destino. Use: 'de Brasília para São Paulo ...'")

    origin_place = m.group(1).strip(" ,.")
    destination_place = m.group(2).strip(" ,.")

    # datas
    ida = None
    volta = None

    # "ida dia 30/03/2026"
    m_ida = re.search(r"\bida\b.*?\bdia\b\s*(\d{1,2}/\d{1,2}(?:/\d{4})?|\d{4}-\d{2}-\d{2})", low)
    if m_ida:
        ida = _parse_date_pt(m_ida.group(1))

    # "somente ida dia 30/03/2026"
    if not ida:
        m_ida = re.search(r"\bdia\b\s*(\d{1,2}/\d{1,2}(?:/\d{4})?|\d{4}-\d{2}-\d{2})", low)
        if m_ida:
            ida = _parse_date_pt(m_ida.group(1))

    # "volta dia 05/04/2026"
    m_volta = re.search(r"\bvolta\b.*?\bdia\b\s*(\d{1,2}/\d{1,2}(?:/\d{4})?|\d{4}-\d{2}-\d{2})", low)
    if m_volta:
        volta = _parse_date_pt(m_volta.group(1))

    if not ida:
        raise ValueError("Não consegui extrair a data de ida. Ex: 'ida dia 30/03/2026'.")

    trip_type = "roundtrip" if volta else "oneway"

    return {
        "origin_place": origin_place,
        "destination_place": destination_place,
        "date_start": ida,
        "return_start": volta,
        "trip_type": trip_type,
        "adults": 1,
        "cabin": "e",
    }

