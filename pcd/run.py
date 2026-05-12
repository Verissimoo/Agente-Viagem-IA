import argparse
import re
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from typing import Optional, Tuple

from pcd.core.schema import (
    SearchRequest, TripType, CabinClass, PipelineResult,
)
from pcd.adapters.kayak_adapter import KayakAdapter
from pcd.adapters.mcp_award_adapter import McpAwardAdapter, McpQatarAdapter
from pcd.adapters.buscamilhas_adapter import (
    BuscaMilhasLatamAdapter, BuscaMilhasGolAdapter, BuscaMilhasAzulAdapter,
    BuscaMilhasTapAdapter, BuscaMilhasIberiaAdapter,
    BuscaMilhasAmericanAdapter, BuscaMilhasInterlineAdapter,
    BuscaMilhasCopaAdapter,
)
from miles_app.buscamilhas_client import COMPANHIAS_NACIONAIS
from pcd.core.ranking import rank_offers
from pcd.core.formatter import build_ui_report
from pcd.core.tracer import PipelineTracer
from pcd.core.errors import OfflineModeError
from pcd.core.layover_classifier import classify_many
from pcd.core.flex_dates import build_date_plan, compute_best_day


# Mapa companhia → classe do adapter
_ADAPTER_MAP = {
    "KAYAK":             KayakAdapter,
    "LATAM":             BuscaMilhasLatamAdapter,
    "GOL":               BuscaMilhasGolAdapter,
    "AZUL":              BuscaMilhasAzulAdapter,
    "TAP":               BuscaMilhasTapAdapter,
    "IBERIA":            BuscaMilhasIberiaAdapter,
    "AMERICAN":          BuscaMilhasAmericanAdapter,
    "AMERICAN AIRLINES": BuscaMilhasAmericanAdapter,
    "INTERLINE":         BuscaMilhasInterlineAdapter,
    "COPA":              BuscaMilhasCopaAdapter,
    "MCP_AWARD":         McpAwardAdapter,
    "QATAR":             McpQatarAdapter,
}

# Limite de buscas concorrentes. Cobre as ~10 cias do mapa.
_MAX_PARALLEL_ADAPTERS = 10


def _parse_iatas_from_prompt(prompt: str) -> Tuple[str, str]:
    """Extrai dois IATAs do prompt (fallback usado pelo CLI)."""
    iatas = re.findall(r"\b[A-Z]{3}\b", (prompt or "").upper())
    origin = iatas[0] if len(iatas) >= 1 else "GRU"
    destination = iatas[1] if len(iatas) >= 2 else "MIA"
    return origin, destination


# Limite de dias para flex_mode="range" na busca principal. BuscaMilhas/
# Economilhas não suportam range nativo; fazemos N chamadas em paralelo.
# Acima de 7 datas estoura quota rápido — limite imposto aqui.
MAX_FLEX_RANGE_DAYS = 7


def _build_search_request(
    prompt: str,
    *,
    origin: Optional[str],
    destination: Optional[str],
    date_start: Optional[date],
    date_end: Optional[date],
    date_return: Optional[date],
    direct_only: bool,
    flex_days: int,
    flex_return: bool,
    flex_mode: str,
    trip_type_override: Optional[TripType] = None,
) -> SearchRequest:
    """Monta o SearchRequest. UI passa todos os campos; CLI cai no parser
    de IATAs e em datas default (hoje + 30 dias)."""
    if not origin or not destination:
        guess_o, guess_d = _parse_iatas_from_prompt(prompt)
        origin = origin or guess_o
        destination = destination or guess_d

    if date_start is None:
        date_start = date.today() + timedelta(days=30)

    final_end = date_end if (flex_mode == "range" and date_end) else date_start

    # Cap explícito do range para preservar quota das APIs de milhas.
    if flex_mode == "range" and final_end and final_end > date_start:
        max_end = date_start + timedelta(days=MAX_FLEX_RANGE_DAYS - 1)
        if final_end > max_end:
            final_end = max_end

    if trip_type_override is not None:
        trip_type_final = trip_type_override
    else:
        trip_type_final = TripType.ROUNDTRIP if date_return else TripType.ONEWAY

    return SearchRequest(
        origin=[origin.upper()],
        destination=[destination.upper()],
        date_start=date_start,
        date_end=final_end,
        return_start=date_return if trip_type_final == TripType.ROUNDTRIP else None,
        return_end=date_return if trip_type_final == TripType.ROUNDTRIP else None,
        trip_type=trip_type_final,
        adults=1,
        cabin=CabinClass.ECONOMY,
        baggage_checked=False,
        direct_only=direct_only,
        flex_days=flex_days,
        flex_return=flex_return,
        flex_mode=flex_mode,
    )


