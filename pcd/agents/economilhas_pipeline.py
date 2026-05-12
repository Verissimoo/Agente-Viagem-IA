"""
economilhas_pipeline.py
-----------------------
Pipeline alternativo do Agente de Cotação que usa a API Economilhas para
milhas. Emite o MESMO `PipelineResult` consumido pelo Streamlit, para que
o resto do app não precise saber qual provedor disparou a busca.

Decisões:

  - Cash continua via Kayak (KayakAdapter) — formato e cobertura idênticos
    ao run_pipeline padrão.
  - Milhas vem em UMA chamada Economilhas com todos os programas marcados.
  - Cada programa retornado pela Economilhas é mapeado a um SourceType já
    existente (não tocamos no enum em pcd/core/schema.py):
        SMILES         → BUSCAMILHAS_GOL
        LATAM          → BUSCAMILHAS_LATAM
        AZUL           → BUSCAMILHAS_AZUL
        AZUL_INTERLINE → BUSCAMILHAS_INTERLINE
        COPA           → BUSCAMILHAS_COPA
        IBERIA         → BUSCAMILHAS_IBERIA
        BRITISH        → MCP_AWARD (com miles_program="Avios" para CPM correto)
  - O `airline` da UnifiedOffer recebe o nome amigável usado na tabela
    `RATES_BRL_PER_MILE` (LATAM/GOL/AZUL/...) para o CPM resolver direto.
"""
from __future__ import annotations

import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from typing import Any, Dict, List, Optional, Tuple

from pcd.core.schema import (
    CabinClass, Itinerary, LayoverCategory, PipelineResult,
    SearchRequest, Segment, SourceType, TripType, UnifiedOffer,
)
from pcd.core.tracer import PipelineTracer
from pcd.core.layover_classifier import classify_many
from pcd.core.ranking import rank_offers
from pcd.core.formatter import build_ui_report
from pcd.core.flex_dates import build_date_plan, compute_best_day
from pcd.adapters.kayak_adapter import KayakAdapter

from economilhas_client import (
    EconomilhasError, EconomilhasQuotaExceeded, EconomilhasAuthError,
    search_flights_economilhas,
)
from economilhas_offer_parser import (
    extract_rows_from_economilhas, PROGRAM_AIRLINE_INFO,
)


# ──────────────────────────────────────────────────────────────────
# Mapa Programa Economilhas → (SourceType, airline label, miles_program)
# ──────────────────────────────────────────────────────────────────
PROGRAM_TO_SOURCE: Dict[str, Tuple[SourceType, str, Optional[str]]] = {
    "SMILES":         (SourceType.BUSCAMILHAS_GOL,       "GOL",     "Smiles"),
    "LATAM":          (SourceType.BUSCAMILHAS_LATAM,     "LATAM",   "LATAM Pass"),
    "AZUL":           (SourceType.BUSCAMILHAS_AZUL,      "AZUL",    "TudoAzul"),
    "AZUL_INTERLINE": (SourceType.BUSCAMILHAS_INTERLINE, "INTERLINE", "Azul Interline"),
    "COPA":           (SourceType.BUSCAMILHAS_COPA,      "COPA",    "ConnectMiles"),
    "IBERIA":         (SourceType.BUSCAMILHAS_IBERIA,    "IBERIA",  "Avios"),
    "BRITISH":        (SourceType.MCP_AWARD,             "BRITISH AIRWAYS", "Avios"),
}


# ──────────────────────────────────────────────────────────────────
# SearchRequest builder (lógica equivalente a pcd.run)
# ──────────────────────────────────────────────────────────────────
MAX_FLEX_RANGE_DAYS_ECO = 7  # cap espelhando pcd/run.py


