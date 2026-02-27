import unittest
import os
from pcd.core.schema import UnifiedOffer, TripType, SourceType, Itinerary, Segment, LayoverCategory
from pcd.core.ranking import rank_offers

class TestRankingMVP(unittest.TestCase):

    def setUp(self):
        # Set explicitly for tests
        os.environ["COST_PER_MILE_BRL"] = "0.0285"
        os.environ["CONNECTION_PENALTY_BRL"] = "80.0"
        
    def tearDown(self):
        # Cleanup
        if "COST_PER_MILE_BRL" in os.environ: del os.environ["COST_PER_MILE_BRL"]
        if "CONNECTION_PENALTY_BRL" in os.environ: del os.environ["CONNECTION_PENALTY_BRL"]

    def _dummy_direct_outbound(self):
        from pcd.tests.test_layover_classifier import TestLayoverClassifier
        return Itinerary(segments=[TestLayoverClassifier()._make_dummy_segment()])

    def _dummy_conn_outbound(self):
        from pcd.tests.test_layover_classifier import TestLayoverClassifier
        helper = TestLayoverClassifier()
        return Itinerary(segments=[helper._make_dummy_segment(), helper._make_dummy_segment()])


    def test_miles_equivalence(self):
        # CPM = 0.0285, Taxes = 100
        # 10.000 * 0.0285 + 100 = 285 + 100 = 385.0
        offer = UnifiedOffer(
            source=SourceType.MOBLIX_LATAM, 
            airline="LA", 
            trip_type=TripType.ONEWAY, 
            outbound=self._dummy_direct_outbound(), 
            miles=10000,
            taxes_brl=100.0
        )
        # O validador do schema já preenche layover_out=DIRECT
        top, best, just = rank_offers([offer])
        self.assertAlmostEqual(best.equivalent_brl, 385.0)


    def test_money_equivalence(self):
        # Direct money offer = 450.0
        offer = UnifiedOffer(
            source=SourceType.KAYAK, 
            airline="LA", 
            trip_type=TripType.ONEWAY, 
            outbound=self._dummy_direct_outbound(), 
            price_brl=450.0
        )
        
        top, best, just = rank_offers([offer])
        self.assertAlmostEqual(best.equivalent_brl, 450.0)


    def test_connection_penalty_reordering(self):
        # Offer A: Dinheiro Direto. price = 500
        # Eq = 500
        offA = UnifiedOffer(
            source=SourceType.KAYAK, 
            airline="LA", 
            trip_type=TripType.ONEWAY, 
            outbound=self._dummy_direct_outbound(), 
            price_brl=500.0
        )
        
        # Offer B: Dinheiro Conexão. price = 450
        # Penalty = 80. Eq = 450 + 80 = 530
        offB = UnifiedOffer(
            source=SourceType.KAYAK, 
            airline="LA", 
            trip_type=TripType.ONEWAY, 
            outbound=self._dummy_conn_outbound(), 
            price_brl=450.0
        )
        
        # rank_offers internamente chama o classificador ou o validador já resolveu
        top, best, just = rank_offers([offA, offB])
        
        # Apesar da A ser mais cara o nominal de price (500 > 450)
        # B com conexão pena e fica 530. A A vence.
        self.assertEqual(best.price_brl, 500.0)
        self.assertEqual(top[0], offA)
        self.assertEqual(top[1], offB)
        
        self.assertTrue(any("voo direto" in p.lower() for p in just))


if __name__ == "__main__":
    unittest.main()
