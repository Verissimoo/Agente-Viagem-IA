"""
Health-check das APIs de milhas — BuscaMilhas e Economilhas.

Para cada programa, dispara uma única consulta em um itinerário com alta
probabilidade de retornar voos. Captura:
  • status (OK / EMPTY / FAIL)
  • tempo de resposta
  • nº de ofertas (rows IsMiles=True)
  • amostra (menor milhagem + taxas)
  • mensagem de erro, se houver

Execução:
    python tests/test_apis_health.py
"""
from __future__ import annotations

import io
import os
import sys
import time
from datetime import date, timedelta

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


# Datas — escolha 70 dias à frente para maximizar disponibilidade.
TODAY = date.today()
DEPART = (TODAY + timedelta(days=70)).isoformat()    # YYYY-MM-DD
DEPART_BR = (TODAY + timedelta(days=70)).strftime("%d/%m/%Y")  # DD/MM/AAAA p/ BuscaMilhas


# Itinerários por programa — escolhidos por alta densidade de voos.
# Buscamilhas: companhia → (origem, destino)
BUSCAMILHAS_CASES: list[tuple[str, str, str, bool]] = [
    # (companhia, origem, destino, internacional)
    ("LATAM",             "GRU", "FOR", False),  # nacional
    ("GOL",               "GRU", "FOR", False),
    ("AZUL",              "GRU", "FOR", False),
    ("TAP",               "GRU", "LIS", True),
    ("IBERIA",            "GRU", "MAD", True),
    ("AMERICAN AIRLINES", "GRU", "MIA", True),
    ("INTERLINE",         "GRU", "MIA", True),   # Azul Pelo Mundo via BuscaMilhas
    ("COPA",              "GRU", "PTY", True),
]

# Economilhas: airline → (origem, destino)
ECONOMILHAS_CASES: list[tuple[str, str, str]] = [
    ("SMILES",         "GRU", "FOR"),
    ("LATAM",          "GRU", "FOR"),
    ("AZUL",           "GRU", "FOR"),
    ("AZUL_INTERLINE", "GRU", "MIA"),
    ("COPA",           "GRU", "PTY"),
    ("IBERIA",         "GRU", "MAD"),
    ("BRITISH",        "GRU", "LHR"),
]


def _short(s, n=120) -> str:
    if not s:
        return ""
    s = str(s)
    return s if len(s) <= n else s[: n - 3] + "..."


def _summary_offer(rows):
    """Devolve (count, melhor_milhas, melhor_taxa) — só linhas IsMiles=True."""
    miles_rows = [r for r in rows if r.get("IsMiles")]
    if not miles_rows:
        return (0, None, None, None)
    best = min(miles_rows, key=lambda r: r.get("Milhas") or 10**9)
    return (
        len(miles_rows),
        best.get("Milhas"),
        best.get("Taxas (R$)"),
        best.get("NumeroVoo"),
    )


