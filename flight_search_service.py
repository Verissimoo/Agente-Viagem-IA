# flight_search_service.py
from __future__ import annotations

import os
from datetime import timedelta

from iata_resolver import resolve_place_to_iatas

# Kayak
from kayak_client import search_flights as kayak_search
from offer_parser import extract_offers as kayak_extract_offers
from fx_rates import convert


TARGET_CURRENCY = os.getenv("TARGET_CURRENCY", "BRL").upper()
MAX_PAGES = int(os.getenv("KAYAK_MAX_PAGES", "2"))


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

    selected = []
    used_keys = set()

    def offer_key(o: dict):
        if o.get("trip_type") == "roundtrip":
            return (o.get("out_leg_id"), o.get("in_leg_id"), o.get("out_departure_time"), o.get("in_departure_time"))
        return (o.get("leg_id"), o.get("departure_time"))

    best = all_offers[0]
    selected.append(best)
    used_keys.add(offer_key(best))

    cheapest_by_dest = {}
    for o in all_offers[1:]:
        dest = o.get("destination")
        k = offer_key(o)
        if not dest or not k or k in used_keys:
            continue
        if dest not in cheapest_by_dest:
            cheapest_by_dest[dest] = o

    for o in cheapest_by_dest.values():
        if len(selected) >= top_n:
            break
        k = offer_key(o)
        if k in used_keys:
            continue
        selected.append(o)
        used_keys.add(k)

    for o in all_offers[1:]:
        if len(selected) >= top_n:
            break
        k = offer_key(o)
        if not k or k in used_keys:
            continue
        selected.append(o)
        used_keys.add(k)

    return selected


def _sort_key_kayak(o: dict):
    p = o.get("price")
    return (float(p) if p is not None else 10**18)


def search_best_in_range(parsed: dict, top_n: int = 8) -> dict:
    """
    Busca pagante via Kayak.
    Mantém interface compatível com o app: recebe o parsed do nlp_parser.
    """
    trip_type = parsed.get("trip_type", "oneway")

    origin_iatas = resolve_place_to_iatas(parsed["origin_place"])
    dest_iatas = resolve_place_to_iatas(parsed["destination_place"])

    all_offers: list[dict] = []
    last_meta = {"pretty": None, "status": None, "searchStatus": None}
    fx_notes: list[str] = []

    # ===== ROUNDTRIP (datas fixas, igual seu padrão anterior) =====
    if trip_type == "roundtrip":
        if parsed["date_start"] != parsed["date_end"]:
            raise ValueError("Por enquanto, ida e volta (Kayak) funciona apenas com data fixa de ida (sem flex).")
        if not parsed.get("return_start") or not parsed.get("return_end"):
            raise ValueError("Para ida e volta, informe a data da volta. Ex.: 'volta dia 15/3'.")
        if parsed["return_start"] != parsed["return_end"]:
            raise ValueError("Por enquanto, ida e volta (Kayak) funciona apenas com data fixa de volta (sem flex).")
        if parsed["return_start"] < parsed["date_start"]:
            raise ValueError("A data de volta não pode ser antes da data de ida.")

        dep_str = parsed["date_start"].strftime("%Y-%m-%d")
        ret_str = parsed["return_start"].strftime("%Y-%m-%d")

        for o in origin_iatas:
            for d in dest_iatas:
                # RT: você pode manter 1 página (mais rápido). Se quiser mais, troque min(...) por MAX_PAGES
                for page in range(1, min(MAX_PAGES, 1) + 1):
                    raw = kayak_search(
                        origin=o,
                        destination=d,
                        departure_date=dep_str,
                        return_date=ret_str,
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

                    offers = kayak_extract_offers(raw)
                    for off in offers:
                        off["departure_date"] = dep_str
                        off["return_date"] = ret_str
                        off["origin"] = o
                        off["destination"] = d
                        off["page"] = page

                    all_offers.extend(offers)
                    if not offers:
                        break

    # ===== ONEWAY (pode ter flex) =====
    else:
        for dep_date in daterange(parsed["date_start"], parsed["date_end"]):
            dep_str = dep_date.strftime("%Y-%m-%d")
            for o in origin_iatas:
                for d in dest_iatas:
                    for page in range(1, MAX_PAGES + 1):
                        raw = kayak_search(
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

                        offers = kayak_extract_offers(raw)
                        for off in offers:
                            off["departure_date"] = dep_str
                            off["origin"] = o
                            off["destination"] = d
                            off["page"] = page

                        all_offers.extend(offers)
                        if not offers:
                            break

    all_offers, notes = _normalize_offers_currency(all_offers, TARGET_CURRENCY)
    fx_notes.extend(notes)

    all_offers = [o for o in all_offers if o.get("price") is not None]
    all_offers.sort(key=_sort_key_kayak)

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
            "trip_type": trip_type,
            "pricing_source": "kayak",
        },
        "notes": fx_notes,
    }




























