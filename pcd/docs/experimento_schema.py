import os
import sys
# Ajuste do path para rodar pcd.core.schema
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from datetime import date, datetime, timezone
from pcd.core.schema import (
    TripType, CabinClass, LayoverCategory, SourceType,
    SearchRequest, Segment, Itinerary, UnifiedOffer
)

def format_offer(o: UnifiedOffer) -> str:
    res = []
    res.append(f"Fonte: {o.source.value} | Companhia: {o.airline} | Trip: {o.trip_type.value}")
    if o.price_brl is not None:
         res.append(f"Preço: R$ {o.price_brl:.2f}")
    if o.miles is not None:
         res.append(f"Preço: {o.miles} milhas + R$ {o.taxes_brl:.2f} (Taxas)")
         
    res.append(f"Ida: {o.outbound.segments[0].origin} -> {o.outbound.segments[-1].destination} ({o.layover_out.value} | Stops: {o.stops_out})")
    if o.inbound:
        res.append(f"Volta: {o.inbound.segments[0].origin} -> {o.inbound.segments[-1].destination} ({o.layover_in.value} | Stops: {o.stops_in})")
    return "\n".join(res)

print("=== INSTANCIANDO O SEARCH REQUEST ===")
req = SearchRequest(
    origin=["GRU", "CGH", "VCP"],
    destination=["MIA"],
    date_start=date(2026, 4, 15),
    date_end=date(2026, 4, 15),
    trip_type=TripType.ONEWAY,
    adults=2,
    cabin=CabinClass.ECONOMY,
    baggage_checked=True,
    flex_days=3
)
print("Request:", req.model_dump())

print("\n=== EXEMPLO 1: KAYAK EM BRL (DINHEIRO) ===")
seg1 = Segment(
    origin="GRU",
    destination="MIA",
    departure_dt=datetime(2026, 4, 15, 23, 30, tzinfo=timezone.utc),
    arrival_dt=datetime(2026, 4, 16, 7, 10, tzinfo=timezone.utc),
    carrier="AA",
    flight_number="AA906"
)

itin1 = Itinerary(segments=[seg1], duration_min=520)

oferta_dinheiro = UnifiedOffer(
    source=SourceType.KAYAK,
    airline="American Airlines",
    trip_type=TripType.ONEWAY,
    outbound=itin1,
    layover_out=LayoverCategory.DIRECT,
    price_brl=3540.50,
    deeplink="https://kayak.com/example"
)
print(format_offer(oferta_dinheiro))


print("\n=== EXEMPLO 2: MOBLIX LATAM EM MILHAS (ROUNDTRIP) ===")
# IDA (Com conexão)
seg_ida1 = Segment(
    origin="GRU",
    destination="LIM",
    departure_dt=datetime(2026, 5, 10, 8, 0, tzinfo=timezone.utc),
    arrival_dt=datetime(2026, 5, 10, 11, 30, tzinfo=timezone.utc),
    carrier="LA",
)
seg_ida2 = Segment(
    origin="LIM",
    destination="MIA",
    departure_dt=datetime(2026, 5, 10, 13, 0, tzinfo=timezone.utc),
    arrival_dt=datetime(2026, 5, 10, 19, 30, tzinfo=timezone.utc),
    carrier="LA",
)
itin_ida = Itinerary(segments=[seg_ida1, seg_ida2], duration_min=690)

# VOLTA Direta
seg_volta = Segment(
    origin="MIA",
    destination="GRU",
    departure_dt=datetime(2026, 5, 20, 20, 0, tzinfo=timezone.utc),
    arrival_dt=datetime(2026, 5, 21, 5, 30, tzinfo=timezone.utc),
    carrier="LA",
)
itin_volta = Itinerary(segments=[seg_volta], duration_min=510)


oferta_milhas = UnifiedOffer(
    source=SourceType.MOBLIX_LATAM,
    airline="LATAM Airlines",
    trip_type=TripType.ROUNDTRIP,
    outbound=itin_ida,
    inbound=itin_volta,
    layover_out=LayoverCategory.CONNECTION,
    layover_in=LayoverCategory.DIRECT,
    miles=85500,
    taxes_brl=450.75,
    deeplink="https://apidevoos.dev/booking/example"
)
print(format_offer(oferta_milhas))
