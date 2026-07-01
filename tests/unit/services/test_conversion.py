"""Tests for tiered miles→BRL conversion."""
import pytest

from backend.app.domain.models import SourceType
from backend.app.services.conversion import (
    cost_per_mile,
    estimate_miles_for_brl,
    miles_to_brl,
    skiplagged_estimation_program,
)


def test_cost_per_mile_program_weights():
    """Pesos por programa (milheiro ÷ 1000) da tabela PCD 2026-07-01."""
    assert cost_per_mile(program="LATAM")     == pytest.approx(0.0285)   # 28,5
    assert cost_per_mile(program="SMILES")    == pytest.approx(0.0180)   # 18
    assert cost_per_mile(program="AZUL")      == pytest.approx(0.0180)
    assert cost_per_mile(program="INTERLINE") == pytest.approx(0.0180)
    assert cost_per_mile(program="TAP")       == pytest.approx(0.0550)   # 55
    assert cost_per_mile(program="AVIOS")     == pytest.approx(0.0640)   # 64
    assert cost_per_mile(program="COPA")      == pytest.approx(0.0720)   # 72


def test_rate_for_volume_tier_logic():
    """A lógica de faixas (_rate_for_volume) segue válida (max_miles inclusivo)."""
    from backend.app.services.conversion import _rate_for_volume
    tiers = [
        {"max_miles": 17000, "rate": 0.030},
        {"max_miles": 24000, "rate": 0.029},
        {"max_miles": None,  "rate": 0.025},
    ]
    assert _rate_for_volume(tiers, 17000) == pytest.approx(0.030)  # limite = faixa de baixo
    assert _rate_for_volume(tiers, 17001) == pytest.approx(0.029)
    assert _rate_for_volume(tiers, 999999) == pytest.approx(0.025)  # topo


def test_cost_per_mile_uses_source_when_program_missing():
    """GOL é single-tier R$ 18,00/mil (rates.json)."""
    rate = cost_per_mile(source=SourceType.BUSCAMILHAS_GOL, miles=80000)
    assert rate == pytest.approx(0.0180)


def test_award_uses_program_weight_when_known():
    """seats.aero/AwardTool agora aplicam o PESO POR PROGRAMA (antes tudo era a
    tarifa base 0.05). LifeMiles/Alaska/Air France/Qatar via award usam a tabela."""
    assert cost_per_mile(source=SourceType.SEATS_AERO, program="LifeMiles") == pytest.approx(0.085)
    assert cost_per_mile(source=SourceType.SEATS_AERO, program="Alaska Mileage Plan") == pytest.approx(0.090)
    assert cost_per_mile(source=SourceType.SEATS_AERO, program="Flying Blue") == pytest.approx(0.100)
    assert cost_per_mile(source=SourceType.AWARDTOOL, program="Qatar Privilege Club") == pytest.approx(0.064)


def test_award_falls_back_when_program_unknown():
    """Award com programa NÃO listado (ex.: Aeroplan/Emirates) cai na base intl."""
    assert cost_per_mile(source=SourceType.SEATS_AERO, program="Emirates Skywards") == pytest.approx(0.05)
    rate = cost_per_mile(source=SourceType.MCP_AWARD, miles=20000)
    assert rate == pytest.approx(0.05)  # sem programa → international_fallback_rate


def test_cost_per_mile_default_when_nothing_matches():
    rate = cost_per_mile(airline="ZZZ", program="UNKNOWN", miles=10000)
    # DEFAULT tier 1 (0-50000) = 0.025
    assert rate == pytest.approx(0.025)


def test_miles_to_brl_uses_program_weight():
    # 100k LATAM @ 0.0285 = 2850
    assert miles_to_brl(100000, program="LATAM") == pytest.approx(2850.0)


def test_estimate_miles_for_brl_converges_to_tier():
    """Cash R$ 2.500 with GOL reference → should land in some GOL tier."""
    miles, program, rate = estimate_miles_for_brl(2500.0, program="GOL")
    assert program == "GOL"
    # Cross-check: miles * rate should be close to the original BRL
    assert miles * rate == pytest.approx(2500.0, rel=0.01)


def test_estimate_miles_uses_default_program_when_none():
    miles, program, _ = estimate_miles_for_brl(1500.0)
    assert program == skiplagged_estimation_program()
    assert miles > 0


def test_estimate_miles_returns_int():
    miles, _, _ = estimate_miles_for_brl(2500.0, program="LATAM")
    assert isinstance(miles, int)


def test_estimate_miles_tier_selection_is_stable():
    """Two-pass iteration must converge to a consistent rate."""
    miles, _, rate = estimate_miles_for_brl(10000.0, program="LATAM")
    # Verify the selected rate matches the tier the computed miles fall into
    from backend.app.services.conversion import _programs, _rate_for_volume
    expected_rate = _rate_for_volume(_programs()["LATAM"], miles)
    assert rate == pytest.approx(expected_rate)
