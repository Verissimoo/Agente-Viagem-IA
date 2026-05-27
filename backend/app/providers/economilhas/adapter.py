"""Economilhas adapter — primary miles source.

Economilhas is the PRIMARY miles source in this app: a single API call
returns availability across LATAM Pass, Smiles, TudoAzul, Azul Pelo Mundo,
Copa ConnectMiles, Iberia Plus, and British Avios. BuscaMilhas continues
to run in parallel as a complement / fallback.

This adapter wraps `search_flights_economilhas` + `extract_rows_from_economilhas`
+ `_rows_to_unified_offers` (from the existing pipeline) behind the standard
`BaseSearchAdapter.search()` contract so the orchestrator can fan out to it
just like any other provider.
"""
from __future__ import annotations

import os
from typing import List

from backend.app.domain.errors import OfflineModeError
from backend.app.domain.models import SearchRequest, TripType, UnifiedOffer
from backend.app.infrastructure.config import config
from backend.app.providers.base import BaseSearchAdapter
from backend.app.providers.economilhas.client import (
    EconomilhasAuthError,
    EconomilhasError,
    EconomilhasQuotaExceeded,
    search_flights_economilhas,
)
from backend.app.providers.economilhas.parser import extract_rows_from_economilhas
from backend.app.services.economilhas_pipeline import _rows_to_unified_offers

# Programs Economilhas supports. The API resolves them all in a single call.
ECONOMILHAS_PROGRAMS_DEFAULT: List[str] = [
    "SMILES",
    "LATAM",
    "AZUL",
    "AZUL_INTERLINE",
    "COPA",
    "IBERIA",
    "BRITISH",
]


def _enabled() -> bool:
    return os.getenv("ECONOMILHAS_ENABLED", "1") not in ("0", "false", "False", "")


class EconomilhasAdapter(BaseSearchAdapter):
    """Synchronous adapter — runs inside the orchestrator's ThreadPoolExecutor.

    Failure is absorbed (returns []) so an Economilhas outage never degrades
    the rest of the search.
    """

    def search(
        self,
        request: SearchRequest,
        use_fixtures: bool = False,
        debug_dump: bool = False,
    ) -> List[UnifiedOffer]:
        if not _enabled():
            return []
        if config.PCD_OFFLINE:
            raise OfflineModeError("Economilhas")
        if not os.getenv("ECONOMILHAS_API_KEY"):
            # No credential → silently no results.
            return []

        cabin = (request.cabin.value if request.cabin else "economy").upper()
        trip_type_str = "RT" if request.return_start else "OW"
        airlines = ECONOMILHAS_PROGRAMS_DEFAULT

        try:
            response = search_flights_economilhas(
                airlines=airlines,
                origin=request.origin[0],
                destination=request.destination[0],
                departure_date=request.date_start.isoformat(),
                return_date=request.return_start.isoformat() if request.return_start else None,
                adults=request.adults,
                cabin=cabin,
                price_type="MILES",
            )
        except (EconomilhasAuthError, EconomilhasQuotaExceeded, EconomilhasError) as e:
            print(f"[economilhas_adapter] {type(e).__name__}: {str(e)[:200]}")
            return []
        except Exception as e:
            print(f"[economilhas_adapter] unexpected: {e}")
            return []

        try:
            rows, _failures = extract_rows_from_economilhas(
                response, trip_type=trip_type_str, debug=debug_dump
            )
            offers = _rows_to_unified_offers(rows, trip_type_str)
        except Exception as e:
            print(f"[economilhas_adapter] parse error: {e}")
            return []

        return offers
