"""Sanitizer de ofertas — remove provider/deeplink, mantém categoria + why."""
import unittest

from backend.app.ai.agents.sanitizer import sanitize_offer, sanitize_offers


SAMPLE_OFFER = {
    "source": "skiplagged",
    "airline": "TAP",
    "price_brl": 2400.0,
    "deeplink": "https://skiplagged.com/foo",
    "scenario": "hidden_city",
    "miles_equivalent_program": "GOL",
    "outbound": {"segments": [{"origin": "GRU", "destination": "LIS"}]},
}


class TestSanitizer(unittest.TestCase):
    def test_removes_source_and_deeplink(self):
        out = sanitize_offer(SAMPLE_OFFER)
        self.assertNotIn("source", out)
        self.assertNotIn("deeplink", out)
        self.assertNotIn("miles_equivalent_program", out)

    def test_maps_scenario_to_label(self):
        out = sanitize_offer(SAMPLE_OFFER)
        self.assertEqual(out.get("category"), "Hidden City")

    def test_provides_category_why(self):
        out = sanitize_offer(SAMPLE_OFFER)
        why = (out.get("category_why") or "").lower()
        self.assertIn("hidden city", why)
        # Tem que mencionar o risco principal
        self.assertIn("bagagem", why)

    def test_keeps_price_and_airline(self):
        out = sanitize_offer(SAMPLE_OFFER)
        self.assertEqual(out.get("price_brl"), 2400.0)
        self.assertEqual(out.get("airline"), "TAP")

    def test_assigns_stable_offer_id(self):
        a = sanitize_offer(SAMPLE_OFFER)
        b = sanitize_offer(SAMPLE_OFFER)
        self.assertEqual(a["offer_id"], b["offer_id"])
        self.assertTrue(a["offer_id"].startswith("o_"))

    def test_none_input(self):
        self.assertIsNone(sanitize_offer(None))

    def test_list_filters_none(self):
        out = sanitize_offers([SAMPLE_OFFER, None, SAMPLE_OFFER])
        self.assertEqual(len(out), 2)


if __name__ == "__main__":
    unittest.main()
