"""Busca multi-data — quando o vendedor especifica RANGE de datas + DURAÇÃO de viagem.

Caso típico: "BSB-XAP, qualquer data entre 1-13 jun, viagem de 3 dias".
- Range: 01/06 a 13/06 (13 dias úteis)
- Duração: 3 dias
- Geramos pares ida/volta: (01-04), (02-05), ..., (10-13)
- Buscamos cada um em paralelo (cap N pares pra controlar latência)
- Consolidamos e re-ordenamos pelo preço total
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


def generate_date_pairs(
    start: date, end: date, duration_days: int, max_pairs: int = 5,
) -> List[Tuple[date, date]]:
    """Gera pares (ida, volta) onde:
       - ida ∈ [start, end - duration_days]
       - volta = ida + duration_days
       - Distribui uniformemente se total possível > max_pairs.
    """
    if duration_days <= 0:
        return []
    last_dep = end - timedelta(days=duration_days)
    if last_dep < start:
        # Range menor que duração — usa start como ida e start+duration como volta
        return [(start, start + timedelta(days=duration_days))]

    total_days = (last_dep - start).days + 1
    if total_days <= max_pairs:
        return [
            (start + timedelta(days=i), start + timedelta(days=i + duration_days))
            for i in range(total_days)
        ]

    # Distribui em max_pairs pontos quase-uniformes
    if max_pairs == 1:
        return [(start, start + timedelta(days=duration_days))]
    step = (total_days - 1) / (max_pairs - 1)
    pairs = []
    seen = set()
    for i in range(max_pairs):
        offset = round(i * step)
        ida = start + timedelta(days=offset)
        if ida > last_dep:
            ida = last_dep
        if ida in seen:
            continue
        seen.add(ida)
        pairs.append((ida, ida + timedelta(days=duration_days)))
    return pairs


def run_multi_date_search(
    *,
    date_pairs: List[Tuple[date, date]],
    common_args: Dict[str, Any],
    max_workers: int = 3,
) -> Dict[str, Any]:
    """Roda várias buscas (uma por par de datas) em paralelo e consolida.

    common_args = todos os args de run_search EXCETO date_start/date_return.
    Retorna dict no mesmo formato que `run_search`.
    """
    from backend.app.ai.agents.tools import run_search

    if not date_pairs:
        return {"ok": False, "error": "no date pairs"}

    all_money: List[Dict[str, Any]] = []
    all_miles: List[Dict[str, Any]] = []
    all_ranked: List[Dict[str, Any]] = []
    errors: List[str] = []

    def _do_one(ida: date, volta: date) -> Optional[Dict[str, Any]]:
        try:
            r = run_search(date_start=ida, date_return=volta, **common_args)
            if not r.get("ok"):
                errors.append(f"{ida}->{volta}: {r.get('error', '?')}")
                return None
            # Anota o par no metadata de cada oferta
            for off in (r.get("ranked_offers") or []) + (r.get("money_offers") or []) + (r.get("miles_offers") or []):
                if isinstance(off, dict):
                    off.setdefault("_multi_pair", {"ida": ida.isoformat(), "volta": volta.isoformat()})
            return r
        except Exception as e:
            logger.exception("multi-date search falhou para %s->%s", ida, volta)
            errors.append(f"{ida}->{volta}: {e}")
            return None

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(_do_one, ida, volta): (ida, volta) for ida, volta in date_pairs}
        for fut in as_completed(futures):
            r = fut.result()
            if not r:
                continue
            all_money.extend(r.get("money_offers") or [])
            all_miles.extend(r.get("miles_offers") or [])
            all_ranked.extend(r.get("ranked_offers") or [])

    if not (all_money or all_miles or all_ranked):
        return {"ok": False, "error": "todas as buscas falharam ou vieram vazias",
                "errors": errors}

    def _sort_key(o: Dict[str, Any]) -> float:
        return float(
            o.get("equivalent_brl")
            or o.get("price_brl")
            or ((o.get("taxes_brl") or 0) + (o.get("miles") or 0) * 0.015)
            or 9e9
        )

    all_ranked.sort(key=_sort_key)
    all_money.sort(key=_sort_key)
    all_miles.sort(key=_sort_key)

    # Resumo: melhor preço por par
    best_per_pair: Dict[str, float] = {}
    for o in all_ranked:
        p = o.get("_multi_pair") or {}
        key = f"{p.get('ida')} → {p.get('volta')}"
        val = _sort_key(o)
        if key not in best_per_pair or val < best_per_pair[key]:
            best_per_pair[key] = val

    return {
        "ok": True,
        "request_id": "multi-" + "-".join(f"{i.isoformat()}_{v.isoformat()}" for i, v in date_pairs[:3]),
        "best_overall": all_ranked[0] if all_ranked else None,
        "best_money": all_money[0] if all_money else None,
        "best_miles": all_miles[0] if all_miles else None,
        "ranked_offers": all_ranked[:15],
        "money_offers": all_money,
        "miles_offers": all_miles,
        "best_depart_date": None,
        "best_depart_date_equivalent_brl": None,
        "date_best_map": {},
        "justification": [
            f"Buscamos {len(date_pairs)} combinações de ida e volta dentro do range pedido.",
            f"Melhor preço total: {min(best_per_pair.values()):.0f} BRL na combinação "
            f"{min(best_per_pair, key=best_per_pair.get)}.",
        ] if best_per_pair else [],
        "direct_filter_warning": None,
        "multi_date_info": {
            "pairs_searched": [
                {"ida": p[0].isoformat(), "volta": p[1].isoformat()} for p in date_pairs
            ],
            "best_per_pair": best_per_pair,
            "errors": errors,
        },
    }
