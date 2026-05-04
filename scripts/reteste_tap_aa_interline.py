"""
scripts/reteste_tap_aa_interline.py
=====================================
Testa TAP, American Airlines e INTERLINE com variações de rota e data
para identificar se o STATUS_CODE_ERROR_9000 é:
  - Credencial da conta (todas falham)
  - Rota específica (algumas rotas funcionam)
  - Janela de data (futuro distante falha)

Variações testadas por companhia:
  TAP        : GRU->LIS (+60d), GRU->LIS (+30d), GRU->OPO (+60d), SSA->LIS (+60d)
  AMERICAN   : GRU->JFK (+60d), GRU->MIA (+30d), EZE->MIA (+60d), GIG->JFK (+60d)
  INTERLINE  : GRU->MIA (+60d), GRU->JFK (+60d), GRU->EZE (+60d)

Saida: terminal + debug_dumps/reteste_tap_aa_interline.md
"""

import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

_ROOT = Path(__file__).parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from pcd.core.schema import SearchRequest, CabinClass, TripType
from pcd.adapters.buscamilhas_adapter import (
    BuscaMilhasTapAdapter,
    BuscaMilhasAmericanAdapter,
    BuscaMilhasInterlineAdapter,
)

_TODAY = datetime.now()
OUTPUT_DIR  = _ROOT / "debug_dumps"
REPORT_FILE = OUTPUT_DIR / "reteste_tap_aa_interline.md"

def _d(days: int) -> str:
    return (_TODAY + timedelta(days=days)).date().strftime("%d/%m/%Y")

def _date(days: int):
    return (_TODAY + timedelta(days=days)).date()

# ---------------------------------------------------------------------------
# Casos de teste — múltiplas variações por companhia
# ---------------------------------------------------------------------------
TEST_CASES = [
    # ─── TAP ────────────────────────────────────────────────────────────────
    {"cia": "TAP", "origin": "GRU", "dest": "LIS", "days": 60,  "label": "TAP GRU->LIS +60d"},
    {"cia": "TAP", "origin": "GRU", "dest": "LIS", "days": 30,  "label": "TAP GRU->LIS +30d"},
    {"cia": "TAP", "origin": "GRU", "dest": "OPO", "days": 60,  "label": "TAP GRU->OPO +60d (Porto)"},
    {"cia": "TAP", "origin": "SSA", "dest": "LIS", "days": 60,  "label": "TAP SSA->LIS +60d (Salvador)"},
    {"cia": "TAP", "origin": "FOR", "dest": "LIS", "days": 60,  "label": "TAP FOR->LIS +60d (Fortaleza)"},

    # ─── AMERICAN AIRLINES ──────────────────────────────────────────────────
    {"cia": "AMERICAN AIRLINES", "origin": "GRU", "dest": "JFK", "days": 60,  "label": "AA  GRU->JFK +60d"},
    {"cia": "AMERICAN AIRLINES", "origin": "GRU", "dest": "MIA", "days": 30,  "label": "AA  GRU->MIA +30d"},
    {"cia": "AMERICAN AIRLINES", "origin": "EZE", "dest": "MIA", "days": 60,  "label": "AA  EZE->MIA +60d (Buenos Aires)"},
    {"cia": "AMERICAN AIRLINES", "origin": "GIG", "dest": "JFK", "days": 60,  "label": "AA  GIG->JFK +60d (Galeao)"},
    {"cia": "AMERICAN AIRLINES", "origin": "GRU", "dest": "DFW", "days": 60,  "label": "AA  GRU->DFW +60d (Dallas hub)"},

    # ─── INTERLINE ──────────────────────────────────────────────────────────
    {"cia": "INTERLINE", "origin": "GRU", "dest": "MIA", "days": 60,  "label": "IL  GRU->MIA +60d"},
    {"cia": "INTERLINE", "origin": "GRU", "dest": "JFK", "days": 60,  "label": "IL  GRU->JFK +60d"},
    {"cia": "INTERLINE", "origin": "GRU", "dest": "EZE", "days": 60,  "label": "IL  GRU->EZE +60d (Argentina)"},
]

_ADAPTERS = {
    "TAP":              BuscaMilhasTapAdapter(),
    "AMERICAN AIRLINES":BuscaMilhasAmericanAdapter(),
    "INTERLINE":        BuscaMilhasInterlineAdapter(),
}


