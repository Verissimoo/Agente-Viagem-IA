"""Skiplagged parser tests — synthetic payload mirroring the real `/api/search.php` shape."""
from backend.app.domain.models import Scenario, SourceType, TripType
from backend.app.providers.skiplagged.parser import extract_offers


def _payload(itineraries: list, flights: dict) -> dict:
    return {
        "airlines": {},
        "cities": {},
        "airports": {},
        "flights": flights,
        "itineraries": {"outbound": itineraries},
        "info": {},
    }


_DIRECT_FLIGHT = {
    "abc123": {
        "segments": [
            {
                "airline": "G3",
                "flight_number": 1588,
                "departure": {"airport": "GIG", "time": "2026-07-15T08:00:00-03:00"},
                "arrival": {"airport": "SSA", "time": "2026-07-15T10:30:00-03:00"},
                "duration": 9000,
            }
        ],
        "duration": 9000,
        "count": 1,
    }
}

_SPLIT_FLIGHT = {
    "splt": {
        "segments": [
            {
                "airline": "LA",
                "flight_number": 3201,
                "departure": {"airport": "GIG", "time": "2026-07-15T06:00:00-03:00"},
                "arrival": {"airport": "GRU", "time": "2026-07-15T07:00:00-03:00"},
                "duration": 3600,
            },
            {
                "airline": "LA",
                "flight_number": 3450,
                "departure": {"airport": "GRU", "time": "2026-07-15T10:00:00-03:00"},
                "arrival": {"airport": "SSA", "time": "2026-07-15T12:30:00-03:00"},
                "duration": 9000,
            },
        ],
        "duration": 23400,
        "count": 2,
    }
}

# Hidden city: ticket goes GIG → SSA → BSB. Requested dest is SSA — passenger
# deplanes at SSA and discards the BSB leg.
_HIDDEN_FLIGHT = {
    "hide": {
        "segments": [
            {
                "airline": "AD",
                "flight_number": 4501,
                "departure": {"airport": "GIG", "time": "2026-07-15T08:00:00-03:00"},
                "arrival": {"airport": "SSA", "time": "2026-07-15T10:30:00-03:00"},
                "duration": 9000,
            },
            {
                "airline": "AD",
                "flight_number": 4502,
                "departure": {"airport": "SSA", "time": "2026-07-15T12:00:00-03:00"},
                "arrival": {"airport": "BSB", "time": "2026-07-15T14:00:00-03:00"},
                "duration": 7200,
            },
        ],
        "duration": 21600,
        "count": 2,
    }
}


def test_extract_direct_cash_offer():
    raw = _payload(
        itineraries=[{"flight": "abc123", "one_way_price": 35730}],
        flights=_DIRECT_FLIGHT,
    )
    offers = extract_offers(raw, requested_origin="GIG", requested_destination="SSA")
    assert len(offers) == 1
    o = offers[0]
    assert o.source == SourceType.SKIPLAGGED
    assert o.scenario == Scenario.CASH_DIRECT
    assert o.layover_city is None
    assert o.price_currency == "USD"
    assert o.price_amount == 357.30
    assert o.outbound.duration_min == 150


def test_extract_split_cash_offer():
    raw = _payload(
        itineraries=[{"flight": "splt", "one_way_price": 38200}],
        flights=_SPLIT_FLIGHT,
    )
    offers = extract_offers(raw, requested_origin="GIG", requested_destination="SSA")
    assert len(offers) == 1
    assert offers[0].scenario == Scenario.SPLIT_CASH
    assert offers[0].layover_city is None


def test_extract_hidden_city_offer():
    """Detected by requested-dest appearing as an intermediate stop."""
    raw = _payload(
        itineraries=[{"flight": "hide", "one_way_price": 29000}],
        flights=_HIDDEN_FLIGHT,
    )
    offers = extract_offers(raw, requested_origin="GIG", requested_destination="SSA")
    assert len(offers) == 1
    o = offers[0]
    assert o.scenario == Scenario.HIDDEN_CITY
    assert o.layover_city == "SSA"
    assert o.risk_notes and "hidden city" in o.risk_notes.lower()
    assert o.trip_type == TripType.ONEWAY  # hidden city is always one-way


def test_extract_mixed_payload():
    """A real `/api/search.php` response mixes regular and hidden city."""
    raw = _payload(
        itineraries=[
            {"flight": "abc123", "one_way_price": 35730},
            {"flight": "splt",   "one_way_price": 38200},
            {"flight": "hide",   "one_way_price": 29000},
        ],
        flights={**_DIRECT_FLIGHT, **_SPLIT_FLIGHT, **_HIDDEN_FLIGHT},
    )
    offers = extract_offers(raw, requested_origin="GIG", requested_destination="SSA")
    assert len(offers) == 3
    scenarios = {o.scenario for o in offers}
    assert scenarios == {Scenario.CASH_DIRECT, Scenario.SPLIT_CASH, Scenario.HIDDEN_CITY}


def test_extract_empty_payload():
    assert extract_offers(None, requested_origin="GIG", requested_destination="SSA") == []
    assert extract_offers({}, requested_origin="GIG", requested_destination="SSA") == []
    assert extract_offers(
        _payload(itineraries=[], flights={}),
        requested_origin="GIG",
        requested_destination="SSA",
    ) == []


def test_extract_skips_entries_with_missing_flight_lookup():
    raw = _payload(
        itineraries=[
            {"flight": "ghost", "one_way_price": 30000},  # not in lookup
            {"flight": "abc123", "one_way_price": 28000},
        ],
        flights=_DIRECT_FLIGHT,
    )
    offers = extract_offers(raw, requested_origin="GIG", requested_destination="SSA")
    assert len(offers) == 1
    assert offers[0].price_amount == 280.0


def test_extract_skips_entries_without_price():
    raw = _payload(
        itineraries=[{"flight": "abc123"}],
        flights=_DIRECT_FLIGHT,
    )
    offers = extract_offers(raw, requested_origin="GIG", requested_destination="SSA")
    assert offers == []


def test_extract_roundtrip_falls_back_to_oneway():
    raw = _payload(
        itineraries=[{"flight": "abc123", "one_way_price": 25000}],
        flights=_DIRECT_FLIGHT,
    )
    offers = extract_offers(
        raw,
        requested_origin="GIG",
        requested_destination="SSA",
        trip_type=TripType.ROUNDTRIP,
    )
    assert offers[0].trip_type == TripType.ONEWAY


def test_hidden_city_is_always_oneway_even_when_caller_wants_roundtrip():
    """Hidden city is structurally one-way: the airline cancels the rest of
    the PNR when you skip the last segment. So even if the caller asked for
    round-trip, hidden city offers MUST be marked one-way."""
    raw = _payload(
        itineraries=[{"flight": "hide", "one_way_price": 29000}],
        flights=_HIDDEN_FLIGHT,
    )
    offers = extract_offers(
        raw,
        requested_origin="GIG",
        requested_destination="SSA",
        trip_type=TripType.ROUNDTRIP,
    )
    assert len(offers) == 1
    o = offers[0]
    assert o.scenario == Scenario.HIDDEN_CITY
    assert o.trip_type == TripType.ONEWAY
