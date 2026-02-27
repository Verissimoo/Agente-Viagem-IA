import argparse
import json
from datetime import date, timedelta

from pcd.core.schema import SearchRequest, TripType, CabinClass
from pcd.adapters.kayak_adapter import KayakAdapter
from pcd.adapters.moblix_adapter import MoblixLatamAdapter
from pcd.core.ranking import rank_offers
from pcd.core.formatter import build_ui_report

import uuid
from pcd.core.tracer import PipelineTracer

def run_demo_report(use_fixtures: bool, trace_out: str = None):
    request_id = str(uuid.uuid4())[:8]
    tracer = PipelineTracer(request_id)

    req = SearchRequest(
        origin=["BSB"],
        destination=["GRU"],
        date_start=date.today() + timedelta(days=30),
        date_end=date.today() + timedelta(days=30),
        trip_type=TripType.ONEWAY,
        adults=1,
        cabin=CabinClass.ECONOMY,
        baggage_checked=False
    )
    
    all_offers = []
    
    with tracer.track_stage("adapters_fetch", "Fetching data from all adapters"):
        adapters = {"kayak": KayakAdapter(), "moblix": MoblixLatamAdapter()}
        for name, adapter in adapters.items():
            with tracer.track_stage(name, f"Calling {name} source") as info:
                try:
                    offers = adapter.search(req, use_fixtures=use_fixtures)
                    all_offers.extend(offers)
                    info["offers_count"] = len(offers)
                except Exception as e:
                    print(f"[{name}] Falhou: {e}")
                    # Re-raise within track_stage context if we want to trace the error
                    # or handle it and log manually. Tracer handles 'raise'.
                    # For demo purposes, we log it and continue.
                    pass
            
    if not all_offers:
        print("Nenhuma oferta encontrada pelas fontes.")
        tracer.save(trace_out)
        return
        
    with tracer.track_stage("ranking", "Ranking and scoring offers") as info:
        top_offers, best_offer, justifications = rank_offers(all_offers, top_n=5)
        info["offers_count"] = len(top_offers)
    
    with tracer.track_stage("formatting", "Building UI report"):
        report_json, report_text = build_ui_report(top_offers, best_offer, justifications)
    
    # Final Output
    print("\n" + "="*50)
    print(" REPORT TEXT ".center(50, "="))
    print("="*50)
    print(report_text)
    print("\n")

    if trace_out:
        tracer.save(trace_out)
        print(f"Trace salvo em {trace_out}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--use-fixtures", action="store_true", help="Usa respostas JSON locais mockadas")
    parser.add_argument("--trace-out", type=str, help="Caminho para exportar o log de execução (json ou jsonl)")
    args = parser.parse_args()
    
    run_demo_report(args.use_fixtures, args.trace_out)
