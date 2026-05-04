"""
scripts/validate_free_mcp.py
============================
Script de validacao de malha para o plano gratuito do Award Travel Finder.

Testa rotas "certeiras" por companhia (hubs reais onde cada airline opera):
  - British Airways : LHR -> JFK  (hub Londres, rota transatlantica classica)
  - Qatar Airways   : DOH -> JFK  (hub Doha, rota operada diariamente)
  - Cathay Pacific  : HKG -> LHR  (hub Hong Kong, rota longa operada diariamente)

Para cada sucesso:
  - Salva JSON em debug_dumps/success_{airline}_{route}.json
  - Exibe extrato das cabines disponíveis com pontos e taxas

Rate limit: chamadas sequenciais com pausa de 2s entre cada uma.

Uso:
  python scripts/validate_free_mcp.py
"""

import json
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

# Garante que a raiz do projeto está no sys.path
_ROOT = Path(__file__).parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from mcp_client import call_rest_availability
from mcp_offer_parser import extract_mcp_offers

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

OUTPUT_DIR  = _ROOT / "debug_dumps"
SEARCH_DATE = (datetime.now() + timedelta(days=120)).strftime("%Y-%m-%d")  # +4 meses
PAUSE_SEC   = 2  # pausa entre chamadas (respeitar rate limit)

# Rotas certeiras por companhia (hub -> hub)
TEST_CASES = [
    {
        "airline":     "british_airways",
        "departure":   "LHR",
        "arrival":     "JFK",
        "description": "British Airways | Londres -> Nova York (transatlantica principal)",
    },
    {
        "airline":     "qatar_airways",
        "departure":   "DOH",
        "arrival":     "JFK",
        "description": "Qatar Airways | Doha -> Nova York (rota diaria operada)",
    },
    {
        "airline":     "cathay_pacific",
        "departure":   "HKG",
        "arrival":     "LHR",
        "description": "Cathay Pacific | Hong Kong -> Londres (rota longa diaria)",
    },
]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _save_success(airline: str, departure: str, arrival: str, payload: dict) -> Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    route    = f"{departure}_{arrival}"
    filename = f"success_{airline}_{route}.json"
    filepath = OUTPUT_DIR / filename
    with open(filepath, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    return filepath


def _print_offers_summary(airline: str, raw_payload: dict) -> None:
    """Extrai e exibe as ofertas disponiveis do payload bruto."""
    # Monta estrutura esperada pelo parser
    wrapper = {
        "airlines": {
            airline: raw_payload.get("data", raw_payload)
        }
    }
    offers = extract_mcp_offers(wrapper)

    if not offers:
        print("    -> Nenhuma cabine disponivel (data_available: false)")
        return

    cabin_label = {
        "economy":         "Economy",
        "premium_economy": "Premium Economy",
        "business":        "Business",
        "first":           "First",
    }

    for o in offers:
        cab  = cabin_label.get(o["cabin_class"], o["cabin_class"].title())
        prog = o.get("miles_program") or "pontos"
        m    = o["miles"]
        tx   = o["taxes_brl"]
        print(f"    [AVAIL]  {cab:<18} {m:>9,} {prog:<15}  Taxas R$ {tx:.2f}")


# ---------------------------------------------------------------------------
# Runner principal
# ---------------------------------------------------------------------------

def run_validation():
    print("=" * 65)
    print("  Award Travel Finder — Validacao de Malha (Plano Free)")
    print(f"  Data de busca : {SEARCH_DATE}  (hoje + 4 meses)")
    print(f"  Total de casos: {len(TEST_CASES)}")
    print("=" * 65)

    results = []

    for idx, case in enumerate(TEST_CASES, start=1):
        airline    = case["airline"]
        departure  = case["departure"]
        arrival    = case["arrival"]
        route_str  = f"{departure} -> {arrival}"

        print(f"\n[{idx}/{len(TEST_CASES)}] {case['description']}")
        print(f"         Rota : {route_str} | Data: {SEARCH_DATE}")

        # Pausa antes de cada chamada (exceto a primeira)
        if idx > 1:
            print(f"         Aguardando {PAUSE_SEC}s (rate limit)...")
            time.sleep(PAUSE_SEC)

        try:
            payload = call_rest_availability(
                airline=airline,
                departure=departure,
                arrival=arrival,
                date=SEARCH_DATE,
            )

            # Salva JSON de sucesso
            filepath = _save_success(airline, departure, arrival, payload)
            size_kb  = filepath.stat().st_size / 1024

            print(f"         [OK] HTTP 200 -> Salvo em {filepath.name} ({size_kb:.1f} KB)")
            _print_offers_summary(airline, payload)

            offers_found = extract_mcp_offers({"airlines": {airline: payload.get("data", payload)}})
            results.append({
                "airline":   airline,
                "route":     route_str,
                "status":    "OK",
                "file":      str(filepath),
                "has_data":  bool(offers_found),
            })

        except Exception as exc:
            # Extrai código HTTP do erro se possível
            err_str = str(exc)
            http_code = ""
            if "403" in err_str: http_code = "HTTP 403 (Forbidden — rota não coberta pelo plano Free)"
            elif "404" in err_str: http_code = "HTTP 404 (Not Found — airline não suportada)"
            elif "400" in err_str: http_code = "HTTP 400 (Bad Request — parâmetros incorretos)"
            elif "429" in err_str: http_code = "HTTP 429 (Rate Limit atingido)"
            elif "401" in err_str: http_code = "HTTP 401 (Autenticação inválida)"
            else: http_code = err_str[:120]

            print(f"         [FALHA] {http_code}")
            results.append({
                "airline": airline,
                "route":   route_str,
                "status":  "FAIL",
                "reason":  http_code,
            })

    # ---------------------------------------------------------------------------
    # Relatório final
    # ---------------------------------------------------------------------------
    print("\n" + "=" * 65)
    print("  RELATÓRIO FINAL")
    print("=" * 65)

    ok_count   = sum(1 for r in results if r["status"] == "OK")
    fail_count = len(results) - ok_count
    data_count = sum(1 for r in results if r.get("has_data"))

    print(f"  Testadas : {len(results)}")
    print(f"  Sucesso  : {ok_count}  |  Falha: {fail_count}")
    print(f"  Com dados disponíveis (available=true): {data_count}")
    print()

    for r in results:
        status_icon = "✅" if r["status"] == "OK" else "❌"
        data_icon   = "📋" if r.get("has_data") else "📭"
        if r["status"] == "OK":
            print(f"  {status_icon} {data_icon}  {r['airline']:<25}  {r['route']}")
        else:
            print(f"  {status_icon}    {r['airline']:<25}  {r['route']}  -> {r.get('reason','')}")

    # Salva relatório consolidado
    report_file = OUTPUT_DIR / "validate_free_mcp_report.json"
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(report_file, "w", encoding="utf-8") as fh:
        json.dump({
            "run_date":    datetime.now().isoformat(),
            "search_date": SEARCH_DATE,
            "results":     results,
        }, fh, ensure_ascii=False, indent=2)
    print(f"\n  Relatório salvo: {report_file.name}")

    return ok_count, fail_count


if __name__ == "__main__":
    ok, fail = run_validation()
    sys.exit(0 if fail == 0 else 1)
