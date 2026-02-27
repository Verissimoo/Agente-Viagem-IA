import argparse
from datetime import date, timedelta
from typing import List

from pcd.core.schema import SearchRequest, TripType, CabinClass

from pcd.adapters.kayak_adapter import KayakAdapter
from pcd.adapters.moblix_adapter import MoblixLatamAdapter

def run_demo(use_fixtures: bool):
    print(f"Iniciando demo_adapters.py (use_fixtures={use_fixtures})")
    
    # Montar request dummy
    req = SearchRequest(
        origin=["GRU"],
        destination=["MIA"],
        date_start=date.today() + timedelta(days=30),
        date_end=date.today() + timedelta(days=30),
        return_start=date.today() + timedelta(days=40),
        return_end=date.today() + timedelta(days=40),
        trip_type=TripType.ROUNDTRIP,
        adults=1,
        cabin=CabinClass.ECONOMY,
        baggage_checked=False
    )
    
    adapters = [KayakAdapter(), MoblixLatamAdapter()]
    
    for adapter in adapters:
        name = adapter.__class__.__name__
        try:
            offers = adapter.search(req, use_fixtures=use_fixtures)
            print(f"[{name}] Encontradas {len(offers)} ofertas unificadas.")
            if offers:
                first = offers[0]
                print(f"  -> Exemplo (source={first.source.value}): Trip={first.trip_type.value}, Airline={first.airline}, BRL={first.price_brl}, Miles={first.miles}")
        except Exception as e:
            print(f"[{name}] ERRO: {e}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--use-fixtures", action="store_true", help="Usa respostas JSON locais para não chamar APIs reias")
    args = parser.parse_args()
    
    run_demo(args.use_fixtures)
