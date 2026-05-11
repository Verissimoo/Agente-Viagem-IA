"""
Testes obrigatórios da Quebra de Trecho (Fase 1 — hub fixo GRU).

Roda 5 cenários reais e imprime route_type, número de ofertas por perna
e amostra de preços para validação manual.

Execução:
    python tests/test_segment_split_v1.py
"""
from __future__ import annotations

import os
import sys

# garantir import a partir da raiz do repo
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

# carregar .env (RAPIDAPI_KEY etc.) — os clientes leem via os.getenv
try:
    from dotenv import load_dotenv  # type: ignore
    load_dotenv(os.path.join(ROOT, ".env"))
except Exception:
    pass

from pcd.agents.segment_split import SegmentSplitAgent, SimpleSegmentResult


CASES = [
    # (origin, destination, date, expected_route_type)
    ("BSB", "MIA", "2026-06-14", "br_to_intl"),
    ("MAD", "GIG", "2026-07-20", "intl_to_br"),
    ("CGB", "MCZ", "2026-08-10", "br_domestic"),
    ("GRU", "CGH", "2026-06-05", "not_applicable"),
    ("LIS", "MIA", "2026-09-15", "not_applicable"),
]


def _fmt_offers(offers):
    if offers is None:
        return "  (n/a — perna não pesquisada para este tipo de rota)"
    if not offers:
        return "  (vazio — Kayak não retornou ofertas)"
    out = []
    for i, o in enumerate(offers[:3]):
        cias = ",".join(o.airlines_iata) or ",".join(o.airlines[:2]) or "?"
        dep = o.departure_dt.strftime("%H:%M") if o.departure_dt else "—"
        arr = o.arrival_dt.strftime("%H:%M") if o.arrival_dt else "—"
        out.append(
            f"  #{i+1} {o.origin}->{o.destination} {dep}->{arr} "
            f"{o.duration_min}min stops={o.stops} R${o.price_brl:,.2f} cias=[{cias}]"
        )
    if len(offers) > 3:
        out.append(f"  ... +{len(offers)-3} ofertas")
    return "\n".join(out)


def run_case(ori: str, dst: str, date: str, expected: str) -> tuple[bool, str]:
    print("=" * 76)
    print(f"CASO: {ori} -> {dst} em {date}  (esperado: {expected})")
    print("=" * 76)
    agent = SegmentSplitAgent()
    res: SimpleSegmentResult = agent.run(origin=ori, destination=dst, date=date)

    print(f"  route_type           : {res.route_type}")
    print(f"  not_applicable_reason: {res.not_applicable_reason}")
    print(f"  notes                : {res.notes}")

    if res.direct_offer is not None:
        print(
            f"  direct_offer (ref)   : R${res.direct_offer.price_brl:,.2f} "
            f"cias={res.direct_offer.airlines_iata or res.direct_offer.airlines}"
        )
    else:
        print("  direct_offer (ref)   : (não disponível)")

    print("  leg_to_gru  (origem -> GRU):")
    print(_fmt_offers(res.leg_to_gru))
    print("  leg_from_gru (GRU -> destino):")
    print(_fmt_offers(res.leg_from_gru))

    ok = (res.route_type == expected)
    if expected != "not_applicable":
        if expected == "br_to_intl":
            ok = ok and bool(res.leg_from_gru) and (res.leg_to_gru is None)
        elif expected == "intl_to_br":
            ok = ok and bool(res.leg_to_gru) and (res.leg_from_gru is None)
        elif expected == "br_domestic":
            ok = ok and (res.leg_to_gru is not None) and (res.leg_from_gru is not None)
    verdict = "PASS" if ok else "FAIL"
    print(f"  >>> {verdict} <<<")
    return ok, res.route_type


def main():
    results = []
    for ori, dst, date, expected in CASES:
        ok, got = run_case(ori, dst, date, expected)
        results.append((ori, dst, date, expected, got, ok))
        print()

    print("=" * 76)
    print("RESUMO")
    print("=" * 76)
    for ori, dst, date, expected, got, ok in results:
        flag = "OK " if ok else "XX "
        print(f"  {flag} {ori}->{dst} {date}  esperado={expected:<16} got={got}")

    fails = sum(1 for _, _, _, _, _, ok in results if not ok)
    print(f"\nTotal: {len(results)} casos · falhas: {fails}")
    sys.exit(0 if fails == 0 else 1)


if __name__ == "__main__":
    main()
