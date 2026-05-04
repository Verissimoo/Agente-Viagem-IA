"""
scripts/diagnostico_buscamilhas_completo.py
============================================
Health Check completo de todos os adapters do Busca Milhas.

Testa cada companhia em uma rota de alta probabilidade de voos,
daqui a 3 meses (para maximizar chances de resultado).

Companhias testadas:
  Nacionais  : LATAM, GOL, AZUL          (GRU -> BSB)
  TAP        : TAP                        (GRU -> LIS)
  Iberia     : IBERIA                     (GRU -> MAD)
  American   : AMERICAN AIRLINES          (GRU -> MIA)
  Interline  : INTERLINE                  (GRU -> MIA)
  Copa       : COPA                       (GRU -> PTY)

Saidas:
  - Terminal: relatorio formatado
  - Arquivo : debug_dumps/relatorio_buscamilhas.md
"""

import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

# Garante raiz no sys.path
_ROOT = Path(__file__).parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from pcd.core.schema import SearchRequest, CabinClass, TripType
from pcd.adapters.buscamilhas_adapter import (
    BuscaMilhasLatamAdapter,
    BuscaMilhasGolAdapter,
    BuscaMilhasAzulAdapter,
    BuscaMilhasTapAdapter,
    BuscaMilhasIberiaAdapter,
    BuscaMilhasAmericanAdapter,
    BuscaMilhasInterlineAdapter,
    BuscaMilhasCopaAdapter,
)

# ---------------------------------------------------------------------------
# Data de busca: hoje + 3 meses
# ---------------------------------------------------------------------------
_TODAY     = datetime.now()
_DATE_IDA  = (_TODAY + timedelta(days=90)).date()
_DATE_STR  = _DATE_IDA.strftime("%d/%m/%Y")
_ISO_STR   = _DATE_IDA.isoformat()

OUTPUT_DIR  = _ROOT / "debug_dumps"
REPORT_FILE = OUTPUT_DIR / "relatorio_buscamilhas.md"

# ---------------------------------------------------------------------------
# Mapa de testes: adapter + rota de alta probabilidade
# ---------------------------------------------------------------------------
TEST_PLAN = [
    {
        "label":  "LATAM",
        "adapter": BuscaMilhasLatamAdapter(),
        "origin":  "GRU",
        "dest":    "BSB",
        "descr":   "Nacional alta frequencia (GRU->BSB)",
    },
    {
        "label":  "GOL",
        "adapter": BuscaMilhasGolAdapter(),
        "origin":  "GRU",
        "dest":    "BSB",
        "descr":   "Nacional alta frequencia (GRU->BSB)",
    },
    {
        "label":  "AZUL",
        "adapter": BuscaMilhasAzulAdapter(),
        "origin":  "VCP",   # Azul usa Viracopos como hub principal
        "dest":    "BSB",
        "descr":   "Nacional alta frequencia (VCP->BSB)",
    },
    {
        "label":  "TAP",
        "adapter": BuscaMilhasTapAdapter(),
        "origin":  "GRU",
        "dest":    "LIS",
        "descr":   "Internacional (GRU->LIS - hub TAP Lisboa)",
    },
    {
        "label":  "IBERIA",
        "adapter": BuscaMilhasIberiaAdapter(),
        "origin":  "GRU",
        "dest":    "MAD",
        "descr":   "Internacional (GRU->MAD - hub Iberia Madrid)",
    },
    {
        "label":  "AMERICAN AIRLINES",
        "adapter": BuscaMilhasAmericanAdapter(),
        "origin":  "GRU",
        "dest":    "MIA",
        "descr":   "Internacional (GRU->MIA - hub American Miami)",
    },
    {
        "label":  "INTERLINE",
        "adapter": BuscaMilhasInterlineAdapter(),
        "origin":  "GRU",
        "dest":    "MIA",
        "descr":   "Internacional (GRU->MIA)",
    },
    {
        "label":  "COPA",
        "adapter": BuscaMilhasCopaAdapter(),
        "origin":  "GRU",
        "dest":    "PTY",
        "descr":   "Internacional (GRU->PTY - hub Copa Panama City)",
    },
]


# ---------------------------------------------------------------------------
# Helper: monta SearchRequest minimo
# ---------------------------------------------------------------------------

