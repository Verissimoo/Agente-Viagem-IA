from __future__ import annotations

import os
import sys
import json

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from miles_app.buscamilhas_client import search_flights_buscamilhas

def main():
    print("Calling BuscaMilhas (LATAM)...")
    r = search_flights_buscamilhas(
        companhia="LATAM",
        origem="BSB",
        destino="GRU",
        data_ida="30/05/2026",
        data_volta=None,
        somente_milhas=True
    )
    
    os.makedirs("debug_dumps", exist_ok=True)
    with open("debug_dumps/test_buscamilhas.json", "w", encoding="utf-8") as f:
        json.dump(r, f, indent=2, ensure_ascii=False)
        
    status = r.get("Status", {})
    print(f"Status: {status}")
    trechos = r.get("Trechos", {})
    for k, v in trechos.items():
        voos = v.get("Voos", [])
        print(f"Trecho: {k}, Voos: {len(voos)}")

if __name__ == "__main__":
    main()
