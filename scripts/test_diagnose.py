"""
test_diagnose.py  — diagnóstico detalhado por companhia
Roda: python -X utf8 scripts/test_diagnose.py
"""
from __future__ import annotations
import json, os, sys, time
from datetime import date, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from dotenv import load_dotenv
load_dotenv()

from miles_app.buscamilhas_client import BuscaMilhasClient, build_payload, _env, BUSCAMILHAS_ENDPOINT

def build_client():
    chave = _env("BUSCAMILHAS_CHAVE", "") or ""
    senha = _env("BUSCAMILHAS_SENHA", "") or ""
    endpoint = _env("BUSCAMILHAS_ENDPOINT", BUSCAMILHAS_ENDPOINT) or BUSCAMILHAS_ENDPOINT
    return BuscaMilhasClient(chave=chave, senha=senha, endpoint=endpoint,
                             connect_timeout=15, read_timeout=90, max_attempts=1)

hoje = date.today()

print("="*70)
print("DIAGNOSTICO DETALHADO — AMERICAN / INTERLINE / TAP BAGAGEM")
print("="*70)

client = build_client()

# ────────────────────────────────────────────────────────────── 
# 1) AMERICAN — investigar erro 500 "searchBody is not defined"
# ────────────────────────────────────────────────────────────── 
print("\n[1] AMERICAN — payload enviado vs retorno 500")
datas_american = [
    (hoje + timedelta(days=30)).strftime("%d/%m/%Y"),
    (hoje + timedelta(days=60)).strftime("%d/%m/%Y"),
    (hoje + timedelta(days=90)).strftime("%d/%m/%Y"),
]
for d in datas_american:
    payload = build_payload("AMERICAN", "GRU", "MIA", d, internacional=True,
                            chave=client.chave, senha=client.senha)
    print(f"\n  Data: {d} | Payload:")
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    try:
        raw = client.search(payload)
        status = raw.get("Status") or {}
        print(f"  -> Erro={status.get('Erro')} | Sucesso={status.get('Sucesso')} | Alertas={status.get('Alerta')}")
        trechos = raw.get("Trechos") or {}
        total = sum(len((v.get("Voos") or [])) for v in trechos.values())
        print(f"  -> Voos: {total}")
    except Exception as e:
        print(f"  -> EXCECAO: {e}")
    time.sleep(1)

# ────────────────────────────────────────────────────────────── 
# 2) INTERLINE — testar múltiplas rotas e datas
# ────────────────────────────────────────────────────────────── 
print("\n\n[2] INTERLINE — testando varias rotas e datas")
interline_tests = [
    ("GRU", "JFK", (hoje + timedelta(days=30)).strftime("%d/%m/%Y")),
    ("GRU", "JFK", (hoje + timedelta(days=60)).strftime("%d/%m/%Y")),
    ("GRU", "JFK", (hoje + timedelta(days=90)).strftime("%d/%m/%Y")),
    ("GRU", "MIA", (hoje + timedelta(days=30)).strftime("%d/%m/%Y")),
    ("GRU", "MIA", (hoje + timedelta(days=60)).strftime("%d/%m/%Y")),
    ("GRU", "MCO", (hoje + timedelta(days=60)).strftime("%d/%m/%Y")),  # Orlando
    ("GRU", "LAX", (hoje + timedelta(days=60)).strftime("%d/%m/%Y")),  # Los Angeles
    ("GRU", "ORD", (hoje + timedelta(days=60)).strftime("%d/%m/%Y")),  # Chicago
]
for (orig, dest, d) in interline_tests:
    payload = build_payload("INTERLINE", orig, dest, d, internacional=True,
                            chave=client.chave, senha=client.senha)
    try:
        raw = client.search(payload)
        status = raw.get("Status") or {}
        is_err = status.get("Erro", False)
        alertas = status.get("Alerta") or []
        trechos = raw.get("Trechos") or {}
        total = sum(len((v.get("Voos") or [])) for v in trechos.values())
        if is_err:
            print(f"  {orig}->{dest} {d}: ERRO | {alertas}")
        else:
            print(f"  {orig}->{dest} {d}: OK | {total} voos")
    except Exception as e:
        print(f"  {orig}->{dest} {d}: EXCECAO | {e}")
    time.sleep(0.5)

# ────────────────────────────────────────────────────────────── 
# 3) TAP — verificar bagagem nas rows do parser
# ────────────────────────────────────────────────────────────── 
print("\n\n[3] TAP — verificar campo Bagagem apos correcao do parser")
from miles_app.buscamilhas_offer_parser import extract_rows_from_buscamilhas

# Carrega o dump existente do TAP
dump_path = os.path.join(ROOT, "debug_dumps", "buscamilhas_tap_ow_GRU_LIS_1776180734.json")
with open(dump_path, encoding="utf-8") as f:
    tap_raw = json.load(f)
rows = extract_rows_from_buscamilhas(tap_raw, companhia="TAP", trip_type="OW")
print(f"  Total de rows: {len(rows)}")
for i, r in enumerate(rows[:5]):
    print(f"  Row {i+1}: Milhas={r.get('Milhas')} | Taxas={r.get('Taxas (R$)')} | Bagagem={r.get('Bagagem')} | Voo={r.get('NumeroVoo')}")

# ────────────────────────────────────────────────────────────── 
# 4) INTERLINE — verificar bagagem no dump GRU-JFK existente
# ────────────────────────────────────────────────────────────── 
print("\n\n[4] INTERLINE — verificar campo Bagagem (dump GRU-JFK existente)")
dump_path2 = os.path.join(ROOT, "debug_dumps", "buscamilhas_interline_ow_GRU_JFK_1776180906.json")
with open(dump_path2, encoding="utf-8") as f:
    il_raw = json.load(f)
rows2 = extract_rows_from_buscamilhas(il_raw, companhia="INTERLINE", trip_type="OW")
print(f"  Total de rows: {len(rows2)}")
for i, r in enumerate(rows2[:5]):
    print(f"  Row {i+1}: Milhas={r.get('Milhas')} | Taxas={r.get('Taxas (R$)')} | Bagagem={r.get('Bagagem')} | Voo={r.get('NumeroVoo')}")

print("\n[FIM DO DIAGNOSTICO]")
