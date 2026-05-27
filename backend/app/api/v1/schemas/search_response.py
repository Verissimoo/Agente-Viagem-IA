"""
DTOs de response para o endpoint POST /search.

Agrupa ofertas por `scenario` (cash_direct, miles_direct, hidden_city,
split_cash, split_miles) para facilitar render no frontend Angular.
"""
from __future__ import annotations

from datetime import date
from typing import List, Optional

from pydantic import BaseModel, Field

from backend.app.domain.models import Scenario, UnifiedOffer


class SearchResponseDTO(BaseModel):
    """Saída do POST /search consumida pelo frontend."""

    request_id: str
    best_overall: Optional[UnifiedOffer] = None
    best_money: Optional[UnifiedOffer] = None
    best_miles: Optional[UnifiedOffer] = None

    ranked_offers: List[UnifiedOffer] = Field(default_factory=list, description="Top-N rankeado")
    money_offers: List[UnifiedOffer] = Field(default_factory=list)
    miles_offers: List[UnifiedOffer] = Field(default_factory=list)

    # Ofertas agrupadas por cenário — frontend usa para tabs/badges
    scenarios: dict[Scenario, List[UnifiedOffer]] = Field(default_factory=dict)

    best_depart_date: Optional[date] = None
    best_depart_date_equivalent_brl: Optional[float] = None
    best_depart_date_source: Optional[str] = None
    # Mapa ISO-date → menor equivalent_brl naquela data (preenchido quando flex_days > 0).
    date_best_map: dict[str, float] = Field(default_factory=dict)
    offers_by_depart_date: dict[str, int] = Field(default_factory=dict)

    justification: List[str] = Field(default_factory=list)
    direct_filter_warning: Optional[str] = None
    summary: Optional[str] = Field(None, description="Resumo gerado por IA (se include_summary=True)")


class HealthResponseDTO(BaseModel):
    status: str = "ok"
    version: str = "1.0.0"
    adapters: List[str] = Field(default_factory=list)


class ParseIntentRequestDTO(BaseModel):
    text: str = Field(..., min_length=1, max_length=500)


class ParseIntentResponseDTO(BaseModel):
    """Espelha `backend.app.domain.models.ParsedIntent` mas como DTO HTTP."""

    origin_iata: Optional[str] = None
    destination_iata: Optional[str] = None
    origin_city: Optional[str] = None
    destination_city: Optional[str] = None
    date_start: Optional[date] = None
    date_return: Optional[date] = None
    trip_type: str = "oneway"
    cabin: str = "economy"
    adults: int = 1
    direct_only: bool = False
    flex_mode: str = "none"
    flex_days: Optional[int] = None
    confidence: float = 0.0
    notes: Optional[str] = None