def _build_search_request(
    *,
    origin: Optional[str], destination: Optional[str],
    date_start: Optional[date], date_end: Optional[date], date_return: Optional[date],
    direct_only: bool, flex_days: int, flex_return: bool, flex_mode: str,
    adults: int = 1,
    trip_type_override: Optional[TripType] = None,
) -> SearchRequest:
    if not origin or not destination:
        raise RuntimeError("origin e destination são obrigatórios na pipeline Economilhas.")
    if date_start is None:
        from datetime import timedelta
        date_start = date.today() + timedelta(days=30)
    final_end = date_end if (flex_mode == "range" and date_end) else date_start

    if flex_mode == "range" and final_end and final_end > date_start:
        from datetime import timedelta as _td
        max_end = date_start + _td(days=MAX_FLEX_RANGE_DAYS_ECO - 1)
        if final_end > max_end:
            final_end = max_end

    if trip_type_override is not None:
        trip_type_final = trip_type_override
    else:
        trip_type_final = TripType.ROUNDTRIP if date_return else TripType.ONEWAY

    return SearchRequest(
        origin=[origin.upper()], destination=[destination.upper()],
        date_start=date_start, date_end=final_end,
        return_start=date_return if trip_type_final == TripType.ROUNDTRIP else None,
        return_end=date_return if trip_type_final == TripType.ROUNDTRIP else None,
        trip_type=trip_type_final,
        adults=adults, cabin=CabinClass.ECONOMY,
        baggage_checked=False, direct_only=direct_only,
        flex_days=flex_days, flex_return=flex_return, flex_mode=flex_mode,
    )


# ──────────────────────────────────────────────────────────────────
# Helpers de conversão row → UnifiedOffer (lógica espelhada do BaseBuscaMilhasAdapter)
# ──────────────────────────────────────────────────────────────────
def _build_itinerary(row: Dict[str, Any], leg_label: str, default_carrier: str) -> Optional[Itinerary]:
    """Converte uma row no formato do parser para um Itinerary do schema."""
    if not row:
        return None
    # segments_raw já vem populado pelos parsers; evita o caminho de placeholder.
    segs: List[Segment] = []
    if leg_label == "IDA":
        segs = list(row.get("outbound_segments_raw") or row.get("segments_raw") or [])
    else:
        segs = list(row.get("inbound_segments_raw") or row.get("segments_raw") or [])
    if not segs:
        return None
    # Calcula duration_min total a partir dos segmentos quando ausente.
    dur_min = 0
    dur_str = row.get("Duração", "") or ""
    for part in dur_str.split():
        if part.endswith("h"):
            try: dur_min += int(part[:-1]) * 60
            except: pass
        elif part.endswith("m"):
            try: dur_min += int(part[:-1])
            except: pass
    if dur_min <= 0 and segs:
        dur_min = max(0, int((segs[-1].arrival_dt - segs[0].departure_dt).total_seconds() / 60))
    return Itinerary(segments=segs, duration_min=dur_min if dur_min > 0 else None)


