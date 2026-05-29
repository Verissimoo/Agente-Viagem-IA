"""Estimador de preço total por composição de passageiros.

Baseado em convenções tarifárias amplamente adotadas (IATA + companhias
domésticas BR):
- Infant lap (0-1 ano, colo do adulto): ~10% da tarifa adulta
- Criança (2-11 anos): ~75% da tarifa adulta
- 12+: tarifa adulta integral

São ESTIMATIVAS — cada cia/tarifa tem regras próprias. O usuário do
sistema deve confirmar com a cia antes de emitir.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


# Multiplicadores médios — ajustáveis se quisermos calibrar por rota.
INFANT_MULTIPLIER = 0.10
CHILD_MULTIPLIER = 0.75
YOUTH_MULTIPLIER = 1.0   # 12+ é adulto

INFANT_MAX_AGE = 1       # 0 e 1 anos = infant lap
CHILD_MAX_AGE = 11       # 2-11 = criança


@dataclass
class PaxLine:
    """Uma linha do breakdown — 1 passageiro ou um grupo idêntico."""
    label: str            # "Adulto", "Criança (3 anos, ~75%)", etc.
    quantity: int
    unit_price_brl: Optional[float] = None
    unit_miles: Optional[int] = None
    unit_taxes_brl: Optional[float] = None
    line_total_brl: Optional[float] = None
    line_total_miles: Optional[int] = None
    line_total_taxes_brl: Optional[float] = None


@dataclass
class PaxBreakdown:
    lines: List[PaxLine] = field(default_factory=list)
    grand_total_brl: float = 0.0
    grand_total_miles: int = 0
    grand_total_taxes_brl: float = 0.0
    is_miles: bool = False
    has_estimate: bool = False   # True se algum line é estimativa (criança/bebê)


def _categorize(age: int) -> tuple[str, float]:
    """Retorna (label_categoria, multiplicador)."""
    if age <= INFANT_MAX_AGE:
        return (f"Bebê ({age} ano{'s' if age != 1 else ''}, colo, ~{int(INFANT_MULTIPLIER*100)}%)", INFANT_MULTIPLIER)
    if age <= CHILD_MAX_AGE:
        return (f"Criança ({age} anos, ~{int(CHILD_MULTIPLIER*100)}%)", CHILD_MULTIPLIER)
    return (f"Adulto ({age} anos)", YOUTH_MULTIPLIER)


def estimate_pax_breakdown(
    *,
    adult_price_brl: Optional[float],
    adult_miles: Optional[int],
    adult_taxes_brl: Optional[float],
    adults: int,
    children_ages: List[int],
    infants: int = 0,
) -> PaxBreakdown:
    """Calcula breakdown e total estimado.

    `infants` é usado se você sabe que tem bebês mas não tem as idades
    explícitas em children_ages (assume todos com 1 ano).
    """
    out = PaxBreakdown(is_miles=adult_miles is not None)

    # Adultos
    if adults > 0:
        line = PaxLine(label="Adulto", quantity=adults)
        if adult_price_brl is not None:
            line.unit_price_brl = adult_price_brl
            line.line_total_brl = adult_price_brl * adults
            out.grand_total_brl += line.line_total_brl
        if adult_miles is not None:
            line.unit_miles = adult_miles
            line.line_total_miles = adult_miles * adults
            out.grand_total_miles += line.line_total_miles
        if adult_taxes_brl is not None:
            line.unit_taxes_brl = adult_taxes_brl
            line.line_total_taxes_brl = adult_taxes_brl * adults
            out.grand_total_taxes_brl += line.line_total_taxes_brl
        out.lines.append(line)

    # Bebês implícitos (sem idade): assume 1 ano
    extra_ages = list(children_ages)
    extra_ages.extend([1] * infants)

    # Agrupa por (label/multiplier) — várias crianças com mesma idade viram 1 linha
    groups: dict[tuple[str, float], int] = {}
    for age in extra_ages:
        cat = _categorize(age)
        groups[cat] = groups.get(cat, 0) + 1

    for (label, mult), qty in groups.items():
        line = PaxLine(label=label, quantity=qty)
        if adult_price_brl is not None:
            unit = round(adult_price_brl * mult, 2)
            line.unit_price_brl = unit
            line.line_total_brl = round(unit * qty, 2)
            out.grand_total_brl += line.line_total_brl
        if adult_miles is not None:
            unit_mi = int(round(adult_miles * mult))
            line.unit_miles = unit_mi
            line.line_total_miles = unit_mi * qty
            out.grand_total_miles += line.line_total_miles
        if adult_taxes_brl is not None:
            unit_tax = round(adult_taxes_brl * mult, 2) if mult > 0 else 0.0
            line.unit_taxes_brl = unit_tax
            line.line_total_taxes_brl = round(unit_tax * qty, 2)
            out.grand_total_taxes_brl += line.line_total_taxes_brl
        out.lines.append(line)
        if mult != 1.0:
            out.has_estimate = True

    return out


def format_breakdown_text(bd: PaxBreakdown) -> str:
    """Renderiza o breakdown em texto pra injetar no prompt do LLM."""
    if not bd.lines:
        return ""
    rows = []
    for line in bd.lines:
        if bd.is_miles:
            parts = []
            if line.unit_miles is not None:
                parts.append(f"{line.unit_miles:,} mi".replace(",", "."))
            if line.unit_taxes_brl:
                parts.append(f"+ R$ {line.unit_taxes_brl:.2f}")
            unit_str = " ".join(parts)
            tot_parts = []
            if line.line_total_miles:
                tot_parts.append(f"{line.line_total_miles:,} mi".replace(",", "."))
            if line.line_total_taxes_brl:
                tot_parts.append(f"+ R$ {line.line_total_taxes_brl:.2f}")
            tot_str = " ".join(tot_parts)
            rows.append(f"  • {line.quantity}× {line.label} = {unit_str} → {tot_str}")
        else:
            unit_str = f"R$ {line.unit_price_brl:.2f}" if line.unit_price_brl else "—"
            tot_str = f"R$ {line.line_total_brl:.2f}" if line.line_total_brl else "—"
            rows.append(f"  • {line.quantity}× {line.label} = {unit_str} → {tot_str}")
    if bd.is_miles:
        grand = []
        if bd.grand_total_miles:
            grand.append(f"{bd.grand_total_miles:,} mi".replace(",", "."))
        if bd.grand_total_taxes_brl:
            grand.append(f"+ R$ {bd.grand_total_taxes_brl:.2f}")
        rows.append(f"  TOTAL ESTIMADO: {' '.join(grand)}")
    else:
        rows.append(f"  TOTAL ESTIMADO: R$ {bd.grand_total_brl:.2f}")
    if bd.has_estimate:
        rows.append("  (criança/bebê é estimativa por convenção de mercado — confirmar com a cia)")
    return "\n".join(rows)
