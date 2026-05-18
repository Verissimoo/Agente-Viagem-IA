"""
Diagnóstico de saúde de todas as APIs externas do Agente de Cotação PcD.

Cobre Kayak (RapidAPI), BuscaMilhas, Economilhas e Groq (LLM de intent).
Salva relatório consolidado em tests/diagnostic_report.json e imprime
resumo legível no stdout.

Execução:
    python tests/test_all_apis.py
"""
from __future__ import annotations

import io
import json
import os
import sys
import time
import traceback
from datetime import date, datetime, timedelta

# UTF-8 no Windows
try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

try:
    from dotenv import load_dotenv  # type: ignore
    load_dotenv(os.path.join(ROOT, ".env"))
except Exception:
    pass


# Rotas usadas em todos os providers para comparabilidade.
ROUTES = [
    {"origin": "BSB", "destination": "MAD", "offset": 30, "name": "BR_to_INTL_BSB_MAD",  "intl": True},
    {"origin": "GRU", "destination": "MIA", "offset": 45, "name": "BR_to_USA_GRU_MIA",   "intl": True},
    {"origin": "GRU", "destination": "REC", "offset": 20, "name": "domestica_GRU_REC",   "intl": False},
    {"origin": "LIS", "destination": "GRU", "offset": 60, "name": "INTL_to_BR_LIS_GRU",  "intl": True},
]


def _short(s, n=200) -> str:
    if not s:
        return ""
    s = str(s)
    return s if len(s) <= n else s[: n - 3] + "..."


def _route_dates(route: dict) -> tuple[str, str]:
    """ISO (YYYY-MM-DD) e BR (DD/MM/AAAA) com offset configurado."""
    d = date.today() + timedelta(days=route["offset"])
    return d.isoformat(), d.strftime("%d/%m/%Y")


# ──────────────────────────────────────────────────────────────────
# KAYAK
# ──────────────────────────────────────────────────────────────────
def test_kayak() -> list[dict]:
    print("\n" + "=" * 70)
    print("[KAYAK] (RapidAPI)")
    print("=" * 70)
    results: list[dict] = []

    try:
        from kayak_client import search_flights as kayak_search
        from offer_parser import extract_offers as kayak_extract
    except Exception as ex:
        results.append({"route": "*", "status": "IMPORT_FAIL", "error": str(ex), "elapsed_s": 0.0})
        print(f"  IMPORT_FAIL: {ex!r}")
        return results

    for route in ROUTES:
        iso, _ = _route_dates(route)
        print(f"\n  [KAYAK] {route['name']:30} {route['origin']}->{route['destination']} {iso}")
        t0 = time.time()
        try:
            raw = kayak_search(
                origin=route["origin"],
                destination=route["destination"],
                departure_date=iso,
                adults=1,
                cabin="e",
            )
            elapsed = time.time() - t0
            offers = kayak_extract(raw) or []
            data = (raw or {}).get("data") or {}
            search_status = (
                data.get("searchStatus") if isinstance(data, dict) else None
            ) or (data.get("status") if isinstance(data, dict) else None)
            row = {
                "route": route["name"],
                "iso": iso,
                "elapsed_s": round(elapsed, 2),
                "offers_count": len(offers),
                "kayak_searchStatus": search_status,
            }
            if offers:
                row["status"] = "OK"
                first = offers[0]
                row["sample_price"] = first.get("price")
                row["sample_currency"] = first.get("currency")
                row["sample_airlines"] = first.get("airlines")
                print(f"     OK     {elapsed:.2f}s · {len(offers)} ofertas · "
                      f"primeira: {first.get('price')} {first.get('currency')} ({first.get('airlines')})")
            else:
                row["status"] = "EMPTY"
                print(f"     EMPTY  {elapsed:.2f}s · searchStatus={search_status}")
            results.append(row)
        except Exception as ex:
            elapsed = time.time() - t0
            err = _short(str(ex), 400)
            results.append({
                "route": route["name"], "iso": iso, "status": "ERROR",
                "elapsed_s": round(elapsed, 2), "error": err,
            })
            print(f"     ERROR  {elapsed:.2f}s · {err}")

    return results