def _rows_to_unified_offers(
    rows: List[Dict[str, Any]], trip_type_str: str,
) -> List[UnifiedOffer]:
    """Converte rows Economilhas em UnifiedOffer agrupando por programa.

    Em RT, faz `zip` de IDA com VOLTA dentro do mesmo programa (idêntico
    ao BaseBuscaMilhasAdapter), o que casa as rows pela ordem em que o
    parser as devolveu (já ordenadas por preço/milhas)."""
    out: List[UnifiedOffer] = []

    # Agrupa por programa, ignorando rows informativas (parser pendente)
    by_program: Dict[str, List[Dict[str, Any]]] = {}
    for r in rows:
        if r.get("_unparsed"):
            continue
        prog = str(r.get("Programa") or "").upper()
        by_program.setdefault(prog, []).append(r)

    for program, group in by_program.items():
        meta = PROGRAM_TO_SOURCE.get(program)
        if meta is None:
            # Programa não mapeado — pula (seria classificado como genérico)
            continue
        source_type, airline_label, miles_program_label = meta

        idas    = [r for r in group if r.get("Trecho") == "IDA"   and r.get("IsMiles")]
        voltas  = [r for r in group if r.get("Trecho") == "VOLTA" and r.get("IsMiles")]
        idas_c  = [r for r in group if r.get("Trecho") == "IDA"   and not r.get("IsMiles")]
        voltas_c= [r for r in group if r.get("Trecho") == "VOLTA" and not r.get("IsMiles")]

        def _emit_miles_pair(ida: Dict[str, Any], volta: Optional[Dict[str, Any]]):
            outbound = _build_itinerary(ida, "IDA", airline_label)
            if outbound is None:
                return
            inbound = _build_itinerary(volta, "VOLTA", airline_label) if volta else None
            if trip_type_str == "RT" and inbound is None:
                return
            kwargs: Dict[str, Any] = {
                "source": source_type,
                "airline": airline_label,
                "trip_type": TripType.ROUNDTRIP if (trip_type_str == "RT") else TripType.ONEWAY,
                "outbound": outbound,
                "miles_program": miles_program_label,
                "layover_out": LayoverCategory.DIRECT,
            }
            taxes_out = float(ida.get("Taxas (R$)") or 0.0)
            miles_out = int(ida.get("Milhas") or 0)
            if trip_type_str == "RT" and volta is not None:
                taxes_in = float(volta.get("Taxas (R$)") or 0.0)
                miles_in = int(volta.get("Milhas") or 0)
                kwargs.update({
                    "inbound": inbound,
                    "miles_out": miles_out, "miles_in": miles_in,
                    "miles": miles_out + miles_in,
                    "taxes_brl_out": taxes_out, "taxes_brl_in": taxes_in,
                    "taxes_brl": taxes_out + taxes_in,
                    "baggage_miles_out": ida.get("Bagagem") if isinstance(ida.get("Bagagem"), (int, float)) else None,
                    "baggage_miles_in": volta.get("Bagagem") if isinstance(volta.get("Bagagem"), (int, float)) else None,
                })
            else:
                kwargs.update({
                    "miles": miles_out, "taxes_brl": taxes_out,
                    "baggage_miles_out": ida.get("Bagagem") if isinstance(ida.get("Bagagem"), (int, float)) else None,
                })
            out.append(UnifiedOffer(**kwargs))

        if trip_type_str == "RT":
            for ida, volta in zip(idas, voltas):
                _emit_miles_pair(ida, volta)
        else:
            for ida in idas:
                _emit_miles_pair(ida, None)

        # Cash do mesmo programa (raro — Economilhas cash separado)
        def _emit_cash_pair(ida: Dict[str, Any], volta: Optional[Dict[str, Any]]):
            outbound = _build_itinerary(ida, "IDA", airline_label)
            if outbound is None:
                return
            inbound = _build_itinerary(volta, "VOLTA", airline_label) if volta else None
            if trip_type_str == "RT" and inbound is None:
                return
            price_out = float(ida.get("Preço") or 0.0)
            taxes_out = float(ida.get("Taxas (R$)") or 0.0)
            kwargs: Dict[str, Any] = {
                "source": SourceType.KAYAK,
                "airline": airline_label,
                "trip_type": TripType.ROUNDTRIP if (trip_type_str == "RT") else TripType.ONEWAY,
                "outbound": outbound,
                "layover_out": LayoverCategory.DIRECT,
            }
            if trip_type_str == "RT" and volta is not None:
                price_in = float(volta.get("Preço") or 0.0)
                taxes_in = float(volta.get("Taxas (R$)") or 0.0)
                kwargs.update({
                    "inbound": inbound,
                    "price_brl_out": price_out, "price_brl_in": price_in,
                    "price_brl": price_out + price_in,
                    "price_amount": price_out + price_in, "price_currency": "BRL",
                    "taxes_brl_out": taxes_out, "taxes_brl_in": taxes_in,
                    "taxes_brl": taxes_out + taxes_in,
                })
            else:
                kwargs.update({
                    "price_brl": price_out, "price_amount": price_out, "price_currency": "BRL",
                    "taxes_brl": taxes_out,
                })
            out.append(UnifiedOffer(**kwargs))

        if trip_type_str == "RT":
            for ida, volta in zip(idas_c, voltas_c):
                _emit_cash_pair(ida, volta)
        else:
            for ida in idas_c:
                _emit_cash_pair(ida, None)

    return out


