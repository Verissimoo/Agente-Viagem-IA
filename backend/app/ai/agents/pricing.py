"""Estimador de preço total por composição de passageiros.

Política de tarifa (decisão de negócio — SEM idade, só 2 faixas):
- BEBÊ DE COLO (`infants`, <2 anos, sem assento): ~10% da tarifa adulta + taxas,
  OU gratuito dependendo da companhia. Multiplicador 0.10 (estimativa).
- CRIANÇA (`children`, com assento próprio): TARIFA CHEIA, igual adulto.
  Multiplicador 1.0.

A distinção bebê-de-colo vs criança vem da PALAVRA que o vendedor usa
("1 bebê" vs "1 criança"), NÃO da idade — nunca perguntamos idade.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


# Bebê de colo = ~10% da tarifa adulta (ou gratuito, varia por cia). Criança com
# assento = tarifa cheia (1.0). Sem faixas de idade.
INFANT_MULTIPLIER = 0.10


@dataclass
class PaxLine:
    """Uma linha do breakdown — 1 passageiro ou um grupo idêntico."""
    label: str            # "Adulto", "Criança (assento, tarifa cheia)", etc.
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
    has_estimate: bool = False   # True se há bebê de colo (valor estimado/gratuito)


def _add_group(out: PaxBreakdown, label: str, qty: int, mult: float, *,
               adult_price_brl, adult_miles, adult_taxes_brl) -> None:
    """Acrescenta uma linha de `qty` passageiros com multiplicador `mult`."""
    if qty <= 0:
        return
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


def estimate_pax_breakdown(
    *,
    adult_price_brl: Optional[float],
    adult_miles: Optional[int],
    adult_taxes_brl: Optional[float],
    adults: int,
    children: int = 0,
    infants: int = 0,
) -> PaxBreakdown:
    """Calcula o breakdown e o total estimado, sem idade.

    Adultos + crianças (com assento) pagam tarifa cheia; bebês de colo ~10%.
    """
    out = PaxBreakdown(is_miles=adult_miles is not None)

    _add_group(out, "Adulto", adults, 1.0,
               adult_price_brl=adult_price_brl, adult_miles=adult_miles,
               adult_taxes_brl=adult_taxes_brl)
    _add_group(out, "Criança (assento, tarifa cheia)", children, 1.0,
               adult_price_brl=adult_price_brl, adult_miles=adult_miles,
               adult_taxes_brl=adult_taxes_brl)
    _add_group(out, "Bebê de colo (~10% ou gratuito)", infants, INFANT_MULTIPLIER,
               adult_price_brl=adult_price_brl, adult_miles=adult_miles,
               adult_taxes_brl=adult_taxes_brl)
    if infants > 0:
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
        rows.append("  (bebê de colo é estimativa ~10% ou gratuito — confirmar com a cia)")
    return "\n".join(rows)