def _run_one_adapter(cia_up, adapter_cls, req, use_fixtures, debug_dump):
    """Worker: executa um adapter e devolve dados do resultado.

    Roda em uma thread. Não pode tocar no tracer ou em listas
    compartilhadas — devolve só o resultado para o agregador no main thread.
    """
    t0 = time.perf_counter()
    try:
        offers = adapter_cls().search(
            req, use_fixtures=use_fixtures, debug_dump=debug_dump
        )
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        return cia_up, offers, None, elapsed_ms
    except (OfflineModeError, Exception) as e:
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        return cia_up, [], e, elapsed_ms


def _execute_dates_x_adapters_parallel(
    search_plan, companhias_ativas,
    use_fixtures, debug_dump, tracer,
):
    """Dispara TODAS as combinações (data × adapter) num único pool.

    Mantém o tracer com o mesmo formato por-adapter-por-data que o pipeline
    sequencial usava (mesmo `stage_name`, mesma latência); a UI consome o
    tracer agnóstico a como o pool foi montado.

    Cap defensivo de workers em 10: o gargalo passa a ser o semáforo de
    cada cliente (SEM_KAYAK=5, SEM_BUSCAMILHAS=3, SEM_ECONOMILHAS=5) — e
    não a contagem de tasks.
    """
    tasks = []
    for req_i in search_plan:
        date_trace_id = f"_{req_i.date_start.isoformat()}"
        if req_i.return_start:
            date_trace_id += f"_ret_{req_i.return_start.isoformat()}"
        for cia in companhias_ativas:
            cia_up = cia.upper()
            adapter_cls = _ADAPTER_MAP.get(cia_up)
            if adapter_cls is None:
                print(f"[!] Companhia '{cia_up}' sem adapter — ignorada.")
                continue
            tasks.append((cia_up, adapter_cls, req_i, date_trace_id))

    if not tasks:
        return []

    aggregated = []
    workers = min(_MAX_PARALLEL_ADAPTERS, len(tasks))
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {
            ex.submit(
                _run_one_adapter, cia_up, cls, req_i, use_fixtures, debug_dump,
            ): (cia_up, req_i, dt_id)
            for cia_up, cls, req_i, dt_id in tasks
        }
        for fut in as_completed(futures):
            _cia_meta, req_meta, dt_id = futures[fut]
            cia_up, offers, error, elapsed_ms = fut.result()
            stage_name = f"adapter_search_{cia_up.lower()}{dt_id}"
            if error is not None:
                tracer.log_event(
                    stage=stage_name, status="error",
                    latency_ms=elapsed_ms, offers_count=0, error=str(error),
                )
                print(f"[!] Adapter {cia_up} failed for {req_meta.date_start}: {error}")
            else:
                tracer.log_event(
                    stage=stage_name, status="end",
                    latency_ms=elapsed_ms, offers_count=len(offers),
                    message=req_meta.date_start.isoformat(),
                )
                aggregated.extend(offers)
                print(
                    f"DEBUG: Adapter {cia_up} retornou {len(offers)} ofertas "
                    f"para {req_meta.date_start} em {elapsed_ms:.0f}ms"
                )

    return aggregated