def _make_request(origin: str, dest: str, days: int) -> SearchRequest:
    d = _date(days)
    return SearchRequest(
        origin=[origin.upper()],
        destination=[dest.upper()],
        date_start=d,
        date_end=d,
        trip_type=TripType.ONEWAY,
        adults=1,
        cabin=CabinClass.ECONOMY,
    )


def run():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    print("=" * 70)
    print("  Reteste: TAP / American Airlines / INTERLINE")
    print(f"  {len(TEST_CASES)} combinacoes de rota + data")
    print("=" * 70)

    results = []

    for idx, case in enumerate(TEST_CASES, 1):
        cia    = case["cia"]
        origin = case["origin"]
        dest   = case["dest"]
        days   = case["days"]
        label  = case["label"]
        date_s = (_TODAY + timedelta(days=days)).strftime("%d/%m/%Y")

        adapter = _ADAPTERS[cia]
        req     = _make_request(origin, dest, days)

        # Pequena pausa entre chamadas
        if idx > 1:
            time.sleep(1.0)

        t0 = time.time()
        try:
            offers  = adapter.search(req, use_fixtures=False, debug_dump=False)
            elapsed = time.time() - t0
            n       = len(offers)

            if n > 0:
                status = "OK"
                icon   = "[OK]"
                detail = f"{n} oferta(s)"
                m  = getattr(offers[0], "miles", None)
                tx = getattr(offers[0], "taxes_brl", None)
                sample = f"{m:,} milhas + R$ {tx or 0:.2f}" if m else ""
            else:
                status = "VAZIO"
                icon   = "[VAZIO]"
                detail = "0 ofertas"
                sample = ""

        except Exception as exc:
            elapsed = time.time() - t0
            err     = str(exc)
            status  = "ERRO"
            icon    = "[ERRO]"
            detail  = err[:120]
            sample  = ""

        elapsed_s = f"{elapsed:.1f}s"
        print(f"[{idx:02d}] {label:<40} {date_s}  {icon}  {detail}  ({elapsed_s})")
        if sample:
            print(f"     -> {sample}")

        results.append({
            "label":   label,
            "cia":     cia,
            "route":   f"{origin}->{dest}",
            "date":    date_s,
            "status":  status,
            "icon":    icon,
            "detail":  detail,
            "sample":  sample,
            "elapsed": elapsed_s,
        })

    # Relatório
    print("\n" + "=" * 70)
    ok_n    = sum(1 for r in results if r["status"] == "OK")
    empty_n = sum(1 for r in results if r["status"] == "VAZIO")
    err_n   = sum(1 for r in results if r["status"] == "ERRO")
    print(f"  Resultado: {ok_n} OK | {empty_n} Vazio | {err_n} Erro")

    # Agrupado por companhia
    for cia_name in ["TAP", "AMERICAN AIRLINES", "INTERLINE"]:
        cia_results = [r for r in results if r["cia"] == cia_name]
        ok_cia = [r for r in cia_results if r["status"] == "OK"]
        print(f"\n  {cia_name}: {len(ok_cia)}/{len(cia_results)} OK")
        for r in cia_results:
            print(f"    {r['icon']}  {r['label']:<40}  {r['detail']}")
            if r["sample"]:
                print(f"         -> {r['sample']}")

    # Salva MD
    md_lines = [
        "# Reteste TAP / American Airlines / INTERLINE",
        "",
        f"**Executado em:** {datetime.now().strftime('%d/%m/%Y %H:%M')}",
        "",
        f"| Resultado | Count |",
        f"|---|---|",
        f"| OK | {ok_n} |",
        f"| Vazio | {empty_n} |",
        f"| Erro | {err_n} |",
        "",
        "## Tabela de resultados",
        "",
        "| Companhia | Rota | Data | Status | Detalhe |",
        "|---|---|---|---|---|",
    ]
    for r in results:
        detail = r["detail"].replace("|", "\\|")[:80]
        md_lines.append(f"| {r['cia']} | {r['route']} | {r['date']} | {r['status']} | {detail} |")

    md_lines += ["", "## Amostras", ""]
    for r in results:
        if r["sample"]:
            md_lines.append(f"- **{r['label']}**: {r['sample']}")

    REPORT_FILE.write_text("\n".join(md_lines), encoding="utf-8")
    print(f"\n  Relatorio salvo: {REPORT_FILE.name}")
    print("=" * 70)

    return ok_n, err_n


if __name__ == "__main__":
    ok, err = run()
    sys.exit(0 if ok > 0 else 1)
