"""Parses the Skiplagged `/api/search.php` payload into `UnifiedOffer`.

Payload shape:
    {
      "flights": {<flight_id>: {"segments": [...], "duration": <s>, "count": <n>}},
      "itineraries": {
        "outbound": [{"flight": <flight_id>, "one_way_price": <USD cents>}, ...]
      }
    }

Prices come in USD cents — converted to BRL via `fx_rates`.

Scenario detection:
- N segments, requested destination appears *before* the final segment
  → `HIDDEN_CITY` (passenger deplanes at the requested destination; ticket
  continues to a city beyond).
- 1 segment → `CASH_DIRECT`.
- N segments, final = requested destination → `SPLIT_CASH`.
"""
from __future__ import annotations

import os
from datetime import datetime
from typing import Any, Iterable, List, Optional

from backend.app.domain.models import (
    Itinerary,
    LayoverCategory,
    Scenario,
    Segment,
    SourceType,
    TripType,
    UnifiedOffer,
)
from backend.app.infrastructure import fx_rates
from backend.app.services.conversion import estimate_miles_for_brl

_HIDDEN_CITY_NOTE = (
    "Hidden City — só ida. Você desembarca na conexão e descarta os trechos "
    "restantes. A companhia cancela automaticamente o restante do PNR (incluindo "
    "qualquer volta). Sem bagagem despachada — vai para o destino oficial do "
    "bilhete. Use só com bagagem de mão e fora de programa de milhagem."
)


def _parse_dt(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            pass
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value if value < 10**12 else value / 1000.0)
        except (OSError, ValueError, OverflowError):
            pass
    return datetime.now()


def _coerce_brl(amount_usd: float) -> Optional[float]:
    try:
        return float(fx_rates.convert(amount_usd, "USD", "BRL"))
    except Exception:
        return amount_usd


def _segment_from_skiplagged(raw_seg: dict) -> Segment:
    dep = raw_seg.get("departure") or {}
    arr = raw_seg.get("arrival") or {}
    return Segment(
        origin=str(dep.get("airport") or "???").upper(),
        destination=str(arr.get("airport") or "???").upper(),
        departure_dt=_parse_dt(dep.get("time")),
        arrival_dt=_parse_dt(arr.get("time")),
        carrier=str(raw_seg.get("airline") or "UNK")[:3].upper(),
        flight_number=str(raw_seg.get("flight_number")) if raw_seg.get("flight_number") is not None else None,
    )


def _detect_scenario(
    segments: List[Segment],
    requested_destination: str,
) -> tuple[Scenario, Optional[str], Optional[str]]:
    """Returns (scenario, layover_city, risk_notes)."""
    dest = requested_destination.upper()
    if not segments:
        return Scenario.CASH_DIRECT, None, None

    final_dest = segments[-1].destination
    intermediates = [s.destination for s in segments[:-1]]

    # Hidden city: requested destination is a layover, ticket continues beyond.
    if dest in intermediates and final_dest != dest:
        return Scenario.HIDDEN_CITY, dest, _HIDDEN_CITY_NOTE

    if len(segments) == 1:
        return Scenario.CASH_DIRECT, None, None
    return Scenario.SPLIT_CASH, None, None


def _iter_itineraries(raw: dict) -> Iterable[tuple[dict, dict]]:
    """Yields (itinerary, flight_details) tuples, joined by flight_id."""
    itinerary_block = raw.get("itineraries") or {}
    flights_lookup = raw.get("flights") or {}
    outbound = itinerary_block.get("outbound") if isinstance(itinerary_block, dict) else None
    if not isinstance(outbound, list):
        return
    for it in outbound:
        if not isinstance(it, dict):
            continue
        flight = flights_lookup.get(it.get("flight"))
        if isinstance(flight, dict):
            yield it, flight


def _build_offer(
    itinerary: dict,
    flight: dict,
    *,
    requested_origin: str,
    requested_destination: str,
    trip_type: TripType,
) -> Optional[UnifiedOffer]:
    raw_segments = flight.get("segments") or []
    if not raw_segments:
        return None

    segments = [_segment_from_skiplagged(s) for s in raw_segments]

    duration_s = flight.get("duration")
    duration_min = int(duration_s // 60) if isinstance(duration_s, (int, float)) and duration_s > 0 else None
    outbound = Itinerary(segments=segments, duration_min=duration_min)

    price_cents = itinerary.get("one_way_price")
    if price_cents is None:
        return None
    price_usd = float(price_cents) / 100.0
    price_brl = _coerce_brl(price_usd)
    if price_brl is None:
        return None

    scenario, layover_city, risk = _detect_scenario(segments, requested_destination)

    # Hidden city is ALWAYS one-way regardless of caller intent.
    # Reasoning: hidden-city works by skipping the final segment. If you book
    # a round-trip ticket with a hidden-city outbound and don't show up at the
    # official destination, the airline auto-cancels every remaining segment
    # in the PNR (including the return). So a round-trip hidden-city ticket
    # is effectively useless. The buyer should purchase the return separately.
    effective_trip = TripType.ONEWAY if scenario == Scenario.HIDDEN_CITY else trip_type

    flight_id = itinerary.get("flight", "")
    deeplink = (
        f"https://skiplagged.com/flights/{requested_origin}/{requested_destination}/"
        f"{segments[0].departure_dt.date().isoformat()}#{flight_id}"
        if flight_id
        else ""
    )

    miles_equiv, miles_program, _ = estimate_miles_for_brl(price_brl)

    return UnifiedOffer(
        source=SourceType.SKIPLAGGED,
        airline=segments[0].carrier,
        trip_type=effective_trip,
        outbound=outbound,
        price_brl=price_brl,
        price_amount=price_usd,
        price_currency="USD",
        equivalent_brl=price_brl,
        deeplink=deeplink,
        layover_out=LayoverCategory.CONNECTION if len(segments) > 1 else LayoverCategory.DIRECT,
        scenario=scenario,
        layover_city=layover_city,
        risk_notes=risk,
        miles_equivalent=miles_equiv,
        miles_equivalent_program=miles_program,
    )


def extract_offers(
    raw: Optional[dict],
    *,
    requested_origin: str,
    requested_destination: str,
    trip_type: TripType = TripType.ONEWAY,
) -> List[UnifiedOffer]:
    """Converts Skiplagged raw payload into UnifiedOffer list."""
    if not raw or not isinstance(raw, dict):
        return []

    debug = os.getenv("SKIPLAGGED_PARSER_DEBUG") == "1"
    offers: List[UnifiedOffer] = []
    skipped = 0

    # Skiplagged outbound payload has no inbound leg.
    effective_trip = TripType.ONEWAY if trip_type == TripType.ROUNDTRIP else trip_type

    for itinerary, flight in _iter_itineraries(raw):
        try:
            offer = _build_offer(
                itinerary,
                flight,
                requested_origin=requested_origin,
                requested_destination=requested_destination,
                trip_type=effective_trip,
            )
            if offer is None:
                skipped += 1
                continue
            offers.append(offer)
        except Exception:
            skipped += 1

    if debug:
        scenarios: dict[str, int] = {}
        for o in offers:
            k = o.scenario.value if o.scenario else "none"
            scenarios[k] = scenarios.get(k, 0) + 1
        print(f"[skiplagged_parser] {len(offers)} offers ({scenarios}), {skipped} skipped")

    return offers
