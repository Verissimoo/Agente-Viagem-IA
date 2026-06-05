"""Etapa de confirmação internacional (flex > 3 dias): radar via MATRIZ flex do
Kayak das rotas (direto + hubs GRU/VCP) → pergunta → confirma → busca validada
(cada rota na sua melhor data)."""
from datetime import date

from langchain_core.messages import HumanMessage

import backend.app.ai.agents.orchestrator as orch


def _state(slots, messages=None):
    return {"slots": dict(slots), "user_id": "u", "thread_id": "t",
            "messages": messages or []}


_BASE = {
    "origin_iata": "GYN", "destination_iata": "LIS",
    "date_start": "2026-09-10", "date_end": "2026-09-16",
    "trip_type": "oneway", "adults": 1, "cabin": "economy",
}


def _mock_matrix(monkeypatch, by_route):
    """Mocka a matriz flex do Kayak: {(origin,dest): {iso: price}}."""
    def fake(origin, dest, center, flex_days=3):
        return {"prices_by_date": by_route.get((origin, dest)) or {}}
    monkeypatch.setattr("backend.app.providers.kayak.scraper.fetch_kayak_matrix", fake)


_GYN_LIS = {"2026-09-10": 3825.0, "2026-09-11": 3823.0, "2026-09-12": 3166.0,
            "2026-09-13": 3166.0, "2026-09-14": 2793.0, "2026-09-15": 3539.0,
            "2026-09-16": 3451.0}
_GRU_LIS = {"2026-09-13": 2609.0, "2026-09-16": 2469.0, "2026-09-11": 2955.0}
_VCP_LIS = {"2026-09-13": 2808.0, "2026-09-10": 2938.0}


def test_phase1_asks_with_direct_and_hubs(monkeypatch):
    monkeypatch.setenv("INTERNATIONAL_SPLIT_ENABLED", "1")
    _mock_matrix(monkeypatch, {("GYN", "LIS"): _GYN_LIS,
                               ("GRU", "LIS"): _GRU_LIS, ("VCP", "LIS"): _VCP_LIS})

    out = orch.orchestrator_node(_state(_BASE))
    assert out["next_node"] == "end"
    s = out["slots"]
    assert s["intl_awaiting_confirmation"] is True
    conf = s["intl_confirmation"]
    assert conf["direct_day"] == "2026-09-14"        # melhor data do direto (matriz)
    assert conf["hubs"]["GRU"] == "2026-09-16"       # melhor data do GRU
    assert conf["hubs"]["VCP"] == "2026-09-13"       # melhor data do VCP
    txt = out["messages"][0].content.lower()
    assert "voo direto" in txt and "via gru" in txt and "via vcp" in txt
    assert "14/09" in txt and "referência de outras datas" in txt


def test_phase2_runs_direct_and_each_hub_on_own_date(monkeypatch):
    monkeypatch.setenv("INTERNATIONAL_SPLIT_ENABLED", "1")
    captured = {}

    def fake_quote(**kw):
        captured["direct_days"] = kw.get("direct_days")
        captured["hubs"] = kw.get("hubs")
        return {"options": [{"type": "direct_miles", "date": "2026-09-14",
                             "total_brl": 2000, "airline": "LATAM", "segments": [{}]}],
                "reference": {}, "market_signal": None, "hubs": {}}

    monkeypatch.setattr("backend.app.services.international_split.quote_international", fake_quote)

    slots = {**_BASE, "intl_awaiting_confirmation": True,
             "intl_radar_dates": ["2026-09-14", "2026-09-15"],
             "intl_confirmation": {"direct_day": "2026-09-14",
                                   "hubs": {"GRU": "2026-09-16", "VCP": "2026-09-13"}}}
    out = orch.orchestrator_node(_state(slots, [HumanMessage(content="sim, pode")]))

    assert out["next_node"] == "presenter"
    assert captured["direct_days"] == [date(2026, 9, 14)]
    assert captured["hubs"] == {"GRU": date(2026, 9, 16), "VCP": date(2026, 9, 13)}
    assert out["slots"].get("intl_awaiting_confirmation") is False
    assert out["slots"].get("intl_confirmation") is None


def test_phase2_direct_day_missing_uses_hub_not_datestart(monkeypatch):
    """Regressão: radar do direto falhou (direct_day=None) → NÃO pode buscar em
    date_start (10/09); usa a melhor data de hub conhecida."""
    monkeypatch.setenv("INTERNATIONAL_SPLIT_ENABLED", "1")
    captured = {}

    def fake_quote(**kw):
        captured["direct_days"] = kw.get("direct_days")
        return {"options": [{"type": "direct_miles", "date": "2026-09-13",
                             "total_brl": 2000, "airline": "X", "segments": [{}]}]}

    monkeypatch.setattr("backend.app.services.international_split.quote_international", fake_quote)
    slots = {**_BASE, "intl_awaiting_confirmation": True, "intl_radar_dates": [],
             "intl_confirmation": {"direct_day": None,
                                   "hubs": {"GRU": "2026-09-16", "VCP": "2026-09-13"}}}
    out = orch.orchestrator_node(_state(slots, [HumanMessage(content="sim")]))
    assert captured["direct_days"] == [date(2026, 9, 13)]   # melhor hub, não date_start
    assert captured["direct_days"] != [date(2026, 9, 10)]   # NUNCA o início do range


def test_phase2_picks_other_date_for_direct(monkeypatch):
    monkeypatch.setenv("INTERNATIONAL_SPLIT_ENABLED", "1")
    captured = {}

    def fake_quote(**kw):
        captured["direct_days"] = kw.get("direct_days")
        return {"options": [{"type": "direct_miles", "date": "2026-09-15",
                             "total_brl": 2500, "airline": "TAP", "segments": [{}]}]}

    monkeypatch.setattr("backend.app.services.international_split.quote_international", fake_quote)

    slots = {**_BASE, "intl_awaiting_confirmation": True,
             "intl_radar_dates": ["2026-09-14", "2026-09-15"],
             "intl_confirmation": {"direct_day": "2026-09-14", "hubs": {"GRU": "2026-09-16"}}}
    out = orch.orchestrator_node(_state(slots, [HumanMessage(content="prefiro dia 15/09")]))
    assert captured["direct_days"] == [date(2026, 9, 15)]


def test_short_flex_skips_confirmation(monkeypatch):
    monkeypatch.setenv("INTERNATIONAL_SPLIT_ENABLED", "1")
    monkeypatch.setattr("backend.app.services.international_split.quote_international",
                        lambda **k: {"options": [{"type": "direct_miles", "date": "2026-09-11",
                                                  "total_brl": 2000, "airline": "LATAM", "segments": [{}]}]})
    # Flex de só 2 dias (10→12) → sem confirmação, busca direto.
    slots = {**_BASE, "date_start": "2026-09-10", "date_end": "2026-09-12"}
    out = orch.orchestrator_node(_state(slots))
    assert out["next_node"] == "presenter"
    assert out["slots"].get("intl_awaiting_confirmation") in (None, False)
