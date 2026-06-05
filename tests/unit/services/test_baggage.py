"""Testes da regra de bagagem despachada (services/baggage.py)."""
from datetime import datetime

from backend.app.domain.models import (
    Itinerary,
    Scenario,
    Segment,
    SourceType,
    TripType,
    UnifiedOffer,
)
from backend.app.services.baggage import (
    ADDABLE,
    NOT_ALLOWED,
    SMILES_DOMESTIC_BAG_BRL,
    UNKNOWN,
    baggage_for_row,
    baggage_from_dict,
)


def _seg(o: str, d: str, carrier: str = "G3") -> Segment:
    return Segment(
        origin=o, destination=d, carrier=carrier,
        departure_dt=datetime(2026, 7, 20, 10, 0),
        arrival_dt=datetime(2026, 7, 20, 12, 0),
    )


def _offer(segments, **kw) -> UnifiedOffer:
    base = dict(
        source=SourceType.BUSCAMILHAS_GOL,
        airline="GOL",
        trip_type=TripType.ONEWAY,
        outbound=Itinerary(segments=segments),
    )
    base.update(kw)
    return UnifiedOffer(**base)


def test_hidden_city_not_allowed():
    offer = _offer(
        [_seg("BSB", "SSA"), _seg("SSA", "CNF")],
        scenario=Scenario.HIDDEN_CITY, price_brl=277.0,
    )
    info = baggage_for_row(offer, offer.outbound, "IDA", real_cost=277.0)
    assert info.status == NOT_ALLOWED
    assert info.extra_brl is None


def test_smiles_domestic_flat_fee():
    offer = _offer(
        [_seg("BSB", "CNF")],
        scenario=Scenario.MILES_DIRECT, miles=8500, miles_out=8500,
        taxes_brl=33.0, equivalent_brl=201.0,
    )
    info = baggage_for_row(offer, offer.outbound, "IDA", real_cost=201.0)
    assert info.status == ADDABLE
    assert info.extra_brl == SMILES_DOMESTIC_BAG_BRL


def test_international_unknown_when_no_data():
    offer = _offer(
        [_seg("GRU", "LIS", carrier="TP")],
        source=SourceType.BUSCAMILHAS_TAP, airline="TAP",
        scenario=Scenario.CASH_DIRECT, price_brl=3000.0,
    )
    info = baggage_for_row(offer, offer.outbound, "IDA", real_cost=3000.0)
    assert info.status == UNKNOWN
    assert info.certain is False


def test_real_data_uses_baggage_tier():
    offer = _offer(
        [_seg("GRU", "GIG", carrier="LA")],
        source=SourceType.BUSCAMILHAS_LATAM, airline="LATAM",
        scenario=Scenario.MILES_DIRECT, miles=10000, miles_out=10000,
        baggage_miles_out=13000, taxes_brl=50.0, equivalent_brl=350.0,
    )
    info = baggage_for_row(offer, offer.outbound, "IDA", real_cost=350.0)
    assert info.status == ADDABLE
    assert info.extra_miles == 3000


def test_dict_variant_matches_hidden_city():
    d = {
        "scenario": "hidden_city", "airline": "GOL", "price_brl": 277,
        "outbound": {"segments": [
            {"origin": "BSB", "destination": "SSA", "carrier": "G3"},
            {"origin": "SSA", "destination": "CNF", "carrier": "G3"},
        ]},
    }
    assert baggage_from_dict(d).status == NOT_ALLOWED
