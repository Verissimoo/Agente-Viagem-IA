"""Pipeline orchestrator — fans out across all providers, ranks, returns top-N."""
from __future__ import annotations

import os
import re
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from concurrent.futures import TimeoutError as FuturesTimeoutError
from datetime import date, timedelta
from typing import Optional, Tuple

from backend.app.domain.models import (
    SearchRequest, TripType, CabinClass, PipelineResult,
)
from backend.app.providers.kayak.adapter import KayakAdapter
from backend.app.providers.mcp_award.adapter import McpAwardAdapter, McpQatarAdapter
from backend.app.providers.buscamilhas.adapter import (
    BuscaMilhasLatamAdapter, BuscaMilhasGolAdapter, BuscaMilhasAzulAdapter,
    BuscaMilhasAzulCashAdapter,
    BuscaMilhasTapAdapter, BuscaMilhasIberiaAdapter,
    BuscaMilhasAmericanAdapter, BuscaMilhasInterlineAdapter,
    BuscaMilhasCopaAdapter,
)
from backend.app.providers.economilhas.adapter import EconomilhasAdapter
from backend.app.providers.seats_aero.adapter import SeatsAeroAdapter
from backend.app.providers.awardtool.adapter import AwardToolAdapter
from backend.app.providers.skiplagged.adapter import SkiplaggedAdapter
from backend.app.providers.buscamilhas.client import COMPANHIAS_NACIONAIS
from backend.app.services.ranking import rank_offers
from backend.app.services.formatter import build_ui_report
from backend.app.infrastructure.tracer import PipelineTracer
from backend.app.domain.errors import OfflineModeError
from backend.app.services.layover_classifier import classify_many
from backend.app.services.flex_dates import build_date_plan, compute_best_day


# Mapa companhia → classe do adapter
_ADAPTER_MAP = {
    "KAYAK":             KayakAdapter,
    "LATAM":             BuscaMilhasLatamAdapter,
    "GOL":               BuscaMilhasGolAdapter,
    "AZUL":              BuscaMilhasAzulAdapter,
    # Cash OFICIAL da Azul via BuscaMilhas — sempre roda em paralelo (na
    # _ALWAYS_INCLUDE abaixo) porque é uma tarifa estratégica pra agência
    # (consegue vender com lucro incluso).
    "AZUL_CASH":         BuscaMilhasAzulCashAdapter,
    "TAP":               BuscaMilhasTapAdapter,
    "IBERIA":            BuscaMilhasIberiaAdapter,
    "AMERICAN":          BuscaMilhasAmericanAdapter,
    "AMERICAN AIRLINES": BuscaMilhasAmericanAdapter,
    "INTERLINE":         BuscaMilhasInterlineAdapter,
    "COPA":              BuscaMilhasCopaAdapter,
    "MCP_AWARD":         McpAwardAdapter,
    "QATAR":             McpQatarAdapter,
    # Economilhas é a FONTE PRIMÁRIA de milhas — uma única chamada retorna
    # disponibilidade em todos os programas (Smiles, LATAM Pass, TudoAzul,
    # Azul Pelo Mundo, COPA, Iberia, British). BuscaMilhas continua como
    # fallback/complemento por cia.
    "ECONOMILHAS":       EconomilhasAdapter,
    # seats.aero: award internacional multi-programa (Aeroplan, Lifemiles,
    # Flying Blue no piloto). Uma chamada cobre vários programas; sem key
    # (SEATS_AERO_API_KEY) o adapter devolve [] e não atrapalha.
    "SEATS_AERO":        SeatsAeroAdapter,
    # AwardTool: award multi-programa via scraping da conta Pro (Playwright).
    # PESADO + ToS-sensível → gated por AWARDTOOL_ENABLED (default 0 → []).
    "AWARDTOOL":         AwardToolAdapter,
    # Skiplagged é COMPLEMENTAR: sempre rodando em paralelo, fornece
    # hidden-city + split-cash. Falha do Skiplagged não derruba o pipeline.
    "SKIPLAGGED":        SkiplaggedAdapter,
}