# ──────────────────────────────────────────────────────────────────
# BUSCAMILHAS
# ──────────────────────────────────────────────────────────────────
def test_buscamilhas() -> list[dict]:
    print("\n" + "=" * 70)
    print("[BUSCAMILHAS]")
    print("=" * 70)
    results: list[dict] = []

    try:
        from miles_app.buscamilhas_client import search_flights_buscamilhas
        from miles_app.buscamilhas_offer_parser import extract_rows_from_buscamilhas
    except Exception as ex:
        results.append({"route": "*", "status": "IMPORT_FAIL", "error": str(ex)})
        print(f"  IMPORT_FAIL: {ex!r}")
        return results

    cases = [
        ("LATAM", False),
        ("GOL",   False),
        ("AZUL",  False),
    ]

    for route in ROUTES:
        _, br = _route_dates(route)
        for companhia, _intl_default in cases:
            intl = route["intl"]
            print(f"\n  [BM] {companhia:8} {route['origin']}->{route['destination']} "
                  f"{br}  intl={intl}")
            t0 = time.time()
            try:
                raw = search_flights_buscamilhas(
                    companhia=companhia,
                    origem=route["origin"], destino=route["destination"],
                    data_ida=br, adultos=1,
                    somente_milhas=True, internacional=intl,
                )
                elapsed = time.time() - t0
                rows = extract_rows_from_buscamilhas(raw, companhia, "OW")
                miles_rows = [r for r in rows if r.get("IsMiles")]
                status_payload = (raw or {}).get("Status") or {}
                alerta = status_payload.get("Alerta") or status_payload.get("Mensagem")
                row = {
                    "route": route["name"], "company": companhia,
                    "elapsed_s": round(elapsed, 2),
                    "offers_count": len(miles_rows),
                    "api_alert": _short(alerta) if alerta else None,
                }
                if miles_rows:
                    row["status"] = "OK"
                    best = min(miles_rows, key=lambda r: r.get("Milhas") or 10**9)
                    row["best_miles"] = best.get("Milhas")
                    row["best_taxa_brl"] = best.get("Taxas (R$)")
                    print(f"     OK     {elapsed:.2f}s · {len(miles_rows)} ofertas · "
                          f"melhor: {best.get('Milhas')} mi + R${best.get('Taxas (R$)')}")
                else:
                    row["status"] = "EMPTY"
                    print(f"     EMPTY  {elapsed:.2f}s · alert: {alerta or 'nenhum'}")
                results.append(row)
            except Exception as ex:
                elapsed = time.time() - t0
                err = _short(str(ex), 300)
                results.append({
                    "route": route["name"], "company": companhia,
                    "status": "ERROR", "elapsed_s": round(elapsed, 2),
                    "error": err,
                })
                print(f"     ERROR  {elapsed:.2f}s · {err}")

    return results


# ──────────────────────────────────────────────────────────────────
# ECONOMILHAS
# ──────────────────────────────────────────────────────────────────
def test_economilhas() -> list[dict]:
    print("\n" + "=" * 70)
    print("[ECONOMILHAS]")
    print("=" * 70)
    results: list[dict] = []

    try:
        from economilhas_client import search_flights_economilhas
        from economilhas_offer_parser import extract_rows_from_economilhas
    except Exception as ex:
        results.append({"route": "*", "status": "IMPORT_FAIL", "error": str(ex)})
        print(f"  IMPORT_FAIL: {ex!r}")
        return results

    AIRLINES = ["SMILES", "LATAM", "AZUL"]

    for route in ROUTES:
        iso, _ = _route_dates(route)
        print(f"\n  [EC] airlines={AIRLINES} {route['origin']}->{route['destination']} {iso}")
        t0 = time.time()
        try:
            raw = search_flights_economilhas(
                airlines=AIRLINES,
                origin=route["origin"], destination=route["destination"],
                departure_date=iso, adults=1, price_type="MILES",
            )
            elapsed = time.time() - t0
            rows, partial = extract_rows_from_economilhas(raw, "OW")
            miles_rows = [r for r in rows if r.get("IsMiles")]
            per_airline: dict = {}
            try:
                for item in (raw or {}).get("results", []) or []:
                    al = item.get("airline") or item.get("airlineLoyalty")
                    if al:
                        per_airline[al] = {
                            "success": item.get("success", item.get("ok")),
                            "providerStatusCode": item.get("providerStatusCode"),
                            "message": _short(item.get("message"), 80),
                        }
            except Exception:
                pass
            row = {
                "route": route["name"],
                "elapsed_s": round(elapsed, 2),
                "offers_count": len(miles_rows),
                "per_airline": per_airline,
            }
            if miles_rows:
                row["status"] = "OK"
                best = min(miles_rows, key=lambda r: r.get("Milhas") or 10**9)
                row["best_miles"] = best.get("Milhas")
                row["best_taxa_brl"] = best.get("Taxas (R$)")
                print(f"     OK     {elapsed:.2f}s · {len(miles_rows)} ofertas · "
                      f"melhor: {best.get('Milhas')} mi + R${best.get('Taxas (R$)')}")
            else:
                row["status"] = "EMPTY"
                row["partial"] = [
                    {"airline": p.get("airline"), "code": p.get("providerStatusCode"),
                     "msg": _short(p.get("message"), 120)}
                    for p in (partial or [])
                ][:5]
                print(f"     EMPTY  {elapsed:.2f}s · partial={row['partial']}")
            results.append(row)
        except Exception as ex:
            elapsed = time.time() - t0
            err = _short(str(ex), 400)
            results.append({
                "route": route["name"], "status": "ERROR",
                "elapsed_s": round(elapsed, 2), "error": err,
            })
            print(f"     ERROR  {elapsed:.2f}s · {err}")

    return results


