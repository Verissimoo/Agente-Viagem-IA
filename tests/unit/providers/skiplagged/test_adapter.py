"""
Testa o SkiplaggedAdapter sem hitar rede.

Cobre:
  - Sucesso (mock fetch retorna payload válido).
  - Erro do fetch é absorvido (lista vazia, não propaga).
  - Cache hit evita segundo fetch.
  - Feature flag SKIPLAGGED_ENABLED=0 desliga o adapter.
"""
import os
from datetime import date

import pytest

from backend.app.providers.skiplagged import adapter as adapter_mod
from backend.app.providers.skiplagged.adapter import SkiplaggedAdapter
from backend.app.domain.models import CabinClass, SearchRequest, SourceType, TripType
from backend.app.infrastructure import cache as _cache


@pytest.fixture(autouse=True)
def _clear_cache():
    _cache.invalidate("skiplagged")
    yield
    _cache.invalidate("skiplagged")


def _make_request():
    return SearchRequest(
        origin=["GIG"],
        destination=["SSA"],
        date_start=date(2026, 7, 15),
        date_end=date(2026, 7, 15),
        trip_type=TripType.ONEWAY,
        adults=1,
        cabin=CabinClass.ECONOMY,
    )


_FAKE_PAYLOAD = {
    "airlines": {},
    "cities": {},
    "airports": {},
    "flights": {
        "fid01": {
            "segments": [
                {
                    "airline": "G3",
                    "flight_number": 100,
                    "departure": {"airport": "GIG", "time": "2026-07-15T08:00:00-03:00"},
                    "arrival": {"airport": "SSA", "time": "2026-07-15T10:30:00-03:00"},
                    "duration": 9000,
                }
            ],
            "duration": 9000,
            "count": 1,
        }
    },
    "itineraries": {
        "outbound": [{"flight": "fid01", "one_way_price": 35000}],
    },
}


def test_adapter_returns_offers_on_success(monkeypatch):
    monkeypatch.setattr(adapter_mod, "fetch_skiplagged", lambda *a, **kw: _FAKE_PAYLOAD)
    offers = SkiplaggedAdapter().search(_make_request())
    assert len(offers) == 1
    assert offers[0].source == SourceType.SKIPLAGGED


def test_adapter_returns_empty_when_fetch_raises(monkeypatch):
    def _boom(*a, **kw):
        raise RuntimeError("Skiplagged offline")
    monkeypatch.setattr(adapter_mod, "fetch_skiplagged", _boom)
    offers = SkiplaggedAdapter().search(_make_request())
    assert offers == []


def test_adapter_returns_empty_when_payload_none(monkeypatch):
    monkeypatch.setattr(adapter_mod, "fetch_skiplagged", lambda *a, **kw: None)
    offers = SkiplaggedAdapter().search(_make_request())
    assert offers == []


def test_adapter_uses_cache_on_second_call(monkeypatch):
    calls = {"n": 0}
    def _fetch(*a, **kw):
        calls["n"] += 1
        return _FAKE_PAYLOAD
    monkeypatch.setattr(adapter_mod, "fetch_skiplagged", _fetch)
    SkiplaggedAdapter().search(_make_request())
    SkiplaggedAdapter().search(_make_request())
    assert calls["n"] == 1, "segundo search deveria ter pego do cache"


def test_feature_flag_disabled_skips_fetch(monkeypatch):
    monkeypatch.setenv("SKIPLAGGED_ENABLED", "0")
    def _boom(*a, **kw):
        pytest.fail("fetch não deveria ser chamado com flag desligada")
    monkeypatch.setattr(adapter_mod, "fetch_skiplagged", _boom)
    offers = SkiplaggedAdapter().search(_make_request())
    assert offers == []
