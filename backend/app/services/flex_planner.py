"""Planejamento de pares (ida, volta) candidatos para cotações flexíveis.

Centraliza a geração das combinações que o radar Kayak vai varrer. Cobre:
  • Janela de ida × janela de volta  ("ir 10-12, voltar 25-26") → cross-product.
  • Duração + janela total            ("5 dias entre 10 e 25")   → desliza a duração.
  • Range de ida + volta fixa         ("ir 10-13, voltar 25")    → ida flex.
  • ±N dias                            (plusminus)                → expande em torno.

Sempre limita o total de pares (amostragem uniforme) pra não explodir a latência
do radar. Mantém `volta >= ida` em todos os pares.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import List, Optional, Tuple

from backend.app.ai.agents.multi_date import generate_date_pairs
from backend.app.services.flex_dates import expand_dates

Pair = Tuple[date, date]


def _date_span(start: date, end: date, cap_days: int = 15) -> List[date]:
    """Lista inclusiva de datas [start..end], não-passadas, com teto de dias."""
    if end < start:
        end = start
    today = date.today()
    span = (end - start).days
    if span > cap_days - 1:
        span = cap_days - 1
    out = []
    for i in range(span + 1):
        d = start + timedelta(days=i)
        if d >= today:
            out.append(d)
    return out


def _sample(pairs: List[Pair], cap: int) -> List[Pair]:
    """Reduz a lista a no máximo `cap` pares, amostrando uniformemente.
    Preserva sempre o primeiro e o último (extremos da janela)."""
    if len(pairs) <= cap:
        return pairs
    if cap <= 1:
        return pairs[:1]
    step = (len(pairs) - 1) / (cap - 1)
    picked: List[Pair] = []
    seen = set()
    for i in range(cap):
        idx = round(i * step)
        if idx >= len(pairs):
            idx = len(pairs) - 1
        if idx in seen:
            continue
        seen.add(idx)
        picked.append(pairs[idx])
    return picked


def build_candidate_dates(
    *,
    start: date,
    end: Optional[date] = None,
    flex_mode: str = "none",
    flex_days: int = 0,
    cap: int = 16,
) -> List[date]:
    """Datas candidatas de IDA para uma busca SÓ-IDA com flexibilidade
    (range ou ±N). Amostra uniformemente se passar do cap."""
    if flex_mode == "range" and end:
        dates = _date_span(start, end)
    elif flex_mode in ("plusminus", "plus", "minus") and flex_days > 0:
        dates = expand_dates(start, min(flex_days, 7))
    else:
        dates = [start]
    if len(dates) <= cap:
        return dates
    # Amostra uniforme preservando extremos.
    step = (len(dates) - 1) / (cap - 1)
    picked, seen = [], set()
    for i in range(cap):
        idx = min(round(i * step), len(dates) - 1)
        if idx not in seen:
            seen.add(idx)
            picked.append(dates[idx])
    return picked


def build_candidate_pairs(
    *,
    depart_start: date,
    depart_end: Optional[date] = None,
    return_start: Optional[date] = None,
    return_end: Optional[date] = None,
    single_return: Optional[date] = None,
    duration_days: int = 0,
    flex_mode: str = "none",
    flex_days: int = 0,
    cap: int = 16,
) -> List[Pair]:
    """Gera os pares (ida, volta) candidatos conforme a forma de flexibilidade.

    Retorna lista possivelmente amostrada (≤ cap), sempre com volta >= ida.
    """
    # ── Caso A: janela de volta própria → cross-product ida × volta ──
    if return_start and return_end:
        depart_dates = _date_span(depart_start, depart_end or depart_start)
        return_dates = _date_span(return_start, return_end)
        pairs = [
            (d, r) for d in depart_dates for r in return_dates if r >= d
        ]
        return _sample(sorted(set(pairs)), cap)

    # ── Caso B: duração fixa dentro de um range total ──
    if duration_days > 0 and depart_end and depart_end > depart_start:
        pairs = generate_date_pairs(depart_start, depart_end, duration_days, max_pairs=cap)
        return pairs

    # ── Datas de ida (range ou ±N) ──
    if flex_mode == "range" and depart_end:
        depart_dates = _date_span(depart_start, depart_end)
    elif flex_mode in ("plusminus", "plus", "minus") and flex_days > 0:
        depart_dates = expand_dates(depart_start, min(flex_days, 7))
    else:
        depart_dates = [depart_start]

    # ── Caso C/D: volta única (eventualmente com flex própria) ──
    if single_return:
        if flex_mode in ("plusminus", "plus", "minus") and flex_days > 0:
            return_dates = expand_dates(single_return, min(flex_days, 2))
        else:
            return_dates = [single_return]
        pairs = [(d, r) for d in depart_dates for r in return_dates if r >= d]
        return _sample(sorted(set(pairs)), cap)

    # Sem volta definida → não é caso de par (oneway flex segue outro caminho).
    return []
