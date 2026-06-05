"""Testes do radar de datas (Kayak first, fallback milhas)."""
from datetime import date, datetime

import backend.app.services.date_radar as radar
from backend.app.domain.models import (
    Itinerary, Segment, SourceType, TripType, UnifiedOffer,
)


def _offer(price=None, equiv=None, miles=None) -> UnifiedOffer:
    seg = Segment(
        origin="BSB", destination="SSA", carrier="G3",
        departure_dt=datetime(2099, 9, 10, 8, 0),
        arrival_dt=datetime(2099, 9, 10, 10, 0),
    )
    return UnifiedOffer(
        source=SourceType.KAYAK, airline="GOL", trip_type=TripType.ONEWAY,
        outbound=Itinerary(segments=[seg]),
        price_brl=price, miles=miles, equivalent_brl=equiv,
    )


def test_radar_ranks_by_cash(monkeypatch):
    def fake(adapter_cls, req):
        price = 600.0 if req.date_start.day == 11 else 800.0
        return [_offer(price=price)]
    monkeypatch.setattr(radar, "_safe_search", fake)
    pairs = [(date(2099, 9, 10), date(2099, 9, 25)), (date(2099, 9, 11), date(2099, 9, 25))]
    res = radar.scan_dates(pairs, origin="BSB", destination="SSA")
    assert res.source == "kayak"
    assert res.ranked_pairs[0] == (date(2099, 9, 11), date(2099, 9, 25))


def test_radar_fallback_to_miles(monkeypatch):
    def fake(adapter_cls, req):
        if adapter_cls.__name__ == "KayakAdapter":
            return []  # Kayak não cobre a rota
        return [_offer(miles=8500, equiv=300.0 if req.date_start.day == 10 else 400.0)]
    monkeypatch.setattr(radar, "_safe_search", fake)
    pairs = [(date(2099, 9, 10), date(2099, 9, 15)), (date(2099, 9, 12), date(2099, 9, 17))]
    res = radar.scan_dates(pairs, origin="BSB", destination="SSA")
    assert res.source == "miles_sample"
    assert res.ranked_pairs[0][0] == date(2099, 9, 10)


def test_radar_empty_when_nothing(monkeypatch):
    monkeypatch.setattr(radar, "_safe_search", lambda a, r: [])
    pairs = [(date(2099, 9, 10), date(2099, 9, 15))]
    res = radar.scan_dates(pairs, origin="BSB", destination="SSA")
    assert res.source == "none"
