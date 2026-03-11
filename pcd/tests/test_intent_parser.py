import pytest
from datetime import date
from pcd.nlp.intent_parser import parse_intent_regex
from pcd.core.schema import TripType, CabinClass

def test_parse_direct_flight():
    prompt = "quero voo direto de BSB para LIS dia 20/06/2026"
    intent = parse_intent_regex(prompt)
    assert intent.direct_only is True
    assert intent.origin_iata == "BSB"
    assert intent.destination_iata == "LIS"
    assert intent.date_start == date(2026, 6, 20)

def test_parse_plusminus_flex():
    prompt = "Madrid para São Paulo dia 09/03/2026 ou datas próximas se tiver melhor preço"
    intent = parse_intent_regex(prompt)
    assert intent.flex_mode == "plusminus"
    assert intent.flex_days == 3
    assert intent.origin_iata == "MAD"
    assert intent.destination_iata == "SAO" # SAO é o código da cidade

def test_parse_range_flex():
    prompt = "para o dia 9/03, com flexibilidade do dia 5 ao dia 15"
    # Note: Regex de cidade pode falhar aqui se não houver de/para, mas testamos a flexibilidade
    intent = parse_intent_regex(prompt)
    assert intent.flex_mode == "range"
    assert intent.depart_date_from.day == 5
    assert intent.depart_date_to.day == 15
    # Verifica se assumiu o mês/ano da data base (9/03)
    assert intent.depart_date_from.month == 3
    assert intent.depart_date_to.month == 3

def test_parse_range_with_slashes():
    prompt = "de GRU para MIA entre 05/03/2026 e 15/03/2026"
    intent = parse_intent_regex(prompt)
    assert intent.flex_mode == "range"
    assert intent.depart_date_from == date(2026, 3, 5)
    assert intent.depart_date_to == date(2026, 3, 15)

def test_parse_no_stops_synonyms():
    prompts = ["sem escalas", "sem conexões", "voo direto"]
    for p in prompts:
        intent = parse_intent_regex(f"BSB para GRU {p}")
        assert intent.direct_only is True, f"Falhou para: {p}"
