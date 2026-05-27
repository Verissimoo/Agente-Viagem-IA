from datetime import date, timedelta

from backend.app.domain.models import (
    Itinerary,
    Segment,
    SourceType,
    TripType,
    UnifiedOffer,
)
from backend.app.services.flex_dates import compute_best_day, expand_dates


def test_expand_dates_filters_past():
    """`expand_dates` nunca devolve datas anteriores a hoje."""
    base = date.today() + timedelta(days=30)
    res = expand_dates(base, 3)
    assert len(res) == 7
    assert res[0] == base - timedelta(days=3)
    assert res[-1] == base + timedelta(days=3)


def test_expand_dates_zero_flex_returns_single():
    base = date.today() + timedelta(days=10)
    assert expand_dates(base, 0) == [base]


def test_compute_best_day_picks_min_equivalent_brl():
    future = date.today() + timedelta(days=30)

    def make_offer(dep_date, price):
        dt = __import__("datetime").datetime.combine(dep_date, __import__("datetime").datetime.min.time())
        seg = Segment(origin="BSB", destination="GRU", departure_dt=dt, arrival_dt=dt, carrier="LA")
        return UnifiedOffer(
            source=SourceType.KAYAK,
            airline="LATAM",
            trip_type=TripType.ONEWAY,
            outbound=Itinerary(segments=[seg]),
            price_brl=price,
            equivalent_brl=price,
        )

    offers = [
        make_offer(future - timedelta(days=1), 500.0),
        make_offer(future,                     400.0),
        make_offer(future + timedelta(days=1), 600.0),
    ]

    best_date, best_val, _, date_map, counts_map = compute_best_day(offers)
    assert best_date == future
    assert best_val == 400.0
    assert date_map[future.isoformat()] == 400.0
    assert counts_map[future.isoformat()] == 1
