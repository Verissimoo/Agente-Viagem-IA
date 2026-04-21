"""
diagnostico_completo.py
Testa cada companhia ativa com uma rota que quase certamente tem voos.
Salva o resultado raw em debug_dumps e exibe um sumario.
"""
from __future__ import annotations
import json, os, sys, time
from datetime import date, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from dotenv import load_dotenv; load_dotenv()

from miles_app.buscamilhas_client import build_payload, _env, BuscaMilhasClient, BUSCAMILHAS_ENDPOINT
from miles_app.buscamilhas_offer_parser import extract_rows_from_buscamilhas

def build_client():
    return BuscaMilhasClient(
        chave=_env("BUSCAMILHAS_CHAVE",""), senha=_env("BUSCAMILHAS_SENHA",""),
        endpoint=BUSCAMILHAS_ENDPOINT, connect_timeout=15, read_timeout=90, max_attempts=1
    )

hoje = date.today()
D45 = (hoje + timedelta(days=45)).strftime("%d/%m/%Y")
D50 = (hoje + timedelta(days=50)).strftime("%d/%m/%Y")
D60 = (hoje + timedelta(days=60)).strftime("%d/%m/%Y")

# Rotas "certas" por companhia
TESTES = [
    # (companhia, origem, destino, data, internacional, motivo)
    ("LATAM",             "GRU", "GIG", D60, False, "LATAM doméstico SP→RJ — sempre tem"),
    ("GOL",               "GRU", "SSA", D60, False, "GOL doméstico SP→Salvador — sempre tem"),
    ("AZUL",              "VCP", "GIG", D60, False, "AZUL doméstico VCP→RJ — hub Campinas"),
    ("TAP",               "GRU", "LIS", D60, True,  "TAP GRU→Lisboa — rota principal TAP"),
    ("AMERICAN AIRLINES", "GRU", "MIA", D60, True,  "AA GRU→Miami — rota principal AA"),
    ("AMERICAN AIRLINES", "GRU", "JFK", D45, True,  "AA GRU→JFK — hub AA"),
    ("INTERLINE",         "GRU", "JFK", D45, True,  "INTERLINE GRU→JFK até 60d — funciona"),
    ("INTERLINE",         "GRU", "MIA", D45, True,  "INTERLINE GRU→MIA"),
]

client = build_client()
resultados = []

print("="*65)
print("DIAGNOSTICO — Chamadas por companhia com rotas 'certas'")
print("="*65)

for (companhia, orig, dest, data, intl, motivo) in TESTES:
    label = f"[{companhia}] {orig}->{dest} {data}"
    print(f"\n{label}")
    print(f"  Motivo: {motivo}")

    payload = build_payload(companhia=companhia, origem=orig, destino=dest,
                            data_ida=data, internacional=intl,
                            chave=client.chave, senha=client.senha)
    t0 = time.time()
    try:
        raw = client.search(payload)
        elapsed = round(time.time()-t0, 1)
        status  = raw.get("Status") or {}
        alertas = status.get("Alerta") or []
        is_err  = status.get("Erro", False)

        # Conta voos brutos
        trechos = raw.get("Trechos") or {}
        total_voos_brutos = sum(len((v.get("Voos") or [])) for v in trechos.values())

        # Tenta parsear rows
        rows = []
        if not is_err:
            rows = extract_rows_from_buscamilhas(raw, companhia=companhia, trip_type="OW")

        # Salva o dump
        slug = companhia.lower().replace(" ","_")
        fname = f"diag_{slug}_{orig}_{dest}_{int(time.time())}.json"
        fpath = os.path.join(ROOT, "debug_dumps", fname)
        with open(fpath, "w", encoding="utf-8") as f:
            json.dump(raw, f, indent=2, ensure_ascii=False)

        if is_err:
            print(f"  [ERRO API] Alertas: {alertas} | {elapsed}s")
            st = "ERRO_API"
        elif total_voos_brutos == 0:
            print(f"  [SEM VOOS] API OK mas 0 voos retornados | {elapsed}s")
            st = "SEM_VOOS"
        else:
            melhor_milhas = min((r.get("Milhas",0) or 0 for r in rows), default=0)
            melhor_taxas  = min((r.get("Taxas (R$)",0) or 0 for r in rows), default=0)
            print(f"  [OK] {total_voos_brutos} voos brutos | {len(rows)} rows parseadas | "
                  f"Melhor: {melhor_milhas:,} mi + R${melhor_taxas:.2f} | {elapsed}s")
            print(f"  Dump: {fname}")
            st = "OK"

        resultados.append({"companhia": companhia, "rota": f"{orig}-{dest}",
                            "status": st, "voos_brutos": total_voos_brutos,
                            "rows": len(rows), "dump": fname, "elapsed": elapsed})

    except Exception as e:
        elapsed = round(time.time()-t0, 1)
        print(f"  [EXCECAO] {e} | {elapsed}s")
        resultados.append({"companhia": companhia, "rota": f"{orig}-{dest}",
                            "status": "EXCECAO", "erro": str(e)[:120]})
    time.sleep(0.5)

print("\n\n" + "="*65)
print("SUMARIO FINAL")
print("="*65)
print(f"{'Companhia':<20} {'Rota':<10} {'Status':<12} {'Voos':<6} {'Rows':<6} {'Tempo'}")
print("-"*65)
for r in resultados:
    print(f"{r['companhia']:<20} {r['rota']:<10} {r['status']:<12} "
          f"{r.get('voos_brutos','?'):<6} {r.get('rows','?'):<6} {r.get('elapsed','?')}s")
