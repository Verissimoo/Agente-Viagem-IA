from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple


def _sum_taxes(price: Dict[str, Any]) -> float:
    taxes = price.get("taxes") or []
    total = 0.0
    for t in taxes:
        try:
            total += float(t.get("amount") or 0)
        except Exception:
            pass
    return float(total)


def _get_points_total(offer: Dict[str, Any]) -> Optional[int]:
    price = offer.get("price") or {}
    pi = price.get("pointsInfo") or {}
    v = pi.get("totalPoints")
    if v is None:
        return None
    try:
        return int(float(v))
    except Exception:
        return None


def _get_points_split(offer: Dict[str, Any]) -> Tuple[Optional[int], Optional[int]]:
    price = offer.get("price") or {}
    pi = price.get("pointsInfo") or {}
    
    out_v = pi.get("outboundPoints")
    in_v = pi.get("inboundPoints")
    
    def _to_int(v):
        if v is None: return None
        try: return int(float(v))
        except: return None

    return _to_int(out_v), _to_int(in_v)


def _get_booking_url(offer: Dict[str, Any]) -> str:
    booking = offer.get("booking") or {}
    url = booking.get("bookingUrl") or ""
    if url:
        return url
    for s in (offer.get("suppliers") or []):
        u = (s or {}).get("bookingUrl")
        if u:
            return u
    return ""


def _has_checked_bag(offer: Dict[str, Any]) -> bool:
    """
    Detecta se a oferta tem bagagem despachada incluída.
    Obs: o JSON nem sempre informa "23kg", normalmente vem "1 bagagem despachada".
    """
    for b in (offer.get("baggageIncluded") or []):
        if not isinstance(b, dict):
            continue
        inc = bool(b.get("isIncluded"))
        if not inc:
            continue
        bt = str(b.get("type") or "").lower()
        desc = str(b.get("description") or "").lower()

        # sinais fortes
        if bt in ("despachar", "checked", "checkedbag", "bagagem_despachada"):
            return True
        if "despach" in desc:
            return True

    return False


def _fmt_duration(minutes: Optional[int]) -> str:
    if minutes is None:
        return ""
    try:
        m = int(minutes)
    except Exception:
        return ""
    h = m // 60
    r = m % 60
    if h <= 0:
        return f"{r}m"
    if r <= 0:
        return f"{h}h"
    return f"{h}h {r}m"


def _leg_row(it: Dict[str, Any]) -> Dict[str, Any]:
    segs = it.get("segments") or []
    if not segs:
        return {}

    first = segs[0]
    last = segs[-1]
    dep = first.get("departure") or {}
    arr = last.get("arrival") or {}

    leg_type = (it.get("type") or "").lower()
    trecho = "IDA" if leg_type == "outbound" else "VOLTA"

    return {
        "Trecho": trecho,
        "Origem": dep.get("airport") or "",
        "Destino": arr.get("airport") or "",
        "Data": (dep.get("dateTime") or "")[:10],
        "Saída": dep.get("time") or "",
        "Chegada": arr.get("time") or "",
        "Escalas": it.get("stops"),
        "Duração": _fmt_duration(it.get("duration")),
    }


