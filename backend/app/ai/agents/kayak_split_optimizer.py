"""Otimizador de datas pra splits de trecho via Kayak (cash).

Quando o vendedor especifica flexibilidade de datas e o sistema identifica
um split de trecho promissor (ex.: BSB → GRU + GRU → LIS), este módulo:

1. Faz busca cash com flex ±3 dias pra CADA perna do split (paralelo)
2. Identifica a melhor data por perna (mais barata)
3. Combina as melhores datas (mantendo ordem: ida antes da volta)
4. Retorna breakdown com comparação vs split atual

Use case: cliente quer "BSB → LIS em algum dia da próxima quinzena" — o
split otimizado pode trocar 11/jun→GRU + 12/jun→LIS pelo 11/jun→GRU +
14/jun→LIS (se o segundo bilhete tiver tarifa melhor pra 14).
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date as _date, timedelta
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def _depart_date(offer: Dict[str, Any]) -> Optional[str]:
    segs = (offer.get("outbound") or {}).get("segments") or []
    if not segs:
        return None
    dep = str(segs[0].get("departure_dt", ""))
    return dep[:10] if len(dep) >= 10 else None


def _cash_sort_key(offer: Dict[str, Any]) -> float:
    return float(
        offer.get("price_brl")
        or offer.get("equivalent_brl")
        or 9e9
    )


def find_best_dates_per_leg_via_kayak(
    split_offer: Dict[str, Any],
    *,
    adults: int,
    cabin: str = "economy",
    flex_days: int = 3,
    max_workers: int = 2,
) -> Optional[Dict[str, Any]]:
    """Pra cada perna do split, busca cash com flex e pega a melhor data.

    Latência: 2-3 requests adicionais por split, em paralelo.
    Retorna None se split tem <2 segmentos ou alguma perna sem resultado.
    """
    segs = (split_offer.get("outbound") or {}).get("segments") or []
    if len(segs) < 2:
        return None

    leg_specs: List[Dict[str, Any]] = []
    for seg in segs:
        origin = seg.get("origin")
        dest = seg.get("destination")
        dep_str = str(seg.get("departure_dt", ""))[:10]
        if not (origin and dest and dep_str):
            return None
        try:
            base_date = _date.fromisoformat(dep_str)
        except ValueError:
            return None
        leg_specs.append({
            "origin": origin, "destination": dest,
            "base_date": base_date,
            "carrier_hint": seg.get("carrier") or "",
        })

    from backend.app.ai.agents.tools import run_search as _run_search

    def _search_leg(leg: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        try:
            r = _run_search(
                origin=leg["origin"], destination=leg["destination"],
                date_start=leg["base_date"],
                adults=adults, cabin=cabin,
                flex_mode="plusminus", flex_days=flex_days,
                top_n=10,
            )
        except Exception as e:
            logger.warning("kayak split leg %s→%s falhou: %s",
                           leg["origin"], leg["destination"], e)
            return None
        if not r.get("ok"):
            return None
        # Pool: money_offers (cash) — Kayak retorna aqui.
        # Filtramos só os que tem price_brl pra escolher cash mesmo.
        cash_pool = [
            o for o in (r.get("money_offers") or [])
            if o.get("price_brl") is not None
        ]
        if not cash_pool:
            return None
        best = min(cash_pool, key=_cash_sort_key)
        return best

    leg_results: List[Optional[Dict[str, Any]]] = [None] * len(leg_specs)
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(_search_leg, leg): i for i, leg in enumerate(leg_specs)}
        for fut in as_completed(futures):
            i = futures[fut]
            try:
                leg_results[i] = fut.result()
            except Exception:
                leg_results[i] = None

    if any(r is None for r in leg_results):
        logger.info("kayak split optimization incompleta — alguma perna vazia")
        return None

    # Verifica ordem cronológica das datas selecionadas (ida antes de volta)
    selected_dates: List[Optional[str]] = []
    for r in leg_results:
        selected_dates.append(_depart_date(r) if r else None)

    # Se as datas escolhidas violam ordem (ex.: perna 1 dia 15, perna 2 dia 12),
    # o split não funciona — fallback pra mesma data do split original.
    valid_order = True
    for i in range(len(selected_dates) - 1):
        a, b = selected_dates[i], selected_dates[i + 1]
        if a and b and a > b:
            valid_order = False
            break
    if not valid_order:
        logger.info("kayak optimization: datas escolhidas fora de ordem cronológica, descartando")
        return None

    # Monta breakdown
    breakdown = []
    total_price = 0.0
    for leg_spec, leg_result in zip(leg_specs, leg_results):
        if not leg_result:
            continue
        price = float(leg_result.get("price_brl") or 0)
        total_price += price
        breakdown.append({
            "origin": leg_spec["origin"],
            "destination": leg_spec["destination"],
            "base_date": leg_spec["base_date"].isoformat(),
            "best_date": _depart_date(leg_result),
            "airline": leg_result.get("airline"),
            "price_brl": round(price, 2),
            "moved_days": (
                (_date.fromisoformat(_depart_date(leg_result)) - leg_spec["base_date"]).days
                if _depart_date(leg_result) else 0
            ),
        })

    # Compara com preço do split original
    original_price = float(split_offer.get("price_brl") or 0)
    savings = round(original_price - total_price, 2) if original_price else None

    return {
        "validated": True,
        "kayak_optimized": True,
        "breakdown": breakdown,
        "total_price_brl": round(total_price, 2),
        "original_price_brl": original_price if original_price else None,
        "savings_brl": savings,
        "flex_days_used": flex_days,
    }


def optimize_split_dates_via_kayak(
    offers: List[Dict[str, Any]],
    *,
    adults: int,
    cabin: str = "economy",
    flex_days: int = 3,
    max_optimizations: int = 1,
) -> List[Dict[str, Any]]:
    """Pra ATÉ N ofertas split no `offers`, faz otimização de datas via Kayak.

    Anexa campo `kayak_date_optimization` na oferta com breakdown e economia.
    Não substitui a oferta original — só adiciona contexto pro vendedor decidir.
    """
    out = []
    done = 0
    for offer in offers:
        cat = (offer.get("category") or "").lower()
        if "split" not in cat or done >= max_optimizations:
            out.append(offer)
            continue
        if offer.get("kayak_date_optimization"):
            out.append(offer)
            continue

        optimized = find_best_dates_per_leg_via_kayak(
            offer, adults=adults, cabin=cabin, flex_days=flex_days,
        )
        if optimized:
            offer = dict(offer)
            offer["kayak_date_optimization"] = optimized
            done += 1
        out.append(offer)
    return out
