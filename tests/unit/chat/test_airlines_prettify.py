"""Mapeamento de códigos IATA → nomes amigáveis + programas de milhas."""
import unittest

from backend.app.ai.agents.airlines import (
    carrier_to_program,
    miles_program_name,
    prettify_carrier,
)
from backend.app.ai.agents.sanitizer import sanitize_offer


class TestPrettifyCarrier(unittest.TestCase):
    def test_codes_become_friendly_names(self):
        self.assertEqual(prettify_carrier("G3"), "GOL")
        self.assertEqual(prettify_carrier("LA"), "LATAM")
        self.assertEqual(prettify_carrier("AD"), "AZUL")
        self.assertEqual(prettify_carrier("TP"), "TAP Portugal")
        self.assertEqual(prettify_carrier("AA"), "American Airlines")

    def test_lowercase_codes_also_work(self):
        self.assertEqual(prettify_carrier("g3"), "GOL")
        self.assertEqual(prettify_carrier("la"), "LATAM")

    def test_unknown_returns_original(self):
        self.assertEqual(prettify_carrier("ZZ"), "ZZ")
        self.assertEqual(prettify_carrier("CustomCia"), "CustomCia")

    def test_none_empty(self):
        self.assertIsNone(prettify_carrier(None))
        self.assertEqual(prettify_carrier(""), "")


class TestMilesProgramName(unittest.TestCase):
    def test_brazilian_programs(self):
        self.assertEqual(miles_program_name("G3"), "Smiles")
        self.assertEqual(miles_program_name("LA"), "LATAM Pass")
        self.assertEqual(miles_program_name("AD"), "TudoAzul")
        self.assertEqual(miles_program_name("TP"), "Miles&Go")

    def test_international(self):
        self.assertEqual(miles_program_name("AC"), "Aeroplan")
        self.assertEqual(miles_program_name("AF"), "Flying Blue")
        self.assertEqual(miles_program_name("BA"), "Avios")
        self.assertEqual(miles_program_name("LH"), "Miles & More")

    def test_carrier_to_program_accepts_name(self):
        self.assertEqual(carrier_to_program("GOL"), "Smiles")
        self.assertEqual(carrier_to_program("LATAM"), "LATAM Pass")


class TestSanitizerPrettify(unittest.TestCase):
    def test_offer_airline_becomes_friendly(self):
        offer = {
            "airline": "G3", "price_brl": 500,
            "outbound": {"segments": [{
                "origin": "BSB", "destination": "SSA",
                "departure_dt": "2026-09-15T10:00:00",
                "arrival_dt": "2026-09-15T12:00:00",
                "carrier": "G3",
            }]},
        }
        s = sanitize_offer(offer)
        self.assertEqual(s["airline"], "GOL")
        self.assertEqual(s["airline_code"], "G3")
        self.assertEqual(s["outbound"]["segments"][0]["carrier"], "GOL")
        self.assertEqual(s["outbound"]["segments"][0]["carrier_code"], "G3")

    def test_miles_offer_gets_program_label(self):
        offer = {
            "airline": "LA", "miles": 8000, "taxes_brl": 33,
            "outbound": {"segments": [{
                "origin": "BSB", "destination": "SSA",
                "departure_dt": "2026-09-15T10:00:00",
                "arrival_dt": "2026-09-15T12:00:00",
                "carrier": "LA",
            }]},
        }
        s = sanitize_offer(offer)
        self.assertEqual(s["airline"], "LATAM")
        self.assertEqual(s["miles_program_label"], "LATAM Pass")


if __name__ == "__main__":
    unittest.main()
