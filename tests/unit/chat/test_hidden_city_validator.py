"""Cross-reference hidden city ↔ alternativa em milhas."""
import unittest
from unittest.mock import patch

from backend.app.ai.agents.hidden_city_validator import (
    enrich_hidden_city_offers,
    find_miles_alternative,
    mark_hidden_city_disembark,
    validate_hidden_city_with_supplementary,
    validate_split_with_supplementary,
    supplementary_miles_search_for_split,
    _connects_through,
    _ticket_destination,
)


def _offer(*, category, airline, origin, destination, dep_date,
           miles=None, taxes=None, price=None, eq=None, segs_extra=None):
    """Builder de oferta de teste. segs_extra adiciona segmentos extras (split/hidden)."""
    segs = [{
        "origin": origin, "destination": destination,
        "departure_dt": f"{dep_date}T10:00:00",
        "arrival_dt": f"{dep_date}T12:00:00",
        "carrier": airline,
    }]
    if segs_extra:
        segs.extend(segs_extra)
    o = {
        "offer_id": f"o_{origin}_{destination}_{dep_date}_{airline}_{category}",
        "category": category,
        "airline": airline,
        "outbound": {"segments": segs},
    }
    if miles is not None: o["miles"] = miles
    if taxes is not None: o["taxes_brl"] = taxes
    if price is not None: o["price_brl"] = price
    if eq is not None: o["equivalent_brl"] = eq
    return o


class TestHiddenCityValidator(unittest.TestCase):
    def test_find_exact_match_carrier_route_date(self):
        # Hidden city BSB→CNF (vendido como BSB→GIG) operado pela LATAM
        hidden = _offer(
            category="Hidden City", airline="LA",
            origin="BSB", destination="CNF", dep_date="2026-09-15",
            price=237, segs_extra=[{
                "origin": "CNF", "destination": "GIG",
                "departure_dt": "2026-09-15T14:00:00",
                "arrival_dt": "2026-09-15T15:30:00",
                "carrier": "LA",
            }],
        )
        miles_pool = [
            _offer(category="Milhas", airline="LA",
                   origin="BSB", destination="CNF", dep_date="2026-09-15",
                   miles=8000, taxes=33, eq=235),
            _offer(category="Milhas", airline="G3",
                   origin="BSB", destination="CNF", dep_date="2026-09-15",
                   miles=12000, taxes=45, eq=270),
        ]
        match = find_miles_alternative(hidden, miles_pool, real_destination="CNF")
        self.assertIsNotNone(match)
        # Prefere LATAM (mesma cia) mesmo sendo mais barata
        self.assertEqual(match["airline"], "LA")

    def test_no_match_when_different_destination(self):
        hidden = _offer(
            category="Hidden City", airline="LA",
            origin="BSB", destination="CNF", dep_date="2026-09-15",
            price=237,
        )
        miles_pool = [
            _offer(category="Milhas", airline="LA",
                   origin="BSB", destination="GRU", dep_date="2026-09-15",
                   miles=5000, taxes=30),
        ]
        match = find_miles_alternative(hidden, miles_pool, real_destination="CNF")
        self.assertIsNone(match)

    def test_fallback_to_route_only_when_date_differs(self):
        hidden = _offer(
            category="Hidden City", airline="LA",
            origin="BSB", destination="CNF", dep_date="2026-09-15",
            price=237,
        )
        miles_pool = [
            _offer(category="Milhas", airline="G3",
                   origin="BSB", destination="CNF", dep_date="2026-09-16",
                   miles=8000, taxes=33, eq=200),
        ]
        match = find_miles_alternative(hidden, miles_pool, real_destination="CNF")
        # Pega a opção mesmo com data diferente (fallback 3ª passada)
        self.assertIsNotNone(match)
        self.assertEqual(match["airline"], "G3")

    def test_enrich_only_hidden_offers(self):
        offers = [
            _offer(category="Cash direto", airline="LA",
                   origin="BSB", destination="CNF", dep_date="2026-09-15", price=400),
            _offer(category="Hidden City", airline="G3",
                   origin="BSB", destination="CNF", dep_date="2026-09-15", price=237),
        ]
        miles_pool = [
            _offer(category="Milhas", airline="G3",
                   origin="BSB", destination="CNF", dep_date="2026-09-15",
                   miles=8000, taxes=33, eq=200),
        ]
        enriched = enrich_hidden_city_offers(offers, miles_pool, real_destination="CNF")
        # Cash não recebe miles_alternative
        self.assertNotIn("miles_alternative", enriched[0])
        # Hidden city recebe
        self.assertIn("miles_alternative", enriched[1])
        self.assertEqual(enriched[1]["miles_alternative"]["miles"], 8000)

    def test_picks_cheapest_when_multiple_carriers_match(self):
        hidden = _offer(
            category="Hidden City", airline="ZZ",  # cia desconhecida no pool
            origin="BSB", destination="CNF", dep_date="2026-09-15",
            price=200,
        )
        miles_pool = [
            _offer(category="Milhas", airline="LA",
                   origin="BSB", destination="CNF", dep_date="2026-09-15",
                   miles=10000, taxes=50, eq=300),
            _offer(category="Milhas", airline="G3",
                   origin="BSB", destination="CNF", dep_date="2026-09-15",
                   miles=8000, taxes=33, eq=200),
        ]
        match = find_miles_alternative(hidden, miles_pool, real_destination="CNF")
        # Sem match de carrier → pega a mais barata (G3 com eq=200)
        self.assertIsNotNone(match)
        self.assertEqual(match["airline"], "G3")