# ──────────────────────────────────────────────────────────────────
# Buscamilhas
# ──────────────────────────────────────────────────────────────────
def test_buscamilhas() -> list[dict]:
    print("=" * 80)
    print(f"BUSCAMILHAS · data ida={DEPART_BR} · pax=1 adulto · classe=econômica")
    print("=" * 80)
    results: list[dict] = []

    try:
        from miles_app.buscamilhas_client import search_flights_buscamilhas
        from miles_app.buscamilhas_offer_parser import extract_rows_from_buscamilhas
    except Exception as ex:
        print(f"  ✗ Não foi possível importar BuscaMilhas: {ex!r}")
        return [{"provider": "buscamilhas", "program": "*", "status": "IMPORT_FAIL",
                 "error": str(ex), "elapsed_s": 0.0}]

    chave = os.getenv("BUSCAMILHAS_CHAVE", "")
    senha = os.getenv("BUSCAMILHAS_SENHA", "")
    if not chave or not senha:
        print("  ⚠ BUSCAMILHAS_CHAVE/SENHA ausentes no .env — todas as consultas falharão")

    for companhia, ori, dst, intl in BUSCAMILHAS_CASES:
        print(f"\n[BM] {companhia:20} {ori}→{dst}  intl={intl}")
        t0 = time.time()
        try:
            raw = search_flights_buscamilhas(
                companhia=companhia, origem=ori, destino=dst,
                data_ida=DEPART_BR, adultos=1,
                somente_milhas=True, internacional=intl,
            )
            elapsed = time.time() - t0
            try:
                rows = extract_rows_from_buscamilhas(raw, companhia, "OW")
            except Exception as ex_parse:
                results.append({
                    "provider": "buscamilhas", "program": companhia,
                    "route": f"{ori}->{dst}", "status": "PARSE_FAIL",
                    "elapsed_s": round(elapsed, 2),
                    "error": _short(str(ex_parse)),
                })
                print(f"   ✗ parse falhou em {elapsed:.2f}s: {ex_parse!r}")
                continue

            count, miles, taxa, voo = _summary_offer(rows)
            status_payload = (raw or {}).get("Status") or {}
            api_alerta = status_payload.get("Alerta") or status_payload.get("Mensagem")
            if count == 0:
                results.append({
                    "provider": "buscamilhas", "program": companhia,
                    "route": f"{ori}->{dst}", "status": "EMPTY",
                    "elapsed_s": round(elapsed, 2),
                    "error": _short(api_alerta) if api_alerta else None,
                })
                print(f"   ⚠ EMPTY  {elapsed:.2f}s  (alerta API: {api_alerta or 'nenhum'})")
            else:
                results.append({
                    "provider": "buscamilhas", "program": companhia,
                    "route": f"{ori}->{dst}", "status": "OK",
                    "elapsed_s": round(elapsed, 2),
                    "count": count, "best_miles": miles, "best_taxa_brl": taxa,
                    "best_voo": voo,
                })
                print(f"   ✓ OK     {elapsed:.2f}s  · {count} ofertas · "
                      f"melhor: {miles} mi + R${taxa:.2f} ({voo})"
                      if isinstance(taxa, (int, float)) else
                      f"   ✓ OK     {elapsed:.2f}s  · {count} ofertas · "
                      f"melhor: {miles} mi ({voo})")
        except Exception as ex:
            elapsed = time.time() - t0
            tb = _short(str(ex), 200)
            results.append({
                "provider": "buscamilhas", "program": companhia,
                "route": f"{ori}->{dst}", "status": "FAIL",
                "elapsed_s": round(elapsed, 2),
                "error": tb,
            })
            print(f"   ✗ FAIL   {elapsed:.2f}s  · {tb}")

    return results


# ──────────────────────────────────────────────────────────────────
# Economilhas
# ──────────────────────────────────────────────────────────────────
def test_economilhas() -> list[dict]:
    print("\n" + "=" * 80)
    print(f"ECONOMILHAS · data ida={DEPART} · pax=1 adulto · cabin=ECONOMY")
    print("=" * 80)
    results: list[dict] = []

    try:
        from economilhas_client import search_flights_economilhas
        from economilhas_offer_parser import extract_rows_from_economilhas
    except Exception as ex:
        print(f"  ✗ Não foi possível importar Economilhas: {ex!r}")
        return [{"provider": "economilhas", "program": "*", "status": "IMPORT_FAIL",
                 "error": str(ex), "elapsed_s": 0.0}]

    api_key = os.getenv("ECONOMILHAS_API_KEY", "")
    if not api_key:
        print("  ⚠ ECONOMILHAS_API_KEY ausente no .env — todas as consultas falharão")

    for airline, ori, dst in ECONOMILHAS_CASES:
        print(f"\n[EC] {airline:20} {ori}→{dst}")
        t0 = time.time()
        try:
            raw = search_flights_economilhas(
                airlines=[airline], origin=ori, destination=dst,
                departure_date=DEPART, adults=1, price_type="MILES",
            )
            elapsed = time.time() - t0
            try:
                rows, partial = extract_rows_from_economilhas(raw, "OW")
            except Exception as ex_parse:
                results.append({
                    "provider": "economilhas", "program": airline,
                    "route": f"{ori}->{dst}", "status": "PARSE_FAIL",
                    "elapsed_s": round(elapsed, 2),
                    "error": _short(str(ex_parse)),
                })
                print(f"   ✗ parse falhou em {elapsed:.2f}s: {ex_parse!r}")
                continue

            count, miles, taxa, voo = _summary_offer(rows)
            partial_msg = None
            if partial:
                p0 = partial[0]
                partial_msg = (
                    f"airline={p0.get('airline')} "
                    f"code={p0.get('providerStatusCode')} "
                    f"msg={p0.get('message')}"
                )
            if count == 0:
                results.append({
                    "provider": "economilhas", "program": airline,
                    "route": f"{ori}->{dst}", "status": "EMPTY",
                    "elapsed_s": round(elapsed, 2),
                    "error": _short(partial_msg) if partial_msg else None,
                })
                print(f"   ⚠ EMPTY  {elapsed:.2f}s  (partial: {partial_msg or 'nenhum'})")
            else:
                results.append({
                    "provider": "economilhas", "program": airline,
                    "route": f"{ori}->{dst}", "status": "OK",
                    "elapsed_s": round(elapsed, 2),
                    "count": count, "best_miles": miles, "best_taxa_brl": taxa,
                    "best_voo": voo,
                })
                taxa_str = f"R${taxa:.2f}" if isinstance(taxa, (int, float)) else "—"
                print(f"   ✓ OK     {elapsed:.2f}s  · {count} ofertas · "
                      f"melhor: {miles} mi + {taxa_str} ({voo})")
        except Exception as ex:
            elapsed = time.time() - t0
            tb = _short(str(ex), 200)
            results.append({
                "provider": "economilhas", "program": airline,
                "route": f"{ori}->{dst}", "status": "FAIL",
                "elapsed_s": round(elapsed, 2),
                "error": tb,
            })
            print(f"   ✗ FAIL   {elapsed:.2f}s  · {tb}")

    return results


