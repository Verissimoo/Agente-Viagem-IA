"""Asserções de negócio sobre POST /api/v1/smart-quote/explore (Phase 1)."""
from __future__ import annotations

import pytest


ROUTE = "/api/v1/smart-quote/explore"


def _post(client, **overrides):
    payload = {
        "origin": "GRU",
        "destination": "SSA",
        "adults": 1,
        "flex_days": 3,
    }
    payload.update(overrides)
    return client.post(ROUTE, json=payload)


def test_explore_returns_trio_cards(client, future_date):
    """Phase 1 sempre retorna os 3 cards do topo do smart-explore."""
    body = _post(client, date_start=future_date).json()
    assert "requested_date" in body
    assert "requested_date_price_brl" in body
    assert "savings_brl" in body
    assert "is_already_best" in body
    assert "stability" in body


def test_explore_calendar_no_outliers(client, future_date):
    """Filtro de outlier (mediana×0.4) garante que nenhum dia mostre preço
    absurdamente baixo (ex.: $20 USD = R$ 100 fake)."""
    body = _post(client, date_start=future_date).json()
    valid_prices = [d["min_price_brl"] for d in body["days"] if d["min_price_brl"]]
    if len(valid_prices) < 2:
        pytest.skip("dados insuficientes")
    cmin = min(valid_prices)
    cmed = sorted(valid_prices)[len(valid_prices) // 2]
    assert cmin >= cmed * 0.40 - 1, (
        f"min price R$ {cmin:.2f} suspeito: < 40% da mediana R$ {cmed:.2f}"
    )


def test_best_date_matches_min_price(client, future_date):
    body = _post(client, date_start=future_date).json()
    if not body.get("best_date"):
        pytest.skip("sem ofertas")
    best_day = next(d for d in body["days"] if d["date"] == body["best_date"])
    other_min = min(
        (d["min_price_brl"] for d in body["days"] if d["min_price_brl"]),
        default=None,
    )
    assert best_day["min_price_brl"] == other_min


def test_savings_calculation_consistent(client, future_date):
    body = _post(client, date_start=future_date).json()
    if body["requested_date_price_brl"] is None or body["best_price_brl"] is None:
        pytest.skip("dados parciais")
    expected = max(0.0, body["requested_date_price_brl"] - body["best_price_brl"])
    assert abs(body["savings_brl"] - expected) < 0.01
