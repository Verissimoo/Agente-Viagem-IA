"""
scripts/teste_fogo_itinerario_mcp.py
======================================
Valida se o MCP Award Travel Finder retorna dados completos de itinerário
(número de voo, horários de saída e chegada válidos).
"""

import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

_ROOT = Path(__file__).parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from mcp_client import call_rest_availability

def check_itinerary(origin: str, destination: str, days_ahead: int, target_airline: str, slug: str):
    date_str = (datetime.now() + timedelta(days=days_ahead)).strftime("%Y-%m-%d")
    print(f"\n--- Buscando {origin} -> {destination} em {date_str} (foco: {target_airline}) ---")
    
    try:
        response = call_rest_availability(slug, origin, destination, date_str)
        
        # O JSON real fica na raiz do response (que já foi decodificado)
        if isinstance(response, dict):
            if "data" in response:
                results = response["data"]
            else:
                results = [response]
        elif isinstance(response, list):
            results = response
        else:
            results = []

        if not results:
            print(f"[{target_airline}] Sem resultados ou formato desconhecido.")
            return

        for offer in results:
            # REST API devolve {"response_type": "flights", "data": [...]} ou já a lista
            if isinstance(offer, dict):
                if "response_type" in offer:
                    process_airline_response(target_airline, offer)
                elif "airline" in offer:
                    process_single_flight(offer, default_airline=target_airline)
    except Exception as e:
        print(f"Erro na busca: {e}")

def process_airline_response(airline_key: str, data_block: dict):
    resp_type = data_block.get("response_type", "unknown")
    
    if resp_type == "calendar":
        print(f"[{airline_key.upper()}] - Tipo: calendar - Voo: N/A - Saída: N/A - Chegada: N/A")
        return
        
    flights = data_block.get("data", [])
    if not flights:
        print(f"[{airline_key.upper()}] - Tipo: {resp_type} - Voo: N/A - Saída: N/A - Chegada: N/A")
        return
        
    for flight in flights:
        process_single_flight(flight, default_airline=airline_key)

def process_single_flight(flight: dict, default_airline="UNKNOWN"):
    airline = flight.get("airline", default_airline).upper()
    resp_type = flight.get("response_type", "flights") # Assumindo flights se tiver os dados
    
    segments = flight.get("segments", [])
    if not segments:
        print(f"[{airline}] - Tipo: {resp_type} - Voo: N/A - Saída: N/A - Chegada: N/A")
        return
        
    for seg in segments:
        fnum = seg.get("flight_number", "N/A")
        dep  = seg.get("departure_time", "N/A")
        arr  = seg.get("arrival_time", "N/A")
        
        # Validar se os horários não são vazios ou zerados
        if not fnum: fnum = "N/A"
        if not dep or dep == "00:00:00": dep = "N/A"
        if not arr or arr == "00:00:00": arr = "N/A"
        
        print(f"[{airline}] - Tipo: {resp_type} - Voo: {fnum} - Saída: {dep} - Chegada: {arr}")

def run():
    print("Iniciando Teste de Fogo de Itinerários MCP...\n")
    # 4 meses a frente (aprox 120 dias)
    check_itinerary("DOH", "JFK", 120, "QATAR", "qatar_airways")
    time.sleep(2)
    check_itinerary("LHR", "JFK", 120, "BRITISH AIRWAYS", "british_airways")

if __name__ == "__main__":
    run()
