"""Testa o AwardToolAdapter sem hitar rede/navegador.

Cobre: flag desligada → []; sem credencial → []; erro do client absorvido → [];
sucesso (client mockado) → UnifiedOffers; cache evita 2ª chamada ao client.
"""
from datetime import date

import pytest

from backend.app.domain.models import CabinClass, SearchRequest, SourceType, TripType
from backend.app.infrastructure import cache as _cache
from backend.app.providers.awardtool import adapter as adapter_mod
from backend.app.providers.awardtool import client as client_mod
from backend.app.providers.awardtool import parser as parser_mod
from backend.app.providers.awardtool.adapter import AwardToolAdapter


@pytest.fixture(autouse=True)
def _clear_cache():
    _cache.invalidate("awardtool")
    yield
    _cache.invalidate("awardtool")


def _req():
    return SearchRequest(
        origin=["GRU"], destination=["LIS"],
        date_start=date(2026, 7, 15), date_end=date(2026, 7, 15),
        trip_type=TripType.ONEWAY, adults=1, cabin=CabinClass.ECONOMY,
    )


_RAW = [{
    "id": "TP_20260715_GRU_LIS_ECONOMY_TP84", "p_c": "TP", "a_p": 53000,
    "c": "USD", "sc": 41.91, "a_n": "TAP Portugal", "c_t": "Economy",
    "date": "2026-07-15", "url": "https://booking.flytap.com",
    "fare": {"ps": [{"or": "GRU", "de": "LIS", "d_t": "2026-07-15T00:45:00",
                     "a_t": "2026-07-15T14:35:00", "a_c": "TP", "f_n": "TP84"}]},
}]


def test_disabled_returns_empty(monkeypatch):
    monkeypatch.setenv("AWARDTOOL_ENABLED", "0")
    monkeypatch.setenv("AWARDTOOL_EMAIL", "x@y.com")
    monkeypatch.setenv("AWARDTOOL_PASSWORD", "pw")
    assert AwardToolAdapter().search(_req()) == []


def test_no_credentials_returns_empty(monkeypatch):
    monkeypatch.setenv("AWARDTOOL_ENABLED", "1")
    monkeypatch.delenv("AWARDTOOL_EMAIL", raising=False)
    monkeypatch.delenv("AWARDTOOL_PASSWORD", raising=False)
    assert AwardToolAdapter().search(_req()) == []


def test_success(monkeypatch):
    monkeypatch.setenv("AWARDTOOL_ENABLED", "1")
    monkeypatch.setenv("AWARDTOOL_EMAIL", "x@y.com")
    monkeypatch.setenv("AWARDTOOL_PASSWORD", "pw")
    monkeypatch.setattr(parser_mod.fx_rates, "convert", lambda a, f, t: a * 5.4)
    monkeypatch.setattr(adapter_mod, "search_awardtool", lambda *a, **kw: _RAW)
    offers = AwardToolAdapter().search(_req())
    assert len(offers) == 1
    assert offers[0].source == SourceType.AWARDTOOL
    assert offers[0].miles == 53000
    assert offers[0].miles_program == "TAP Miles&Go"


def test_client_error_absorbed(monkeypatch):
    monkeypatch.setenv("AWARDTOOL_ENABLED", "1")
    monkeypatch.setenv("AWARDTOOL_EMAIL", "x@y.com")
    monkeypatch.setenv("AWARDTOOL_PASSWORD", "pw")

    def _boom(*a, **kw):
        raise client_mod.AwardToolError("playwright crashed")
    monkeypatch.setattr(adapter_mod, "search_awardtool", _boom)
    assert AwardToolAdapter().search(_req()) == []


def test_cache_avoids_second_call(monkeypatch):
    monkeypatch.setenv("AWARDTOOL_ENABLED", "1")
    monkeypatch.setenv("AWARDTOOL_EMAIL", "x@y.com")
    monkeypatch.setenv("AWARDTOOL_PASSWORD", "pw")
    calls = {"n": 0}

    def _fn(*a, **kw):
        calls["n"] += 1
        return _RAW
    monkeypatch.setattr(adapter_mod, "search_awardtool", _fn)
    AwardToolAdapter().search(_req())
    AwardToolAdapter().search(_req())
    assert calls["n"] == 1, "2ª busca deveria vir do cache"
