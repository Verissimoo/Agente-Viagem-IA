import argparse
import sys
import uuid
from datetime import date, datetime, timedelta
from typing import Optional

from pcd.core.schema import SearchRequest, TripType, CabinClass
from pcd.adapters.kayak_adapter import KayakAdapter
from pcd.adapters.moblix_adapter import MoblixLatamAdapter
from pcd.core.ranking import rank_offers
from pcd.core.formatter import build_ui_report
from pcd.core.tracer import PipelineTracer
from pcd.core.errors import OfflineModeError

from pcd.core.layover_classifier import classify_many

def simple_prompt_parser(prompt: str) -> SearchRequest:
    """Mock de um parser de linguagem natural para SearchRequest"""
    # Regex simples para tentar pegar origens e destinos (ex: BSB para GRU)
    import re
    iata_match = re.findall(r'\b[A-Z]{3}\b', prompt.upper())
    
    # Defaults base
    origin = ["GRU"]
    destination = ["MIA"]
    
    if len(iata_match) >= 2:
        origin = [iata_match[0]]
        destination = [iata_match[1]]
    
    target_date = date.today() + timedelta(days=30)
    return SearchRequest(
        origin=origin,
        destination=destination,
        date_start=target_date,
        date_end=target_date,
        trip_type=TripType.ONEWAY,
        adults=1,
        cabin=CabinClass.ECONOMY,
        baggage_checked=False
    )

from pcd.core.schema import SearchRequest, TripType, CabinClass, PipelineResult

def run_pipeline(
    prompt: str, 
    top_n: int = 5, 
    use_fixtures: bool = False, 
    trace_out: str = None,
    date_start: date = None,
    date_return: Optional[date] = None,
    direct_only: bool = False,
    origin: Optional[str] = None,
    destination: Optional[str] = None,
    debug_dump_kayak: bool = False,
    debug_dump_moblix: bool = False
) -> PipelineResult:
    request_id = str(uuid.uuid4())[:8]
    tracer = PipelineTracer(request_id)
    
    result = PipelineResult(
        request_id=request_id,
        trace_path=trace_out
    )

    try:
        # 1. Stage: parse
        with tracer.track_stage("parse"):
            request = simple_prompt_parser(prompt)
            # Sobrescrever campos se vierem da UI
            if origin:
                request.origin = [origin.upper()]
            if destination:
                request.destination = [destination.upper()]
            
            if date_start:
                request.date_start = date_start
                request.date_end = date_start
            if date_return:
                request.return_start = date_return
                request.return_end = date_return
                request.trip_type = TripType.ROUNDTRIP
            
            request.direct_only = direct_only

        # 2. Stage: kayak_search
        all_offers = []
        with tracer.track_stage("kayak_search") as info:
            try:
                offers = KayakAdapter().search(request, use_fixtures=use_fixtures, debug_dump=debug_dump_kayak)
                all_offers.extend(offers)
                info["offers_count"] = len(offers)
            except (OfflineModeError, Exception) as e:
                print(f"[!] Kayak failed: {e}")

        # 3. Stage: moblix_search
        with tracer.track_stage("moblix_search") as info:
            try:
                offers = MoblixLatamAdapter().search(request, use_fixtures=use_fixtures, debug_dump=debug_dump_moblix)
                all_offers.extend(offers)
                info["offers_count"] = len(offers)
            except (OfflineModeError, Exception) as e:
                print(f"[!] Moblix failed: {e}")

        if not all_offers:
            return result

        # 4. Stage: layover_classify
        with tracer.track_stage("layover_classify") as info:
            all_offers = classify_many(all_offers)
            
            # Filtro de voos diretos se solicitado
            if request.direct_only:
                filtered = []
                for o in all_offers:
                    direct_out = (o.stops_out == 0)
                    direct_in = (o.trip_type == TripType.ONEWAY or o.stops_in == 0)
                    if direct_out and direct_in:
                        filtered.append(o)
                all_offers = filtered
                
            info["offers_count"] = len(all_offers)

        if not all_offers:
            result.justification = ["Nenhum voo direto encontrado; desative o filtro para ver conexões."]
            return result

        # 5. Stage: score_rank
        with tracer.track_stage("score_rank") as info:
            ranked_offers, best_overall, justifications = rank_offers(all_offers, top_n=top_n)
            
            # Categorizar e achar melhores específicos
            money_list = [o for o in all_offers if o.price_brl is not None]
            miles_list = [o for o in all_offers if o.miles is not None]
            
            result.ranked_offers = ranked_offers
            result.best_overall = best_overall
            result.money_offers = money_list
            result.miles_offers = miles_list
            
            if money_list:
                result.best_money = min(money_list, key=lambda x: x.price_brl)
            if miles_list:
                result.best_miles = min(miles_list, key=lambda x: x.equivalent_brl)
                
            result.justification = justifications
            info["offers_count"] = len(ranked_offers)

        # 6. Stage: format_report
        with tracer.track_stage("format_report"):
            report_json, report_text = build_ui_report(ranked_offers, best_overall, justifications)
            result.table_rows = report_json
            
        return result

    finally:
        if trace_out:
            tracer.save(trace_out)

def main():
    parser = argparse.ArgumentParser(description="PCD Runner - Flight Search Pipeline")
    parser.add_argument("--prompt", type=str, required=True, help="Prompt de busca")
    parser.add_argument("--top", type=int, default=5, help="Número de ofertas no ranking")
    parser.add_argument("--use-fixtures", action="store_true", help="Usa dados locais mockados")
    parser.add_argument("--trace-out", type=str, help="Caminho para o trace JSONL")
    
    args = parser.parse_args()
    
    res = run_pipeline(args.prompt, args.top, args.use_fixtures, args.trace_out)
    
    if res.best_overall:
        print(f"\n[*] Melhor Oferta Geral: {res.best_overall.airline} ({res.best_overall.source.value})")
        
        if res.best_money:
            print(f"[*] Melhor em Dinheiro: {res.best_money.airline} - R$ {res.best_money.price_brl:.2f}")
            
        if res.best_miles:
             bm = res.best_miles
             print(f"[*] Melhor em Milhas: {bm.airline} - {bm.miles} milhas + R$ {bm.taxes_brl:.2f} (Eq: R$ {bm.equivalent_brl:.2f})")

        print("\nJustificativas:")
        for j in res.justification:
            print(f"- {j}")
    else:
        print("\n[!] Nenhuma oferta encontrada.")

    if res.trace_path:
        print(f"\n[*] Trace salvo em {res.trace_path}")

if __name__ == "__main__":
    main()