# ──────────────────────────────────────────────────────────────────
# Pipeline
# ──────────────────────────────────────────────────────────────────
def run_pipeline_economilhas(
    *,
    prompt: str = "",
    top_n: int = 5,
    use_fixtures: bool = False,
    date_start: Optional[date] = None,
    date_return: Optional[date] = None,
    direct_only: bool = False,
    origin: Optional[str] = None,
    destination: Optional[str] = None,
    flex_days: int = 0,
    flex_return: bool = False,
    flex_mode: str = "none",
    date_end: Optional[date] = None,
    adults: int = 1,
    miles_airlines: Optional[List[str]] = None,
    use_kayak_cash: bool = True,
    cabin: str = "ECONOMY",
    debug: bool = False,
    trip_type: Optional[TripType] = None,
) -> Tuple[PipelineResult, List[Dict[str, Any]]]:
    """Roda o pipeline com provedor Economilhas para milhas e Kayak para cash.

    Devolve (PipelineResult, partial_failures). `partial_failures` lista as
    companhias que vieram com `success=false` ou que o parser não conseguiu
    interpretar — a UI usa para mostrar avisos no topo.
    """
    request_id = str(uuid.uuid4())[:8]
    tracer = PipelineTracer(request_id)
    result = PipelineResult(request_id=request_id)
    partial_failures: List[Dict[str, Any]] = []

    # 1. Stage: parse
    with tracer.track_stage("parse"):
        request = _build_search_request(
            origin=origin, destination=destination,
            date_start=date_start, date_end=date_end, date_return=date_return,
            direct_only=direct_only, flex_days=flex_days,
            flex_return=flex_return, flex_mode=flex_mode,
            adults=max(1, int(adults or 1)),
            trip_type_override=trip_type,
        )

    # 2. Stage: date_planning
    with tracer.track_stage("date_planning") as info:
        search_plan = build_date_plan(request)
        info["offers_count"] = len(search_plan)

    miles_airlines = [a.upper() for a in (miles_airlines or [])]
    trip_type_str = "RT" if request.return_start else "OW"

    # 3. Stage: dispara TODAS as combinações (data × {kayak_cash, economilhas_miles})
    # num único pool — antes o loop de datas era sequencial. Para flex ±N dias o
    # tempo passa de N×max(t_kayak, t_eco) para ~max(t_kayak, t_eco).
    all_offers: List[UnifiedOffer] = []
    tasks: List[tuple] = []  # (kind, req_i, date_trace_id)
    for req_i in search_plan:
        date_trace_id = f"_{req_i.date_start.isoformat()}"
        if req_i.return_start:
            date_trace_id += f"_ret_{req_i.return_start.isoformat()}"
        if use_kayak_cash:
            tasks.append(("kayak_cash", req_i, date_trace_id))
        if miles_airlines:
            tasks.append(("economilhas_miles", req_i, date_trace_id))

    if tasks:
        # Cap em 8 — Kayak/Economilhas têm semáforos próprios (5/5) então o
        # pool maior só ajuda a saturar os dois canais simultaneamente.
        workers = min(8, len(tasks))
        t_cart = time.perf_counter()
        with ThreadPoolExecutor(max_workers=workers) as ex:
            future_meta = {}
            for kind, req_i, dt_id in tasks:
                if kind == "kayak_cash":
                    f = ex.submit(_run_kayak_cash, req_i, use_fixtures)
                else:  # economilhas_miles
                    f = ex.submit(
                        _run_economilhas_miles,
                        req_i, miles_airlines, cabin, trip_type_str, debug,
                    )
                future_meta[f] = (kind, req_i, dt_id)

            for f in as_completed(future_meta):
                kind_meta, req_meta, dt_id = future_meta[f]
                kind2, day_offers, kind_failures, elapsed_ms = f.result()
                stage_name = f"{kind2}{dt_id}"
                tracer.log_event(
                    stage=stage_name, status="end",
                    latency_ms=elapsed_ms, offers_count=len(day_offers),
                    message=req_meta.date_start.isoformat(),
                )
                all_offers.extend(day_offers)
                if kind_failures:
                    partial_failures.extend(kind_failures)

        cart_elapsed_ms = (time.perf_counter() - t_cart) * 1000.0
        tracer.log_event(
            stage="economilhas_x_dates_parallel", status="end",
            latency_ms=cart_elapsed_ms, offers_count=len(all_offers),
            message=f"{len(tasks)} tasks ({len(search_plan)} datas × kayak+eco)",
        )
        # TEMP_PERF — remover após validar
        print(
            f"⏱ TEMP_PERF economilhas_pipeline cartesiano: {len(tasks)} tasks → "
            f"{cart_elapsed_ms/1000.0:.1f}s, {len(all_offers)} ofertas"
        )

    if not all_offers:
        result.justification = ["Nenhuma oferta encontrada (Economilhas)."]
        return result, partial_failures

    # 4. Stage: layover_classify
    with tracer.track_stage("layover_classify") as info:
        all_offers = classify_many(all_offers)
        info["offers_count"] = len(all_offers)

    # Filtro Rigoroso de Voo Direto (mesmo do run_pipeline)
    if request.direct_only:
        direct_only_offers = [
            o for o in all_offers
            if (o.stops_out == 0)
            and (o.trip_type == TripType.ONEWAY or o.stops_in == 0)
        ]
        if direct_only_offers:
            all_offers = direct_only_offers
        else:
            result.direct_filter_warning = (
                "Nenhum voo direto encontrado. Exibindo todas as opções disponíveis."
            )

    # 5. Stage: score_rank
    with tracer.track_stage("score_rank") as info:
        ranked, best_overall, justifications = rank_offers(all_offers, top_n=top_n)
        money_list = [o for o in all_offers if o.price_brl is not None]
        miles_list = [o for o in all_offers if o.miles is not None]
        result.ranked_offers = ranked
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
        info["offers_count"] = len(ranked)

    # 6. Stage: format_report
    with tracer.track_stage("format_report"):
        report_json, _ = build_ui_report(ranked, best_overall, justifications)
        result.table_rows = report_json

    return result, partial_failures


