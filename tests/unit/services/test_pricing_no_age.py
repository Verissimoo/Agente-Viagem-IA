"""Política tarifária SEM idade: criança (assento) = tarifa cheia, bebê = ~10%."""
from backend.app.ai.agents.pricing import estimate_pax_breakdown


def test_child_full_fare_infant_ten_percent():
    bd = estimate_pax_breakdown(
        adult_price_brl=1000.0, adult_miles=None, adult_taxes_brl=0.0,
        adults=2, children=1, infants=1,
    )
    # 2 adultos × 1000 + 1 criança × 1000 (cheia) + 1 bebê × 100 (10%) = 3100
    assert round(bd.grand_total_brl) == 3100
    assert bd.has_estimate is True            # bebê é estimativa
    labels = [l.label for l in bd.lines]
    assert any("Adulto" in x for x in labels)
    assert any("Criança" in x for x in labels)
    assert any("Bebê" in x for x in labels)


def test_only_children_no_estimate():
    # Só crianças (assento) = tarifa cheia, sem estimativa de bebê.
    bd = estimate_pax_breakdown(
        adult_price_brl=500.0, adult_miles=None, adult_taxes_brl=0.0,
        adults=1, children=2, infants=0,
    )
    assert round(bd.grand_total_brl) == 1500   # (1+2) × 500
    assert bd.has_estimate is False


def test_miles_breakdown():
    bd = estimate_pax_breakdown(
        adult_price_brl=None, adult_miles=20000, adult_taxes_brl=100.0,
        adults=1, children=1, infants=1,
    )
    # adulto 20000 + criança 20000 + bebê 2000 = 42000 mi
    assert bd.grand_total_miles == 42000
    assert bd.is_miles is True