def _select_base_and_bag_points(points_offers: List[Dict[str, Any]]) -> Tuple[Optional[int], Optional[int], Dict[str, Any]]:
    """
    Regra do projeto (negócio):
      - milhas_base = menor totalPoints do grupo
      - bag_points = menor totalPoints > milhas_base
         1) preferindo offers com bagagem despachada
         2) fallback: próximo tier (segunda menor) mesmo que não esteja marcado como despachar
      - se algo vier < base, é ignorado como bagagem

    Retorna: (base_points, bag_points, best_offer_for_base)
    """
    scored: List[Tuple[int, float, bool, Dict[str, Any]]] = []
    for o in points_offers:
        pts = _get_points_total(o)
        if pts is None or pts <= 0:
            continue
        taxes = _sum_taxes(o.get("price") or {})
        has_bag = _has_checked_bag(o)
        scored.append((pts, taxes, has_bag, o))

    if not scored:
        return None, None, {}

    # best base: menor points, desempata por menor taxa
    scored.sort(key=lambda x: (x[0], x[1]))
    base_pts = scored[0][0]
    best_offer = scored[0][3]

    # candidates com bagagem despachada e maior que base
    bag_candidates = sorted({pts for (pts, _tx, has_bag, _o) in scored if has_bag and pts > base_pts})
    bag_pts = bag_candidates[0] if bag_candidates else None

    # fallback: se não achou bag por marcação, usa o próximo tier (segunda menor) > base
    if bag_pts is None:
        next_tier = sorted({pts for (pts, _tx, _hb, _o) in scored if pts > base_pts})
        bag_pts = next_tier[0] if next_tier else None

    # garantia da regra do projeto: bagagem nunca pode ser menor/igual
    if bag_pts is not None and bag_pts <= base_pts:
        bag_pts = None

    return base_pts, bag_pts, best_offer


def extract_latam_miles_rows(raw: Dict[str, Any], trip_type: str) -> List[Dict[str, Any]]:
    """
    LATAM-only:
    - pega apenas ofertas com pointsInfo.totalPoints
    - Milhas = menor totalPoints do grupo
    - Bagagem (23kg pts) = menor totalPoints > Milhas (preferindo offers com despachar; fallback = próximo tier)
    - RT: retorna 2 linhas por opção (IDA e VOLTA), uma abaixo da outra
    """
    groups = raw.get("flightGroups") or []
    out: List[Dict[str, Any]] = []

    for g in groups:
        offers = g.get("offers") or []

        # só offers com pontos
        points_offers = []
        for o in offers:
            pts = _get_points_total(o)
            if pts is not None and pts > 0:
                points_offers.append(o)

        if not points_offers:
            continue

        miles, bag_miles, best_offer = _select_base_and_bag_points(points_offers)
        if miles is None:
            continue

        price = (best_offer.get("price") or {}) if isinstance(best_offer, dict) else {}
        taxes = _sum_taxes(price)
        link = _get_booking_url(best_offer) if isinstance(best_offer, dict) else ""
        
        # Split points
        miles_out, miles_in = _get_points_split(best_offer) if isinstance(best_offer, dict) else (None, None)

        group_id = g.get("signature") or g.get("humanSignature") or ""

        base = {
            "Programa": "LATAM",
            "Tipo": trip_type,
            "Milhas": miles,
            "Taxas (R$)": taxes,
            "Bagagem": bag_miles if bag_miles is not None else "—",
            "GroupId": group_id,
            "Link": link,
            # Novos campos para leg split
            "outbound_total": price.get("outboundTotal"),
            "inbound_total": price.get("inboundTotal"),
            "miles_out": miles_out,
            "miles_in": miles_in,
        }

        fi = g.get("flightInfo") or {}
        itins = fi.get("itineraries") or []

        if trip_type == "RT" and len(itins) >= 2:
            for leg_idx, it in enumerate(itins[:2]):
                row = dict(base)
                row.update(_leg_row(it))
                row["_sort_miles"] = miles or 10**18
                row["_sort_gid"] = group_id
                row["_sort_leg"] = leg_idx
                out.append(row)
        else:
            it = itins[0] if itins else {}
            row = dict(base)
            row.update(_leg_row(it))
            row["_sort_miles"] = miles or 10**18
            row["_sort_gid"] = group_id
            row["_sort_leg"] = 0
            out.append(row)

    out.sort(key=lambda r: (r.get("_sort_miles", 10**18), r.get("_sort_gid", ""), r.get("_sort_leg", 0)))
    for r in out:
        r.pop("_sort_miles", None)
        r.pop("_sort_gid", None)
        r.pop("_sort_leg", None)

    return out








