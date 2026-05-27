"""POST /api/v1/miles-match — given a Kayak leg (from /split) and the paired
leg's timing, quote the same flight in miles across all relevant programs.

This is the second half of the split-ticketing workflow:
  /split   → finds combinations of domestic + international legs in cash
  /miles-match → for a chosen leg, quotes the same flight in miles
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from backend.app.services.miles_match import MilesMatchAgent
from backend.app.services.segment_split import KayakOffer

router = APIRouter(tags=["miles-match"])


class LegInput(BaseModel):
    """Slim Kayak leg passed back from the frontend (subset of KayakLegDTO)."""
    airline: str
    airlines: list[str] = []
    airlines_iata: list[str] = []
    origin: str
    destination: str
    departure_dt: Optional[str] = None
    arrival_dt: Optional[str] = None
    duration_min: int = 0
    stops: int = 0
    price_brl: float = 0.0


class MilesMatchRequest(BaseModel):
    leg: LegInput = Field(..., description="The Kayak leg to quote in miles")
    leg_type: Literal["domestic", "international"]
    other_leg_dt: str = Field(..., description="Departure ISO of the paired leg (for layover window)")
    other_leg_direction: Literal["before_intl", "after_intl"] = Field(
        ...,
        description="Position of the paired leg relative to the leg being quoted",
    )
    with_baggage: bool = False
    adults: int = Field(1, ge=1, le=9)
    provider: Literal["buscamilhas", "economilhas"] = "buscamilhas"


class MilesMatchOptionDTO(BaseModel):
    program: str
    miles: int
    miles_brl_equivalent: float
    taxes_brl: float
    total_real_cost_brl: float
    flight_number: str
    carrier: str
    departure_dt: Optional[str] = None
    arrival_dt: Optional[str] = None
    is_exact_match: bool
    is_in_window: bool
    layover_minutes: int


class MilesMatchResponse(BaseModel):
    leg_type: str
    target_carrier: str
    programs_searched: list[str]
    options: list[MilesMatchOptionDTO] = []
    has_exact_match: bool = False
    no_results_reason: Optional[str] = None
    notes: list[str] = []


def _parse_dt(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def _leg_to_kayak_offer(leg: LegInput) -> KayakOffer:
    return KayakOffer(
        origin=leg.origin.upper(),
        destination=leg.destination.upper(),
        airlines=leg.airlines or ([leg.airline] if leg.airline else []),
        airlines_iata=leg.airlines_iata,
        departure_dt=_parse_dt(leg.departure_dt),
        arrival_dt=_parse_dt(leg.arrival_dt),
        duration_min=leg.duration_min,
        stops=leg.stops,
        price_brl=leg.price_brl,
    )


@router.post("/miles-match", response_model=MilesMatchResponse)
def miles_match(payload: MilesMatchRequest) -> MilesMatchResponse:
    other_dt = _parse_dt(payload.other_leg_dt)
    if other_dt is None:
        raise HTTPException(status_code=400, detail="other_leg_dt inválido")

    kayak_offer = _leg_to_kayak_offer(payload.leg)
    agent = MilesMatchAgent()

    try:
        if payload.leg_type == "domestic":
            result = agent.match_domestic_leg(
                kayak_offer=kayak_offer,
                other_leg_dt=other_dt,
                other_leg_direction=payload.other_leg_direction,
                with_baggage=payload.with_baggage,
                adults=payload.adults,
                provider=payload.provider,
            )
        else:
            result = agent.match_international_leg(
                kayak_offer=kayak_offer,
                domestic_leg_dt=other_dt,
                domestic_leg_direction=payload.other_leg_direction,
                with_baggage=payload.with_baggage,
                adults=payload.adults,
                provider=payload.provider,
            )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Falha no miles-match: {e}") from e

    return MilesMatchResponse(
        leg_type=result.leg_type,
        target_carrier=result.target_carrier,
        programs_searched=result.programs_searched,
        options=[
            MilesMatchOptionDTO(
                program=o.program,
                miles=o.miles,
                miles_brl_equivalent=o.miles_brl_equivalent,
                taxes_brl=o.taxes_brl,
                total_real_cost_brl=o.total_real_cost_brl,
                flight_number=o.flight_number,
                carrier=o.carrier,
                departure_dt=o.departure_dt.isoformat() if o.departure_dt else None,
                arrival_dt=o.arrival_dt.isoformat() if o.arrival_dt else None,
                is_exact_match=o.is_exact_match,
                is_in_window=o.is_in_window,
                layover_minutes=o.layover_minutes,
            )
            for o in result.options
        ],
        has_exact_match=result.has_exact_match,
        no_results_reason=result.no_results_reason,
        notes=result.notes,
    )
