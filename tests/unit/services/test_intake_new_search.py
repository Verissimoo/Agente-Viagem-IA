"""Regressão: uma nova busca não pode herdar slots de buscas anteriores no
mesmo thread (thread 'envenenado')."""
from langchain_core.messages import AIMessage, HumanMessage

from backend.app.ai.agents.intake import intake_node, is_new_search


def test_is_new_search_requires_full_route():
    assert is_new_search({"origin_iata": "BSB", "destination_iata": "SSA"}) is True
    assert is_new_search({"origin_iata": "BSB"}) is False
    assert is_new_search({}) is False


def test_new_search_clears_poisoned_flex_slots():
    poisoned = {
        "origin_iata": "VOO", "destination_iata": "SSA",
        "date_start": "2026-09-10", "date_end": "2026-09-12",
        "trip_type": "roundtrip", "return_from": "2026-09-25",
        "return_to": "2026-09-26", "flex_mode": "range", "adults": 1,
    }
    history = [
        HumanMessage(content="ida e volta entre 10 e 12 de setembro voltando entre 25 e 26"),
        AIMessage(content="..."),
        HumanMessage(content="voo de brasilia para salvador dia 20/09 so ida"),
    ]
    out = intake_node({"messages": history, "slots": dict(poisoned),
                       "user_id": "d", "thread_id": "t"})
    s = out["slots"]
    assert s.get("origin_iata") == "BSB"          # rota nova sobrescreve o lixo
    assert s.get("destination_iata") == "SSA"
    assert s.get("date_start") == "2026-09-20"     # data nova, não a velha
    assert s.get("trip_type") == "oneway"          # "só ida" respeitado
    assert not s.get("return_from") and not s.get("return_to")  # janelas velhas limpas
    assert s.get("flex_mode") in (None, "none")


def test_refinement_turn_keeps_route():
    # Turno só com data (sem rota) NÃO é nova busca — mantém a rota do turno anterior.
    base = {"origin_iata": "REC", "destination_iata": "FOR"}
    hist = [
        HumanMessage(content="quero ir de recife para fortaleza"),
        AIMessage(content="Qual a data?"),
        HumanMessage(content="dia 15/10 so ida"),
    ]
    out = intake_node({"messages": hist, "slots": dict(base),
                       "user_id": "d", "thread_id": "t2"})
    s = out["slots"]
    assert s.get("origin_iata") == "REC" and s.get("destination_iata") == "FOR"
    assert s.get("date_start") == "2026-10-15"
