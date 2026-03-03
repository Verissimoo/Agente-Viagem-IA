from datetime import date
from pcd.core.flex_dates import expand_dates, compute_best_day
from pcd.core.schema import UnifiedOffer, Itinerary, Segment, SourceType, TripType, CabinClass
from datetime import datetime

def test_expand_dates():
    base = date(2026, 3, 20)
    # Mocking date.today to a fixed past date for consistency if needed, 
    # but since 2026 is far ahead, it's fine.
    res = expand_dates(base, 3)
    assert len(res) == 7
    assert res[0] == date(2026, 3, 17)
    assert res[-1] == date(2026, 3, 23)

def test_compute_best_day():
    # Helper to create a dummy offer
    def make_offer(dep_date, price, source=SourceType.KAYAK):
        dt = datetime.combine(dep_date, datetime.min.time())
        seg = Segment(origin="BSB", destination="GRU", departure_dt=dt, arrival_dt=dt, carrier="LA")
        it = Itinerary(segments=[seg])
        return UnifiedOffer(
            source=source,
            airline="LATAM",
            trip_type=TripType.ONEWAY,
            outbound=it,
            price_brl=price,
            equivalent_brl=price
        )

    offers = [
        make_offer(date(2026, 3, 19), 500.0),
        make_offer(date(2026, 3, 20), 400.0),
        make_offer(date(2026, 3, 21), 600.0),
    ]
    
    best_date, best_val, best_source, date_map, counts_map = compute_best_day(offers)
    
    assert best_date == date(2026, 3, 20)
    assert best_val == 400.0
    assert date_map["2026-03-20"] == 400.0
    assert counts_map["2026-03-20"] == 1

if __name__ == "__main__":
    test_expand_dates()
    test_compute_best_day()
    print("Tests passed!")
