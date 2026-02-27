import unittest
from datetime import datetime
from pcd.core.schema import UnifiedOffer, TripType, SourceType, Itinerary, Segment, LayoverCategory
from pcd.core.layover_classifier import classify_offer, classify_many

class TestLayoverClassifier(unittest.TestCase):

    def _make_dummy_segment(self, carrier="LA"):
        return Segment(
            origin="GRU",
            destination="MIA",
            departure_dt=datetime(2025, 5, 1, 10, 0),
            arrival_dt=datetime(2025, 5, 1, 18, 0),
            carrier=carrier
        )

    def test_direct_flight(self):
        # Offer com voo direto (1 segmento)
        outbound = Itinerary(segments=[self._make_dummy_segment()])
        
        offer = UnifiedOffer(
            source=SourceType.KAYAK,
            airline="LATAM",
            trip_type=TripType.ONEWAY,
            outbound=outbound,
            price_brl=1500.0,
            # Passando dummy inicial apenas pra validar que classify_offer forçará o valor correto 
            layover_out=LayoverCategory.CONNECTION 
        )
        
        # Action
        classified = classify_offer(offer)
        
        # Validations
        self.assertEqual(classified.stops_out, 0)
        self.assertEqual(classified.layover_out, LayoverCategory.DIRECT)

    def test_connection_flight(self):
        # Offer com conexão (2 segmentos)
        outbound = Itinerary(segments=[self._make_dummy_segment(), self._make_dummy_segment("AA")])
        
        offer = UnifiedOffer(
            source=SourceType.KAYAK,
            airline="LATAM",
            trip_type=TripType.ONEWAY,
            outbound=outbound,
            price_brl=2500.0,
            # Passando dummy default 
            layover_out=LayoverCategory.DIRECT
        )

        classified = classify_offer(offer)

        self.assertEqual(classified.stops_out, 1) # pcd.core.schema.Itinerary deriva stops de len(segments) - 1
        self.assertEqual(classified.layover_out, LayoverCategory.CONNECTION)

    def test_classify_many(self):
        out1 = Itinerary(segments=[self._make_dummy_segment()])
        out2 = Itinerary(segments=[self._make_dummy_segment(), self._make_dummy_segment()])
        
        o1 = UnifiedOffer(source=SourceType.MOBLIX_LATAM, airline="LA", trip_type=TripType.ONEWAY, outbound=out1, miles=10000, layover_out=LayoverCategory.CONNECTION)
        o2 = UnifiedOffer(source=SourceType.KAYAK, airline="LA", trip_type=TripType.ONEWAY, outbound=out2, price_brl=2000.0, layover_out=LayoverCategory.DIRECT)

        # Pre-assign fake layovers
        o1.layover_out = LayoverCategory.CONNECTION
        o2.layover_out = LayoverCategory.DIRECT

        classified = classify_many([o1, o2])
        self.assertEqual(classified[0].layover_out, LayoverCategory.DIRECT)
        self.assertEqual(classified[1].layover_out, LayoverCategory.CONNECTION)

if __name__ == "__main__":
    unittest.main()
