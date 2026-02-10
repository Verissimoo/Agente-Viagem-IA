from __future__ import annotations
from typing import Any


def _as_float(x) -> float | None:
    if x is None:
        return None
    if isinstance(x, (int, float)):
        return float(x)
    if isinstance(x, dict) and "price" in x:
        return _as_float(x.get("price"))
    return None


def _segment_id_from_ref(seg_ref: Any) -> str | None:
    if isinstance(seg_ref, dict):
        v = seg_ref.get("id")
        return v if isinstance(v, str) else None
    if isinstance(seg_ref, str):
        return seg_ref
    return None


def _airline_names_from_leg(data: dict, leg: dict) -> list[str]:
    airlines_map = data.get("airlines") or {}
    segments = data.get("segments") or {}

    codes: list[str] = []
    displays: list[str] = []

    seg_refs = leg.get("segments") or []
    if isinstance(seg_refs, list):
        for sr in seg_refs:
            sid = _segment_id_from_ref(sr)
            if not (isinstance(segments, dict) and isinstance(sid, str) and sid in segments):
                continue
            seg = segments[sid]
            code = seg.get("airline")
            if isinstance(code, str) and 2 <= len(code) <= 3:
                codes.append(code)
            disp = seg.get("operationalDisplay")
            if isinstance(disp, str) and disp.strip():
                displays.append(disp.strip())

    if codes:
        out = []
        for c in codes:
            info = airlines_map.get(c)
            name = info.get("name") if isinstance(info, dict) else None
            out.append(name or c)
        uniq = []
        for x in out:
            if x not in uniq:
                uniq.append(x)
        return uniq

    uniq = []
    for x in displays:
        if x not in uniq:
            uniq.append(x)
    return uniq or ["(não identificado)"]


def _min_price_from_booking_options(core: dict) -> tuple[float | None, str | None, str | None, str | None]:
    """
    Retorna o menor preço encontrado dentro de bookingOptions, preferindo:
      1) fees.totalPrice
      2) fees.rawPrice
      3) displayPrice
    Também retorna providerCode/providerName do bookingOption vencedor.
    """
    best_price = None
    best_currency = None
    best_provider_code = None
    best_provider_name = None

    bos = core.get("bookingOptions") or []
    if not isinstance(bos, list):
        return None, None, None, None

    for bo in bos:
        if not isinstance(bo, dict):
            continue

        currency = bo.get("currency") if isinstance(bo.get("currency"), str) else None
        provider_code = bo.get("providerCode") if isinstance(bo.get("providerCode"), str) else None
        provider_name = bo.get("providerName") if isinstance(bo.get("providerName"), str) else None

        # 1) fees.totalPrice
        fees = bo.get("fees") if isinstance(bo.get("fees"), dict) else None
        if fees:
            tp = fees.get("totalPrice")
            p = _as_float(tp) if isinstance(tp, dict) else None
            c = tp.get("currency") if isinstance(tp, dict) else None
            if p is not None:
                if best_price is None or p < best_price:
                    best_price = float(p)
                    best_currency = (c or currency)
                    best_provider_code = provider_code
                    best_provider_name = provider_name
                continue

            rp = fees.get("rawPrice")
            p = _as_float(rp) if isinstance(rp, dict) else None
            c = rp.get("currency") if isinstance(rp, dict) else None
            if p is not None:
                if best_price is None or p < best_price:
                    best_price = float(p)
                    best_currency = (c or currency)
                    best_provider_code = provider_code
                    best_provider_name = provider_name
                continue

        # 2) displayPrice
        dp = bo.get("displayPrice")
        p = _as_float(dp) if isinstance(dp, dict) else None
        c = dp.get("currency") if isinstance(dp, dict) else None
        if p is not None:
            if best_price is None or p < best_price:
                best_price = float(p)
                best_currency = (c or currency)
                best_provider_code = provider_code
                best_provider_name = provider_name

    return best_price, best_currency, best_provider_code, best_provider_name


def _bucket_top_price(core: dict) -> tuple[float | None, str | None]:
    best_price = None
    best_currency = None

    buckets = core.get("bookingOptionsBuckets") or []
    if isinstance(buckets, list):
        for b in buckets:
            if not isinstance(b, dict):
                continue
            tp = b.get("topPrice")
            if not isinstance(tp, dict):
                continue
            p = _as_float(tp)
            cur = tp.get("currency")
            if p is None:
                continue
            if best_price is None or p < best_price:
                best_price = float(p)
                best_currency = cur

    return best_price, best_currency


