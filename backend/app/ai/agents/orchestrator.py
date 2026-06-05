"""Orchestrator — converte slots em parâmetros de busca e chama a tool.

Nó determinístico (não chama LLM). Recebe `slots` do state, monta os
argumentos certos, invoca `run_search`, e guarda o resultado no state.

Aplica o `RateLimiter` do usuário antes de buscar — busca é cara e
explorável. Se passar do limite, devolve mensagem amigável e não busca.
"""
from __future__ import annotations

import logging
from datetime import date as _date
from typing import Any, Optional

from langchain_core.messages import AIMessage

from backend.app.ai.agents.state import ChatState, IntakeSlots
from backend.app.ai.agents.tools import run_search
from backend.app.chat.security.audit import get_audit_logger
from backend.app.chat.security.rate_limit import RateLimitExceeded, get_rate_limiter

logger = logging.getLogger(__name__)


def _parse_iso_date(value: Optional[str]) -> Optional[_date]:
    if not value:
        return None
    try:
        return _date.fromisoformat(value)
    except ValueError:
        return None


def orchestrator_node(state: ChatState) -> ChatState:
    slots: IntakeSlots = state.get("slots") or {}  # type: ignore[assignment]
    user_id = state.get("user_id", "")
    thread_id = state.get("thread_id")
    audit = get_audit_logger()

    origin = slots.get("origin_iata")
    destination = slots.get("destination_iata")
    date_start = _parse_iso_date(slots.get("date_start"))

    if not origin or not destination or not date_start:
        # Não deveria acontecer (Intake garante), mas defesa em profundidade.
        return {
            **state,
            "next_node": "intake",
            "messages": [AIMessage(content=(
                "Não consegui montar a busca com as informações que temos. "
                "Pode confirmar a origem, destino e data?"
            ))],
            "errors": [*state.get("errors", []), "missing_required_after_intake"],
        }

    try:
        get_rate_limiter().check_search(user_id)
    except RateLimitExceeded as e:
        audit.log(
            "rate_limit.exceeded",
            user_id=user_id,
            thread_id=thread_id,
            severity="warn",
            detail={"kind": e.kind, "retry_in_s": e.retry_in_s},
        )
        return {
            **state,
            "next_node": "end",
            "messages": [AIMessage(content=(
                f"Você atingiu o limite de buscas por agora. Tente novamente em "
                f"{int(e.retry_in_s)} segundos."
            ))],
        }

    audit.log(
        "search.run",
        user_id=user_id,
        thread_id=thread_id,
        detail={
            "origin": origin,
            "destination": destination,
            "date_start": date_start.isoformat(),
            "trip_type": slots.get("trip_type", "oneway"),
        },
    )

    # Normaliza flex_mode: "plus", "minus", "plusminus" → o pipeline interno
    # aceita "plusminus" (busca simétrica). Mantemos o slot original pra
    # exibir ao vendedor "antes"/"depois" quando ele perguntar.
    flex_mode_raw = (slots.get("flex_mode") or "none").lower()
    flex_mode_for_pipeline = (
        "plusminus" if flex_mode_raw in ("plus", "minus", "plusminus") else flex_mode_raw
    )

    # Cap real do pipeline é 7 dias (15 datas). Se vendedor pediu mais,
    # registra warning pro presenter mencionar na resposta.
    flex_days_requested = int(slots.get("flex_days", 0) or 0)
    PIPELINE_FLEX_CAP = 7
    flex_clamped = flex_days_requested > PIPELINE_FLEX_CAP
    if flex_clamped:
        logger.info(
            "orchestrator: flex_days=%d > cap=%d → limitando busca",
            flex_days_requested, PIPELINE_FLEX_CAP,
        )

    # ─── Flex de ROUNDTRIP em duas fases ─────────────────────────────
    # O radar Kayak (barato) varre as combinações (ida, volta) candidatas e
    # escolhe as melhores datas; só nas TOP 2-3 rodamos a cotação completa
    # (milhas + hidden city). Cobre: janela-ida × janela-volta, duração+range,
    # ida-flex + volta fixa, e ±N dias com volta.
    trip_duration = int(slots.get("trip_duration_days") or 0)
    date_end_parsed = _parse_iso_date(slots.get("date_end"))
    return_from = _parse_iso_date(slots.get("return_from"))
    return_to = _parse_iso_date(slots.get("return_to"))
    single_return = _parse_iso_date(slots.get("date_return"))
    trip_type = slots.get("trip_type") or "oneway"

    is_rt_flex = trip_type == "roundtrip" and (
        (return_from and return_to)
        or (trip_duration > 0 and date_end_parsed and date_end_parsed > date_start)
        or (flex_mode_for_pipeline == "range" and date_end_parsed and single_return)
        or (flex_mode_for_pipeline == "plusminus" and flex_days_requested > 0 and single_return)
    )

    if is_rt_flex:
        from backend.app.services.flex_planner import build_candidate_pairs
        from backend.app.services.date_radar import scan_dates
        from backend.app.ai.agents.multi_date import run_multi_date_search

        pairs = build_candidate_pairs(
            depart_start=date_start, depart_end=date_end_parsed,
            return_start=return_from, return_end=return_to,
            single_return=single_return, duration_days=trip_duration,
            flex_mode=flex_mode_for_pipeline, flex_days=flex_days_requested, cap=16,
        )
        if pairs:
            radar = scan_dates(
                pairs, origin=origin, destination=destination,
                adults=int(slots.get("adults", 1)), cabin=slots.get("cabin", "economy"),
            )
            top = (radar.ranked_pairs or pairs)[:3]
            logger.info(
                "orchestrator: radar=%s, %d candidatos → top %d: %s",
                radar.source, len(pairs), len(top),
                [(str(i), str(v)) for i, v in top],
            )
            audit.log(
                "search.run.flex_radar",
                user_id=user_id, thread_id=thread_id,
                detail={
                    "origin": origin, "destination": destination,
                    "candidates": len(pairs), "radar_source": radar.source,
                    "top": [{"ida": str(i), "volta": str(v)} for i, v in top],
                },
            )
            common = dict(
                origin=origin, destination=destination,
                adults=int(slots.get("adults", 1)),
                cabin=slots.get("cabin", "economy"),
                direct_only=bool(slots.get("direct_only", False)),
                baggage_checked=bool(slots.get("baggage_checked") or False),
                flex_mode="none", flex_days=0,
            )
            result = run_multi_date_search(date_pairs=top, common_args=common)
            if result.get("ok"):
                info = result.setdefault("multi_date_info", {})
                info["radar_source"] = radar.source
                info["candidates_scanned"] = len(pairs)
                info["radar_prices"] = radar.price_by_pair

                # HIDDEN CITY ida-e-volta = DOIS bilhetes só-ida somados (hidden
                # city é one-way). Cota a melhor data do radar como ida (O→D) +
                # volta (D→O) separadas e soma. O presenter compara com o RT
                # normal e recomenda o mais barato validado.
                try:
                    from backend.app.services.roundtrip_hidden_city import quote_roundtrip_two_oneways
                    best_ida, best_volta = top[0]
                    hc_rt = quote_roundtrip_two_oneways(
                        origin=origin, destination=destination,
                        ida_date=best_ida, volta_date=best_volta,
                        adults=int(slots.get("adults", 1)),
                        cabin=slots.get("cabin", "economy"),
                    )
                    if hc_rt:
                        result["roundtrip_two_oneways"] = hc_rt
                except Exception as e:
                    logger.warning("orchestrator: two-oneways falhou: %s", e)

                return {**state, "next_node": "validator", "search_results": result}

            # Cotação completa falhou nas top datas → tenta uma busca fixa na
            # melhor combinação do radar (nunca cai no watchdog genérico).
            logger.warning("orchestrator: flex radar/multi vazio (%s); fallback fixo", result.get("error"))
            best_ida, best_volta = top[0]
            fb = run_search(
                origin=origin, destination=destination,
                date_start=best_ida, date_return=best_volta,
                adults=int(slots.get("adults", 1)), cabin=slots.get("cabin", "economy"),
                direct_only=bool(slots.get("direct_only", False)),
                flex_mode="none", flex_days=0,
                baggage_checked=bool(slots.get("baggage_checked") or False),
            )
            if fb.get("ok") and (fb.get("ranked_offers") or fb.get("money_offers") or fb.get("miles_offers")):
                fb.setdefault("justification", []).insert(
                    0, f"Melhor combinação encontrada: ida {best_ida.strftime('%d/%m')}, volta {best_volta.strftime('%d/%m')}.",
                )
                return {**state, "next_node": "validator", "search_results": fb}
            return {
                **state, "next_node": "end",
                "search_failed_notice": True,
                "messages": [AIMessage(content=(
                    f"Comparei as datas entre {date_start.strftime('%d/%m')} e "
                    f"{(date_end_parsed or return_to or single_return or date_start).strftime('%d/%m')}, "
                    "mas as fontes não retornaram tarifas pra essas combinações agora. "
                    "Quer tentar com datas fixas ou outro período?"
                ))],
            }

    # SÓ-IDA com flexibilidade: o pipeline normal (build_date_plan) já busca
    # todas as datas num único pool paralelo e o ranking escolhe a mais barata —
    # eficiente. (O radar Kayak-first só compensa no ROUNDTRIP, onde o
    # cross-product ida×volta é grande; em ida-só ele só adiciona overhead.)
    result = run_search(
        origin=origin,
        destination=destination,
        date_start=date_start,
        date_return=_parse_iso_date(slots.get("date_return")),
        adults=int(slots.get("adults", 1)),
        cabin=slots.get("cabin", "economy"),
        direct_only=bool(slots.get("direct_only", False)),
        flex_mode=flex_mode_for_pipeline,
        flex_days=int(slots.get("flex_days", 0)),
        date_end=_parse_iso_date(slots.get("date_end")),
        flex_return=bool(slots.get("flex_return", False)),
        baggage_checked=bool(slots.get("baggage_checked") or False),
    )

    if not result.get("ok"):
        audit.log(
            "search.failed",
            user_id=user_id, thread_id=thread_id,
            severity="error",
            detail={"error": result.get("error")},
        )
        return {
            **state,
            "next_node": "end",
            "messages": [AIMessage(content=(
                "Tive um problema ao consultar agora. Pode tentar de novo em alguns segundos?"
            ))],
            "search_results": None,
        }

    # Anexa flag de flex truncado pra o presenter avisar o vendedor.
    if flex_clamped:
        result["flex_clamped_info"] = {
            "requested_days": flex_days_requested,
            "actual_cap_days": PIPELINE_FLEX_CAP,
        }

    # ROUNDTRIP FIXO (sem flex): hidden city é one-way, então o ida-e-volta real
    # também é a soma de 2 bilhetes só-ida (ida O→D + volta D→O), cada um
    # validado. O presenter compara com o RT normal e recomenda o mais barato.
    if trip_type == "roundtrip" and single_return:
        try:
            from backend.app.services.roundtrip_hidden_city import quote_roundtrip_two_oneways
            hc_rt = quote_roundtrip_two_oneways(
                origin=origin, destination=destination,
                ida_date=date_start, volta_date=single_return,
                adults=int(slots.get("adults", 1)),
                cabin=slots.get("cabin", "economy"),
            )
            if hc_rt:
                result["roundtrip_two_oneways"] = hc_rt
        except Exception as e:
            logger.warning("orchestrator: two-oneways (RT fixo) falhou: %s", e)

    return {
        **state,
        "next_node": "validator",
        "search_results": result,
    }
