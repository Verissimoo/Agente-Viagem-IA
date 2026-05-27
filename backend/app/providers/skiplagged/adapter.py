"""Skiplagged provider — complementary source for hidden-city and split-cash deals.

Failure is absorbed silently (returns `[]`) so Skiplagged outages never
degrade the miles/cash results from the other providers.
"""
from __future__ import annotations

import os
import time
from typing import List

from backend.app.providers.base import BaseSearchAdapter
from backend.app.domain.errors import OfflineModeError
from backend.app.infrastructure.config import config
from backend.app.domain.models import SearchRequest, TripType, UnifiedOffer
from backend.app.infrastructure import cache as _cache

from backend.app.providers.skiplagged.client import fetch_skiplagged
from backend.app.providers.skiplagged.parser import extract_offers as skiplagged_extract


_FEATURE_FLAG_ENV = "SKIPLAGGED_ENABLED"


def _is_enabled() -> bool:
    """Feature flag para desligar Skiplagged rapidamente se a estrutura
    do site mudar. Default = habilitado."""
    return os.getenv(_FEATURE_FLAG_ENV, "1") not in ("0", "false", "False", "")


class SkiplaggedAdapter(BaseSearchAdapter):
    """Adapter síncrono — roda em ThreadPoolExecutor do pipeline. Falha
    do Skiplagged não pode degradar resultados de milhas, portanto qualquer
    erro é absorvido (lista vazia + log)."""

    def search(
        self,
        request: SearchRequest,
        use_fixtures: bool = False,
        debug_dump: bool = False,
    ) -> List[UnifiedOffer]:
        if not _is_enabled():
            return []

        if config.PCD_OFFLINE:
            # Coerência com demais adapters
            raise OfflineModeError("Skiplagged")

        origin = request.origin[0].upper()
        destination = request.destination[0].upper()
        date_ = request.date_start.isoformat()
        adults = request.adults or 1

        cache_params = {
            "o": origin,
            "d": destination,
            "date": date_,
            "adults": adults,
        }
        cache_key = _cache.make_key("skiplagged", cache_params)
        hit = _cache.get(cache_key)
        if hit is not None:
            raw = hit
        else:
            t0 = time.perf_counter()
            try:
                raw = fetch_skiplagged(origin, destination, date_, adults=adults)
            except Exception as e:
                # Skiplagged nunca derruba o pipeline
                print(f"[skiplagged_adapter] fetch error: {e}")
                return []
            elapsed = (time.perf_counter() - t0) * 1000.0
            if debug_dump:
                print(f"[skiplagged_adapter] fetch {origin}->{destination} {date_} em {elapsed:.0f}ms")
            if raw is not None:
                _cache.set_(cache_key, raw)

        if not raw:
            return []

        try:
            offers = skiplagged_extract(
                raw,
                requested_origin=origin,
                requested_destination=destination,
                trip_type=TripType.ROUNDTRIP if request.return_start else TripType.ONEWAY,
            )
        except Exception as e:
            print(f"[skiplagged_adapter] parse error: {e}")
            return []

        return offers
