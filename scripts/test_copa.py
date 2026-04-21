"""
test_copa.py
========================
Testa possíveis nomenclaturas para a companhia Copa Airlines na API BuscaMilhas.
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

def build_client():
    chave = _env("BUSCAMILHAS_CHAVE", "") or ""
    senha = _env("BUSCAMILHAS_SENHA", "") or ""
    endpoint = _env("BUSCAMILHAS_ENDPOINT", BUSCAMILHAS_ENDPOINT) or BUSCAMILHAS_ENDPOINT
    return BuscaMilhasClient(chave=chave, senha=senha, endpoint=endpoint,
                             connect_timeout=15, read_timeout=90, max_attempts=1)

hoje = date.today()
DATA_TESTE = (hoje + timedelta(days=60)).strftime("%d/%m/%Y")

NOMENCLATURAS = [
    "COPA",
    "COPAAIRLINES",
    "CM",
    "CMP",
    "CONNECTMILES"
]

print("Testando nomenclaturas para COPA AIRLINES...")

try:
    client = build_client()
except Exception as e:
    print(f"Erro ao criar cliente: {e}")
    sys.exit(1)

for nome in NOMENCLATURAS:
    print(f"\n--- Testando nome: {nome} ---")
    payload = build_payload(
        companhia=nome,
        origem="GRU",
        destino="MIA",  # Copa voa GRU-PTY-MIA
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
                print(f"  !!! NOMENCLATURA ENCONTRADA: {nome} !!!")
                # Salva o res para vermos depois
                path = os.path.join(ROOT, "debug_dumps", f"test_copa_sucesso_{nome}.json")
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(raw, f, indent=2, ensure_ascii=False)
                print(f"  Salvo em {path}")

    except Exception as e:
        print(f"  [EXCECAO HTTP] Falha na requisicao: {e}")
        
    time.sleep(1)

print("\nTeste finalizado.")
