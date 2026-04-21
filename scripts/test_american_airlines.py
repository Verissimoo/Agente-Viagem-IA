"""
test_american_airlines.py
========================
Testa a nomenclatura 'AMERICAN AIRLINES' na API BuscaMilhas.
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import date, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from dotenv import load_dotenv
load_dotenv()

from miles_app.buscamilhas_client import build_payload, _env, BUSCAMILHAS_ENDPOINT, BuscaMilhasClient
from miles_app.buscamilhas_offer_parser import extract_rows_from_buscamilhas

def build_client():
    chave = _env("BUSCAMILHAS_CHAVE", "") or ""
    senha = _env("BUSCAMILHAS_SENHA", "") or ""
    return BuscaMilhasClient(chave=chave, senha=senha, endpoint=BUSCAMILHAS_ENDPOINT,
                             connect_timeout=15, read_timeout=90, max_attempts=1)

hoje = date.today()
DATA_TESTE = (hoje + timedelta(days=60)).strftime("%d/%m/%Y")

print("Testando nomenclatura para AMERICAN AIRLINES...")

try:
    client = build_client()
except Exception as e:
    print(f"Erro ao criar cliente: {e}")
    sys.exit(1)

payload = build_payload(
    companhia="AMERICAN AIRLINES",
    origem="GRU",
    destino="MIA",  
    data_ida=DATA_TESTE,
    internacional=True,
    chave=client.chave,
    senha=client.senha,
)

try:
    raw = client.search(payload)
    status = raw.get("Status") or {}
    alertas = status.get("Alerta") or []
    
    if status.get("Erro"):
        print(f"  [ERRO] A API retornou Erro=True. Alertas: {alertas}")
    else:
        trechos = raw.get("Trechos") or {}
        total_voos = sum(len((v.get("Voos") or [])) for v in trechos.values())
        print(f"  [SUCESSO] API respondeu OK! Total de voos: {total_voos}")
        
        if total_voos > 0:
            rows = extract_rows_from_buscamilhas(raw, companhia="AMERICAN AIRLINES", trip_type="OW")
            print(f"  Extraidas {len(rows)} rows!")
            if rows:
                r0 = rows[0]
                print(f"  Melhor: {r0.get('Milhas')} milhas + {r0.get('Taxas (R$)')} taxas | Voo: {r0.get('NumeroVoo')}")
                
            path = os.path.join(ROOT, "debug_dumps", f"test_aa_sucesso.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(raw, f, indent=2, ensure_ascii=False)
            print(f"  Salvo em {path}")

except Exception as e:
    print(f"  [EXCECAO HTTP] Falha na requisicao: {e}")

print("\nTeste finalizado.")
