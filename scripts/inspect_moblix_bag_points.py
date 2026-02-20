from __future__ import annotations

import argparse
import json
import os
import re
from typing import Any, Dict, List, Optional, Tuple


def _num(x: Any) -> Optional[float]:
    if x is None:
        return None
    if isinstance(x, (int, float)):
        return float(x)
    s = str(x).strip().replace(",", ".")
    try:
        return float(s)
    except Exception:
        return None


def _get_offer_points(offer: Dict[str, Any]) -> Optional[int]:
    price = offer.get("price") or {}
    pi = price.get("pointsInfo") or {}
    tp = pi.get("totalPoints")
    if tp is None:
        return None
    try:
        return int(float(tp))
    except Exception:
        return None


def _sum_taxes(offer: Dict[str, Any]) -> float:
    price = offer.get("price") or {}
    taxes = price.get("taxes") or []
    total = 0.0
    for t in taxes:
        if not isinstance(t, dict):
            continue
        amt = _num(t.get("amount"))
        if amt is not None:
            total += float(amt)
    return float(total)


def _has_checked_bag_23kg(offer: Dict[str, Any]) -> bool:
    bags = offer.get("baggageIncluded") or []
    for b in bags:
        if not isinstance(b, dict):
            continue
        t = str(b.get("type") or "").lower()
        desc = str(b.get("description") or "").lower()
        inc = b.get("isIncluded")

        # regra forte
        if t == "despachar" and inc is True:
            return True

        # fallback por texto (quando o tipo vem diferente)
        if "despach" in t or "despach" in desc:
            return True
        if "23" in desc and "kg" in desc:
            return True

    return False


def _pick_itinerary(group: Dict[str, Any], which: str) -> Optional[Dict[str, Any]]:
    fi = group.get("flightInfo") or {}
    itins = fi.get("itineraries") or []
    for it in itins:
        if isinstance(it, dict) and it.get("type") == which:
            return it
    return None


def _itin_summary(it: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not it:
        return {}
    segs = it.get("segments") or []
    if not segs:
        return {}

    first = segs[0]
    last = segs[-1]
    dep = first.get("departure") or {}
    arr = last.get("arrival") or {}

    # pontos por trecho (se existir)
    leg_points = None
    ip = (it.get("price") or {}).get("pointsInfo") or {}
    if ip.get("totalPoints") is not None:
        try:
            leg_points = int(float(ip.get("totalPoints")))
        except Exception:
            leg_points = None

    # flight numbers
    fnums = []
    for s in segs:
        fn = s.get("flightNumber")
        cc = ((s.get("marketingCarrier") or {}).get("code")) or ""
        if fn:
            fnums.append(f"{cc}{fn}" if cc else str(fn))

    return {
        "origin": dep.get("airport") or dep.get("city"),
        "destination": arr.get("airport") or arr.get("city"),
        "date": str(dep.get("dateTime") or "")[:10],
        "dep": dep.get("time") or str(dep.get("dateTime") or "")[11:16],
        "arr": arr.get("time") or str(arr.get("dateTime") or "")[11:16],
        "stops": it.get("stops"),
        "duration_min": it.get("duration"),
        "flight_numbers": ", ".join(fnums) if fnums else "",
        "leg_points": leg_points,
    }


def _select_best_variants(offers: List[Dict[str, Any]]) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    # filtra só offers COM totalPoints
    scored = []
    for o in offers:
        pts = _get_offer_points(o)
        if pts is None:
            continue
        taxes = _sum_taxes(o)
        bag = _has_checked_bag_23kg(o)
        scored.append((bag, pts, taxes, o))

    if not scored:
        return None, None

    # best SEM 23kg
    sem = [x for x in scored if x[0] is False]
    com = [x for x in scored if x[0] is True]

    best_sem = min(sem, key=lambda x: (x[1], x[2]))[3] if sem else None
    best_com = min(com, key=lambda x: (x[1], x[2]))[3] if com else None

    return best_sem, best_com


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", required=True, help="Caminho do JSON raw salvo (debug_dumps/...)")
    ap.add_argument("--provider", default=None, help="Filtrar providerId (ex: latam, smiles, azul)")
    ap.add_argument("--limit-groups", type=int, default=5, help="Quantos flightGroups imprimir (default=5)")
    ap.add_argument("--limit-offers", type=int, default=10, help="Quantas offers por grupo imprimir (default=10)")
    args = ap.parse_args()

    path = args.file
    if not os.path.exists(path):
        raise SystemExit(f"Arquivo não encontrado: {path}")

    data = json.load(open(path, "r", encoding="utf-8"))
    groups = data.get("flightGroups") or []
    print(f"\nFILE: {path}")
    print(f"requestId={data.get('requestId')} | groups={len(groups)}\n")

    shown = 0
    for idx, g in enumerate(groups, start=1):
        if shown >= args.limit_groups:
            break

        offers = g.get("offers") or []
        if args.provider:
            offers = [o for o in offers if str(o.get("providerId") or "").lower() == args.provider.lower()]

        # se depois do filtro não sobrou, pula
        if not offers:
            continue

        hs = g.get("humanSignature") or g.get("signature") or ""
        print("=" * 90)
        print(f"GROUP #{idx}: {hs}")

        out_it = _itin_summary(_pick_itinerary(g, "outbound"))
        in_it = _itin_summary(_pick_itinerary(g, "inbound"))

        if out_it:
            print(f"  IDA  : {out_it.get('origin')}->{out_it.get('destination')} {out_it.get('date')} "
                  f"{out_it.get('dep')}-{out_it.get('arr')} | stops={out_it.get('stops')} | "
                  f"dur={out_it.get('duration_min')}min | flights={out_it.get('flight_numbers')} | "
                  f"leg_points={out_it.get('leg_points')}")
        if in_it:
            print(f"  VOLTA: {in_it.get('origin')}->{in_it.get('destination')} {in_it.get('date')} "
                  f"{in_it.get('dep')}-{in_it.get('arr')} | stops={in_it.get('stops')} | "
                  f"dur={in_it.get('duration_min')}min | flights={in_it.get('flight_numbers')} | "
                  f"leg_points={in_it.get('leg_points')}")

        best_sem, best_com = _select_best_variants(offers)

        # imprime offers (ordenadas por pontos)
        scored = []
        for o in offers:
            pts = _get_offer_points(o)
            if pts is None:
                continue
            taxes = _sum_taxes(o)
            bag = _has_checked_bag_23kg(o)
            scored.append((pts, taxes, bag, o))

        scored.sort(key=lambda x: (x[0], x[1]))
        print(f"\n  OFFERS (com totalPoints): {len(scored)}")
        for (pts, taxes, bag, o) in scored[: args.limit_offers]:
            bag_txt = "COM_23KG" if bag else "SEM_23KG"
            print(f"   - id={o.get('id')} provider={o.get('providerId')} {bag_txt} "
                  f"points={pts} taxes={taxes:.2f} searchType={o.get('searchType')}")

        def _line(label: str, o: Optional[Dict[str, Any]]):
            if not o:
                print(f"\n  {label}: NOT FOUND")
                return
            pts = _get_offer_points(o)
            taxes = _sum_taxes(o)
            bag = _has_checked_bag_23kg(o)
            print(f"\n  {label}: id={o.get('id')} provider={o.get('providerId')} "
                  f"{'COM_23KG' if bag else 'SEM_23KG'} points={pts} taxes={taxes:.2f}")

        _line("BEST_SEM_23KG", best_sem)
        _line("BEST_COM_23KG", best_com)

        shown += 1

    print("\nDONE.\n")


if __name__ == "__main__":
    main()
