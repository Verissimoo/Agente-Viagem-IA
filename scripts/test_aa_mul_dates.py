"""
test_aa_mul_dates.py
========================
Procura em multiplas datas para encontrar voos da AMERICAN AIRLINES.
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

from miles_app.buscamilhas_client import build_payload, _env, BuscaMilhasClient, BUSCAMILHAS_ENDPOINT

def build_client():
    chave = _env("BUSCAMILHAS_CHAVE", "") or ""
    senha = _env("BUSCAMILHAS_SENHA", "") or ""
    return BuscaMilhasClient(chave=chave, senha=senha, endpoint=BUSCAMILHAS_ENDPOINT,
                             connect_timeout=15, read_timeout=90, max_attempts=1)

hoje = date.today()

client = build_client()

print("Buscando voos AMERICAN AIRLINES em varias datas...")

for offset in [30, 60, 90, 120, 150, 180]:
    d = (hoje + timedelta(days=offset)).strftime("%d/%m/%Y")
    payload = build_payload(
        companhia="AMERICAN AIRLINES",
        origem="GRU",
        destino="MIA",  
        data_ida=d,
        internacional=True,
        chave=client.chave,
        senha=client.senha,
    )
    try:
        raw = client.search(payload)
        trechos = raw.get("Trechos") or {}
        total_voos = sum(len((v.get("Voos") or [])) for v in trechos.values())
        print(f"Data {d}: {total_voos} voos")
        if total_voos > 0:
            path = os.path.join(ROOT, "debug_dumps", f"test_aa_sucesso_{offset}.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(raw, f, indent=2, ensure_ascii=False)
            print(f" -> Salvo em {path}")
            break
    except Exception as e:
        print(f"Data {d}: EXCECAO: {e}")
    time.sleep(1)
