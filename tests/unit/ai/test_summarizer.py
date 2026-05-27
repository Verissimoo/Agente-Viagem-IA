"""
Testes do summarizer — sem rede. Cobre flag desligada, ofertas vazias,
falha no LLM (absorvida), e construção do prompt compacto.
"""
from datetime import datetime

import pytest

from backend.app.ai import summarizer as summ
from backend.app.domain.models import (
    Itinerary,
    Scenario,
    Segment,
    SourceType,
    TripType,
    UnifiedOffer,
)


def _offer(
    *,
    price: float | None = 380.0,
    miles: int | None = None,
    scenario: Scenario | None = Scenario.CASH_DIRECT,
    layover: str | None = None,
) -> UnifiedOffer:
    seg = Segment(
        origin="GIG",
        destination="SSA",
        departure_dt=datetime(2026, 7, 15, 8, 0),
        arrival_dt=datetime(2026, 7, 15, 10, 30),
        carrier="G3",
    )
    return UnifiedOffer(
        source=SourceType.SKIPLAGGED if scenario == Scenario.HIDDEN_CITY else SourceType.KAYAK,
        airline="GOL",
        trip_type=TripType.ONEWAY,
        outbound=Itinerary(segments=[seg]),
        price_brl=price,
        miles=miles,
        taxes_brl=80.0 if miles else None,
        equivalent_brl=price or (miles * 0.02 if miles else None),
        scenario=scenario,
        layover_city=layover,
    )


def test_summarize_returns_none_when_flag_disabled(monkeypatch):
    monkeypatch.delenv("ENABLE_AI_SUMMARY", raising=False)
    result = summ.summarize([_offer()], origin="GIG", destination="SSA", date="2026-07-15")
    assert result is None


def test_summarize_returns_none_when_offers_empty(monkeypatch):
    monkeypatch.setenv("ENABLE_AI_SUMMARY", "1")
    monkeypatch.setenv("GROQ_API_KEY", "fake-key")
    assert summ.summarize([], origin="GIG", destination="SSA", date="2026-07-15") is None


def test_summarize_returns_none_when_no_api_key(monkeypatch):
    monkeypatch.setenv("ENABLE_AI_SUMMARY", "1")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    assert summ.summarize([_offer()], origin="GIG", destination="SSA", date="2026-07-15") is None


def test_summarize_absorbs_llm_failure(monkeypatch):
    monkeypatch.setenv("ENABLE_AI_SUMMARY", "1")
    monkeypatch.setenv("GROQ_API_KEY", "fake-key")

    def _boom(**kwargs):
        raise RuntimeError("Groq offline")
    monkeypatch.setattr(summ, "_get_completion", lambda: _boom)

    result = summ.summarize([_offer()], origin="GIG", destination="SSA", date="2026-07-15")
    assert result is None


def test_summarize_returns_llm_text_when_succeeds(monkeypatch):
    monkeypatch.setenv("ENABLE_AI_SUMMARY", "1")
    monkeypatch.setenv("GROQ_API_KEY", "fake-key")

    class _FakeMsg:
        content = "Recomendo o GOL em cash direto por R$ 380; economia de R$ 100 vs hidden-city."
    class _FakeChoice:
        message = _FakeMsg()
    class _FakeResp:
        choices = [_FakeChoice()]

    def _fake(**kwargs):
        # garante que o prompt está montado e compacto (<2500 chars)
        msg = kwargs["messages"][0]["content"]
        assert "GIG" in msg and "SSA" in msg
        assert len(msg) < 2500
        return _FakeResp()
    monkeypatch.setattr(summ, "_get_completion", lambda: _fake)

    result = summ.summarize(
        [_offer(), _offer(price=480.0, scenario=Scenario.HIDDEN_CITY, layover="SSA")],
        origin="GIG",
        destination="SSA",
        date="2026-07-15",
    )
    assert result is not None
    assert "GOL" in result


def test_format_offer_line_compact():
    line = summ._format_offer_line(_offer(scenario=Scenario.HIDDEN_CITY, layover="SSA"))
    assert "R$380" in line
    assert "scenario=hidden_city" in line
    assert "layover=SSA" in line


def test_format_offer_line_miles_offer():
    line = summ._format_offer_line(
        _offer(price=None, miles=10000, scenario=Scenario.MILES_DIRECT)
    )
    assert "10000mi" in line
    assert "+R$80" in line
