"""Validação do novo source 'Azul Oficial' (cash via BuscaMilhas AZUL pagante)."""
import unittest

from backend.app.domain.models import Scenario, SourceType
from backend.app.providers.buscamilhas.adapter import BuscaMilhasAzulCashAdapter
from backend.app.ai.agents.sanitizer import sanitize_offer
from backend.app.ai.agents.presenter import _category_bucket, _RISK_PENALTY


class TestAzulOfficialAdapter(unittest.TestCase):
    def test_adapter_config(self):
        adapter = BuscaMilhasAzulCashAdapter()
        self.assertEqual(adapter.companhia, "AZUL")
        self.assertEqual(adapter.source_type, SourceType.BUSCAMILHAS_AZUL_CASH)
        self.assertEqual(adapter.airline_code, "AD")
        # Só cash, sem milhas
        self.assertFalse(adapter.somente_milhas)
        self.assertTrue(adapter.somente_pagante)
        # Força scenario AZUL_OFFICIAL em todas as ofertas geradas
        self.assertEqual(adapter.scenario_override, Scenario.AZUL_OFFICIAL)

    def test_registered_in_always_include(self):
        from backend.app.services.search_orchestrator import (
            _ADAPTER_MAP, _ALWAYS_INCLUDE,
        )
        self.assertIn("AZUL_CASH", _ADAPTER_MAP)
        self.assertEqual(_ADAPTER_MAP["AZUL_CASH"], BuscaMilhasAzulCashAdapter)
        # Sempre incluído nas buscas (não precisa o vendedor pedir AZUL)
        self.assertIn("AZUL_CASH", _ALWAYS_INCLUDE)


class TestSanitizerAzulOfficial(unittest.TestCase):
    def test_scenario_becomes_azul_oficial_label(self):
        offer = {
            "source": "buscamilhas_azul_cash",
            "airline": "AD",
            "scenario": "azul_official",
            "price_brl": 580,
            "outbound": {"segments": [{
                "origin": "BSB", "destination": "GRU",
                "departure_dt": "2026-07-10T09:00:00",
                "arrival_dt": "2026-07-10T10:45:00",
                "carrier": "AD",
            }]},
        }
        s = sanitize_offer(offer)
        self.assertEqual(s["category"], "Azul Oficial")
        # Why explica o porquê é especial
        self.assertIn("agência", s["category_why"].lower())
        self.assertIn("markup", s["category_why"].lower())


class TestPresenterBucket(unittest.TestCase):
    def test_azul_oficial_bucket(self):
        offer = {"category": "Azul Oficial", "price_brl": 500}
        self.assertEqual(_category_bucket(offer), "azul_oficial")

    def test_azul_oficial_has_bonus_in_risk_penalty(self):
        # Bônus de 5% — sai como mais "vantajosa" no ranking
        self.assertLess(_RISK_PENALTY["azul_oficial"], 1.0)


if __name__ == "__main__":
    unittest.main()
