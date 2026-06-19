"""Testa o SeatsAeroAdapter sem hitar rede.

Cobre:
  - Sem SEATS_AERO_API_KEY → [] (degradação graciosa).
  - Flag SEATS_AERO_ENABLED=0 → [] mesmo com key.
  - Sucesso one-way: 1 oferta por programa, source SEATS_AERO.
  - Erro do /search é absorvido ([], não propaga).
  - Falha do /trips cai no itinerário sintético (oferta ainda sai).
  - SEATS_AERO_MAX_TRIPS limita o nº de /trips (proteção de quota).
  - Cache do client evita segundo /search.
"""
from datetime import date

import pytest

from backend.app.domain.models import CabinClass, SearchRequest, SourceType, TripType
from backend.app.infrastructure import cache as _cache
from backend.app.providers.seats_aero import adapter as adapter_mod
from backend.app.providers.seats_aero import client as client_mod
from backend.app.providers.seats_aero.adapter import SeatsAeroAdapter


@pytest.fixture(autouse=True)
def _clear_cache():
    _cache.invalidate("seats_aero")
    yield
    _cache.invalidate("seats_aero")


def _req(return_date=None):
    return SearchRequest(
        origin=["GRU"], destination=["YYZ"],
        date_start=date(2026, 7, 15), date_end=date(2026, 7, 15),
        return_start=date(2026, 7, 22) if return_date else None,
        return_end=date(2026, 7, 22) if return_date else None,
        trip_type=TripType.ROUNDTRIP if return_date else TripType.ONEWAY,
        adults=1, cabin=CabinClass.ECONOMY,
    )


def _raw_two_sources():
    return {
        "data": [
            {"ID": "a1", "Route": {"OriginAirport": "GRU", "DestinationAirport": "YYZ", "Source": "aeroplan"},
             "Date": "2026-07-15", "YAvailable": True, "YMileageCost": 60000, "YDirect": True, "YAirlines": "AC"},
            {"ID": "l1", "Route": {"OriginAirport": "GRU", "DestinationAirport": "YYZ", "Source": "lifemiles"},
             "Date": "2026-07-15", "YAvailable": True, "YMileageCost": 45000, "YDirect": True, "YAirlines": "AV"},
        ]
    }


def _raw_trip(carrier="AC"):
    return {"data": [{
        "MileageCost": 60000, "Cabin": "economy",
        "AvailabilitySegments": [
            {"FlightNumber": f"{carrier}100", "OriginAirport": "GRU", "DestinationAirport": "YYZ",
             "DepartsAt": "2026-07-15T22:00:00Z", "ArrivesAt": "2026-07-16T06:00:00Z"},
        ],
    }]}


def test_no_api_key_returns_empty(monkeypatch):
    monkeypatch.delenv("SEATS_AERO_API_KEY", raising=False)
    assert SeatsAeroAdapter().search(_req()) == []


def test_disabled_flag_returns_empty(monkeypatch):
    monkeypatch.setenv("SEATS_AERO_API_KEY", "k")
    monkeypatch.setenv("SEATS_AERO_ENABLED", "0")
    assert SeatsAeroAdapter().search(_req()) == []


def test_oneway_success_one_offer_per_source(monkeypatch):
    monkeypatch.setenv("SEATS_AERO_API_KEY", "k")
    monkeypatch.setenv("SEATS_AERO_ENABLED", "1")
    monkeypatch.setattr(adapter_mod, "search_availability", lambda *a, **kw: _raw_two_sources())
    monkeypatch.setattr(adapter_mod, "get_trip", lambda _id: _raw_trip())
    offers = SeatsAeroAdapter().search(_req())
    assert len(offers) == 2
    assert all(o.source == SourceType.SEATS_AERO for o in offers)
    programs = {o.miles_program for o in offers}
    assert programs == {"Aeroplan (Air Canada)", "Lifemiles (Avianca)"}


def test_search_error_absorbed(monkeypatch):
    monkeypatch.setenv("SEATS_AERO_API_KEY", "k")
    monkeypatch.setenv("SEATS_AERO_ENABLED", "1")

    def _boom(*a, **kw):
        raise client_mod.SeatsAeroError("upstream 500")
    monkeypatch.setattr(adapter_mod, "search_availability", _boom)
    assert SeatsAeroAdapter().search(_req()) == []


def test_trip_failure_falls_back_to_synthetic(monkeypatch):
    monkeypatch.setenv("SEATS_AERO_API_KEY", "k")
    monkeypatch.setenv("SEATS_AERO_ENABLED", "1")
    monkeypatch.setattr(adapter_mod, "search_availability", lambda *a, **kw: _raw_two_sources())

    def _boom(_id):
        raise client_mod.SeatsAeroError("trip 500")
    monkeypatch.setattr(adapter_mod, "get_trip", _boom)
    offers = SeatsAeroAdapter().search(_req())
    assert len(offers) == 2
    # Itinerário sintético: 1 segmento, horário aproximado sinalizado.
    assert all(len(o.outbound.segments) == 1 for o in offers)
    assert all("aproximados" in (o.risk_notes or "") for o in offers)


def test_max_trips_budget_limits_offers(monkeypatch):
    monkeypatch.setenv("SEATS_AERO_API_KEY", "k")
    monkeypatch.setenv("SEATS_AERO_ENABLED", "1")
    monkeypatch.setenv("SEATS_AERO_MAX_TRIPS", "1")
    monkeypatch.setattr(adapter_mod, "search_availability", lambda *a, **kw: _raw_two_sources())
    calls = {"n": 0}

    def _trip(_id):
        calls["n"] += 1
        return _raw_trip()
    monkeypatch.setattr(adapter_mod, "get_trip", _trip)
    offers = SeatsAeroAdapter().search(_req())
    assert len(offers) == 1, "budget de /trips deveria limitar a 1 oferta"
    assert calls["n"] == 1


def test_client_search_uses_cache(monkeypatch):
    monkeypatch.setenv("SEATS_AERO_API_KEY", "k")
    calls = {"n": 0}

    class _FakeClient:
        def search(self, params):
            calls["n"] += 1
            return _raw_two_sources()

    monkeypatch.setattr(client_mod, "_make_client_from_env", lambda: _FakeClient())
    client_mod.search_availability("GRU", "YYZ", "2026-07-15", ["aeroplan"], cabin="economy")
    client_mod.search_availability("GRU", "YYZ", "2026-07-15", ["aeroplan"], cabin="economy")
    assert calls["n"] == 1, "segundo /search deveria vir do cache"