# ──────────────────────────────────────────────────────────────────
# Workers
# ──────────────────────────────────────────────────────────────────
def _run_kayak_cash(
    req: SearchRequest, use_fixtures: bool,
) -> Tuple[str, List[UnifiedOffer], List[Dict[str, Any]], float]:
    t0 = time.perf_counter()
    try:
        offers = KayakAdapter().search(req, use_fixtures=use_fixtures, debug_dump=False)
        elapsed = (time.perf_counter() - t0) * 1000.0
        return "kayak_cash", offers, [], elapsed
    except Exception as e:
        elapsed = (time.perf_counter() - t0) * 1000.0
        return "kayak_cash", [], [{
            "airline": "KAYAK", "message": f"Kayak falhou: {str(e)[:200]}", "providerStatusCode": None,
        }], elapsed


def _run_economilhas_miles(
    req: SearchRequest, airlines: List[str], cabin: str,
    trip_type_str: str, debug: bool,
) -> Tuple[str, List[UnifiedOffer], List[Dict[str, Any]], float]:
    t0 = time.perf_counter()
    failures: List[Dict[str, Any]] = []
    try:
        dep_iso = req.date_start.isoformat()
        ret_iso = req.return_start.isoformat() if req.return_start else None
        response = search_flights_economilhas(
            airlines=airlines,
            origin=req.origin[0], destination=req.destination[0],
            departure_date=dep_iso, return_date=ret_iso,
            adults=req.adults, cabin=cabin, price_type="MILES",
        )
        rows, fails = extract_rows_from_economilhas(response, trip_type=trip_type_str, debug=debug)
        failures.extend(fails)
        offers = _rows_to_unified_offers(rows, trip_type_str)
        elapsed = (time.perf_counter() - t0) * 1000.0
        return "economilhas_miles", offers, failures, elapsed
    except EconomilhasQuotaExceeded as e:
        elapsed = (time.perf_counter() - t0) * 1000.0
        return "economilhas_miles", [], [{
            "airline": "ALL", "message": "Quota Economilhas esgotada.",
            "providerStatusCode": 402, "fatal": True,
            "raw": str(e)[:200],
        }], elapsed
    except EconomilhasAuthError as e:
        elapsed = (time.perf_counter() - t0) * 1000.0
        return "economilhas_miles", [], [{
            "airline": "ALL", "message": "ECONOMILHAS_API_KEY inválida ou ausente.",
            "providerStatusCode": 401, "fatal": True,
            "raw": str(e)[:200],
        }], elapsed
    except EconomilhasError as e:
        elapsed = (time.perf_counter() - t0) * 1000.0
        return "economilhas_miles", [], [{
            "airline": "ALL", "message": f"Economilhas falhou: {str(e)[:200]}",
            "providerStatusCode": None, "fatal": True,
        }], elapsed
    except Exception as e:
        elapsed = (time.perf_counter() - t0) * 1000.0
        return "economilhas_miles", [], [{
            "airline": "ALL", "message": f"Erro inesperado: {type(e).__name__}: {str(e)[:200]}",
            "providerStatusCode": None, "fatal": True,
        }], elapsed
