"""Testes da extração de janelas ida/volta separadas (intake)."""
from backend.app.ai.agents.intake import _extract_depart_return_windows


def test_two_windows_with_month_both():
    dep, ret = _extract_depart_return_windows(
        "ir entre 10 e 13 de setembro e voltando entre 25 e 27 de setembro"
    )
    assert dep and ret
    assert (dep[0].day, dep[1].day) == (10, 13)
    assert (ret[0].day, ret[1].day) == (25, 27)
    assert dep[0].month == 9 and ret[0].month == 9


def test_ida_e_volta_idiom_not_confused():
    # "ida e volta" não pode virar marcador de volta (substantivo).
    dep, ret = _extract_depart_return_windows(
        "Quero ida e volta, ir do dia 10 e 12 se setembro, e voltando entre 25 e 26 de setembro"
    )
    assert dep and ret
    assert (dep[0].day, dep[1].day) == (10, 12)
    assert (ret[0].day, ret[1].day) == (25, 26)


def test_return_window_inherits_month():
    dep, ret = _extract_depart_return_windows("ida 10 a 12 de outubro, voltar entre 20 e 22")
    assert dep and ret
    assert ret[0].month == 10 and (ret[0].day, ret[1].day) == (20, 22)


def test_single_date_no_windows():
    dep, ret = _extract_depart_return_windows("voo de brasilia para salvador dia 20 de julho")
    assert dep is None and ret is None


def test_oneway_flex_range_two_months():
    # "voo de ida ... entre 15 de setembro ao dia 20 de setembro" → one-way,
    # range 15-20 (não roundtrip com a 2ª data como volta).
    from langchain_core.messages import HumanMessage
    from backend.app.ai.agents.intake import intake_node
    s = intake_node({
        "messages": [HumanMessage(content=(
            "voo de ida de brasilia para Salvador entre dia 15 de setembro "
            "ao dia 20 de setembro, a data mais barata"
        ))],
        "slots": {}, "user_id": "d", "thread_id": "t",
    })
    sl = s["slots"]
    assert sl.get("origin_iata") == "BSB" and sl.get("destination_iata") == "SSA"
    assert sl.get("trip_type") == "oneway"
    assert sl.get("date_return") is None
    assert sl.get("flex_mode") == "range"
    assert sl.get("date_start") == "2026-09-15" and sl.get("date_end") == "2026-09-20"
