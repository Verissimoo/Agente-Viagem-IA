"""Health-check de programas de milhas — classificação ok/empty/error + filtros."""
from datetime import date, timedelta

import backend.app.services.miles_healthcheck as hc


def _patch_run(monkeypatch, by_program):
    """Mocka _run_one_adapter: by_program[prog] -> (offers, error, elapsed_ms)."""
    def fake(cia_up, adapter_cls, req, use_fixtures, debug_dump):
        offers, error, elapsed = by_program.get(cia_up, ([], None, 10.0))
        return cia_up, offers, error, elapsed
    monkeypatch.setattr(hc, "_run_one_adapter", fake)


def test_status_ok_empty_error(monkeypatch):
    _patch_run(monkeypatch, {
        "LATAM": ([{"x": 1}], None, 120.0),          # ok
        "GOL": ([], None, 90.0),                     # empty
        "AMERICAN": ([], RuntimeError("9000"), 50.0),  # error
    })
    res = {r.program: r for r in hc.run_miles_healthcheck(["LATAM", "GOL", "AMERICAN"])}
    assert res["LATAM"].status == "ok" and res["LATAM"].offers_count == 1
    assert res["GOL"].status == "empty"
    assert res["AMERICAN"].status == "error"
    assert res["AMERICAN"].error_kind == "RuntimeError"
    assert "9000" in res["AMERICAN"].error_detail


def test_default_program_set(monkeypatch):
    _patch_run(monkeypatch, {})
    progs = {r.program for r in hc.run_miles_healthcheck()}
    # AZUL_CASH e o alias duplicado AMERICAN AIRLINES ficam de fora.
    assert "AZUL_CASH" not in progs
    assert "AMERICAN AIRLINES" not in progs
    # Kayak e Skiplagged ENTRAM (pedido do vendedor); milhas presentes.
    assert "KAYAK" in progs and "SKIPLAGGED" in progs
    assert "LATAM" in progs and "AMERICAN" in progs


def test_american_not_duplicated(monkeypatch):
    _patch_run(monkeypatch, {})
    progs = [r.program for r in hc.run_miles_healthcheck()]
    assert progs.count("AMERICAN") == 1  # sem o alias duplicado


def test_azul_canary_departs_vcp():
    assert hc._canary("AZUL")[0] == "VCP"


def test_dates_are_future_oneway(monkeypatch):
    captured = {}

    def fake(cia_up, adapter_cls, req, use_fixtures, debug_dump):
        captured["date_start"] = req.date_start
        captured["return_start"] = req.return_start
        return cia_up, [{"x": 1}], None, 10.0

    monkeypatch.setattr(hc, "_run_one_adapter", fake)
    hc.run_miles_healthcheck(["LATAM"])
    assert captured["date_start"] >= date.today() + timedelta(days=29)  # ~hoje+30
    assert captured["return_start"] is None                            # só-ida


def test_canary_env_override(monkeypatch):
    monkeypatch.setenv("MILES_CANARY_AMERICAN", "GRU>JFK")
    assert hc._canary("AMERICAN") == ("GRU", "JFK")
    # sem override usa o default
    monkeypatch.delenv("MILES_CANARY_LATAM", raising=False)
    assert hc._canary("LATAM") == ("GRU", "GIG")
