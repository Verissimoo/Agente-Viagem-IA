import unittest
import os
import json
from datetime import date, timedelta
from pcd.core.schema import SearchRequest, TripType, CabinClass, UnifiedOffer, LayoverCategory
from pcd.adapters.kayak_adapter import KayakAdapter
from pcd.adapters.moblix_adapter import MoblixLatamAdapter
from pcd.core.layover_classifier import classify_many
from pcd.core.ranking import rank_offers
from pcd.run import run_pipeline
from pcd.core.errors import OfflineModeError
from pcd.core.config import config

class TestOfflineQuality(unittest.TestCase):

    def setUp(self):
        # Default request
        self.req = SearchRequest(
            origin=["GRU"],
            destination=["MIA"],
            date_start=date.today() + timedelta(days=30),
            date_end=date.today() + timedelta(days=30),
            trip_type=TripType.ONEWAY,
            adults=1,
            cabin=CabinClass.ECONOMY
        )
        # Ensure clean state for config
        config.PCD_OFFLINE = 0
        os.environ["COST_PER_MILE_BRL"] = "0.015"
        os.environ["CONNECTION_PENALTY_BRL"] = "80.0"

    def test_adapters_fixtures(self):
        # Kayak
        k_adapter = KayakAdapter()
        k_offers = k_adapter.search(self.req, use_fixtures=True)
        self.assertGreater(len(k_offers), 0, "Deveria carregar ofertas do Kayak")
        for o in k_offers:
            self.assertIsInstance(o, UnifiedOffer)
            self.assertIsNotNone(o.outbound)
            self.assertIsNotNone(o.airline)

        # Moblix
        m_adapter = MoblixLatamAdapter()
        m_offers = m_adapter.search(self.req, use_fixtures=True)
        self.assertGreater(len(m_offers), 0, "Deveria carregar ofertas do Moblix")
        for o in m_offers:
            self.assertIsInstance(o, UnifiedOffer)
            self.assertIsNotNone(o.miles)
            self.assertIsNotNone(o.taxes_brl)

    def test_layover_classifier(self):
        # Direct vs Connection
        k_adapter = KayakAdapter()
        offers = k_adapter.search(self.req, use_fixtures=True)
        classified = classify_many(offers)
        
        # Em pcd/fixtures/kayak_oneway.json, temos 1 direto e 1 conexão
        direct = next((o for o in classified if o.layover_out == LayoverCategory.DIRECT), None)
        conn = next((o for o in classified if o.layover_out == LayoverCategory.CONNECTION), None)
        
        self.assertIsNotNone(direct, "Deveria ter voo direto")
        self.assertIsNotNone(conn, "Deveria ter voo com conexão")

    def test_scoring_equivalent(self):
        # Independente do CPM config (ex: 0.015), LATAM deve usar sempre 0.0285
        # Moblix (fixture) = 20000 milhas + 80.0 taxas = 20000*0.0285 + 80 = 650.0
        k_adapter = KayakAdapter()
        m_adapter = MoblixLatamAdapter()
        
        all_offers = k_adapter.search(self.req, use_fixtures=True) + m_adapter.search(self.req, use_fixtures=True)
        classified = classify_many(all_offers)
        top, best, _ = rank_offers(classified)
        
        # O ranking agora deve dar 650.0 para Mobix Latam
        self.assertEqual(best.equivalent_brl, 650.0)
        self.assertEqual(best.source.value, "moblix_latam")

    def test_runner_offline(self):
        # Teste do runner pcd.run.run_pipeline integral
        trace_path = "test_quality_trace.jsonl"
        res = run_pipeline(prompt="SP para Miami", top_n=5, use_fixtures=True, trace_out=trace_path)
        
        self.assertIsNotNone(res["best_offer"])
        self.assertTrue(len(res["top_offers"]) > 0)
        
        # Verificar se trace tem etapas
        if os.path.exists(trace_path):
            with open(trace_path, "r") as f:
                lines = f.readlines()
                # 6 etapas (parse, kayak, moblix, layover, score, format) * 2 eventos (start/end)
                self.assertGreaterEqual(len(lines), 12) 
            os.remove(trace_path)

    def test_offline_killswitch(self):
        # Forçar PCD_OFFLINE=1 e tentar busca SEM fixtures
        config.PCD_OFFLINE = 1
        
        k_adapter = KayakAdapter()
        with self.assertRaises(OfflineModeError):
            k_adapter.search(self.req, use_fixtures=False)

        m_adapter = MoblixLatamAdapter()
        with self.assertRaises(OfflineModeError):
            m_adapter.search(self.req, use_fixtures=False)
        
        # Limpeza
        config.PCD_OFFLINE = 0

if __name__ == "__main__":
    unittest.main()
