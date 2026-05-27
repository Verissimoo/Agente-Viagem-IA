"""Testes do ranking — opera sobre UnifiedOffer já classificado."""
from datetime import datetime

import pytest

from backend.app.domain.models import (
    Itinerary,
    LayoverCategory,
    Segment,
    SourceType,
    TripType,
    UnifiedOffer,
)
from backend.app.services.layover_classifier import classify_many
from backend.app.services.ranking import rank_offers


def _seg(carrier: str = "LA") -> Segment:
    return Segment(
        origin="GRU",
        destination="MIA",
        departure_dt=datetime(2026, 7, 15, 10, 0),
        arrival_dt=datetime(2026, 7, 15, 18, 0),
        carrier=carrier,
    )


def _direct_itinerary() -> Itinerary:
    return Itinerary(segments=[_seg()])


def _conn_itinerary() -> Itinerary:
    return Itinerary(segments=[_seg(), _seg("AA")])


def test_miles_equivalence_uses_tiered_rate_table():
    """LATAM tier 1 (0-10k miles) @ 0.0350 BRL/milha + R$100 taxas em 10k = R$450."""
    offer = UnifiedOffer(
        source=SourceType.BUSCAMILHAS_LATAM,
        airline="LA",
        trip_type=TripType.ONEWAY,
        outbound=_direct_itinerary(),
        miles=10000,
        taxes_brl=100.0,
    )
    _, best, _ = rank_offers([offer])
    # 10000 * 0.0350 + 100 = 450
    assert best.equivalent_brl == pytest.approx(450.0)


def test_miles_equivalence_high_volume_tier():
    """At 100k miles LATAM moves to tier 3 (0.0260): cheaper per-mile rate."""
    offer = UnifiedOffer(
        source=SourceType.BUSCAMILHAS_LATAM,
        airline="LA",
        trip_type=TripType.ONEWAY,
        outbound=_direct_itinerary(),
        miles=100000,
        taxes_brl=100.0,
    )
    _, best, _ = rank_offers([offer])
    # 100000 * 0.0260 + 100 = 2700
    assert best.equivalent_brl == pytest.approx(2700.0)


def test_money_equivalence_is_price_brl():
    offer = UnifiedOffer(
        source=SourceType.KAYAK,
        airline="LA",
        trip_type=TripType.ONEWAY,
        outbound=_direct_itinerary(),
        price_brl=450.0,
    )
    _, best, _ = rank_offers([offer])
    assert best.equivalent_brl == pytest.approx(450.0)


def test_ranking_prefers_lower_equivalent_brl():
    direct = UnifiedOffer(
        source=SourceType.KAYAK,
        airline="LA",
        trip_type=TripType.ONEWAY,
        outbound=_direct_itinerary(),
        price_brl=500.0,
    )
    conn = UnifiedOffer(
        source=SourceType.KAYAK,
        airline="LA",
        trip_type=TripType.ONEWAY,
        outbound=_conn_itinerary(),
        price_brl=450.0,
    )

    classified = classify_many([direct, conn])
    top, best, _ = rank_offers(classified)

    assert best.price_brl == 450.0
    assert top[0] is conn
    assert top[1] is direct
