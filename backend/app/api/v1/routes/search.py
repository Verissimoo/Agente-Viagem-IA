"""POST /api/v1/search — main flight search endpoint."""
from __future__ import annotations

from collections import defaultdict

from fastapi import APIRouter, HTTPException

from backend.app.ai.summarizer import summarize
from backend.app.api.v1.schemas.search_request import SearchRequestDTO
from backend.app.api.v1.schemas.search_response import SearchResponseDTO
from backend.app.domain.models import Scenario, UnifiedOffer
from backend.app.services.search_orchestrator import run_pipeline

router = APIRouter(tags=["search"])

# Cap por cenário para evitar payload gigante (Skiplagged pode devolver 200+).
MAX_PER_SCENARIO = 20


def _classify(offer: UnifiedOffer) -> Scenario:
    if offer.scenario is not None:
        return offer.scenario
    if offer.miles is not None:
        return Scenario.MILES_DIRECT
    return Scenario.CASH_DIRECT


def _group_by_scenario(offers: list[UnifiedOffer]) -> dict[Scenario, list[UnifiedOffer]]:
    """Groups ALL offers by scenario, sorted by equivalent_brl ascending, capped at MAX_PER_SCENARIO."""
    buckets: dict[Scenario, list[UnifiedOffer]] = defaultdict(list)
    for offer in offers:
        buckets[_classify(offer)].append(offer)

    sort_key = lambda o: o.equivalent_brl if o.equivalent_brl is not None else float("inf")
    return {scen: sorted(group, key=sort_key)[:MAX_PER_SCENARIO] for scen, group in buckets.items()}


@router.post("/search", response_model=SearchResponseDTO)
def search(payload: SearchRequestDTO) -> SearchResponseDTO:
    # Volatile pricing: when the seller explicitly asks to refresh, we bypass
    # the in-memory provider cache so every adapter call goes back to the source.
    if payload.force_refresh:
        from backend.app.infrastructure.cache import invalidate as _cache_invalidate
        _cache_invalidate()

    try:
        result = run_pipeline(
            prompt="",  # frontend já mandou tudo estruturado
            top_n=payload.top_n,
            use_fixtures=payload.use_fixtures,
            date_start=payload.date_start,
            date_return=payload.date_return,
            direct_only=payload.direct_only,
            origin=payload.origin,
            destination=payload.destination,
            flex_days=payload.flex_days,
            flex_return=payload.flex_return,
            flex_mode=payload.flex_mode,
            date_end=payload.date_end,
            companhias=payload.companhias,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Falha no pipeline: {e}") from e

    # Group ALL offers (not just ranked top-N) — ensures cash AND miles are
    # always visible to the user, even when one scenario dominates the ranking.
    all_offers = result.money_offers + result.miles_offers
    scenarios = _group_by_scenario(all_offers)

    summary: str | None = None
    if payload.include_summary:
        summary = summarize(
            result.ranked_offers,
            origin=payload.origin,
            destination=payload.destination,
            date=payload.date_start.isoformat(),
        )

    return SearchResponseDTO(
        request_id=result.request_id,
        best_overall=result.best_overall,
        best_money=result.best_money,
        best_miles=result.best_miles,
        ranked_offers=result.ranked_offers,
        money_offers=result.money_offers,
        miles_offers=result.miles_offers,
        scenarios=scenarios,
        best_depart_date=result.best_depart_date,
        best_depart_date_equivalent_brl=result.best_depart_date_equivalent_brl,
        best_depart_date_source=result.best_depart_date_source,
        date_best_map=result.date_best_map,
        offers_by_depart_date=result.offers_by_depart_date,
        justification=result.justification,
        direct_filter_warning=result.direct_filter_warning,
        summary=summary,
    )
