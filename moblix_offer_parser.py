from __future__ import annotations
from typing import Any


AIRLINE_CODE_TO_NAME = {
    "LA": "LATAM",
    "AD": "AZUL",
    "G3": "GOL",
}


def _deep_find_first_number(d: Any, key_hints: tuple[str, ...]) -> float | None:
    """
    Procura recursivamente por um número em campos cujo nome contenha alguma das dicas.
    Ex: key_hints=("milha","mile","points","pontos")
    """
    if isinstance(d, dict):
        for k, v in d.items():
            k_low = str(k).lower()
            if any(h in k_low for h in key_hints):
                # pode vir como int/float/str
                if isinstance(v, (int, float)):
                    return float(v)
                if isinstance(v, str):
                    try:
                        # remove separadores comuns
                        vv = v.replace(".", "").replace(",", ".")
                        return float(vv)
                    except Exception:
                        pass
            found = _deep_find_first_number(v, key_hints)
            if found is not None:
                return found
    elif isinstance(d, list):
        for it in d:
            found = _deep_find_first_number(it, key_hints)
            if found is not None:
                return found
    return None


def _sum_taxes(price_obj: dict) -> float | None:
    taxes = price_obj.get("taxes")
    if not isinstance(taxes, list):
        return None
    total = 0.0
    ok = False
    for t in taxes:
        if not isinstance(t, dict):
            continue
        amt = t.get("amount")
        if isinstance(amt, (int, float)):
            total += float(amt)
            ok = True
        elif isinstance(amt, str):
            try:
                total += float(amt.replace(",", "."))
                ok = True
            except Exception:
                pass
    return total if ok else None


def _carrier_name_from_segments(segments: list[dict]) -> str | None:
    # tenta achar marketingCarrier.name / code
    for seg in segments or []:
        mc = seg.get("marketingCarrier") if isinstance(seg, dict) else None
        if isinstance(mc, dict):
            name = mc.get("name")
            code = mc.get("code")
            if isinstance(name, str) and name.strip():
                return name.strip().upper()
            if isinstance(code, str) and code.strip():
                return AIRLINE_CODE_TO_NAME.get(code.strip().upper(), code.strip().upper())
    return None


def extract_offers(resp: dict) -> list[dict]:
    """
    Normaliza resposta do Moblix (/flights/search) para a mesma “cara” do Kayak.
    Retorna ofertas com:
      - miles (quando existir)
      - taxes_brl (quando existir)
      - total_brl (quando existir)
    """
    out: list[dict] = []

    groups = (resp or {}).get("flightGroups") or []
    if not isinstance(groups, list):
        return out

    for g in groups:
        if not isinstance(g, dict):
            continue

        group_id = g.get("signature") or g.get("humanSignature")

        flight_info = g.get("flightInfo") if isinstance(g.get("flightInfo"), dict) else {}
        itineraries = flight_info.get("itineraries") if isinstance(flight_info.get("itineraries"), list) else []
        is_roundtrip = bool(flight_info.get("isRoundTrip"))

        # pega outbound principal
        outbound = None
        inbound = None
        for it in itineraries:
            if not isinstance(it, dict):
                continue
            if it.get("type") == "outbound" and outbound is None:
                outbound = it
            if it.get("type") == "inbound" and inbound is None:
                inbound = it

        def _pick_times(itin: dict | None):
            if not isinstance(itin, dict):
                return None, None, None
            segs = itin.get("segments") if isinstance(itin.get("segments"), list) else []
            if not segs:
                return None, None, itin.get("stops")
            first = segs[0]
            last = segs[-1]
            dep = (first.get("departure") or {}).get("dateTime") if isinstance(first, dict) else None
            arr = (last.get("arrival") or {}).get("dateTime") if isinstance(last, dict) else None
            stops = itin.get("stops")
            return dep, arr, stops

        out_dep, out_arr, out_stops = _pick_times(outbound)
        in_dep, in_arr, in_stops = _pick_times(inbound)

        offers = g.get("offers") if isinstance(g.get("offers"), list) else []
        for off in offers:
            if not isinstance(off, dict):
                continue

            provider_id = off.get("providerId") or off.get("provider") or off.get("source")
            price_obj = off.get("price") if isinstance(off.get("price"), dict) else {}

            currency = (price_obj.get("currency") or "BRL")
            total_brl = None
            if isinstance(price_obj.get("total"), (int, float)):
                total_brl = float(price_obj["total"])

            taxes_brl = _sum_taxes(price_obj)

            # tenta achar milhas em qualquer campo conhecido (robusto)
            miles = _deep_find_first_number(off, ("milha", "mile", "miles", "ponto", "points", "pontos"))

            # airline(s)
            # pega dos segmentos do outbound
            airlines = []
            if isinstance(outbound, dict):
                segs = outbound.get("segments") if isinstance(outbound.get("segments"), list) else []
                name = _carrier_name_from_segments(segs)
                if name:
                    airlines = [name]

            # origem/destino (airport)
            origin = None
            destination = None
            if isinstance(outbound, dict):
                segs = outbound.get("segments") if isinstance(outbound.get("segments"), list) else []
                if segs:
                    first = segs[0]
                    last = segs[-1]
                    origin = ((first.get("departure") or {}).get("airport") if isinstance(first, dict) else None)
                    destination = ((last.get("arrival") or {}).get("airport") if isinstance(last, dict) else None)

            normalized = {
                "source": "moblix",
                "group_id": group_id,
                "providerCode": provider_id,
                "providerName": provider_id,
                "shareableUrl": (off.get("booking") or {}).get("bookingUrl") if isinstance(off.get("booking"), dict) else None,
                "trip_type": "roundtrip" if is_roundtrip else "oneway",
                # para comparação:
                "miles": miles,                # pode ser None
                "taxes_brl": taxes_brl,        # pode ser None
                "total_brl": total_brl,        # pode ser None
                # compat (mantém “price/currency” para não quebrar seu app):
                "price": total_brl if total_brl is not None else 0.0,
                "currency": currency,
                "origin": origin,
                "destination": destination,
                "airlines": airlines,
                "departure_time": out_dep,
                "arrival_time": out_arr,
                "stops": out_stops,
                "out_departure_time": out_dep,
                "out_arrival_time": out_arr,
                "out_stops": out_stops,
                "in_departure_time": in_dep,
                "in_arrival_time": in_arr,
                "in_stops": in_stops,
            }
            out.append(normalized)

    return out













