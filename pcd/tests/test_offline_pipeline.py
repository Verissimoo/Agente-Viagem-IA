import unittest
import os
from datetime import date, timedelta
from pcd.core.schema import SearchRequest, TripType, CabinClass
from pcd.adapters.kayak_adapter import KayakAdapter
from pcd.adapters.moblix_adapter import MoblixLatamAdapter
from pcd.core.ranking import rank_offers

class TestOfflinePipeline(unittest.TestCase):

    def setUp(self):
        self.base_request_ow = SearchRequest(
            origin=["GRU"],
            destination=["MIA"],
            date_start=date.today() + timedelta(days=30),
            date_end=date.today() + timedelta(days=30),
            trip_type=TripType.ONEWAY,
            adults=1,
            cabin=CabinClass.ECONOMY,
            baggage_checked=False
        )
        
        self.base_request_rt = SearchRequest(
            origin=["GRU"],
            destination=["FOR"],
            date_start=date.today() + timedelta(days=30),
            date_end=date.today() + timedelta(days=30),
            return_start=date.today() + timedelta(days=40),
            return_end=date.today() + timedelta(days=40),
            trip_type=TripType.ROUNDTRIP,
            adults=1,
            cabin=CabinClass.ECONOMY,
            baggage_checked=False
        )

    def test_kayak_oneway_loading(self):
        adapter = KayakAdapter()
        offers = adapter.search(self.base_request_ow, use_fixtures=True)
        self.assertEqual(len(offers), 2)
        
        direct = next(o for o in offers if len(o.outbound.segments) == 1)
        conn = next(o for o in offers if len(o.outbound.segments) == 2)
        self.assertEqual(direct.outbound.stops, 0)
        self.assertEqual(conn.outbound.stops, 1)

    def test_moblix_roundtrip_loading(self):
        adapter = MoblixLatamAdapter()
        offers = adapter.search(self.base_request_rt, use_fixtures=True)
        self.assertEqual(len(offers), 1)
        self.assertEqual(offers[0].miles, 20000)
        self.assertEqual(offers[0].taxes_brl, 80.0)
        self.assertEqual(offers[0].trip_type, TripType.ROUNDTRIP)
        self.assertIsNotNone(offers[0].inbound)

    def test_full_pipeline_offline(self):
        ka = KayakAdapter()
        ma = MoblixLatamAdapter()
        
        offers_k = ka.search(self.base_request_ow, use_fixtures=True)
        offers_m = ma.search(self.base_request_ow, use_fixtures=True)
        
        all_offers = offers_k + offers_m
        
        top, best, justification = rank_offers(all_offers)
        
        self.assertTrue(len(top) > 0)
        self.assertIsNotNone(best)
        self.assertGreater(best.equivalent_brl, 0)
        
        # Moblix miles 20000 * 0.0285 + 80 = 570 + 80 = 650 (winnings against kayak 1000/1200)
        self.assertEqual(best.miles, 20000)
        self.assertGreaterEqual(len(justification), 2)

if __name__ == "__main__":
    unittest.main()
