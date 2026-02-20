from __future__ import annotations

import argparse
import json
import os
from typing import Any, Dict, List, Optional


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


def offer_points(offer: Dict[str, Any]) -> Optional[int]:
    price = offer.get("price") or {}
    pi = price.get("pointsInfo") or {}
    tp = pi.get("totalPoints")
    if tp is None:
        return None
    try:
        return int(float(tp))
    except Exception:
        return None


def offer_taxes(offer: Dict[str, Any]) -> float:
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


def summarize_bags(offer: Dict[str, Any]) -> List[Dict[str, Any]]:
    out = []
    for b in (offer.get("baggageIncluded") or []):
        if not isinstance(b, dict):
            continue
        out.append(
            {
                "type": b.get("type"),
                "quantity": b.get("quantity"),
                "isIncluded": b.get("isIncluded"),
                "description": (b.get("description") or "")[:140],
            }
        )
    return out


def is_checked_23kg_latam(offer: Dict[str, Any]) -> bool:
    """
    Regra mais forte para evitar falso positivo:
    - isIncluded=True
    - indica despachar
    - descrição contém '23' e 'kg' (e preferencialmente 'despach')
    """
    for b in (offer.get("baggageIncluded") or []):
        if not isinstance(b, dict):
            continue

        inc = b.get("isIncluded") is True
        if not inc:
            continue

        t = str(b.get("type") or "").lower()
        desc = str(b.get("description") or "").lower()

        looks_checked = ("despach" in t) or ("despach" in desc)
        looks_23kg = ("23" in desc and "kg" in desc)

        if looks_checked and looks_23kg:
            return True

    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", required=True, help="Caminho do JSON raw (debug_dumps/...)")
    ap.add_argument("--limit-groups", type=int, default=10, help="Quantos flightGroups imprimir")
    ap.add_argument(
        "--only-human-contains",
        dest="only_human_contains",
        default=None,
        help="Filtra group por humanSignature contendo texto (ex: 'BSB-SDU-3782')",
    )
    args = ap.parse_args()

    if not os.path.exists(args.file):
        raise SystemExit(f"Arquivo não encontrado: {args.file}")

    d = json.load(open(args.file, "r", encoding="utf-8"))
    groups = d.get("flightGroups") or []
    print(f"file={args.file}")
    print(f"requestId={d.get('requestId')} groups={len(groups)}\n")

    shown = 0
    for g in groups:
        if shown >= args.limit_groups:
            break

        hs = g.get("humanSignature") or g.get("signature") or ""

        # ✅ CORRETO: args.only_human_contains (hífen vira underscore)
        if args.only_human_contains and args.only_human_contains not in hs:
            continue

        offers = [o for o in (g.get("offers") or []) if str(o.get("providerId") or "").lower() == "latam"]

        offers_scored = []
        for o in offers:
            pts = offer_points(o)
            if pts is None:
                continue
            offers_scored.append((pts, offer_taxes(o), o))

        if not offers_scored:
            continue

        offers_scored.sort(key=lambda x: (x[0], x[1]))

        print("=" * 100)
        print(f"GROUP: {hs}")
        print(f"offers_with_points={len(offers_scored)}")

        sem_pts: List[int] = []
        com_pts: List[int] = []

        for pts, tx, o in offers_scored:
            bag23 = is_checked_23kg_latam(o)
            bag_txt = "COM_23KG" if bag23 else "SEM_23KG"
            bags = summarize_bags(o)

            print(f"- id={o.get('id')} {bag_txt} points={pts} taxes={tx:.2f} searchType={o.get('searchType')}")
            print(f"  baggageIncluded={bags}")

            if bag23:
                com_pts.append(pts)
            else:
                sem_pts.append(pts)

        if com_pts and sem_pts and (min(com_pts) < min(sem_pts)):
            print(f"⚠️ ALERTA: COM_23KG menor que SEM_23KG. min_com={min(com_pts)} min_sem={min(sem_pts)}")
            print("   Isso sugere falso positivo de 23kg OU uma oferta/bundle diferente.\n")

        shown += 1

    print("\nDONE.")


if __name__ == "__main__":
    main()

