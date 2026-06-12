"""Camada de interpretação por LLM: mapeamento validado + integração no intake.
A LLM em si é mockada (determinístico)."""
from datetime import date

from langchain_core.messages import HumanMessage

import backend.app.ai.agents.interpreter as interp
from backend.app.ai.agents.interpreter import _parse_json, to_slots


def test_parse_json_strips_fences():
    assert _parse_json('```json\n{"adults": 2}\n```') == {"adults": 2}
    assert _parse_json("texto sem json") is None


def test_to_slots_two_windows():
    raw = {
        "origin_city": "Brasília", "destination_city": "Salvador",
        "trip_type": "roundtrip",
        "depart": {"from": "2099-07-10", "to": "2099-07-12"},
        "return": {"from": "2099-07-20", "to": "2099-07-21"},
        "flexible_dates": True, "direct_only": False,
        "time_preference": "manha", "cabin": "economy",
        "adults": 1, "children": 0, "infants": 0,
    }
    s = to_slots(raw, today=date(2099, 1, 1))
    assert s["origin_iata"] == "BSB" and s["destination_iata"] == "SSA"
    assert s["date_start"] == "2099-07-10" and s["date_end"] == "2099-07-12"
    assert s["return_from"] == "2099-07-20" and s["return_to"] == "2099-07-21"
    assert s["flex_mode"] == "range" and s["trip_type"] == "roundtrip"
    assert s["time_preference"] == "manha"


def test_to_slots_duration_plus_window_is_sliding():
    # "viagem de 10 dias entre 10 e 25" → janela de ida + duração, SEM volta fixa
    # (o planner desliza: 10→20, 11→21, …). Regressão do bug "só varreu 10→20".
    raw = {
        "origin_city": "Brasília", "destination_city": "Fortaleza",
        "trip_type": "roundtrip",
        "depart": {"from": "2099-09-10", "to": "2099-09-25"},
        "return": None, "trip_duration_days": 10, "flexible_dates": True,
    }
    s = to_slots(raw, today=date(2099, 1, 1))
    assert s["date_start"] == "2099-09-10" and s["date_end"] == "2099-09-25"
    assert s["trip_duration_days"] == 10
    assert s["flex_mode"] == "range" and s["trip_type"] == "roundtrip"
    assert not s.get("return_from")           # volta NÃO é fixada


def test_to_slots_duration_window_drops_fixed_return():
    # Mesmo se a LLM colapsar e mandar volta fixa, com duração+janela a volta cai.
    raw = {
        "origin_city": "Brasília", "destination_city": "Fortaleza",
        "trip_type": "roundtrip",
        "depart": {"from": "2099-09-10", "to": "2099-09-25"},
        "return": {"from": "2099-09-20", "to": "2099-09-20"},
        "trip_duration_days": 10,
    }
    s = to_slots(raw, today=date(2099, 1, 1))
    assert s["date_end"] == "2099-09-25" and s["trip_duration_days"] == 10
    assert not s.get("return_from") and not s.get("return_to")


def test_to_slots_multi_airport_city_top2():
    # São Paulo tem 3 aeroportos; busca os 2 principais (GRU internacional + VCP
    # Azul), NÃO só o 1º alfabético (CGH doméstico). Regressão do voo Azul VCP perdido.
    raw = {
        "origin_city": "São Paulo", "destination_city": "Lisboa",
        "depart": {"from": "2099-09-06", "to": "2099-09-06"}, "trip_type": "oneway",
    }
    s = to_slots(raw, today=date(2099, 1, 1))
    assert s["origin_iata"] == "GRU"                    # primário = hub internacional
    assert s["origin_iatas"] == ["GRU", "VCP"]          # top-2 (sem CGH doméstico)
    assert s["destination_iata"] == "LIS"
    assert not s.get("destination_iatas")               # Lisboa é único aeroporto


def test_to_slots_oneway_baggage_direct():
    raw = {
        "origin_city": "Recife", "destination_city": "Fortaleza",
        "trip_type": "oneway", "depart": {"from": "2099-09-20", "to": "2099-09-20"},
        "return": None, "baggage_checked": True, "direct_only": True,
    }
    s = to_slots(raw, today=date(2099, 1, 1))
    assert s["origin_iata"] == "REC" and s["destination_iata"] == "FOR"
    assert s["trip_type"] == "oneway"
    assert "return_from" not in s and "date_end" not in s
    assert s["baggage_checked"] is True and s["direct_only"] is True


def test_to_slots_ignores_past_dates():
    raw = {"origin_city": "BSB", "destination_city": "SSA",
           "depart": {"from": "2000-01-01", "to": "2000-01-05"}}
    s = to_slots(raw, today=date(2099, 1, 1))
    assert "date_start" not in s  # data no passado é ignorada


def test_intake_uses_llm_interpretation(monkeypatch):
    # Mocka a LLM devolvendo o caso do print (ida 10-12, volta 20-21).
    monkeypatch.setattr(interp, "interpret", lambda text, today: {
        "origin_city": "Brasília", "destination_city": "Salvador",
        "trip_type": "roundtrip",
        "depart": {"from": "2099-07-10", "to": "2099-07-12"},
        "return": {"from": "2099-07-20", "to": "2099-07-21"},
        "flexible_dates": True,
    })
    from backend.app.ai.agents.intake import intake_node
    out = intake_node({
        "messages": [HumanMessage(content=(
            "Quero um voo de Brasília para Salvador, ida entre 10/07 e 12/07 "
            "e volta sendo dia 20/07 ou 21/07"
        ))],
        "slots": {}, "user_id": "d", "thread_id": "t",
    })
    s = out["slots"]
    assert s.get("origin_iata") == "BSB" and s.get("destination_iata") == "SSA"
    assert s.get("return_from") == "2099-07-20" and s.get("return_to") == "2099-07-21"
    assert s.get("date_start") == "2099-07-10" and s.get("date_end") == "2099-07-12"
    assert s.get("flex_mode") == "range" and s.get("trip_type") == "roundtrip"


def test_to_slots_resolves_international_and_pax():
    # BUG 2: ordem livre + IATA internacional resolve (depende do BUG 1).
    raw = {
        "origin_city": "Salvador", "destination_city": "Marselha",
        "trip_type": "roundtrip", "depart": {"from": "2099-09-21", "to": None},
        "return": {"from": "2099-10-01", "to": None},
        "adults": 3, "children": 1, "infants": 0,
    }
    s = to_slots(raw, today=date(2099, 1, 1))
    assert s["origin_iata"] == "SSA" and s["destination_iata"] == "MRS"
    assert s["adults"] == 3 and s["children"] == 1
    assert s["date_start"] == "2099-09-21" and s["return_from"] == "2099-10-01"
    assert s["trip_type"] == "roundtrip"


def test_to_slots_preserves_unresolved_city_name():
    # Cidade que não resolve em IATA → preserva o NOME (não descarta).
    raw = {"origin_city": "Salvador", "destination_city": "Cidade Totalmente Inexistente",
           "trip_type": "oneway", "adults": 2}
    s = to_slots(raw, today=date(2099, 1, 1))
    assert s["origin_iata"] == "SSA"
    assert s.get("destination_city") == "Cidade Totalmente Inexistente"
    assert not s.get("destination_iata")        # não resolveu, mas não sumiu
    assert s["adults"] == 2
