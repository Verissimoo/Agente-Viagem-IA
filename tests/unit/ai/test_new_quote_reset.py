"""BUG 4: nova cotação no mesmo thread não pode repetir o resultado anterior."""
import pytest

from backend.app.ai.agents.intake import looks_like_new_quote


@pytest.mark.parametrize("text", [
    "de Salvador para Marselha, 21/09, 2 adultos",
    "São Paulo para Lisboa só ida",
    "GRU -> LIS dia 10",
    "voo de Brasília para Fortaleza",
    "quero uma passagem para Madri",
    "saindo de Recife com destino a Lisboa",
])
def test_new_quote_detected(text):
    assert looks_like_new_quote(text) is True


@pytest.mark.parametrize("text", [
    "e voo direto?",
    "qual a bagagem despachada?",
    "aprovar a opção 1",
    "pode gerar o pdf",
    "qual a opção mais barata",
])
def test_refinement_not_treated_as_new_quote(text):
    assert looks_like_new_quote(text) is False


def test_clear_results_helper_wipes_previous_offers():
    from backend.app.api.v1.chat.routes import _clear_results_if_new_quote
    state = {
        "presented_offers": [{"offer_id": "x"}], "search_results": {"ok": True},
        "presented_at": "2026-01-01", "validation_report": {"r": 1},
        "approved_offer_id": "abc", "quote_id": "q1", "slots": {"origin_iata": "GRU"},
    }
    _clear_results_if_new_quote(state, "de Salvador para Marselha, 2 adultos")
    assert "presented_offers" not in state and "search_results" not in state
    assert "approved_offer_id" not in state and "quote_id" not in state
    assert state.get("slots")  # slots NÃO são apagados aqui (intake cuida da rota)


def test_clear_results_noop_on_refinement():
    from backend.app.api.v1.chat.routes import _clear_results_if_new_quote
    state = {"presented_offers": [{"offer_id": "x"}]}
    _clear_results_if_new_quote(state, "e tem voo direto?")
    assert state.get("presented_offers")  # refino mantém os resultados
