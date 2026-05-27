"""GET/PUT /api/v1/rates — read and update the miles BRL-per-mile table.

Frontend uses these to let users tune the rate per program and per volume tier.
"""
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from backend.app.services.conversion import (
    get_rates_snapshot,
    update_rates,
)

router = APIRouter(tags=["rates"])


class RateTier(BaseModel):
    max_miles: int | None = Field(None, description="Inclusive ceiling for the tier; null = unbounded")
    rate: float = Field(..., gt=0, description="BRL paid per single mile")


class RatesUpdateRequest(BaseModel):
    programs: dict[str, list[RateTier]]
    international_fallback_rate: float | None = None
    skiplagged_estimation_program: str | None = None


class RatesResponse(BaseModel):
    programs: dict[str, list[RateTier]]
    international_fallback_rate: float
    skiplagged_estimation_program: str


@router.get("/rates", response_model=RatesResponse)
def get_rates() -> dict[str, Any]:
    return get_rates_snapshot()


@router.put("/rates", response_model=RatesResponse)
def put_rates(payload: RatesUpdateRequest) -> dict[str, Any]:
    try:
        return update_rates(payload.model_dump(exclude_none=True))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