class TestMarkDisembark(unittest.TestCase):
    """Marca segmentos do hidden city: usados (até desembarque) e descartados."""

    def test_marks_used_and_discarded(self):
        # Bilhete BSB→GIG via SSA via CNF; passageiro desembarca em SSA
        offer = {
            "category": "Hidden City",
            "outbound": {"segments": [
                {"origin": "BSB", "destination": "SSA", "departure_dt": "2026-09-15T14:00:00",
                 "arrival_dt": "2026-09-15T16:00:00", "carrier": "G3"},
                {"origin": "SSA", "destination": "CNF", "departure_dt": "2026-09-15T17:00:00",
                 "arrival_dt": "2026-09-15T18:30:00", "carrier": "G3"},
                {"origin": "CNF", "destination": "GIG", "departure_dt": "2026-09-15T19:30:00",
                 "arrival_dt": "2026-09-15T20:30:00", "carrier": "G3"},
            ]},
        }
        marked = mark_hidden_city_disembark(offer, "SSA")
        segs = marked["outbound"]["segments"]
        self.assertTrue(segs[0]["used"])
        self.assertFalse(segs[0]["discarded"])
        self.assertFalse(segs[1]["used"])
        self.assertTrue(segs[1]["discarded"])
        self.assertFalse(segs[2]["used"])
        self.assertTrue(segs[2]["discarded"])
        self.assertEqual(marked["passenger_disembark_at"], "SSA")
        self.assertEqual(marked["discarded_segments_count"], 2)

    def test_no_marks_when_destination_not_found(self):
        # Se o destino real não está em nenhum segmento, não marca nada
        offer = {
            "category": "Hidden City",
            "outbound": {"segments": [
                {"origin": "BSB", "destination": "GRU"},
                {"origin": "GRU", "destination": "GIG"},
            ]},
        }
        marked = mark_hidden_city_disembark(offer, "SSA")
        for seg in marked["outbound"]["segments"]:
            self.assertNotIn("used", seg)
            self.assertNotIn("discarded", seg)


class TestSupplementaryValidation(unittest.TestCase):
    def test_ticket_destination_is_last_segment(self):
        hidden = _offer(
            category="Hidden City", airline="G3",
            origin="BSB", destination="SSA", dep_date="2026-09-15",
            price=237, segs_extra=[{
                "origin": "SSA", "destination": "CNF",
                "departure_dt": "2026-09-15T14:00:00",
                "arrival_dt": "2026-09-15T15:30:00",
                "carrier": "G3",
            }],
        )
        self.assertEqual(_ticket_destination(hidden), "CNF")

    def test_connects_through_detects_hub(self):
        offer = _offer(
            category="Milhas", airline="G3",
            origin="BSB", destination="SSA", dep_date="2026-09-15",
            miles=10000, taxes=50, segs_extra=[{
                "origin": "SSA", "destination": "CNF",
                "departure_dt": "2026-09-15T14:00:00",
                "arrival_dt": "2026-09-15T15:30:00",
                "carrier": "G3",
            }],
        )
        self.assertTrue(_connects_through(offer, "SSA"))
        self.assertFalse(_connects_through(offer, "GIG"))

    def test_supplementary_validation_appends_alternative(self):
        hidden = _offer(
            category="Hidden City", airline="G3",
            origin="BSB", destination="SSA", dep_date="2026-09-15",
            price=237, segs_extra=[{
                "origin": "SSA", "destination": "CNF",
                "departure_dt": "2026-09-15T14:00:00",
                "arrival_dt": "2026-09-15T15:30:00",
                "carrier": "G3",
            }],
        )
        # Mocka run_search retornando ofertas em milhas BSB→CNF via SSA
        fake_result = {
            "ok": True,
            "miles_offers": [
                # Esta passa pelo hub SSA — match exato
                _offer(category="Milhas", airline="G3",
                       origin="BSB", destination="SSA", dep_date="2026-09-15",
                       miles=15000, taxes=60, eq=435, segs_extra=[{
                           "origin": "SSA", "destination": "CNF",
                           "departure_dt": "2026-09-15T14:00:00",
                           "arrival_dt": "2026-09-15T15:30:00",
                           "carrier": "G3",
                       }]),
                # Esta não passa por SSA — não é o mesmo bilhete
                _offer(category="Milhas", airline="G3",
                       origin="BSB", destination="CNF", dep_date="2026-09-15",
                       miles=20000, taxes=80, eq=580),
            ],
        }
        with patch("backend.app.ai.agents.hidden_city_validator.run_search",
                   return_value=fake_result, create=True):
            # patch o import dentro da função — usa importlib trick
            import backend.app.ai.agents.tools as tools_mod
            original = tools_mod.run_search
            tools_mod.run_search = lambda **kw: fake_result
            try:
                out = validate_hidden_city_with_supplementary(
                    [hidden], real_destination="SSA",
                    adults=1, cabin="economy", max_validations=1,
                )
            finally:
                tools_mod.run_search = original

        self.assertEqual(len(out), 1)
        # A busca suplementar (bilhete OFICIAL via escala) agora vai num campo
        # separado `miles_same_ticket` — não sobrescreve mais o award direto.
        self.assertIn("miles_same_ticket", out[0])
        alt = out[0]["miles_same_ticket"]
        self.assertTrue(alt.get("validated"))
        self.assertEqual(alt.get("miles"), 15000)
        self.assertTrue(alt.get("exact_route_match"))
        self.assertEqual(alt.get("ticket_destination"), "CNF")
        self.assertEqual(alt.get("via_hub"), "SSA")


