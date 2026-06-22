"""SeatsAeroAdapter — busca award availability via Partner API do seats.aero.

Multi-programa como o Economilhas: UMA chamada a /search cobre vários programas
(`sources=aeroplan,lifemiles,flyingblue,...`). Cada programa vira um UnifiedOffer
com seu `miles_program` próprio — entra no ranking unificado cego à fonte.

PILOTO (2026-06): só Aeroplan, Lifemiles e Flying Blue habilitados por padrão
(override via env `SEATS_AERO_SOURCES`). Demais programas estão em PROGRAM_DISPLAY
e podem ser ligados sem código.

Auth: API key estática (`Partner-Authorization`) — magic-link do site é irrelevante.
Sem `SEATS_AERO_API_KEY` (ou `SEATS_AERO_ENABLED=0`) → retorna [] sem erro.

Quota Pro: 1000 chamadas/dia/key. Cada busca gasta 1 /search (+1 na volta, se RT)
e até `SEATS_AERO_MAX_TRIPS` chamadas /trips (default 8). Cache de 180s reduz
repetição. Erros nunca derrubam o pipeline (try/except amplo → []).
"""
from __future__ import annotations

import os
from typing import Dict, List, Optional

from backend.app.domain.errors import OfflineModeError
from backend.app.domain.models import SearchRequest, UnifiedOffer
from backend.app.infrastructure.config import config
from backend.app.providers.base import BaseSearchAdapter
from backend.app.providers.seats_aero.client import (
    SeatsAeroAuthError,
    SeatsAeroError,
    SeatsAeroQuotaExceeded,
    get_trip,
    search_availability,
)
from backend.app.providers.seats_aero.parser import (
    Availability,
    build_oneway_offer,
    build_roundtrip_offer,
    itinerary_from_trip,
    parse_availabilities,
    synthetic_itinerary,
)

# Mapa: source code do seats.aero → label exibida em offer.miles_program.
# A label precisa conter uma chave do rates.json p/ resolver o R$/milha:
#   "Aeroplan (Air Canada)" → AIR CANADA · "Lifemiles (Avianca)" → LIFEMILES
#   "Flying Blue (AF/KLM)"  → FLYING BLUE
PROGRAM_DISPLAY: Dict[str, str] = {
    "aeroplan":       "Aeroplan (Air Canada)",
    "lifemiles":      "Lifemiles (Avianca)",
    "flyingblue":     "Flying Blue (AF/KLM)",
    "qatar":          "Qatar Privilege Club",
    "alaska":         "Atmos (Alaska MileagePlan)",
    "finnair":        "Finnair Plus",
    "aeromexico":     "Aeromexico Club Premier",
    "connectmiles":   "Copa ConnectMiles",
    "iberia":         "Iberia Avios",
    "britishairways": "British Avios",
}

# Programas habilitados no piloto. Override: SEATS_AERO_SOURCES="aeroplan,qatar,...".
_DEFAULT_SOURCES = ["aeroplan", "lifemiles", "flyingblue"]

_CABIN_MAP = {"economy": "economy", "business": "business", "first": "first"}


def _enabled() -> bool:
    if os.getenv("SEATS_AERO_ENABLED", "1") in ("0", "false", "False", ""):
        return False
    return bool(os.getenv("SEATS_AERO_API_KEY"))


def _sources() -> List[str]:
    env = os.getenv("SEATS_AERO_SOURCES", "")
    if env.strip():
        return [s.strip().lower() for s in env.split(",") if s.strip()]
    return list(_DEFAULT_SOURCES)


def _max_trips() -> int:
    try:
        return max(1, int(os.getenv("SEATS_AERO_MAX_TRIPS", "8")))
    except ValueError:
        return 8


def _cheapest_per_source(avails: List[Availability]) -> Dict[str, Availability]:
    """Mais barata (menos milhas) por programa."""
    best: Dict[str, Availability] = {}
    for a in avails:
        cur = best.get(a.source)
        if cur is None or a.miles < cur.miles:
            best[a.source] = a
    return best


