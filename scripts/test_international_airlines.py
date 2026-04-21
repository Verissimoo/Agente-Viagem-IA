"""
test_international_airlines.py
================================
Testa cada companhia internacional adicionada ao pipeline BuscaMilhas:
  TAP, IBERIA, AMERICAN, INTERLINE

Para cada companhia:
  1. Chama a API BuscaMilhas
  2. Salva o JSON bruto em debug_dumps/buscamilhas_<cia>_<rota>_<ts>.json
  3. Tenta parsear as linhas com extract_rows_from_buscamilhas()
  4. Exibe um resumo no console

Uso:
  python scripts/test_international_airlines.py
  python scripts/test_international_airlines.py --origem GRU --destino LIS --data 30/06/2026
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime

# Garante que o repo raiz está no path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv(override=False)

from miles_app.buscamilhas_client import (
    search_flights_buscamilhas,
    COMPANHIAS_INTERNACIONAIS,
)
from miles_app.buscamilhas_offer_parser import extract_rows_from_buscamilhas

# ──────────────────────────────────────────────
# Configurações padrão de teste
# ──────────────────────────────────────────────
DEFAULT_ORIGEM  = "GRU"          # São Paulo Guarulhos
DEFAULT_DESTINO = "LIS"          # Lisboa (rota relevante para TAP/IBERIA)
DEFAULT_DATA    = datetime.today().strftime("%d/%m/%Y")


def _cor(txt: str, code: int) -> str:
    """Colore texto no terminal (ANSI)."""
    return f"\033[{code}m{txt}\033[0m"

verde   = lambda t: _cor(t, 32)
amarelo = lambda t: _cor(t, 33)
vermelho= lambda t: _cor(t, 31)
azul    = lambda t: _cor(t, 34)
negrito = lambda t: _cor(t, 1)


def salvar_json(data: dict, cia: str, origem: str, destino: str) -> str:
    os.makedirs("debug_dumps", exist_ok=True)
    ts       = int(time.time())
    filename = f"debug_dumps/buscamilhas_{cia.lower()}_ow_{origem}_{destino}_{ts}.json"
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    return filename


def testar_companhia(cia: str, origem: str, destino: str, data_ida: str) -> dict:
    """Testa UMA companhia. Retorna dicionário com resultado do diagnóstico."""
    print(f"\n{'='*60}")
    print(negrito(f"  🔍 Testando: {cia}"))
    print(f"  Rota  : {origem} → {destino}")
    print(f"  Data  : {data_ida}")
    print(f"{'='*60}")

    resultado = {
        "companhia": cia,
        "origem": origem,
        "destino": destino,
        "data": data_ida,
        "sucesso_api": False,
        "arquivo_json": None,
        "status_api": None,
        "erro_api": None,
        "total_trechos": 0,
        "total_voos": 0,
        "rows_milhas": 0,
        "rows_valor": 0,
        "erro_parser": None,
        "amostra_tipos_milhas": [],
    }

    # ── 1. Chamada à API ──────────────────────────────────────
    try:
        print(f"  ⏳ Chamando API...", end="", flush=True)
        t0  = time.time()
        raw = search_flights_buscamilhas(
            companhia=cia,
            origem=origem,
            destino=destino,
            data_ida=data_ida,
            somente_milhas=True,
        )
        elapsed = time.time() - t0
        print(f" {verde('OK')} ({elapsed:.1f}s)")
    except Exception as e:
        print(f" {vermelho('FALHOU')}: {e}")
        resultado["erro_api"] = str(e)
        return resultado

    resultado["sucesso_api"] = True

    # ── 2. Salvar JSON ────────────────────────────────────────
    arquivo = salvar_json(raw, cia, origem, destino)
    resultado["arquivo_json"] = arquivo
    print(f"  💾 JSON salvo em: {azul(arquivo)}")

    # ── 3. Inspecionar Status ─────────────────────────────────
    status = raw.get("Status") or {}
    resultado["status_api"] = status
    if status.get("Erro"):
        alerta = status.get("Alerta", "Sem detalhe")
        print(f"  {amarelo('⚠️  API retornou Erro=True:')} {alerta}")
    else:
        print(f"  ✅ Status.Erro = False (sem erro da API)")

    # ── 4. Contar trechos e voos ──────────────────────────────
    trechos = raw.get("Trechos") or {}
    total_voos = 0
    for tk, td in trechos.items():
        voos = td.get("Voos") or []
        total_voos += len(voos)
        if voos:
            print(f"  📦 Trecho {tk}: {len(voos)} voo(s)")

    resultado["total_trechos"] = len(trechos)
    resultado["total_voos"]    = total_voos

    if total_voos == 0:
        print(f"  {amarelo('⚠️  Nenhum voo retornado nos Trechos.')}")
        return resultado

    # ── 5. Parsear linhas ─────────────────────────────────────
    try:
        rows = extract_rows_from_buscamilhas(raw, cia, "OW")
        rows_m = [r for r in rows if r.get("IsMiles")]
        rows_v = [r for r in rows if not r.get("IsMiles")]
        resultado["rows_milhas"] = len(rows_m)
        resultado["rows_valor"]  = len(rows_v)

        # Amostrar tipos de milhas encontrados
        tipos = list({r.get("TipoMilhas", "—") for r in rows_m if r.get("TipoMilhas")})
        resultado["amostra_tipos_milhas"] = tipos[:10]

        print(f"  📊 Rows parseados   : {len(rows)} total")
        print(f"     Milhas           : {len(rows_m)} row(s)")
        print(f"     Valor (dinheiro) : {len(rows_v)} row(s)")
        if tipos:
            print(f"     TipoMilhas vistos: {', '.join(tipos)}")

        # Exibir amostra do melhor
        if rows_m:
            best = min(rows_m, key=lambda r: r.get("Milhas") or 10**9)
            print(f"\n  🏆 Melhor oferta em milhas:")
            print(f"     {best.get('Trecho')} | {best.get('Origem')} → {best.get('Destino')}")
            print(f"     Data    : {best.get('Data')} {best.get('Saída')} → {best.get('Chegada')}")
            print(f"     Milhas  : {best.get('Milhas'):,}" if isinstance(best.get('Milhas'), int) else f"     Milhas  : {best.get('Milhas')}")
            print(f"     Taxas   : R$ {best.get('Taxas (R$)', 0):.2f}")
            print(f"     Tipo    : {best.get('TipoMilhas', '—')}")
            print(f"     Escalas : {best.get('Escalas', 0)} ({best.get('Local Escala', 'Direto')})")

    except Exception as e:
        resultado["erro_parser"] = str(e)
        print(f"  {vermelho('❌ Erro no parser:')} {e}")

    return resultado


def main():
    parser = argparse.ArgumentParser(description="Teste das companhias internacionais BuscaMilhas")
    parser.add_argument("--origem",   default=DEFAULT_ORIGEM,  help="IATA origem (padrão: GRU)")
    parser.add_argument("--destino",  default=DEFAULT_DESTINO, help="IATA destino (padrão: LIS)")
    parser.add_argument("--data",     default=DEFAULT_DATA,    help="Data de ida DD/MM/AAAA")
    parser.add_argument("--cias",     nargs="+", default=None,
                        help="Companhias a testar (padrão: todas internacionais)")
    args = parser.parse_args()

    cias_a_testar = [c.upper() for c in args.cias] if args.cias else COMPANHIAS_INTERNACIONAIS

    print(negrito(f"\n✈️  Teste de Companhias Internacionais — BuscaMilhas API"))
    print(f"   Rota  : {args.origem} → {args.destino}")
    print(f"   Data  : {args.data}")
    print(f"   CIAs  : {', '.join(cias_a_testar)}")

    resultados = []
    for cia in cias_a_testar:
        res = testar_companhia(cia, args.origem, args.destino, args.data)
        resultados.append(res)
        time.sleep(1.5)   # respeitar rate limit

    # ── Relatório final ──────────────────────────────────────
    print(f"\n{'='*60}")
    print(negrito("  📋 RELATÓRIO FINAL"))
    print(f"{'='*60}")
    print(f"  {'CIA':<12} {'API':>6} {'Voos':>6} {'Milhas':>8} {'Valor':>7} {'Arquivo'}")
    print(f"  {'-'*12} {'-'*6} {'-'*6} {'-'*8} {'-'*7} {'-'*30}")

    for r in resultados:
        api_s  = verde("OK")   if r["sucesso_api"]   else vermelho("FALHOU")
        arq    = os.path.basename(r["arquivo_json"]) if r["arquivo_json"] else "—"
        print(f"  {r['companhia']:<12} {api_s:>14} {r['total_voos']:>6} "
              f"{r['rows_milhas']:>8} {r['rows_valor']:>7}  {arq}")

    print()

    # Salvar relatório consolidado
    ts_str   = datetime.now().strftime("%Y%m%d_%H%M%S")
    rel_path = f"debug_dumps/relatorio_internacionais_{ts_str}.json"
    os.makedirs("debug_dumps", exist_ok=True)
    with open(rel_path, "w", encoding="utf-8") as f:
        json.dump(resultados, f, indent=2, ensure_ascii=False, default=str)
    print(f"  📄 Relatório consolidado: {azul(rel_path)}")


if __name__ == "__main__":
    main()
