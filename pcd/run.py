import argparse
import sys
import uuid
from datetime import date, datetime, timedelta
from typing import Optional

from pcd.core.schema import SearchRequest, TripType, CabinClass
from pcd.adapters.kayak_adapter import KayakAdapter
from pcd.adapters.buscamilhas_adapter import (
    BuscaMilhasLatamAdapter, BuscaMilhasGolAdapter, BuscaMilhasAzulAdapter,
    BuscaMilhasTapAdapter, BuscaMilhasIberiaAdapter,
    BuscaMilhasAmericanAdapter, BuscaMilhasInterlineAdapter,
)
from miles_app.buscamilhas_client import COMPANHIAS_NACIONAIS
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
from pcd.core.flex_dates import build_date_plan, compute_best_day

# Mapa companhia → classe do adapter
_ADAPTER_MAP = {
    "LATAM":     BuscaMilhasLatamAdapter,
    "GOL":       BuscaMilhasGolAdapter,
    "AZUL":      BuscaMilhasAzulAdapter,
    "TAP":       BuscaMilhasTapAdapter,
    "IBERIA":    BuscaMilhasIberiaAdapter,
    "AMERICAN":  BuscaMilhasAmericanAdapter,
    "AMERICAN AIRLINES": BuscaMilhasAmericanAdapter,
    "INTERLINE": BuscaMilhasInterlineAdapter,
}

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
    debug_dump_buscamilhas: bool = False,
    flex_days: int = 0,
    flex_return: bool = False,
    flex_mode: str = "none",
    date_end: Optional[date] = None,
    companhias: Optional[list] = None,
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
                # No modo range, o date_end vem explicitamente, senão é igual ao start
                request.date_end = date_end if (flex_mode == "range" and date_end) else date_start
            
            if date_return:
                request.return_start = date_return
                request.return_end = date_return
                request.trip_type = TripType.ROUNDTRIP
            
            request.direct_only = direct_only
            request.flex_days = flex_days
            request.flex_return = flex_return
            request.flex_mode = flex_mode

        # 2. Stage: date_planning
        all_offers = []
        with tracer.track_stage("date_planning") as info:
            search_plan = build_date_plan(request)
            info["plan_size"] = len(search_plan)

        # Loop through each request in the plan
        for i, req_i in enumerate(search_plan):
            print(f"DEBUG: Pesquisando data {req_i.date_start}")
            date_trace_id = f"_{req_i.date_start.isoformat()}"
            if req_i.return_start:
                date_trace_id += f"_ret_{req_i.return_start.isoformat()}"

            # 3. Stage: kayak_search
            with tracer.track_stage(f"kayak_search{date_trace_id}") as info:
                try:
                    offers = KayakAdapter().search(req_i, use_fixtures=use_fixtures, debug_dump=debug_dump_kayak)
                    all_offers.extend(offers)
                    print(f"DEBUG: Kayak retornou {len(offers)} ofertas para {req_i.date_start}. Total acumulado: {len(all_offers)}")
                    info["offers_count"] = len(offers)
                    info["date"] = req_i.date_start.isoformat()
                except (OfflineModeError, Exception) as e:
                    print(f"[!] Kayak failed for {req_i.date_start}: {e}")

            # 4. Stage: buscamilhas_search (loop dinâmico por companhia)
            companhias_ativas = companhias if companhias else COMPANHIAS_NACIONAIS
            for cia in companhias_ativas:
                cia_up = cia.upper()
                adapter_cls = _ADAPTER_MAP.get(cia_up)
                if not adapter_cls:
                    print(f"[!] Companhia '{cia_up}' sem adapter registrado — ignorada.")
                    continue
                stage_name = f"buscamilhas_search_{cia_up.lower()}{date_trace_id}"
                with tracer.track_stage(stage_name) as info:
                    try:
                        offers = adapter_cls().search(req_i, use_fixtures=use_fixtures, debug_dump=debug_dump_buscamilhas)
                        all_offers.extend(offers)
                        print(f"DEBUG: BuscaMilhas {cia_up} retornou {len(offers)} ofertas para {req_i.date_start}. Total acumulado: {len(all_offers)}")
                        info["offers_count"] = len(offers)
                        info["date"] = req_i.date_start.isoformat()
                    except (OfflineModeError, Exception) as e:
                        print(f"[!] BuscaMilhas {cia_up} failed for {req_i.date_start}: {e}")

        if not all_offers:
            print("DEBUG: Nenhuma oferta encontrada em nenhuma data.")
            return result
        
        print(f"DEBUG: Processamento concluído. Total de ofertas consolidadas: {len(all_offers)}")

        # 5. Stage: layover_classify
        with tracer.track_stage("layover_classify") as info:
            all_offers = classify_many(all_offers)
            info["offers_count"] = len(all_offers)

        if not all_offers:
            return result

        # Filtro Rigoroso de Voo Direto
        if request.direct_only:
            direct_only_offers = []
            for o in all_offers:
                is_direct_out = (o.stops_out == 0)
                is_direct_in = (o.trip_type == TripType.ONEWAY or o.stops_in == 0)
                if is_direct_out and is_direct_in:
                    direct_only_offers.append(o)
            
            if direct_only_offers:
                all_offers = direct_only_offers
            else:
                result.direct_filter_warning = "Nenhum voo direto encontrado. Exibindo todas as opções disponíveis."

        # 6. Stage: score_rank
        with tracer.track_stage("score_rank") as info:
            # 6.1 Ranking base (agora all_offers já está filtrado, se aplicável)
            ranked_offers, best_overall, justifications = rank_offers(all_offers, top_n=top_n)
            
            if request.direct_only and not result.direct_filter_warning:
                justifications = ["⭐ Voo Direto Priorizado: " + j for j in justifications[:1]] + justifications[1:]

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
                
            # Flex Optimizer
            best_date, best_val, best_source, date_map, counts_map = compute_best_day(all_offers)
            result.best_depart_date = best_date
            result.best_depart_date_equivalent_brl = best_val
            result.best_depart_date_source = best_source
            result.date_best_map = date_map
            result.offers_by_depart_date = counts_map

            result.justification = justifications
            info["offers_count"] = len(ranked_offers)

        # 7. Stage: format_report
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