# ──────────────────────────────────────────────────────────────────
# GROQ (LLM de intent parsing) — o sistema usa Groq, não Grok
# ──────────────────────────────────────────────────────────────────
def test_groq() -> list[dict]:
    print("\n" + "=" * 70)
    print("[GROQ] (intent parser)")
    print("=" * 70)
    results: list[dict] = []

    try:
        from pcd.nlp.intent_parser import parse_intent_ptbr
    except Exception as ex:
        results.append({"prompt": "*", "status": "IMPORT_FAIL", "error": str(ex)})
        print(f"  IMPORT_FAIL: {ex!r}")
        return results

    if not os.getenv("GROQ_API_KEY"):
        results.append({"prompt": "*", "status": "NO_KEY",
                        "error": "GROQ_API_KEY ausente no ambiente"})
        print("  NO_KEY: GROQ_API_KEY ausente")
        return results

    prompts = [
        "quero um voo de Brasilia para Madrid dia 15/06",
        "preciso de 2 passagens de Sao Paulo para Lisboa direto 20/07",
        "voo Rio de Janeiro para Miami ida 10/08 volta 25/08",
    ]
    for prompt in prompts:
        print(f"\n  [GROQ] {prompt!r}")
        t0 = time.time()
        try:
            intent = parse_intent_ptbr(prompt, use_llm=True)
            elapsed = time.time() - t0
            parsed = {
                "origin": getattr(intent, "origin_iata", None),
                "destination": getattr(intent, "destination_iata", None),
                "date_start": str(getattr(intent, "date_start", None)),
                "date_return": str(getattr(intent, "date_return", None)),
                "adults": getattr(intent, "adults", None),
                "direct_only": getattr(intent, "direct_only", None),
                "trip_type": str(getattr(intent, "trip_type", None)),
                "confidence": getattr(intent, "confidence", None),
                "notes": getattr(intent, "notes", None),
            }
            ok = bool(parsed["origin"] and parsed["destination"])
            row = {
                "prompt": prompt,
                "status": "OK" if ok else "PARSED_EMPTY",
                "elapsed_s": round(elapsed, 2),
                "parsed": parsed,
            }
            results.append(row)
            print(f"     {'OK' if ok else 'EMPTY'}  {elapsed:.2f}s · {parsed}")
        except Exception as ex:
            elapsed = time.time() - t0
            err = _short(str(ex), 400)
            results.append({"prompt": prompt, "status": "ERROR",
                            "elapsed_s": round(elapsed, 2), "error": err})
            print(f"     ERROR  {elapsed:.2f}s · {err}")

    return results


# ──────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────
def main():
    print("=" * 70)
    print("DIAGNOSTICO COMPLETO DE APIs - Agente de Cotacao PcD")
    print(f"Timestamp: {datetime.now().isoformat()}")
    print(f"ROOT: {ROOT}")
    print("=" * 70)

    expected_vars = [
        "RAPIDAPI_KEY", "RAPIDAPI_HOST", "KAYAK_BASE_URL",
        "BUSCAMILHAS_CHAVE", "BUSCAMILHAS_SENHA",
        "ECONOMILHAS_API_KEY",
        "GROQ_API_KEY",
    ]
    print("\n[ENV VARS]")
    env_status: dict = {}
    for var in expected_vars:
        v = os.getenv(var, "")
        env_status[var] = bool(v)
        print(f"  {var:25} {'OK setada (' + str(len(v)) + ' chars)' if v else 'VAZIA'}")

    kayak_results = test_kayak()
    bm_results = test_buscamilhas()
    em_results = test_economilhas()
    groq_results = test_groq()

    report = {
        "timestamp": datetime.now().isoformat(),
        "env_vars": env_status,
        "kayak": kayak_results,
        "buscamilhas": bm_results,
        "economilhas": em_results,
        "groq": groq_results,
    }

    out_path = os.path.join(HERE, "diagnostic_report.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False, default=str)

    print("\n" + "=" * 70)
    print("RESUMO")
    print("=" * 70)
    def _summ(rows: list[dict], total: int | None = None) -> str:
        ok = sum(1 for r in rows if r.get("status") == "OK")
        empty = sum(1 for r in rows if r.get("status") == "EMPTY")
        err = sum(1 for r in rows if r.get("status") in (
            "ERROR", "IMPORT_FAIL", "PARSE_FAIL", "NO_KEY", "PARSED_EMPTY"
        ))
        n = total if total is not None else len(rows)
        return f"{ok}/{n} OK · {empty} empty · {err} fail"

    print(f"  Kayak:        {_summ(kayak_results)}")
    print(f"  BuscaMilhas:  {_summ(bm_results)}")
    print(f"  Economilhas:  {_summ(em_results)}")
    print(f"  Groq:         {_summ(groq_results)}")

    print(f"\nRelatorio completo: {out_path}")


if __name__ == "__main__":
    main()
