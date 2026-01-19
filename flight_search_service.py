from __future__ import annotations
import os
from datetime import timedelta

from kayak_client import search_flights
from iata_resolver import resolve_place_to_iatas
from offer_parser import extract_offers
from fx_rates import convert

TARGET_CURRENCY = os.getenv("TARGET_CURRENCY", "BRL").upper()
MAX_PAGES = int(os.getenv("KAYAK_MAX_PAGES", "2"))  # comece com 2


def daterange(start, end):
    cur = start
    while cur <= end:
        yield cur
        cur += timedelta(days=1)


def _pretty_status(search_status, status):
    if isinstance(status, str) and status:
        return status
    if isinstance(search_status, str) and search_status:
        return search_status
    if isinstance(search_status, dict):
        for k in ["state", "status", "phase"]:
            v = search_status.get(k)
            if isinstance(v, str) and v:
                return v
        return "dict"
    return None


def _normalize_offers_currency(offers: list[dict], target_ccy: str) -> tuple[list[dict], list[str]]:
    notes = []
    out = []

    for o in offers:
        if not isinstance(o, dict):
            continue

        price = o.get("price")
        cur = (o.get("currency") or "").upper()

        if price is None or not cur:
            out.append(o)
            continue

        if cur == target_ccy:
            out.append(o)
            continue

        try:
            o["price_original"] = float(price)
            o["currency_original"] = cur

            converted = convert(float(price), cur, target_ccy)
            o["price"] = float(converted)
            o["currency"] = target_ccy
            o["fx_rate_applied"] = o["price"] / o["price_original"] if o["price_original"] else None

        except Exception as e:
            notes.append(f"Falha ao converter {cur}->{target_ccy}: {e}")

        out.append(o)

    return out, notes


def _build_shortlist_with_airport_variety(all_offers: list[dict], top_n: int) -> list[dict]:
    if not all_offers:
        return []

    all_offers = sorted(all_offers, key=lambda x: x["price"])
    best = all_offers[0]

    selected = [best]
    used_leg_ids = {best.get("leg_id")}

    # mais barato por destino (GIG/SDU etc)
    cheapest_by_dest = {}
    for o in all_offers[1:]:
        dest = o.get("destination")
        leg_id = o.get("leg_id")
        if not dest or not leg_id or leg_id in used_leg_ids:
            continue
        if dest not in cheapest_by_dest:
            cheapest_by_dest[dest] = o

    for o in sorted(cheapest_by_dest.values(), key=lambda x: x["price"]):
        if len(selected) >= top_n:
            break
        leg_id = o.get("leg_id")
        if leg_id in used_leg_ids:
            continue
        selected.append(o)
        used_leg_ids.add(leg_id)

    # completa com próximos mais baratos
    for o in all_offers[1:]:
        if len(selected) >= top_n:
            break
        leg_id = o.get("leg_id")
        if not leg_id or leg_id in used_leg_ids:
            continue
        selected.append(o)
        used_leg_ids.add(leg_id)

    return selected


def search_best_in_range(parsed: dict, top_n: int = 8) -> dict:
    origin_iatas = resolve_place_to_iatas(parsed["origin_place"])
    dest_iatas = resolve_place_to_iatas(parsed["destination_place"])

    all_offers = []
    last_meta = {"pretty": None, "status": None, "searchStatus": None}
    fx_notes: list[str] = []

    for dep_date in daterange(parsed["date_start"], parsed["date_end"]):
        dep_str = dep_date.strftime("%Y-%m-%d")

        for o in origin_iatas:
            for d in dest_iatas:
                for page in range(1, MAX_PAGES + 1):
                    raw = search_flights(
                        origin=o,
                        destination=d,
                        departure_date=dep_str,
                        return_date=None,
                        adults=parsed["adults"],
                        cabin=parsed["cabin"],
                        sort_mode="price_a",
                        page=page,
                    )

                    data = (raw or {}).get("data") or {}
                    last_meta = {
                        "searchStatus": data.get("searchStatus"),
                        "status": data.get("status"),
                        "pretty": _pretty_status(data.get("searchStatus"), data.get("status")),
                    }

                    offers = extract_offers(raw)

                    for off in offers:
                        off["departure_date"] = dep_str
                        off["origin"] = o
                        off["destination"] = d
                        off["page"] = page  # debug

                    all_offers.extend(offers)

                    # se a página veio vazia, não faz sentido continuar paginando
                    if not offers:
                        break

    # converte para BRL antes de ordenar/shortlist
    all_offers, notes = _normalize_offers_currency(all_offers, TARGET_CURRENCY)
    fx_notes.extend(notes)

    # ordenação global por menor preço (BRL)
    all_offers = [o for o in all_offers if o.get("price") is not None]
    all_offers.sort(key=lambda x: x["price"])

    shortlist = _build_shortlist_with_airport_variety(all_offers, top_n=top_n)
    best = shortlist[0] if shortlist else None

    return {
        "meta": last_meta,
        "best": best,
        "options": shortlist,
        "debug": {
            "offers_total": len(all_offers),
            "offers_shortlist": len(shortlist),
            "target_currency": TARGET_CURRENCY,
            "max_pages": MAX_PAGES,
        },
        "notes": fx_notes,
    }








