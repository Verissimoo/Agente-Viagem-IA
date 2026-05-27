"""
DTOs de request para o endpoint POST /search.

Mantém o transporte HTTP separado do domínio interno (UnifiedOffer,
SearchRequest). O orchestrator converte SearchRequestDTO → SearchRequest
do domínio antes de invocar o pipeline.
"""
from __future__ import annotations

from datetime import date
from typing import List, Optional

from pydantic import BaseModel, Field

from backend.app.domain.models import CabinClass, TripType


class SearchRequestDTO(BaseModel):
    """Entrada do endpoint /search vinda do frontend Angular."""

    origin: str = Field(..., min_length=3, max_length=4, description="IATA do aeroporto/cidade de origem")
    destination: str = Field(..., min_length=3, max_length=4, description="IATA do aeroporto/cidade de destino")
    date_start: date = Field(..., description="Data de ida")
    date_return: Optional[date] = Field(None, description="Data de volta (None = ida só)")

    adults: int = Field(1, ge=1, le=9)
    cabin: CabinClass = CabinClass.ECONOMY
    direct_only: bool = False
    baggage_checked: bool = False

    flex_mode: str = Field("none", description='"none" | "plusminus" | "range"')
    flex_days: int = Field(0, ge=0, le=14)
    date_end: Optional[date] = Field(None, description="Fim do range se flex_mode=range")
    flex_return: bool = False

    companhias: Optional[List[str]] = Field(
        None,
        description="Lista de cias para buscar; None = COMPANHIAS_NACIONAIS + Skiplagged (sempre)",
    )
    top_n: int = Field(5, ge=1, le=50, description="Ranking top-N para retornar")
    include_summary: bool = Field(False, description="Se True, retorna resumo gerado por IA")
    use_fixtures: bool = Field(False, description="Modo offline com fixtures (testes)")
    force_refresh: bool = Field(
        False,
        description="Se True, invalida o cache antes da busca — usar quando precificar venda real",
    )
