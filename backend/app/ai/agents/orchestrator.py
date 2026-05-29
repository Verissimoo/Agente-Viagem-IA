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

    # ─── Multi-data: RANGE + DURAÇÃO FIXA ────────────────────────────
    # Se temos date_start + date_end (range) + trip_duration_days, geramos
    # combinações de ida/volta dentro do range e buscamos em paralelo.
    trip_duration = int(slots.get("trip_duration_days") or 0)
    date_end_iso = slots.get("date_end")
    trip_type = slots.get("trip_type") or "oneway"
    use_multi = (
        trip_duration > 0
        and date_end_iso
        and trip_type == "roundtrip"
    )
    if use_multi:
        date_end_parsed = _parse_iso_date(date_end_iso)
        if date_end_parsed and date_end_parsed > date_start:
            from backend.app.ai.agents.multi_date import (
                generate_date_pairs, run_multi_date_search,
            )
            pairs = generate_date_pairs(
                date_start, date_end_parsed, trip_duration, max_pairs=5,
            )
            logger.info(
                "orchestrator: multi-data %s → %s, duração=%d, %d pares: %s",
                date_start, date_end_parsed, trip_duration, len(pairs),
                [(str(i), str(v)) for i, v in pairs],
            )
            common = dict(
                origin=origin, destination=destination,
                adults=int(slots.get("adults", 1)),
                cabin=slots.get("cabin", "economy"),
                direct_only=bool(slots.get("direct_only", False)),
                flex_mode="none",   # cada par é exato
                flex_days=0,
            )
            audit.log(
                "search.run.multi_date",
                user_id=user_id, thread_id=thread_id,
                detail={
                    "origin": origin, "destination": destination,
                    "pairs": [{"ida": str(i), "volta": str(v)} for i, v in pairs],
                },
            )
            result = run_multi_date_search(date_pairs=pairs, common_args=common)
            if not result.get("ok"):
                return {
                    **state, "next_node": "end",
                    "messages": [AIMessage(content=(
                        "Tive um problema rodando a busca multi-data. "
                        "Pode tentar com datas fixas?"
                    ))],
                }
            return {
                **state,
                "next_node": "validator",
                "search_results": result,
            }

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

    return {
        **state,
        "next_node": "validator",
        "search_results": result,
    }
