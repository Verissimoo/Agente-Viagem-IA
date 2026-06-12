"""Troca de aeroporto na conexão: detectar, penalizar no ranking e filtrar."""
from langchain_core.messages import HumanMessage

from backend.app.ai.agents.presenter import (
    _has_airport_change, _apply_filter, _detect_filter, _recommendation_score,
)


def _offer(segs, price=1000.0):
    return {"price_brl": price, "category": "Cash direto",
            "outbound": {"segments": segs}}


def test_detects_airport_change():
    # GYN→CGH depois GRU→LIS: chega Congonhas, sai Guarulhos → troca.
    troca = _offer([
        {"origin": "GYN", "destination": "CGH"},
        {"origin": "GRU", "destination": "LIS"},
    ])
    assert _has_airport_change(troca) is True


def test_clean_connection_no_change():
    limpo = _offer([
        {"origin": "GYN", "destination": "GRU"},
        {"origin": "GRU", "destination": "LIS"},
    ])
    assert _has_airport_change(limpo) is False
    assert _has_airport_change(_offer([{"origin": "GYN", "destination": "LIS"}])) is False


def test_airport_change_penalized_in_score():
    troca = _offer([{"origin": "GYN", "destination": "CGH"}, {"origin": "GRU", "destination": "LIS"}], price=1000)
    limpo = _offer([{"origin": "GYN", "destination": "GRU"}, {"origin": "GRU", "destination": "LIS"}], price=1000)
    # Mesmo preço → a com troca pontua PIOR (score maior = pior no ranking).
    assert _recommendation_score(troca) > _recommendation_score(limpo)


def test_filter_excludes_airport_change():
    troca = _offer([{"origin": "GYN", "destination": "CGH"}, {"origin": "GRU", "destination": "LIS"}])
    limpo = _offer([{"origin": "GYN", "destination": "GRU"}, {"origin": "GRU", "destination": "LIS"}])
    out = _apply_filter([troca, limpo], "__no_airport_change__")
    assert out == [limpo]


def test_detect_filter_no_airport_change():
    state = {"messages": [HumanMessage(content="me traz resultados sem troca de aeroporto")]}
    f = _detect_filter(state)
    assert f is not None and f[1] == "__no_airport_change__"
