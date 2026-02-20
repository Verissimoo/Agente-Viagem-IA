from __future__ import annotations

import os
import re
import unicodedata
from typing import List

PLACE_TO_AIRPORTS = {
    # BRASIL
    "brasilia": ["BSB"],
    "brasília": ["BSB"],

    "sao paulo": ["CGH", "GRU", "VCP"],
    "são paulo": ["CGH", "GRU", "VCP"],
    "sp": ["CGH", "GRU", "VCP"],
    "guarulhos": ["GRU"],
    "congonhas": ["CGH"],
    "campinas": ["VCP"],

    "rio de janeiro": ["SDU", "GIG"],
    "rio": ["SDU", "GIG"],
    "rj": ["SDU", "GIG"],

    "belo horizonte": ["CNF", "PLU"],
    "bh": ["CNF", "PLU"],

    "curitiba": ["CWB"],
    "porto alegre": ["POA"],
    "salvador": ["SSA"],
    "recife": ["REC"],
    "fortaleza": ["FOR"],
    "florianopolis": ["FLN"],
    "florianópolis": ["FLN"],

    # INTERNACIONAL (mínimo útil)
    "lisboa": ["LIS"],
    "lisbon": ["LIS"],
    "porto": ["OPO"],
    "madrid": ["MAD"],
    "miami": ["MIA"],
    "nova york": ["NYC"],
    "new york": ["NYC"],
    "londres": ["LON"],
    "london": ["LON"],
    "paris": ["PAR"],
}

MULTI_AIRPORT_SET_TO_CITY_CODE = {
    frozenset({"CGH", "GRU", "VCP"}): "SAO",
    frozenset({"SDU", "GIG"}): "RIO",
    frozenset({"CNF", "PLU"}): "BHZ",
    frozenset({"JFK", "LGA", "EWR"}): "NYC",
    frozenset({"CDG", "ORY"}): "PAR",
    frozenset({"LHR", "LGW"}): "LON",
}


def _normalize(s: str) -> str:
    s = (s or "").strip()
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = s.lower()
    s = re.sub(r"[^a-z0-9\s\(\)\-\/]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def resolve_place_to_codes(place: str) -> List[str]:
    """
    Resolve 'Brasília' -> ['BSB']
    Resolve 'São Paulo' -> ['SAO'] (city code) quando possível.
    Aceita IATA/city direto: 'GRU' -> ['GRU']
    """
    if not place or not place.strip():
        return []

    raw = place.strip()
    up = raw.upper()

    if re.fullmatch(r"[A-Z]{3}", up):
        return [up]

    p = _normalize(raw)

    airports = PLACE_TO_AIRPORTS.get(p)

    if airports is None:
        for k in sorted(PLACE_TO_AIRPORTS.keys(), key=len, reverse=True):
            if k and k in p:
                airports = PLACE_TO_AIRPORTS[k]
                break

    if airports:
        airports = [a.upper() for a in airports]
        if len(airports) > 1:
            city_code = MULTI_AIRPORT_SET_TO_CITY_CODE.get(frozenset(set(airports)))
            if city_code:
                return [city_code]

        limit = int(os.getenv("MILES_MAX_CODES_PER_PLACE", "1"))
        return airports[: max(1, limit)]

    found = re.findall(r"\b[A-Z]{3}\b", up)
    if found:
        uniq = []
        for c in found:
            if c not in uniq:
                uniq.append(c)
        return uniq

    raise ValueError(f"Não consegui mapear '{place}'. Dica: use IATA/city code (ex: BSB, GRU, SAO).")


# Alias de compatibilidade (caso algum arquivo ainda importe este nome)
def resolve_place_to_iatas(place: str) -> List[str]:
    return resolve_place_to_codes(place)





