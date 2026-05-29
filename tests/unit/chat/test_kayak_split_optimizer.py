"""Tests pra kayak_split_optimizer — otimização de datas por perna do split."""
import unittest

from backend.app.ai.agents.kayak_split_optimizer import (
    find_best_dates_per_leg_via_kayak,
    optimize_split_dates_via_kayak,
)


def _split_offer(price_brl=2698):
    """BSB→GRU + GRU→LIS — split de 2 pernas."""
    return {
        "offer_id": "split-test-1",
        "category": "Split de trecho",
        "airline": "AD",
        "price_brl": price_brl,
        "outbound": {"segments": [
            {"origin": "BSB", "destination": "GRU",
             "departure_dt": "2026-06-10T08:00:00",
             "arrival_dt": "2026-06-10T09:45:00", "carrier": "AD"},
            {"origin": "GRU", "destination": "LIS",
             "departure_dt": "2026-06-10T22:00:00",
             "arrival_dt": "2026-06-11T10:00:00", "carrier": "TP"},
        ]},
    }


class TestKayakSplitOptimizer(unittest.TestCase):
    def test_picks_cheapest_date_per_leg_and_combines(self):
        """Cada perna pode ter melhor data diferente — sistema combina."""
        split = _split_offer(price_brl=2698)
        # Fake: BSB→GRU mais barato em 11/jun, GRU→LIS mais barato em 12/jun
        def fake_run(**kw):
            if kw.get("origin") == "BSB" and kw.get("destination") == "GRU":
                return {"ok": True, "money_offers": [
                    {"airline": "AD", "price_brl": 850,
                     "outbound": {"segments": [{
                         "origin": "BSB", "destination": "GRU",
                         "departure_dt": "2026-06-11T07:00:00",
                         "arrival_dt": "2026-06-11T08:45:00", "carrier": "AD",
                     }]}},
                    {"airline": "AD", "price_brl": 1100,
                     "outbound": {"segments": [{
                         "origin": "BSB", "destination": "GRU",
                         "departure_dt": "2026-06-10T08:00:00",
                         "arrival_dt": "2026-06-10T09:45:00", "carrier": "AD",
                     }]}},
                ]}
            if kw.get("origin") == "GRU" and kw.get("destination") == "LIS":
                return {"ok": True, "money_offers": [
                    {"airline": "TP", "price_brl": 1450,
                     "outbound": {"segments": [{
                         "origin": "GRU", "destination": "LIS",
                         "departure_dt": "2026-06-12T22:00:00",
                         "arrival_dt": "2026-06-13T10:00:00", "carrier": "TP",
                     }]}},
                    {"airline": "TP", "price_brl": 1700,
                     "outbound": {"segments": [{
                         "origin": "GRU", "destination": "LIS",
                         "departure_dt": "2026-06-10T22:00:00",
                         "arrival_dt": "2026-06-11T10:00:00", "carrier": "TP",
                     }]}},
                ]}
            return {"ok": False}

        import backend.app.ai.agents.tools as tm
        original = tm.run_search
        tm.run_search = fake_run
        try:
            opt = find_best_dates_per_leg_via_kayak(
                split, adults=1, cabin="economy", flex_days=3,
            )
        finally:
            tm.run_search = original

        self.assertIsNotNone(opt)
        self.assertTrue(opt["kayak_optimized"])
        self.assertEqual(opt["total_price_brl"], 850 + 1450)
        self.assertEqual(opt["savings_brl"], 2698 - 2300)
        # Breakdown traz a melhor data de cada
        bd = opt["breakdown"]
        self.assertEqual(len(bd), 2)
        self.assertEqual(bd[0]["best_date"], "2026-06-11")
        self.assertEqual(bd[0]["moved_days"], 1)
        self.assertEqual(bd[1]["best_date"], "2026-06-12")
        self.assertEqual(bd[1]["moved_days"], 2)

    def test_rejects_when_dates_out_of_order(self):
        """Se a melhor perna 1 ficou DEPOIS da melhor perna 2, descarta."""
        split = _split_offer()
        def fake_run(**kw):
            # Perna 1 melhor em 13/jun, perna 2 melhor em 10/jun → inválido
            if kw.get("origin") == "BSB":
                return {"ok": True, "money_offers": [{
                    "airline": "AD", "price_brl": 800,
                    "outbound": {"segments": [{
                        "origin": "BSB", "destination": "GRU",
                        "departure_dt": "2026-06-13T07:00:00",
                        "arrival_dt": "2026-06-13T08:45:00", "carrier": "AD",
                    }]},
                }]}
            return {"ok": True, "money_offers": [{
                "airline": "TP", "price_brl": 1300,
                "outbound": {"segments": [{
                    "origin": "GRU", "destination": "LIS",
                    "departure_dt": "2026-06-10T22:00:00",
                    "arrival_dt": "2026-06-11T10:00:00", "carrier": "TP",
                }]},
            }]}

        import backend.app.ai.agents.tools as tm
        original = tm.run_search
        tm.run_search = fake_run
        try:
            opt = find_best_dates_per_leg_via_kayak(split, adults=1)
        finally:
            tm.run_search = original

        self.assertIsNone(opt)

    def test_returns_none_when_leg_has_no_cash(self):
        split = _split_offer()
        def fake_run(**kw):
            if kw.get("destination") == "LIS":
                return {"ok": True, "money_offers": []}
            return {"ok": True, "money_offers": [{
                "airline": "AD", "price_brl": 800,
                "outbound": {"segments": [{
                    "origin": "BSB", "destination": "GRU",
                    "departure_dt": "2026-06-10T07:00:00",
                    "arrival_dt": "2026-06-10T08:45:00", "carrier": "AD",
                }]},
            }]}

        import backend.app.ai.agents.tools as tm
        original = tm.run_search
        tm.run_search = fake_run
        try:
            opt = find_best_dates_per_leg_via_kayak(split, adults=1)
        finally:
            tm.run_search = original

        self.assertIsNone(opt)

    def test_optimize_skips_non_split(self):
        """Só processa ofertas com categoria 'split'."""
        cash = {
            "offer_id": "c1", "category": "Cash direto",
            "price_brl": 1000,
            "outbound": {"segments": [{
                "origin": "BSB", "destination": "LIS",
                "departure_dt": "2026-06-10T10:00:00",
                "arrival_dt": "2026-06-10T22:00:00", "carrier": "LA",
            }]},
        }
        out = optimize_split_dates_via_kayak([cash], adults=1)
        self.assertEqual(len(out), 1)
        self.assertNotIn("kayak_date_optimization", out[0])


if __name__ == "__main__":
    unittest.main()
