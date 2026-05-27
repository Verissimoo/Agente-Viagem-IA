"""Inspeção legível de um snapshot capturado.

Uso:
    python tests/snapshots/view.py fixtures/quote-for-date__GRU-SSA__2026-06-15.json
    python tests/snapshots/view.py <arquivo> --check-empty
    python tests/snapshots/view.py <arquivo> --unvalidated-only
"""
from __future__ import annotations

import argparse
import json
import sys
import io
from pathlib import Path

# Forçar stdout em UTF-8 no Windows (default cp1252 não imprime unicode tipo →).
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")


def _fmt_brl(v) -> str:
    if v is None:
        return "—"
    return f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _fmt_int(v) -> str:
    if v is None:
        return "—"
    return f"{int(v):,}".replace(",", ".")


def inspect_quote_for_date(body: dict, opts) -> None:
    print(f"=== Cotação Completa  {body['origin']} -> {body['destination']}  {body['date']}" +
          (f" / volta {body['return_date']}" if body.get('return_date') else "") + " ===\n")

    # Veredito
    print("VEREDITO PcD")
    for v in body.get("verdict", []):
        row = v.get("row")
        if not row:
            print(f"  [{v['label']}]  Sem resultado · {v['description']}")
            continue
        cost = _fmt_brl(row.get('real_cost_brl'))
        miles = _fmt_int(row.get('miles'))
        print(f"  [{v['label']}]  {row['companhia_label']}  {cost}  ({miles} mi)")
    print()

    # Buckets
    print("BUCKETS")
    for code in body.get("bucket_order", []):
        b = body["buckets"][code]
        best = b.get("best")
        best_str = f"melhor: {best['id']} {_fmt_brl(best.get('real_cost_brl'))}" if best else "sem ofertas"
        print(f"  {code:12} {b['label']:32} rows={len(b['rows']):>3}  {best_str}")
    print()

    # Cross-validate
    all_rows = body["buckets"]["ALL"]["rows"]
    miles_rows = [r for r in all_rows if r.get("miles")]
    validated = [r for r in miles_rows if r.get("is_validated")]
    print(f"CROSS-VALIDATE  total milhas={len(miles_rows)}  validadas (Economilhas)={len(validated)}  fonte única (só BuscaMilhas)={len(miles_rows) - len(validated)}\n")

    # Checagens opcionais
    if opts.check_empty:
        print("CAMPOS VAZIOS SUSPEITOS")
        empty_real_cost = [r for r in miles_rows if r.get("real_cost_brl") in (None, 0)]
        if empty_real_cost:
            print(f"  ⚠ {len(empty_real_cost)} linhas de milhas com real_cost_brl vazio:")
            for r in empty_real_cost[:5]:
                print(f"    {r['id']:5} {r['companhia_label']:8} {_fmt_int(r.get('miles'))} mi  taxas={_fmt_brl(r.get('taxes_brl'))}")
        else:
            print("  ✓ todas as linhas de milhas têm real_cost_brl preenchido")

        empty_price_final = [r for r in all_rows if r.get("price_brl") is None]
        if empty_price_final:
            print(f"  ⚠ {len(empty_price_final)} linhas com price_brl=None (coluna PREÇO FINAL fica vazia)")
        else:
            print("  ✓ todas as linhas têm price_brl preenchido")
        print()

    if opts.unvalidated_only:
        unval = [r for r in miles_rows if not r.get("is_validated")]
        print(f"VOOS NÃO VALIDADOS ({len(unval)})")
        for r in unval[:20]:
            print(f"  {r['id']:5} {r['companhia_label']:6} {_fmt_int(r.get('miles')):>10} mi  fonte={r.get('validation_sources')}")
        print()


def inspect_explore(body: dict, opts) -> None:
    print(f"=== Explore  {body['origin']} -> {body['destination']}  centro {body['central_date']} ===\n")
    print(f"Melhor data: {body.get('best_date')} ({_fmt_brl(body.get('best_price_brl'))})")
    print(f"Sua data:    {body.get('requested_date')} ({_fmt_brl(body.get('requested_date_price_brl'))})")
    print(f"Economia:    {_fmt_brl(body.get('savings_brl'))}")
    print(f"Estabilidade: {body.get('stability')} — {body.get('stability_message')}\n")
    print("CALENDÁRIO")
    for d in body.get("days", []):
        marker = "★" if d["date"] == body.get("best_date") else (">" if d["date"] == body.get("requested_date") else " ")
        carriers = ",".join(c["iata"] for c in d.get("carriers", [])[:3])
        print(f"  {marker} {d['date']}  {_fmt_brl(d['min_price_brl']):>12}  ofertas={d['offer_count']:>3}  {carriers}")


def inspect_search(body: dict, opts) -> None:
    print(f"=== Search request_id={body['request_id']} ===\n")
    for kind, label in [("best_overall", "BEST OVERALL"), ("best_miles", "BEST MILHAS"), ("best_money", "BEST CASH")]:
        o = body.get(kind)
        if not o:
            print(f"  [{label}]  Sem ofertas")
            continue
        seg = (o.get("outbound") or {}).get("segments", [{}])[0]
        print(f"  [{label}]  {o.get('airline')}  {_fmt_brl(o.get('equivalent_brl'))}  "
              f"miles={_fmt_int(o.get('miles'))}  source={o.get('source')}  carrier={seg.get('carrier')}")
    print()
    print("CENÁRIOS")
    for scen, offers in (body.get("scenarios") or {}).items():
        print(f"  {scen:14} {len(offers)} ofertas")


def main():
    parser = argparse.ArgumentParser(description="Inspeciona snapshots")
    parser.add_argument("file", type=Path)
    parser.add_argument("--check-empty", action="store_true",
                        help="Reporta campos vazios suspeitos (real_cost, price_brl)")
    parser.add_argument("--unvalidated-only", action="store_true",
                        help="Mostra só ofertas de milhas que NÃO foram cross-validated")
    args = parser.parse_args()

    if not args.file.exists():
        print(f"Arquivo não encontrado: {args.file}")
        sys.exit(1)

    body = json.loads(args.file.read_text(encoding="utf-8"))
    name = args.file.stem
    if name.startswith("quote-for-date"):
        inspect_quote_for_date(body, args)
    elif name.startswith("explore"):
        inspect_explore(body, args)
    elif name.startswith("search"):
        inspect_search(body, args)
    else:
        print(json.dumps(body, indent=2, ensure_ascii=False)[:2000])


if __name__ == "__main__":
    main()