def _leg_summary(data: dict, leg_id: str) -> dict | None:
    legs_map = data.get("legs") or {}
    if not (isinstance(legs_map, dict) and isinstance(leg_id, str) and leg_id in legs_map):
        return None

    leg = legs_map[leg_id]
    departure_time = leg.get("departure")
    arrival_time = leg.get("arrival")
    duration_min = leg.get("duration") if isinstance(leg.get("duration"), int) else None

    seg_refs = leg.get("segments") or []
    stops = max(len(seg_refs) - 1, 0) if isinstance(seg_refs, list) else None

    airlines = _airline_names_from_leg(data, leg)

    return {
        "leg_id": leg_id,
        "departure_time": departure_time,
        "arrival_time": arrival_time,
        "duration_min": duration_min,
        "stops": stops,
        "airlines": airlines,
    }


def extract_offers(raw: dict) -> list[dict]:
    """
    Backward-compatible:
      - ONEWAY continua igual (dedup por leg_id)
      - Se vier ROUNDTRIP (core legs >=2), adiciona campos out_* e in_* e dedup por (out_leg_id,in_leg_id)
    """
    if not isinstance(raw, dict):
        return []

    data = raw.get("data") or {}
    results = data.get("results") or []
    legs_map = data.get("legs") or {}

    if not isinstance(results, list) or not results:
        return []
    if not isinstance(legs_map, dict) or not legs_map:
        return []

    best_by_key: dict[Any, dict] = {}

    for item in results:
        if not isinstance(item, dict):
            continue
        if item.get("type") != "core":
            continue

        core_legs = item.get("legs") or []
        if not isinstance(core_legs, list) or not core_legs:
            continue

        # coleta ids das legs existentes (1 ou 2)
        leg_ids = []
        for x in core_legs:
            if isinstance(x, dict) and isinstance(x.get("id"), str):
                leg_ids.append(x["id"])

        if not leg_ids:
            continue

        # pega o melhor preço real
        price, currency, provider_code, provider_name = _min_price_from_booking_options(item)
        if price is None:
            price, currency = _bucket_top_price(item)
        if price is None:
            continue

        # ===== ONEWAY =====
        if len(leg_ids) == 1:
            leg_id = leg_ids[0]
            if leg_id not in legs_map:
                continue

            ls = _leg_summary(data, leg_id)
            if not ls:
                continue

            offer = {
                "price": float(price),
                "currency": (currency.upper() if isinstance(currency, str) else currency),
                "departure_time": ls["departure_time"],
                "arrival_time": ls["arrival_time"],
                "duration_min": ls["duration_min"],
                "stops": ls["stops"],
                "airlines": ls["airlines"],
                "leg_id": ls["leg_id"],
                "shareableUrl": item.get("shareableUrl"),
                "providerCode": provider_code,
                "providerName": provider_name,
                "trip_type": "oneway",
            }

            key = leg_id  # mantém seu dedup atual
            prev = best_by_key.get(key)
            if prev is None or offer["price"] < prev["price"]:
                best_by_key[key] = offer

        # ===== ROUNDTRIP (2 legs) =====
        else:
            out_leg_id = leg_ids[0]
            in_leg_id = leg_ids[1]

            if out_leg_id not in legs_map or in_leg_id not in legs_map:
                continue

            out_ls = _leg_summary(data, out_leg_id)
            in_ls = _leg_summary(data, in_leg_id)
            if not out_ls or not in_ls:
                continue

            # união de cias
            airlines_total = []
            for a in (out_ls["airlines"] or []) + (in_ls["airlines"] or []):
                if a not in airlines_total:
                    airlines_total.append(a)

            # backward-compatible: mantém campos "genéricos" apontando para a IDA
            offer = {
                "price": float(price),
                "currency": (currency.upper() if isinstance(currency, str) else currency),
                "shareableUrl": item.get("shareableUrl"),
                "providerCode": provider_code,
                "providerName": provider_name,
                "trip_type": "roundtrip",

                # ida
                "out_leg_id": out_ls["leg_id"],
                "out_departure_time": out_ls["departure_time"],
                "out_arrival_time": out_ls["arrival_time"],
                "out_duration_min": out_ls["duration_min"],
                "out_stops": out_ls["stops"],
                "out_airlines": out_ls["airlines"],

                # volta
                "in_leg_id": in_ls["leg_id"],
                "in_departure_time": in_ls["departure_time"],
                "in_arrival_time": in_ls["arrival_time"],
                "in_duration_min": in_ls["duration_min"],
                "in_stops": in_ls["stops"],
                "in_airlines": in_ls["airlines"],

                # compat (mostra ida)
                "leg_id": out_ls["leg_id"],
                "departure_time": out_ls["departure_time"],
                "arrival_time": out_ls["arrival_time"],
                "duration_min": out_ls["duration_min"],
                "stops": out_ls["stops"],
                "airlines": airlines_total,
            }

            key = (out_leg_id, in_leg_id)
            prev = best_by_key.get(key)
            if prev is None or offer["price"] < prev["price"]:
                best_by_key[key] = offer

    offers = list(best_by_key.values())
    offers.sort(key=lambda x: x["price"])
    return offers

















