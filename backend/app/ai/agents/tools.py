"""Ferramentas (tool calls) que os agentes invocam.

A única ferramenta real é `run_search` — wrap do `run_pipeline` do orchestrator.
Os agentes NÃO devem expor essa tool ao LLM via tool-use: o Orchestrator é um
nó determinístico que chama essa função diretamente quando o Intake termina.
Isso reduz superfície de ataque (LLM não pode "decidir" chamar a busca com
parâmetros maliciosos).
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Any, Dict, List, Optional

from backend.app.ai.agents.routes import classify_route
from backend.app.services.search_orchestrator import run_pipeline

logger = logging.getLogger(__name__)


def _companies_for_route(origin: str, destination: str) -> Optional[List[str]]:
    """Companhias a consultar conforme a rota. Internacional → TODAS (inclui
    TAP, Iberia, American, Copa, Interline); doméstico → None (o pipeline usa
    o default nacional LATAM/GOL/AZUL). Sem isso, voos internacionais só
    buscavam milhas em LATAM/GOL/AZUL e ignoravam os programas internacionais."""
    if classify_route(origin, destination) == "international":
        from backend.app.providers.buscamilhas.client import COMPANHIAS_TODAS
        return list(COMPANHIAS_TODAS)
    return None


def run_search(
    *,
    origin: str,
    destination: str,
    date_start: date,
    date_return: Optional[date] = None,
    adults: int = 1,
    cabin: str = "economy",
    direct_only: bool = False,
    flex_mode: str = "none",
    flex_days: int = 0,
    date_end: Optional[date] = None,
    flex_return: bool = False,
    companhias: Optional[List[str]] = None,
    always_include: Optional[List[str]] = None,
    top_n: int = 5,
    baggage_checked: bool = False,
) -> Dict[str, Any]:
    """Executa o pipeline e devolve dict pronto pra consumo pelo Validator/Presenter.

    Não levanta — falha vira `{"ok": False, "error": "..."}` para o agente
    decidir como recuperar (perguntar de novo, sugerir outra data, etc.).
    """
    # Internacional precisa consultar os programas internacionais (TAP, Iberia,
    # American, Copa, Interline) — senão só busca milhas em LATAM/GOL/AZUL.
    if companhias is None:
        companhias = _companies_for_route(origin, destination)

    try:
        result = run_pipeline(
            prompt="",
            top_n=top_n,
            use_fixtures=False,
            date_start=date_start,
            date_return=date_return,
            direct_only=direct_only,
            origin=origin,
            destination=destination,
            flex_days=flex_days,
            flex_return=flex_return,
            flex_mode=flex_mode,
            date_end=date_end,
            companhias=companhias,
            always_include=always_include,
            baggage_checked=baggage_checked,
        )
    except Exception as e:
        logger.exception("run_search falhou")
        return {"ok": False, "error": str(e)}

    # Serializa o PipelineResult em dict — agentes operam em JSON puro.
    return {
        "ok": True,
        "request_id": result.request_id,
        "best_overall": _offer_to_dict(result.best_overall),
        "best_money": _offer_to_dict(result.best_money),
        "best_miles": _offer_to_dict(result.best_miles),
        "ranked_offers": [_offer_to_dict(o) for o in result.ranked_offers],
        "money_offers": [_offer_to_dict(o) for o in result.money_offers],
        "miles_offers": [_offer_to_dict(o) for o in result.miles_offers],
        "best_depart_date": result.best_depart_date.isoformat() if result.best_depart_date else None,
        "best_depart_date_equivalent_brl": result.best_depart_date_equivalent_brl,
        "date_best_map": result.date_best_map,
        "justification": result.justification,
        "direct_filter_warning": result.direct_filter_warning,
    }


def _offer_to_dict(offer: Any) -> Optional[Dict[str, Any]]:
    if offer is None:
        return None
    # `model_dump(mode="json")` converte datetime/Decimal/Enum corretamente.
    try:
        return offer.model_dump(mode="json")
    except AttributeError:
        return dict(offer) if hasattr(offer, "__iter__") else None
