import sys
import json
import time
from datetime import datetime, timedelta
from pathlib import Path

# Adiciona o diretório raiz ao path para importar mcp_client
_ROOT = Path(__file__).parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from mcp_client import call_mcp_search_all_airlines

def run_diagnostico_mcp():
    print("=== Diagnóstico Multi-Companhia MCP Award Travel Finder (PRO) ===")
    
    # Datas de teste (daqui a 4 e 5 meses)
    data_1 = (datetime.now() + timedelta(days=120)).strftime("%Y-%m-%d")
    data_2 = (datetime.now() + timedelta(days=150)).strftime("%Y-%m-%d")
    
    rotas = [
        {"origin": "GRU", "destination": "JFK", "date": data_1},
        {"origin": "GRU", "destination": "LHR", "date": data_2},
        {"origin": "GRU", "destination": "CDG", "date": data_1}
    ]
    
    all_results = {}
    
    for rota in rotas:
        print(f"\nBuscando rota: {rota['origin']} -> {rota['destination']} em {rota['date']}...")
        try:
            # Chama a tool search_all_airlines que varre várias companhias
            res = call_mcp_search_all_airlines(rota['origin'], rota['destination'], rota['date'])
            
            # O retorno do MCP geralmente vem como {"airlines": {...}} ou direto se for o payload parseado
            airlines_found = res.get("airlines", {}) if isinstance(res, dict) else {}
            
            if not airlines_found:
                # Tenta extrair de "result" se vier do JSON-RPC puro
                result = res.get("result", {}) if isinstance(res, dict) else {}
                airlines_found = result.get("airlines", {}) if isinstance(result, dict) else {}

            for cia_name, cia_data in airlines_found.items():
                if cia_name not in all_results:
                    all_results[cia_name] = []
                
                status = "SUCESSO"
                if "http_error" in cia_data:
                    status = f"ERRO {cia_data['http_error']}"
                
                all_results[cia_name].append({
                    "route": f"{rota['origin']}->{rota['destination']}",
                    "date": rota['date'],
                    "status": status,
                    "type": cia_data.get("response_type", "N/A"),
                    "has_flights": len(cia_data.get("flights", [])) > 0 if "flights" in cia_data else False,
                    "has_calendar": "availability" in cia_data
                })
                
            time.sleep(1) # Pequena pausa entre rotas
        except Exception as e:
            print(f"Erro na rota {rota['origin']}->{rota['destination']}: {e}")

    # Salva o dump consolidado
    dump_path = _ROOT / "debug_dumps" / "diagnostico_mcp_multi_consolidado.json"
    with open(dump_path, "w", encoding="utf-8") as f:
        json.dump(all_results, f, indent=2, ensure_ascii=False)
    
    # Gera o Relatório
    print("\n" + "="*80)
    print(f"{'COMPANHIA':<25} | {'STATUS':<15} | {'TIPO':<10} | {'OBSERVAÇÕES'}")
    print("-" * 80)
    
    for cia, tests in all_results.items():
        # Pega o melhor resultado dos testes
        best_test = next((t for t in tests if "ERRO" not in t["status"]), tests[0])
        
        status = best_test["status"]
        tipo = best_test["type"]
        obs = ""
        
        if tipo == "flights":
            obs = "Retorna itinerários" if best_test["has_flights"] else "Sem voos na rota"
        elif tipo == "calendar":
            obs = "Apenas disponibilidade diária"
        
        if "ERRO" in status:
            obs = "Companhia não suportada ou erro na API"

        print(f"{cia:<25} | {status:<15} | {tipo:<10} | {obs}")
    
    print("="*80)
    print(f"JSON consolidado salvo em: {dump_path}")

if __name__ == "__main__":
    run_diagnostico_mcp()
