"""Testes do planejador de pares (ida, volta) para cotações flexíveis."""
from datetime import date

from backend.app.services.flex_planner import build_candidate_pairs


def test_cross_product_window():
    pairs = build_candidate_pairs(
        depart_start=date(2099, 9, 10), depart_end=date(2099, 9, 12),
        return_start=date(2099, 9, 25), return_end=date(2099, 9, 26),
    )
    # 3 idas × 2 voltas = 6, todas com volta >= ida
    assert len(pairs) == 6
    assert all(v >= i for i, v in pairs)
    assert (date(2099, 9, 10), date(2099, 9, 25)) in pairs


def test_duration_within_range():
    pairs = build_candidate_pairs(
        depart_start=date(2099, 9, 10), depart_end=date(2099, 9, 25),
        duration_days=5,
    )
    assert all((v - i).days == 5 for i, v in pairs)
    assert pairs[0][0] == date(2099, 9, 10)


def test_ida_flex_fixed_return():
    pairs = build_candidate_pairs(
        depart_start=date(2099, 9, 10), depart_end=date(2099, 9, 13),
        single_return=date(2099, 9, 25), flex_mode="range",
    )
    assert len(pairs) == 4
    assert all(v == date(2099, 9, 25) for _, v in pairs)


def test_cap_sampling_keeps_extremes():
    pairs = build_candidate_pairs(
        depart_start=date(2099, 9, 1), depart_end=date(2099, 9, 7),
        return_start=date(2099, 9, 20), return_end=date(2099, 9, 26),
        cap=16,
    )
    assert len(pairs) <= 16
    # extremos preservados
    assert (date(2099, 9, 1), date(2099, 9, 20)) in pairs


def test_no_return_info_yields_empty():
    pairs = build_candidate_pairs(depart_start=date(2099, 9, 10))
    assert pairs == []
