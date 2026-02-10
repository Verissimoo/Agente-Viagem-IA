from __future__ import annotations

import os
from datetime import date, timedelta
from typing import Any

from iata_resolver import resolve_place_to_iatas
from moblix_client import search_flights as moblix_search
from moblix_offer_parser import extract_offers


_MULTI_AIRPORT_SET_TO_CITY_CODE = {
    frozenset({"CGH", "GRU", "VCP"}): "SAO",
    frozenset({"SDU", "GIG"}): "RIO",
    frozenset({"CNF", "PLU"}): "BHZ",
    frozenset({"LHR", "LGW"}): "LON",
    frozenset({"CDG", "ORY"}): "PAR",
    frozenset({"JFK", "LGA", "EWR"}): "NYC",
}


def daterange(start: date, end: date):
    cur = start
    while cur <= end:
        yield cur
        cur += timedelta(days=1)


def _map_cabin_to_moblix(cabin: str | None) -> str:
    if not cabin:
        return "economy"

    c = cabin.strip().lower()
    if c in {"economy", "premium_economy", "business", "first"}:
        return c

    # compat com Kayak ("e" etc)
    if c == "e":
        return "economy"
    if c in {"pe", "p"}:
        return "premium_economy"
    if c in {"b", "c", "biz", "business"}:
        return "business"
    if c in {"f", "first"}:
        return "first"

    return "economy"


def _resolve_place_to_moblix_codes(place: str) -> list[str]:
    # 1) tenta iatas do seu resolver
    iatas = resolve_place_to_iatas(place)

    # 2) se já veio algo único, ok
    if len(iatas) == 1:
        return iatas

    # 3) se veio múltiplos aeroportos e a gente conhece o "city code", usa 1 código só
    if len(iatas) > 1:
        city_code = _MULTI_AIRPORT_SET_TO_CITY_CODE.get(frozenset(set(iatas)))
        if city_code:
            return [city_code]

        # fallback: limita para evitar explosão de custo
        limit = int(os.getenv("MOBLIX_MAX_AIRPORTS_PER_PLACE", "2"))
        return iatas[: max(1, limit)]

    # 4) nada encontrado
    return []


def search_best_miles_in_range(parsed: dict[str, Any], top_n: int = 8) -> dict[str, Any]:
    origin_codes = _resolve_place_to_moblix_codes(parsed["origin_place"])
    dest_codes = _resolve_place_to_moblix_codes(parsed["destination_place"])

    if not origin_codes:
        raise ValueError(f"Não consegui mapear origem: {parsed.get('origin_place')}")
    if not dest_codes:
        raise ValueError(f"Não consegui mapear destino: {parsed.get('destination_place')}")

    cabin_class = _map_cabin_to_moblix(parsed.get("cabin"))
    adults = int(parsed.get("adults") or 1)

    date_start = parsed["date_start"]
    date_end = parsed["date_end"]
    if date_start > date_end:
        date_start, date_end = date_end, date_start

    all_offers: list[dict[str, Any]] = []

    for dep_date in daterange(date_start, date_end):
        dep_str = dep_date.strftime("%Y-%m-%d")

        for o in origin_codes:
            for d in dest_codes:
                raw = moblix_search(
                    origin=o,
                    destination=d,
                    departure_date=dep_str,
                    return_date=None,
                    adults=adults,
                    cabin_class=cabin_class,
                    search_type="milhas",
                    max_connections=None,
                    suppliers=None,
                    enable_deduplication=True,
                )

                offers = extract_offers(raw)

                for off in offers:
                    off["query_origin"] = o
                    off["query_destination"] = d
                    if not off.get("departure_date"):
                        off["departure_date"] = dep_str

                all_offers.extend(offers)

    all_offers = [o for o in all_offers if o.get("price") is not None]
    all_offers.sort(key=lambda x: x["price"])

    shortlist = all_offers[:top_n]

    return {
        "query": {
            "origin_place": parsed["origin_place"],
            "destination_place": parsed["destination_place"],
            "date_start": date_start.isoformat(),
            "date_end": date_end.isoformat(),
            "adults": adults,
            "cabin_class": cabin_class,
            "search_type": "milhas",
            "resolved_origin_codes": origin_codes,
            "resolved_destination_codes": dest_codes,
        },
        "best": shortlist[0] if shortlist else None,
        "options": shortlist,
        "notes": [
            "Busca via API de Voos (Moblix) usando searchType='milhas'.",
            "Quando possível, usamos código de cidade (ex: SAO/RIO) para cobrir múltiplos aeroportos com 1 requisição.",
            "Milhas LATAM podem ser estimadas; Azul/Smiles tendem a ser precisos (conforme doc).",
        ],
    }
