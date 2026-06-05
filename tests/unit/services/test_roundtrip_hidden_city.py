"""Hidden city ida-e-volta = duas pernas só-ida somadas."""
from datetime import date


def test_two_oneways_sums_best_per_leg(monkeypatch):
    import backend.app.ai.agents.tools as tools

    def fake_run_search(**kw):
        o, d = kw["origin"], kw["destination"]
        # Award direto em milhas por perna (sem hidden city → validate é no-op).
        eq = 280.0 if o == "BSB" else 300.0
        seg = {
            "origin": o, "destination": d, "carrier": "G3",
            "departure_dt": "2099-09-12T10:00:00",
            "arrival_dt": "2099-09-12T12:00:00",
        }
        return {
            "ok": True, "money_offers": [],
            "miles_offers": [{
                "airline": "GOL", "miles": 15300, "taxes_brl": 33,
                "equivalent_brl": eq, "outbound": {"segments": [seg]},
            }],
        }

    monkeypatch.setattr(tools, "run_search", fake_run_search)

    from backend.app.services.roundtrip_hidden_city import quote_roundtrip_two_oneways
    q = quote_roundtrip_two_oneways(
        origin="BSB", destination="SSA",
        ida_date=date(2099, 9, 12), volta_date=date(2099, 9, 25),
    )
    assert q is not None
    assert q["total_miles"] == 30600          # 15300 ida + 15300 volta
    assert q["total_taxes_brl"] == 66.0       # 33 + 33
    assert round(q["total_brl"]) == 580       # 280 + 300
    assert q["ida"]["destination"] == "SSA"
    assert q["volta"]["destination"] == "BSB"  # volta é D→O


def test_returns_none_when_a_leg_empty(monkeypatch):
    import backend.app.ai.agents.tools as tools

    def fake_run_search(**kw):
        if kw["origin"] == "BSB":  # ida ok
            return {"ok": True, "money_offers": [], "miles_offers": [{
                "airline": "GOL", "miles": 15300, "taxes_brl": 33, "equivalent_brl": 280.0,
                "outbound": {"segments": [{"origin": "BSB", "destination": "SSA", "carrier": "G3",
                                           "departure_dt": "2099-09-12T10:00:00",
                                           "arrival_dt": "2099-09-12T12:00:00"}]},
            }]}
        return {"ok": True, "money_offers": [], "miles_offers": []}  # volta vazia

    monkeypatch.setattr(tools, "run_search", fake_run_search)
    from backend.app.services.roundtrip_hidden_city import quote_roundtrip_two_oneways
    q = quote_roundtrip_two_oneways(
        origin="BSB", destination="SSA",
        ida_date=date(2099, 9, 12), volta_date=date(2099, 9, 25),
    )
    assert q is None
