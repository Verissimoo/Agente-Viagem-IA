"""Testa o parser do seats.aero: availability + trips → UnifiedOffer.

Cobre:
  - parse_availabilities filtra cabine/assento/milhas e normaliza campos.
  - itinerary_from_trip monta segmentos com horário real do /trips.
  - build_oneway_offer / build_roundtrip_offer produzem UnifiedOffer válido,
    com source SEATS_AERO, miles_program que resolve no rates.json, taxa None.
  - synthetic_itinerary serve de fallback quando /trips não tem segmentos.
"""
from backend.app.domain.models import Scenario, SourceType, TripType
from backend.app.providers.seats_aero.parser import (
    build_oneway_offer,
    build_roundtrip_offer,
    itinerary_from_trip,
    parse_availabilities,
    synthetic_itinerary,
)
from backend.app.services.conversion import offer_equivalent_brl

_RAW_SEARCH = {
    "data": [
        {
            "ID": "av-aeroplan-1",
            "Route": {"OriginAirport": "GRU", "DestinationAirport": "YYZ", "Source": "aeroplan"},
            "Date": "2026-07-15",
            "YAvailable": True, "YMileageCost": "60000", "YDirect": True, "YAirlines": "AC",
        },
        {
            "ID": "av-lifemiles-1",
            "Route": {"OriginAirport": "GRU", "DestinationAirport": "YYZ", "Source": "lifemiles"},
            "Date": "2026-07-15",
            "YAvailable": True, "YMileageCost": 45000, "YDirect": False, "YAirlines": ["AV", "CM"],
        },
        {  # sem assento → ignorado
            "ID": "av-x", "Route": {"OriginAirport": "GRU", "DestinationAirport": "YYZ", "Source": "flyingblue"},
            "Date": "2026-07-15", "YAvailable": False, "YMileageCost": 30000,
        },
        {  # milhas 0 → ignorado
            "ID": "av-y", "Route": {"OriginAirport": "GRU", "DestinationAirport": "YYZ", "Source": "qatar"},
            "Date": "2026-07-15", "YAvailable": True, "YMileageCost": 0,
        },
    ]
}

_RAW_TRIP = {
    "data": [
        {
            "MileageCost": 60000, "Cabin": "economy", "TotalTaxes": 12000, "Stops": 0,
            "AvailabilitySegments": [
                {
                    "FlightNumber": "AC091", "OriginAirport": "GRU", "DestinationAirport": "YYZ",
                    "DepartsAt": "2026-07-15T22:10:00Z", "ArrivesAt": "2026-07-16T06:30:00Z",
                },
            ],
        },
    ]
}


def test_parse_availabilities_filters_and_normalizes():
    avails = parse_availabilities(_RAW_SEARCH, "economy")
    sources = {a.source for a in avails}
    assert sources == {"aeroplan", "lifemiles"}
    aeroplan = next(a for a in avails if a.source == "aeroplan")
    assert aeroplan.miles == 60000
    assert aeroplan.direct is True
    assert aeroplan.airlines == ["AC"]
    lifemiles = next(a for a in avails if a.source == "lifemiles")
    assert lifemiles.miles == 45000
    assert lifemiles.airlines == ["AV", "CM"]


def test_itinerary_from_trip_builds_segments():
    avail = parse_availabilities(_RAW_SEARCH, "economy")[0]
    result = itinerary_from_trip(_RAW_TRIP, "economy", avail)
    assert result is not None
    itin, miles, carrier = result
    assert miles == 60000
    assert carrier == "AC"
    assert len(itin.segments) == 1
    seg = itin.segments[0]
    assert seg.origin == "GRU" and seg.destination == "YYZ"
    assert seg.carrier == "AC"
    assert seg.departure_dt.year == 2026 and seg.departure_dt.hour == 22


def test_build_oneway_offer_is_valid_and_priced():
    avail = next(a for a in parse_availabilities(_RAW_SEARCH, "economy") if a.source == "aeroplan")
    itin, miles, carrier = itinerary_from_trip(_RAW_TRIP, "economy", avail)
    offer = build_oneway_offer(itin, miles, carrier, "Aeroplan (Air Canada)")
    assert offer.source == SourceType.SEATS_AERO
    assert offer.trip_type == TripType.ONEWAY
    assert offer.miles == 60000
    assert offer.miles_program == "Aeroplan (Air Canada)"
    assert offer.taxes_brl is None
    assert offer.scenario == Scenario.MILES_DIRECT
    # "Aeroplan (Air Canada)" resolve "AIR CANADA" no rates.json → equivalent_brl > 0
    assert offer_equivalent_brl(offer) > 0


def test_build_roundtrip_offer_sums_miles():
    avails = parse_availabilities(_RAW_SEARCH, "economy")
    itin, miles, carrier = itinerary_from_trip(_RAW_TRIP, "economy", avails[0])
    offer = build_roundtrip_offer(itin, 60000, itin, 50000, carrier, "Aeroplan (Air Canada)")
    assert offer.trip_type == TripType.ROUNDTRIP
    assert offer.inbound is not None
    assert offer.miles == 110000
    assert offer.miles_out == 60000 and offer.miles_in == 50000


def test_seats_aero_base_rate_is_005_for_all_programs():
    # Por ora todos os programas do seats.aero usam a tarifa base 0.05
    # (international_fallback), independente da label/airline da oferta.
    from backend.app.domain.models import SourceType as _ST
    from backend.app.services.conversion import cost_per_mile
    for label in ("Aeroplan (Air Canada)", "Lifemiles (Avianca)", "Flying Blue (AF/KLM)"):
        assert cost_per_mile(program=label, source=_ST.SEATS_AERO, miles=60000) == 0.05


def test_synthetic_itinerary_fallback():
    avail = parse_availabilities(_RAW_SEARCH, "economy")[0]
    itin, miles, carrier = synthetic_itinerary(avail)
    assert len(itin.segments) == 1
    assert itin.segments[0].origin == "GRU"
    assert miles == avail.miles
