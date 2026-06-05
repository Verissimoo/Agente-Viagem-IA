"""Testes do intent parser (regex-only, sem LLM)."""
from datetime import date

import pytest

from backend.app.domain.models import CabinClass, TripType
from backend.app.nlp.intent_parser import parse_intent_regex


def test_parse_direct_flight():
    intent = parse_intent_regex("quero voo direto de BSB para LIS dia 20/06/2026")
    assert intent.direct_only is True
    assert intent.origin_iata == "BSB"
    assert intent.destination_iata == "LIS"
    assert intent.date_start == date(2026, 6, 20)


def test_parse_range_with_full_dates():
    intent = parse_intent_regex("de GRU para MIA entre 05/03/2027 e 15/03/2027")
    assert intent.flex_mode == "range"
    assert intent.depart_date_from == date(2027, 3, 5)
    assert intent.depart_date_to == date(2027, 3, 15)


@pytest.mark.parametrize("direct_phrase", ["sem escalas", "sem conexões", "voo direto"])
def test_parse_direct_synonyms(direct_phrase: str):
    intent = parse_intent_regex(f"BSB para GRU {direct_phrase}")
    assert intent.direct_only is True


@pytest.mark.parametrize(
    "phrase",
    ["com mala despachada", "com bagagem despachada", "23kg", "bagagem de 23kg", "com mala"],
)
def test_parse_baggage_requested(phrase: str):
    intent = parse_intent_regex(f"BSB para SSA {phrase}")
    assert intent.baggage_checked is True


@pytest.mark.parametrize(
    "phrase",
    ["só mochila", "apenas bagagem de mão", "sem bagagem despachada", "sem mala"],
)
def test_parse_baggage_declined(phrase: str):
    intent = parse_intent_regex(f"BSB para SSA {phrase}")
    assert intent.baggage_checked is False


def test_parse_baggage_unmentioned_is_none():
    intent = parse_intent_regex("BSB para SSA dia 20/07/2026")
    assert intent.baggage_checked is None


def test_route_not_confused_by_dates_in_sentence():
    # Regressão: frase longa com datas no meio fazia o regex capturar
    # "voo de brasilia" como origem → IATA lixo "VOO". Deve resolver BSB→SSA.
    intent = parse_intent_regex(
        "Quero um voo ida e volta podendo ir entre o dia 10 e 12 de setembro, "
        "e voltando entre 25 e 26 de setembro, seria um voo de Brasília para "
        "Salvador, veja o valor mais barato e me retorne a cotação"
    )
    assert intent.origin_iata == "BSB"
    assert intent.destination_iata == "SSA"


def test_parse_cabin_business():
    intent = parse_intent_regex("BSB para GRU executiva 20/06/2027")
    assert intent.cabin == CabinClass.BUSINESS


def test_parse_oneway_explicit():
    intent = parse_intent_regex("apenas ida BSB para GRU 10/12/2027")
    assert intent.trip_type == TripType.ONEWAY
    assert intent.date_return is None


def test_parse_ptbr_month_by_extension():
    intent = parse_intent_regex(
        "voo de brasilia para GRU, ida dia 21 de maio de 2027, volta dia 28 de maio de 2027"
    )
    assert intent.origin_iata == "BSB"
    assert intent.destination_iata == "GRU"
    assert intent.date_start == date(2027, 5, 21)
    assert intent.date_return == date(2027, 5, 28)


def test_parse_ptbr_short_month():
    intent = parse_intent_regex("Rio para Salvador em 5 de ago de 2027")
    assert intent.date_start == date(2027, 8, 5)


def test_parse_numeric_short_form():
    """Year-less `dd/mm` falls back to current year (or next if past)."""
    intent = parse_intent_regex("BSB para FOR 30/12 volta 15/01")
    assert intent.date_start is not None
    assert intent.date_return is not None
    # Dates should be sorted; return must come after departure
    assert intent.date_return > intent.date_start