# Limite de buscas concorrentes. Cobre as ~10 cias do mapa + Economilhas + Skiplagged.
_MAX_PARALLEL_ADAPTERS = 14

# Orçamento de tempo da fase de adapters (s). Se um provider trava ou demora
# além disso, seguimos com o que já voltou (resultado parcial) em vez de
# segurar a resposta inteira. Default folgado pra caber o Skiplagged (~20s) no
# caso normal; ajuste via env SEARCH_ADAPTER_BUDGET_S em produção.
try:
    _ADAPTER_BUDGET_S = float(os.getenv("SEARCH_ADAPTER_BUDGET_S", "30"))
except ValueError:
    _ADAPTER_BUDGET_S = 30.0

# Fontes sempre injetadas além das companhias selecionadas pelo usuário.
# Economilhas é uma chamada agregada (todos os programas em 1 hit) então
# sempre roda; Skiplagged é hidden-city/split cash, sem cia atrelada.
_ALWAYS_INCLUDE = ["ECONOMILHAS", "SEATS_AERO", "AWARDTOOL", "SKIPLAGGED", "AZUL_CASH"]


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
    baggage_checked: bool = False,
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
        baggage_checked=baggage_checked,
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
    from datetime import datetime as _dt
    from backend.app.services.conversion import offer_equivalent_brl
    t0 = time.perf_counter()
    try:
        offers = adapter_cls().search(
            req, use_fixtures=use_fixtures, debug_dump=debug_dump
        )
        # Stamp every offer with its capture time + equivalent_brl.
        # UI usa captured_at para "consulted N seconds ago"; equivalent_brl
        # vai pra coluna "Custo Real" da planilha do quote-complete.
        now = _dt.now()
        for o in offers:
            if getattr(o, "captured_at", None) is None:
                try:
                    o.captured_at = now
                except Exception:
                    pass
            if o.equivalent_brl is None or o.equivalent_brl == 0:
                try:
                    v = offer_equivalent_brl(o)
                    if v and v > 0:
                        o.equivalent_brl = float(v)
                except Exception:
                    pass
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        return cia_up, offers, None, elapsed_ms
    except (OfflineModeError, Exception) as e:
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        return cia_up, [], e, elapsed_ms


# Rótulos legíveis por fonte pro painel de progresso ao vivo (visão do vendedor).
_PROGRESS_LABELS = {
    "LATAM": "LATAM (milhas)", "GOL": "Smiles/GOL (milhas)", "AZUL": "TudoAzul (milhas)",
    "TAP": "TAP (milhas)", "IBERIA": "Iberia (milhas)", "AMERICAN": "American (milhas)",
    "AMERICAN AIRLINES": "American (milhas)", "INTERLINE": "Interline (milhas)",
    "COPA": "Copa (milhas)", "MCP_AWARD": "MCP Award (milhas)", "QATAR": "Qatar (milhas)",
    "ECONOMILHAS": "Economilhas (milhas)", "SEATS_AERO": "Seats.aero (award)",
    "AWARDTOOL": "AwardTool (award)",
    "KAYAK": "Kayak (cash)",
    "SKIPLAGGED": "Skiplagged (hidden city)", "AZUL_CASH": "Azul Oficial (cash)",
}


def _progress_label(cia: str) -> str:
    return _PROGRESS_LABELS.get(cia.upper(), cia)


def _emit_progress(on_progress, msg: str) -> None:
    if on_progress:
        try:
            on_progress(msg)
        except Exception:
            pass


