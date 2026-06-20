"""Testa o decoder do ciphered_data_v3 e o parser do AwardTool sobre uma
fixture de dados REAIS (capturada e decodificada em 2026-06-19).
"""
import json
from pathlib import Path

from backend.app.domain.models import SourceType, TripType
from backend.app.providers.awardtool import parser as parser_mod
from backend.app.providers.awardtool.cipher import decode_v3, encode_v3
from backend.app.services.conversion import offer_equivalent_brl

_FIXTURE = json.loads(
    (Path(__file__).parent / "fixture_results.json").read_text(encoding="utf-8")
)


def test_cipher_roundtrip():
    # encode_v3 é a inversa exata de decode_v3 (shift∓1 / reverse / swap / b64 / zlib)
    enc = encode_v3(_FIXTURE)
    assert isinstance(enc, str) and enc
    assert decode_v3(enc) == _FIXTURE


def test_decode_real_sample_head():
    # confirma os campos esperados do JSON decodificado
    item = _FIXTURE["result"][0]
    assert item["a_p"] > 0
    assert item["p_c"]
    assert item["fare"]["ps"]


def test_parser_builds_offers(monkeypatch):
    monkeypatch.setattr(parser_mod.fx_rates, "convert", lambda amt, f, t: amt * 5.4)
    offers = parser_mod.parse_search_result(_FIXTURE)
    assert len(offers) >= 1
    o = offers[0]
    assert o.source == SourceType.AWARDTOOL
    assert o.trip_type == TripType.ONEWAY
    assert o.miles > 0
    assert o.miles_program            # label resolvida (ex "TAP Miles&Go")
    assert o.outbound.segments        # tem segmentos com horário
    assert o.outbound.segments[0].departure_dt is not None
    # tarifa base 0,05 (international fallback p/ AWARDTOOL) → equivalent_brl > 0
    assert offer_equivalent_brl(o) > 0


def test_parser_skips_unpriced():
    payload = {"result": [{"p_c": "AC", "a_p": 0, "fare": {"ps": []}}]}
    assert parser_mod.parse_search_result(payload) == []