def _make_request(origin: str, dest: str) -> SearchRequest:
    return SearchRequest(
        origin=[origin.upper()],
        destination=[dest.upper()],
        date_start=_DATE_IDA,
        date_end=_DATE_IDA,
        trip_type=TripType.ONEWAY,
        adults=1,
        cabin=CabinClass.ECONOMY,
    )


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def run_health_check() -> list[dict]:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    print("=" * 68)
    print("  Busca Milhas — Health Check Completo")
    print(f"  Data de busca: {_DATE_STR}  (hoje + 90 dias)")
    print(f"  Companhias   : {len(TEST_PLAN)}")
    print("=" * 68)

    results = []

    for idx, case in enumerate(TEST_PLAN, start=1):
        label   = case["label"]
        adapter = case["adapter"]
        origin  = case["origin"]
        dest    = case["dest"]
        descr   = case["descr"]
        route   = f"{origin} -> {dest}"

        print(f"\n[{idx:02d}/{len(TEST_PLAN)}] {label:<22} | {descr}")
        print(f"         Data: {_DATE_STR}")

        t0 = time.time()
        try:
            request = _make_request(origin, dest)
            offers  = adapter.search(request, use_fixtures=False, debug_dump=True)
            elapsed = time.time() - t0
            n       = len(offers)

            if n > 0:
                status = "SUCESSO"
                icon   = "[OK]"
                detail = f"{n} oferta(s) retornada(s)"
            else:
                status = "VAZIO"
                icon   = "[VAZIO]"
                detail = "0 ofertas (sem disponibilidade ou fora do plano)"

            # Amostra da primeira oferta
            sample = ""
            if n > 0:
                o = offers[0]
                m  = getattr(o, "miles", None)
                tx = getattr(o, "taxes_brl", None)
                pg = getattr(o, "price_brl", None)
                if m:
                    sample = f"Amostra: {m:,} milhas + R$ {tx or 0:.2f} taxas"
                elif pg:
                    sample = f"Amostra: R$ {pg:,.2f}"

        except Exception as exc:
            elapsed = time.time() - t0
            status  = "ERRO"
            icon    = "[ERRO]"
            detail  = str(exc)[:200]
            sample  = ""

        elapsed_s = f"{elapsed:.1f}s"
        print(f"         {icon} {detail}  ({elapsed_s})")
        if sample:
            print(f"         {sample}")

        results.append({
            "label":   label,
            "route":   route,
            "descr":   descr,
            "status":  status,
            "icon":    icon,
            "detail":  detail,
            "sample":  sample,
            "elapsed": elapsed_s,
        })

    return results


# ---------------------------------------------------------------------------
# Gera relatório final
# ---------------------------------------------------------------------------

def _gerar_relatorio(results: list[dict]) -> str:
    now      = datetime.now().strftime("%d/%m/%Y %H:%M")
    ok_n     = sum(1 for r in results if r["status"] == "SUCESSO")
    empty_n  = sum(1 for r in results if r["status"] == "VAZIO")
    err_n    = sum(1 for r in results if r["status"] == "ERRO")

    linhas = [
        "# Relatorio de Saude — Busca Milhas",
        "",
        f"**Execucao:** {now}  |  **Data de busca:** {_DATE_STR} (hoje + 90 dias)",
        "",
        f"| Resultado | Count |",
        f"|---|---|",
        f"| Sucesso (ofertas retornadas) | {ok_n} |",
        f"| Vazio (0 ofertas) | {empty_n} |",
        f"| Erro (excecao) | {err_n} |",
        f"| **Total** | **{len(results)}** |",
        "",
        "---",
        "",
        "## Detalhe por Companhia",
        "",
        "| Companhia | Rota | Status | Detalhe | Tempo |",
        "|---|---|---|---|---|",
    ]

    for r in results:
        icon_md = {
            "SUCESSO": "OK",
            "VAZIO":   "VAZIO",
            "ERRO":    "ERRO",
        }.get(r["status"], r["status"])
        detail = r["detail"].replace("|", "\\|")[:80]
        linhas.append(
            f"| {r['label']} | {r['route']} | {icon_md} | {detail} | {r['elapsed']} |"
        )

    linhas += [
        "",
        "---",
        "",
        "## Amostras de Ofertas",
        "",
    ]
    for r in results:
        if r["sample"]:
            linhas.append(f"- **{r['label']}** ({r['route']}): {r['sample']}")

    if not any(r["sample"] for r in results):
        linhas.append("*Nenhuma oferta retornada nesta execucao.*")

    return "\n".join(linhas)


def imprimir_relatorio_terminal(results: list[dict]) -> None:
    ok_n    = sum(1 for r in results if r["status"] == "SUCESSO")
    empty_n = sum(1 for r in results if r["status"] == "VAZIO")
    err_n   = sum(1 for r in results if r["status"] == "ERRO")

    print("\n" + "=" * 68)
    print("  RELATORIO FINAL — Busca Milhas Health Check")
    print("=" * 68)
    print(f"  Data testada : {_DATE_STR}  (hoje + 90 dias)")
    print(f"  Companhias   : {len(results)}  |  "
          f"Sucesso: {ok_n}  |  Vazio: {empty_n}  |  Erro: {err_n}")
    print("-" * 68)

    for r in results:
        line = f"  {r['icon']:<8}  {r['label']:<22}  {r['route']:<12}  {r['elapsed']}"
        print(line)
        if r["sample"]:
            print(f"             -> {r['sample']}")
        if r["status"] == "ERRO":
            print(f"             -> {r['detail'][:100]}")

    print("-" * 68)

    # Salva arquivo
    md_text = _gerar_relatorio(results)
    REPORT_FILE.write_text(md_text, encoding="utf-8")
    print(f"\n  Relatorio salvo: {REPORT_FILE}")
    print("=" * 68)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    results = run_health_check()
    imprimir_relatorio_terminal(results)

    # Retorna exit code baseado nos resultados
    has_error = any(r["status"] == "ERRO" for r in results)
    sys.exit(1 if has_error else 0)
