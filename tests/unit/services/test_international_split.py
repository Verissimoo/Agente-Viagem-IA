"""Quebra de trecho INTERNACIONAL (2 tipos): direto + hub-split + skip-split.

Mocka radar (scan_dates), run_search e a validação de split — testa só a
orquestração e a regra de custo (milhas vs cash+10%, encaixe doméstico)."""
from datetime import date

from backend.app.services import international_split as isplit


def _miles_offer(origin, dest, *, eq, miles, taxes, dep, arr, airline="GOL", prog="Smiles"):
    return {
        "airline": airline, "miles": miles, "taxes_brl": taxes,
        "equivalent_brl": eq, "miles_program": prog,
        "outbound": {"segments": [{
            "origin": origin, "destination": dest, "carrier": "G3",
            "departure_dt": dep, "arrival_dt": arr,
        }]},
    }


def test_best_route_prefers_cheapest_miles_over_cash_markup():
    money = [{"source": "kayak", "price_brl": 1000.0, "airline": "TAP",
              "outbound": {"segments": []}}]
    miles = [{"equivalent_brl": 900.0, "miles": 30000, "taxes_brl": 80,
              "airline": "LATAM", "miles_program": "LATAM Pass",
              "outbound": {"segments": []}}]
    best = isplit._best_route(money, miles)
    assert best is not None
    assert best["kind"] == "miles"
    assert best["brl"] == 900.0


def test_best_route_applies_10pct_markup_on_kayak_cash():
    money = [{"source": "kayak", "price_brl": 1000.0, "airline": "TAP",
              "outbound": {"segments": []}}]
    best = isplit._best_route(money, [])
    assert best is not None
    assert best["kind"] == "cash"
    assert best["brl"] == 1100.0  # 1000 × 1.10
    assert best["cash_brl"] == 1000.0


def test_direct_miles_one_option_per_carrier():
    miles = [
        {"airline": "GOL", "miles": 80000, "taxes_brl": 200, "equivalent_brl": 2400,
         "miles_program": "Smiles", "outbound": {"segments": [{}]}},
        {"airline": "GOL", "miles": 90000, "taxes_brl": 200, "equivalent_brl": 2600,
         "miles_program": "Smiles", "outbound": {"segments": [{}]}},   # mais cara → descartada
        {"airline": "LATAM", "miles": 70000, "taxes_brl": 250, "equivalent_brl": 2300,
         "miles_program": "LATAM Pass", "outbound": {"segments": [{}]}},
    ]
    opts = isplit._direct_miles_per_carrier(miles)
    by_air = {o["airline"]: o for o in opts}
    assert set(by_air) == {"GOL", "LATAM"}              # uma por cia
    assert by_air["GOL"]["total_brl"] == 2400           # a mais barata da GOL
    assert by_air["LATAM"]["program"] == "LATAM Pass"


def test_cash_unplugged_only_for_carrier_without_miles():
    miles = [{"airline": "TAP", "equivalent_brl": 2400, "miles": 80000, "taxes_brl": 200,
              "outbound": {"segments": [{}]}}]
    money = [
        {"source": "kayak", "airline": "EMIRATES", "price_brl": 1900.0, "outbound": {"segments": [{}]}},
        {"source": "kayak", "airline": "TAP", "price_brl": 1800.0, "outbound": {"segments": [{}]}},
    ]
    # TAP é a mais barata no cash, MAS já temos TAP em milhas → pula. Emirates
    # não tem milhas na busca → vira a opção cash sem programa.
    out = isplit._direct_cash_unplugged(money, miles)
    assert out is not None
    assert out["airline"] == "EMIRATES"
    assert out["no_miles_program"] is True
    assert out["total_brl"] == round(1900.0 * 1.10, 2)  # +10% markup


def test_cash_unplugged_none_when_all_carriers_have_miles():
    miles = [{"airline": "TAP", "equivalent_brl": 2400, "outbound": {"segments": [{}]}}]
    money = [{"source": "kayak", "airline": "TAP", "price_brl": 1800.0, "outbound": {"segments": [{}]}}]
    assert isplit._direct_cash_unplugged(money, miles) is None


def test_hub_leg_primary_miles_flags_cash_cheaper():
    miles = [{"airline": "TAP", "equivalent_brl": 2000, "miles": 80000, "taxes_brl": 300,
              "miles_program": "Miles&Go", "outbound": {"segments": [{}]}}]
    money = [{"source": "kayak", "airline": "TAP", "price_brl": 1500.0, "outbound": {"segments": [{}]}}]
    leg = isplit._hub_leg(money, miles)
    assert leg["kind"] == "miles"          # primário continua sendo milhas
    assert leg["brl"] == 2000
    assert leg["program"] == "Miles&Go"
    cc = leg["cash_cheaper"]
    assert cc["cash_brl"] == 1500.0
    assert cc["savings_brl"] == 500.0      # 2000 milhas − 1500 cash


def test_hub_leg_no_cash_flag_when_miles_cheaper():
    miles = [{"airline": "TAP", "equivalent_brl": 1500, "miles": 60000, "taxes_brl": 200,
              "miles_program": "Miles&Go", "outbound": {"segments": [{}]}}]
    money = [{"source": "kayak", "airline": "TAP", "price_brl": 2000.0, "outbound": {"segments": [{}]}}]
    leg = isplit._hub_leg(money, miles)
    assert leg["brl"] == 1500
    assert "cash_cheaper" not in leg       # milhas já é mais barato → sem aviso