class SeatsAeroAdapter(BaseSearchAdapter):
    """Adapter síncrono — roda dentro do ThreadPoolExecutor do orchestrator.

    Falha é absorvida (retorna []) para uma indisponibilidade do seats.aero
    nunca degradar o resto da busca.
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
            raise OfflineModeError("seats.aero")

        cabin = _CABIN_MAP.get((request.cabin.value if request.cabin else "economy"), "economy")
        sources = _sources()
        origin = request.origin[0]
        destination = request.destination[0]
        depart = request.date_start.isoformat()
        return_date = request.return_start.isoformat() if request.return_start else None

        try:
            if return_date:
                return self._roundtrip(
                    origin, destination, depart, return_date, sources, cabin,
                    request.direct_only,
                )
            return self._oneway(
                origin, destination, depart, sources, cabin, request.direct_only,
            )
        except SeatsAeroAuthError as e:
            print(f"[seats_aero] auth: {str(e)[:200]}")
            return []
        except (SeatsAeroQuotaExceeded, SeatsAeroError) as e:
            print(f"[seats_aero] {type(e).__name__}: {str(e)[:200]}")
            return []
        except Exception as e:  # pipeline nunca cai por causa de um provider
            print(f"[seats_aero] unexpected: {e}")
            return []

    # ── one-way ──────────────────────────────────────────────────
    def _oneway(
        self, origin: str, destination: str, depart: str,
        sources: List[str], cabin: str, direct_only: bool,
    ) -> List[UnifiedOffer]:
        raw = search_availability(origin, destination, depart, sources,
                                  cabin=cabin, only_direct=direct_only)
        avails = parse_availabilities(raw, cabin)
        if direct_only:
            avails = [a for a in avails if a.direct]
        best = _cheapest_per_source(avails)

        offers: List[UnifiedOffer] = []
        budget = _max_trips()
        for source, avail in sorted(best.items(), key=lambda kv: kv[1].miles):
            if budget <= 0:
                break
            budget -= 1
            label = PROGRAM_DISPLAY.get(source, source.title())
            itin, miles, carrier, approx = self._enrich(avail, cabin)
            offers.append(build_oneway_offer(
                itin, miles, carrier, label, approximate_times=approx,
            ))
        return offers

    # ── round-trip (pareia ida+volta mais baratas por programa) ──
    def _roundtrip(
        self, origin: str, destination: str, depart: str, return_date: str,
        sources: List[str], cabin: str, direct_only: bool,
    ) -> List[UnifiedOffer]:
        out_raw = search_availability(origin, destination, depart, sources,
                                      cabin=cabin, only_direct=direct_only)
        in_raw = search_availability(destination, origin, return_date, sources,
                                     cabin=cabin, only_direct=direct_only)
        out_avails = parse_availabilities(out_raw, cabin)
        in_avails = parse_availabilities(in_raw, cabin)
        if direct_only:
            out_avails = [a for a in out_avails if a.direct]
            in_avails = [a for a in in_avails if a.direct]
        out_best = _cheapest_per_source(out_avails)
        in_best = _cheapest_per_source(in_avails)

        # Só programas com disponibilidade nas DUAS pontas viram roundtrip.
        common = set(out_best) & set(in_best)
        offers: List[UnifiedOffer] = []
        budget = _max_trips()
        ordered = sorted(common, key=lambda s: out_best[s].miles + in_best[s].miles)
        for source in ordered:
            if budget < 2:   # cada RT gasta 2 /trips
                break
            budget -= 2
            label = PROGRAM_DISPLAY.get(source, source.title())
            o_itin, o_miles, o_carrier, o_approx = self._enrich(out_best[source], cabin)
            i_itin, i_miles, _i_carrier, i_approx = self._enrich(in_best[source], cabin)
            offers.append(build_roundtrip_offer(
                o_itin, o_miles, i_itin, i_miles, o_carrier, label,
                approximate_times=o_approx or i_approx,
            ))
        return offers

    # ── enriquecimento via /trips (com fallback sintético) ───────
    def _enrich(self, avail: Availability, cabin: str):
        """Tenta /trips para horários reais; cai p/ itinerário sintético."""
        try:
            trip_raw = get_trip(avail.id)
            result = itinerary_from_trip(trip_raw, cabin, avail)
            if result is not None:
                itin, miles, carrier = result
                return itin, miles, carrier, False
        except (SeatsAeroError, Exception) as e:
            print(f"[seats_aero] trip {avail.id} falhou: {str(e)[:150]}")
        itin, miles, carrier = synthetic_itinerary(avail)
        return itin, miles, carrier, True