class TestSplitValidation(unittest.TestCase):
    """Validação suplementar pra split: 1 busca por perna em paralelo, soma totais."""

    def _split_offer(self):
        # BSB→GRU + GRU→LIS (2 bilhetes separados)
        return _offer(
            category="Split de trecho", airline="AD",
            origin="BSB", destination="GRU", dep_date="2026-06-10",
            price=2698,
            segs_extra=[{
                "origin": "GRU", "destination": "LIS",
                "departure_dt": "2026-06-10T22:00:00",
                "arrival_dt": "2026-06-11T10:00:00",
                "carrier": "TP",
            }],
        )

    def test_supplementary_split_search_sums_legs(self):
        split = self._split_offer()
        # Mocka run_search retornando milhas pra cada perna
        def fake_run(**kw):
            origin = kw.get("origin")
            dest = kw.get("destination")
            if origin == "BSB" and dest == "GRU":
                return {"ok": True, "miles_offers": [{
                    "airline": "AD", "miles": 8000, "taxes_brl": 50, "equivalent_brl": 250,
                    "outbound": {"segments": [{
                        "origin": "BSB", "destination": "GRU",
                        "departure_dt": "2026-06-10T08:00:00", "arrival_dt": "2026-06-10T10:00:00",
                        "carrier": "AD",
                    }]},
                }]}
            if origin == "GRU" and dest == "LIS":
                return {"ok": True, "miles_offers": [{
                    "airline": "TP", "miles": 60000, "taxes_brl": 180, "equivalent_brl": 2820,
                    "outbound": {"segments": [{
                        "origin": "GRU", "destination": "LIS",
                        "departure_dt": "2026-06-10T22:00:00", "arrival_dt": "2026-06-11T10:00:00",
                        "carrier": "TP",
                    }]},
                }]}
            return {"ok": False}

        import backend.app.ai.agents.tools as tm
        original = tm.run_search
        tm.run_search = fake_run
        try:
            result = supplementary_miles_search_for_split(
                split, adults=1, cabin="economy",
            )
        finally:
            tm.run_search = original

        self.assertIsNotNone(result)
        self.assertTrue(result["validated"])
        self.assertEqual(result["total_miles"], 68000)         # 8000 + 60000
        self.assertEqual(result["total_taxes_brl"], 230.0)     # 50 + 180
        self.assertEqual(result["total_equivalent_brl"], 3070.0)  # 250 + 2820
        breakdown = result["split_breakdown"]
        self.assertEqual(len(breakdown), 2)
        self.assertEqual(breakdown[0]["origin"], "BSB")
        self.assertEqual(breakdown[0]["destination"], "GRU")
        self.assertEqual(breakdown[1]["origin"], "GRU")
        self.assertEqual(breakdown[1]["destination"], "LIS")

    def test_supplementary_split_returns_none_if_any_leg_empty(self):
        split = self._split_offer()
        def fake_run(**kw):
            if kw.get("destination") == "LIS":
                return {"ok": True, "miles_offers": []}   # vazio
            return {"ok": True, "miles_offers": [{
                "airline": "AD", "miles": 8000, "taxes_brl": 50, "equivalent_brl": 250,
                "outbound": {"segments": [{
                    "origin": "BSB", "destination": "GRU",
                    "departure_dt": "2026-06-10T08:00:00",
                    "arrival_dt": "2026-06-10T10:00:00", "carrier": "AD",
                }]},
            }]}

        import backend.app.ai.agents.tools as tm
        original = tm.run_search
        tm.run_search = fake_run
        try:
            result = supplementary_miles_search_for_split(split, adults=1)
        finally:
            tm.run_search = original
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