def test_quote_international_combines_direct_and_hub_split(monkeypatch):
    day = date(2026, 10, 15)

    # Radar escolhe sempre o mesmo dia pra direto e pra hub.
    class _Radar:
        def __init__(self):
            self.ranked_pairs = [(day, None)]
            self.price_by_pair = {}
            self.source = "kayak"

    monkeypatch.setattr(
        "backend.app.services.date_radar.scan_dates",
        lambda *a, **k: _Radar(),
    )
    monkeypatch.setattr(
        "backend.app.ai.agents.sanitizer.sanitize_offers",
        lambda offers: offers,
    )
    # Sem splits do Skiplagged neste teste.
    monkeypatch.setattr(
        "backend.app.ai.agents.hidden_city_validator.validate_split_with_supplementary",
        lambda offers, **k: [],
    )

    def fake_run_search(*, origin, destination, **k):
        if (origin, destination) == ("GYN", "LIS"):       # direto
            return {"ok": True, "money_offers": [], "miles_offers": [
                _miles_offer("GYN", "LIS", eq=2360, miles=78000, taxes=300,
                             dep="2026-10-15T20:00:00", arr="2026-10-16T10:00:00",
                             airline="TAP", prog="TAP Miles&Go")]}
        if (origin, destination) == ("GRU", "LIS"):       # internacional do hub
            return {"ok": True, "money_offers": [], "miles_offers": [
                _miles_offer("GRU", "LIS", eq=3004, miles=99000, taxes=350,
                             dep="2026-10-15T22:00:00", arr="2026-10-16T11:00:00",
                             airline="LATAM", prog="LATAM Pass")]}
        if (origin, destination) == ("GYN", "GRU"):       # doméstico pro hub
            return {"ok": True, "money_offers": [], "miles_offers": [
                _miles_offer("GYN", "GRU", eq=346, miles=14000, taxes=40,
                             dep="2026-10-15T16:00:00", arr="2026-10-15T18:00:00")]}
        return {"ok": True, "money_offers": [], "miles_offers": []}

    monkeypatch.setattr("backend.app.ai.agents.tools.run_search", fake_run_search)

    # Datas explícitas (bypassa o radar de matriz): direto + 1 hub (GRU).
    q = isplit.quote_international(origin="GYN", destination="LIS",
                                  direct_days=[day], hubs={"GRU": day})
    opts = q["options"]
    types = {o["type"] for o in opts}
    assert "direct_miles" in types and "hub_split" in types

    direct = next(o for o in opts if o["type"] == "direct_miles")
    assert round(direct["total_brl"]) == 2360
    assert direct["airline"] == "TAP"

    hub = next(o for o in opts if o["type"] == "hub_split")
    assert hub["hub"] == "GRU"
    assert round(hub["total_brl"]) == 3350          # 3004 + 346
    assert round(hub["intl_leg"]["brl"]) == 3004
    assert round(hub["domestic_leg"]["brl"]) == 346

    # Mais barata primeiro (direto milhas < hub).
    assert opts[0]["type"] == "direct_miles"


def test_hub_split_skips_domestic_that_misses_connection(monkeypatch):
    day = date(2026, 10, 15)

    class _Radar:
        ranked_pairs = [(day, None)]
        price_by_pair = {}
        source = "kayak"

    monkeypatch.setattr("backend.app.services.date_radar.scan_dates", lambda *a, **k: _Radar())
    monkeypatch.setattr("backend.app.ai.agents.sanitizer.sanitize_offers", lambda offers: offers)
    monkeypatch.setattr(
        "backend.app.ai.agents.hidden_city_validator.validate_split_with_supplementary",
        lambda offers, **k: [],
    )

    def fake_run_search(*, origin, destination, **k):
        if (origin, destination) == ("GYN", "LIS"):
            return {"ok": True, "money_offers": [], "miles_offers": []}  # sem direto
        if (origin, destination) == ("GRU", "LIS"):
            return {"ok": True, "money_offers": [], "miles_offers": [
                _miles_offer("GRU", "LIS", eq=3000, miles=99000, taxes=350,
                             dep="2026-10-15T22:00:00", arr="2026-10-16T11:00:00")]}
        if (origin, destination) == ("GYN", "GRU"):
            # Chega DEPOIS do voo internacional partir → não conecta.
            return {"ok": True, "money_offers": [], "miles_offers": [
                _miles_offer("GYN", "GRU", eq=346, miles=14000, taxes=40,
                             dep="2026-10-15T23:00:00", arr="2026-10-16T01:00:00")]}
        return {"ok": True, "money_offers": [], "miles_offers": []}

    monkeypatch.setattr("backend.app.ai.agents.tools.run_search", fake_run_search)

    q = isplit.quote_international(origin="GYN", destination="LIS",
                                  direct_days=[day], hubs={"GRU": day})
    # Doméstico não encaixa na janela → sem hub_split.
    assert all(o["type"] != "hub_split" for o in q["options"])