def run_pipeline(
    prompt: str,
    top_n: int = 5,
    use_fixtures: bool = False,
    trace_out: Optional[str] = None,
    date_start: Optional[date] = None,
    date_return: Optional[date] = None,
    direct_only: bool = False,
    origin: Optional[str] = None,
    destination: Optional[str] = None,
    debug_dump_buscamilhas: bool = False,
    flex_days: int = 0,
    flex_return: bool = False,
    flex_mode: str = "none",
    date_end: Optional[date] = None,
    companhias: Optional[list] = None,
    trip_type: Optional[TripType] = None,
) -> PipelineResult:
    request_id = str(uuid.uuid4())[:8]
    tracer = PipelineTracer(request_id)

    result = PipelineResult(request_id=request_id, trace_path=trace_out)

    try:
        # 1. Stage: parse
        with tracer.track_stage("parse"):
            request = _build_search_request(
                prompt,
                origin=origin,
                destination=destination,
                date_start=date_start,
                date_end=date_end,
                date_return=date_return,
                direct_only=direct_only,
                flex_days=flex_days,
                flex_return=flex_return,
                flex_mode=flex_mode,
                trip_type_override=trip_type,
            )

        # 2. Stage: date_planning
        all_offers = []
        with tracer.track_stage("date_planning") as info:
            search_plan = build_date_plan(request)
            info["offers_count"] = len(search_plan)

        companhias_ativas = companhias if companhias else COMPANHIAS_NACIONAIS

        # 3. Stage: produto cartesiano (datas × adapters) num único pool.
        # Antes: loop sequencial de datas × pool paralelo de adapters →
        # N_datas chamadas em série de ~10s cada.
        # Agora: 1 pool de tamanho 10 com todas as combinações date×adapter →
        # tempo total ≈ chamada mais lenta + overhead.
        t_cart = time.perf_counter()
        offers = _execute_dates_x_adapters_parallel(
            search_plan, companhias_ativas,
            use_fixtures, debug_dump_buscamilhas, tracer,
        )
        cart_elapsed_ms = (time.perf_counter() - t_cart) * 1000.0
        tracer.log_event(
            stage="adapters_x_dates_parallel",
            status="end",
            latency_ms=cart_elapsed_ms,
            offers_count=len(offers),
            message=f"{len(companhias_ativas)} adapters x {len(search_plan)} datas",
        )
        all_offers.extend(offers)
        # TEMP_PERF — remover após validar ganho no Streamlit Cloud
        print(
            f"⏱ TEMP_PERF run_pipeline cartesiano: {len(companhias_ativas)} adapters × "
            f"{len(search_plan)} datas = {len(companhias_ativas) * len(search_plan)} tasks → "
            f"{cart_elapsed_ms/1000.0:.1f}s, {len(offers)} ofertas"
        )

        if not all_offers:
            print("DEBUG: Nenhuma oferta encontrada em nenhuma data.")
            return result

        print(f"DEBUG: Processamento concluído. Total de ofertas consolidadas: {len(all_offers)}")

        # 4. Stage: layover_classify
        with tracer.track_stage("layover_classify") as info:
            all_offers = classify_many(all_offers)
            info["offers_count"] = len(all_offers)

        if not all_offers:
            return result

        # Filtro Rigoroso de Voo Direto
        if request.direct_only:
            direct_only_offers = [
                o for o in all_offers
                if (o.stops_out == 0)
                and (o.trip_type == TripType.ONEWAY or o.stops_in == 0)
            ]
            if direct_only_offers:
                all_offers = direct_only_offers
            else:
                result.direct_filter_warning = "Nenhum voo direto encontrado. Exibindo todas as opções disponíveis."

        # 5. Stage: score_rank
        with tracer.track_stage("score_rank") as info:
            ranked_offers, best_overall, justifications = rank_offers(all_offers, top_n=top_n)

            if request.direct_only and not result.direct_filter_warning:
                justifications = ["⭐ Voo Direto Priorizado: " + j for j in justifications[:1]] + justifications[1:]

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

            best_date, best_val, best_source, date_map, counts_map = compute_best_day(all_offers)
            result.best_depart_date = best_date
            result.best_depart_date_equivalent_brl = best_val
            result.best_depart_date_source = best_source
            result.date_best_map = date_map
            result.offers_by_depart_date = counts_map

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