def _execute_dates_x_adapters_parallel(
    search_plan, companhias_ativas,
    use_fixtures, debug_dump, tracer, on_progress=None,
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
    # Sem `with`: não queremos bloquear no __exit__ esperando stragglers além
    # do orçamento — abandonamos o que não voltou a tempo.
    ex = ThreadPoolExecutor(max_workers=workers)
    futures = {
        ex.submit(
            _run_one_adapter, cia_up, cls, req_i, use_fixtures, debug_dump,
        ): (cia_up, req_i, dt_id)
        for cia_up, cls, req_i, dt_id in tasks
    }
    n_cias = len({c.upper() for c in companhias_ativas})
    _emit_progress(on_progress, f"Consultando {n_cias} fontes em {len(search_plan)} data(s)…")
    done_count = 0
    total = len(futures)
    try:
        for fut in as_completed(futures, timeout=_ADAPTER_BUDGET_S):
            _cia_meta, req_meta, dt_id = futures[fut]
            cia_up, offers, error, elapsed_ms = fut.result()
            done_count += 1
            stage_name = f"adapter_search_{cia_up.lower()}{dt_id}"
            day = req_meta.date_start.strftime("%d/%m")
            label = _progress_label(cia_up)
            if error is not None:
                tracer.log_event(
                    stage=stage_name, status="error",
                    latency_ms=elapsed_ms, offers_count=0, error=str(error),
                )
                print(f"[!] Adapter {cia_up} failed for {req_meta.date_start}: {error}")
                _emit_progress(on_progress, f"✗ {label} · {day} — sem resposta [{done_count}/{total}]")
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
                res = f"{len(offers)} oferta(s)" if offers else "sem tarifa"
                _emit_progress(on_progress, f"✓ {label} · {day} — {res} [{done_count}/{total}]")
    except FuturesTimeoutError:
        pending = [(futures[f][0], futures[f][1]) for f in futures if not f.done()]
        for cia_up, req_meta in pending:
            tracer.log_event(
                stage=f"adapter_search_{cia_up.lower()}_budget",
                status="error", latency_ms=int(_ADAPTER_BUDGET_S * 1000),
                offers_count=0, error="budget_exceeded",
            )
        print(
            f"[orchestrator] orçamento de {_ADAPTER_BUDGET_S:.0f}s estourado — "
            f"{done_count}/{len(futures)} adapters concluíram; seguindo com "
            f"parcial ({len(pending)} abandonados: {[c for c, _ in pending]})"
        )
    finally:
        # Não espera os stragglers: eles terminam em background e o resultado é
        # descartado. cancel_futures evita iniciar os que ainda nem rodaram.
        ex.shutdown(wait=False, cancel_futures=True)

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
    baggage_checked: bool = False,
    always_include: Optional[list] = None,
    on_progress=None,
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
                baggage_checked=baggage_checked,
            )

        # 2. Stage: date_planning
        all_offers = []
        with tracer.track_stage("date_planning") as info:
            search_plan = build_date_plan(request)
            info["offers_count"] = len(search_plan)

        companhias_ativas = list(companhias) if companhias else list(COMPANHIAS_NACIONAIS)
        # Economilhas pode estar sem créditos (HTTP 402) — desligar via env evita
        # gastar tempo de orçamento tentando puxá-la. BuscaMilhas cobre as milhas.
        _econ_on = os.getenv("ECONOMILHAS_ENABLED", "1") == "1"
        # Fontes sempre injetadas. `always_include` permite restringir (ex.: VCP =
        # só Azul → always_include=["AZUL_CASH"], sem Skiplagged/Economilhas).
        _always = _ALWAYS_INCLUDE if always_include is None else always_include
        for extra in _always:
            if extra == "ECONOMILHAS" and not _econ_on:
                continue
            if extra not in (c.upper() for c in companhias_ativas):
                companhias_ativas.append(extra)
        # Defesa: se o usuário passou ECONOMILHAS explicitamente mas está off, remove.
        if not _econ_on:
            companhias_ativas = [c for c in companhias_ativas if c.upper() != "ECONOMILHAS"]

        # 3. Stage: produto cartesiano (datas × adapters) num único pool.
        # Antes: loop sequencial de datas × pool paralelo de adapters →
        # N_datas chamadas em série de ~10s cada.
        # Agora: 1 pool de tamanho 10 com todas as combinações date×adapter →
        # tempo total ≈ chamada mais lenta + overhead.
        t_cart = time.perf_counter()
        offers = _execute_dates_x_adapters_parallel(
            search_plan, companhias_ativas,
            use_fixtures, debug_dump_buscamilhas, tracer, on_progress=on_progress,
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
