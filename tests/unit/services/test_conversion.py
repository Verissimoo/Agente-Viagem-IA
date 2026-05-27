"""Tests for tiered miles→BRL conversion."""
import pytest

from backend.app.domain.models import SourceType
from backend.app.services.conversion import (
    cost_per_mile,
    estimate_miles_for_brl,
    miles_to_brl,
    skiplagged_estimation_program,
)


def test_cost_per_mile_zero_volume_is_first_tier():
    """No `miles` arg → uses the first tier (most expensive per-mile)."""
    rate = cost_per_mile(program="LATAM")
    assert rate == pytest.approx(0.0350)


def test_cost_per_mile_tiered_by_volume():
    assert cost_per_mile(program="LATAM", miles=5000)   == pytest.approx(0.0350)
    assert cost_per_mile(program="LATAM", miles=30000)  == pytest.approx(0.0290)
    assert cost_per_mile(program="LATAM", miles=100000) == pytest.approx(0.0260)
    assert cost_per_mile(program="LATAM", miles=300000) == pytest.approx(0.0240)


def test_cost_per_mile_at_tier_boundary():
    """Boundary belongs to the lower tier (max_miles is inclusive)."""
    assert cost_per_mile(program="LATAM", miles=10000) == pytest.approx(0.0350)
    assert cost_per_mile(program="LATAM", miles=10001) == pytest.approx(0.0290)


def test_cost_per_mile_uses_source_when_program_missing():
    rate = cost_per_mile(source=SourceType.BUSCAMILHAS_GOL, miles=80000)
    # GOL tier 3 (50001-150000) = 0.0185
    assert rate == pytest.approx(0.0185)


def test_cost_per_mile_international_fallback_for_mcp_award():
    rate = cost_per_mile(source=SourceType.MCP_AWARD, miles=20000)
    assert rate == pytest.approx(0.05)  # international_fallback_rate


def test_cost_per_mile_default_when_nothing_matches():
    rate = cost_per_mile(airline="ZZZ", program="UNKNOWN", miles=10000)
    # DEFAULT tier 1 (0-50000) = 0.025
    assert rate == pytest.approx(0.025)


def test_miles_to_brl_uses_appropriate_tier():
    # 100k LATAM @ tier 3 (0.0260) = 2600
    assert miles_to_brl(100000, program="LATAM") == pytest.approx(2600.0)


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