# ──────────────────────────────────────────────────────────────────
# Relatório
# ──────────────────────────────────────────────────────────────────
def render_report(all_rows: list[dict]):
    print("\n" + "=" * 80)
    print("RELATÓRIO DE SAÚDE DAS APIs")
    print("=" * 80)
    print(f"Data de pesquisa: {DEPART}  ({DEPART_BR})")
    print(f"Hoje: {TODAY.isoformat()}")
    print()

    # Tabela
    header = f"{'Provider':<13} {'Programa':<22} {'Rota':<12} {'Status':<7} {'Tempo':>7}  {'Ofertas':>8}  {'Melhor':<24}  Erro"
    print(header)
    print("-" * len(header))
    for r in all_rows:
        provider = r.get("provider", "")
        prog = r.get("program", "")
        route = r.get("route", "")
        status = r.get("status", "")
        elapsed = r.get("elapsed_s", 0.0)
        count = r.get("count", "")
        miles = r.get("best_miles")
        taxa = r.get("best_taxa_brl")
        if isinstance(miles, (int, float)) and isinstance(taxa, (int, float)):
            best = f"{int(miles):,} mi + R${taxa:.0f}"
        elif isinstance(miles, (int, float)):
            best = f"{int(miles):,} mi"
        else:
            best = "—"
        err = _short(r.get("error") or "", 60)
        print(f"{provider:<13} {prog:<22} {route:<12} {status:<7} {elapsed:>6.2f}s  "
              f"{str(count):>8}  {best:<24}  {err}")

    # Métricas
    by_provider = {}
    for r in all_rows:
        p = r.get("provider", "?")
        by_provider.setdefault(p, []).append(r)

    print()
    for prov, rows in by_provider.items():
        ok = sum(1 for r in rows if r.get("status") == "OK")
        empty = sum(1 for r in rows if r.get("status") == "EMPTY")
        fail = sum(1 for r in rows if r.get("status") in ("FAIL", "PARSE_FAIL", "IMPORT_FAIL"))
        avg_time_ok = (
            sum(r.get("elapsed_s", 0) for r in rows if r.get("status") == "OK") / max(ok, 1)
        )
        print(f"  {prov.upper():<13}: {ok} OK · {empty} vazio · {fail} falha · "
              f"tempo médio (OK): {avg_time_ok:.2f}s")


def main():
    bm_rows = test_buscamilhas()
    ec_rows = test_economilhas()
    render_report(bm_rows + ec_rows)


if __name__ == "__main__":
    main()
