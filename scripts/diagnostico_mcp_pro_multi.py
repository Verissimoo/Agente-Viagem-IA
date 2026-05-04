import sys
import json
import time
import os
from datetime import datetime, timedelta
from pathlib import Path

# Adiciona o diretório raiz ao path
_ROOT = Path(__file__).parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from mcp_client import call_rest_availability

def run_diagnostico_completo():
    print("="*80)
    print("  DIAGNÓSTICO MULTI-COMPANHIA AWARD TRAVEL FINDER (REST PRO)")
    print("="*80)
    
    # Configurações de teste
    data_teste = (datetime.now() + timedelta(days=135)).strftime("%Y-%m-%d")
    rotas = [
        {"origin": "GRU", "destination": "JFK", "label": "América do Norte"},
        {"origin": "GRU", "destination": "LHR", "label": "Europa"}
    ]
    
    # Slugs oficiais das companhias para a REST API
    airlines = [
        "qatar_airways", 
        "british_airways", 
        "american_airlines", 
        "united_airlines",
        "air_canada",
        "delta_airlines"
    ]
    
    consolidado = {}
    
    for rota in rotas:
        print(f"\n[ROTA] {rota['label']}: {rota['origin']} -> {rota['destination']} ({data_teste})")
        
        for cia in airlines:
            print(f"  -> Testando {cia:.<20} ", end="", flush=True)
            try:
                # Chama a REST API (validada)
                res = call_rest_availability(cia, rota['origin'], rota['destination'], data_teste)
                
                status = "SUCESSO"
                data_block = res.get("data", {})
                resp_type = data_block.get("response_type", "unknown")
                
                # Verifica se há dados reais
                has_data = False
                if resp_type == "flights":
                    has_data = len(data_block.get("flights", [])) > 0
                elif resp_type == "calendar":
                    has_data = data_block.get("availability", {}).get("data_available", False)
                
                if cia not in consolidado:
                    consolidado[cia] = []
                
                consolidado[cia].append({
                    "rota": f"{rota['origin']}->{rota['destination']}",
                    "status": status,
                    "tipo": resp_type,
                    "tem_disponibilidade": has_data,
                    "usage": res.get("usage", {})
                })
                print(f"[{status}] - {resp_type}")
                
            except Exception as e:
                print(f"[FALHA] - {str(e)[:50]}")
                if cia not in consolidado:
                    consolidado[cia] = []
                consolidado[cia].append({
                    "rota": f"{rota['origin']}->{rota['destination']}",
                    "status": "FALHA",
                    "erro": str(e)
                })
            
            time.sleep(1.5) # Respeitar rate limit do plano PRO

    # Salva o resultado
    dump_dir = _ROOT / "debug_dumps"
    dump_dir.mkdir(exist_ok=True)
    filepath = dump_dir / "mcp_multi_cia_pro_report.json"
    
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(consolidado, f, indent=2, ensure_ascii=False)
        
    # Relatório Final
    print("\n" + "="*80)
    print(f"{'COMPANHIA':<20} | {'STATUS PRO':<12} | {'TIPO DADO':<10} | {'DISPONIBILIDADE'}")
    print("-" * 80)
    
    for cia, tests in consolidado.items():
        # Considera o sucesso se qualquer rota funcionou
        success_test = next((t for t in tests if t["status"] == "SUCESSO"), None)
        
        if success_test:
            status = "ATIVO"
            tipo = success_test["tipo"]
            disp = "SIM" if any(t.get("tem_disponibilidade") for t in tests) else "NÃO (na data)"
        else:
            status = "ERRO/INDISP."
            tipo = "N/A"
            disp = "N/A"
            
        print(f"{cia:<20} | {status:<12} | {tipo:<10} | {disp}")
    
    print("="*80)
    print(f"Relatório detalhado salvo em: {filepath}")

if __name__ == "__main__":
    run_diagnostico_completo()
