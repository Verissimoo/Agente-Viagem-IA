"""Cotação de IDA-E-VOLTA como DOIS bilhetes só-ida.

Necessário porque hidden city é one-way por natureza (o PNR cancela a volta se
você não embarca no destino oficial). Então um ida-e-volta com hidden city =
dois bilhetes separados: ida (origem→destino) + volta (destino→origem),
cada um pesquisado/validado isoladamente e SOMADO.

Ex.: BSB↔SSA → busca BSB→SSA e SSA→BSB, pega a melhor opção validada em milhas
de cada perna e soma pra concluir o valor real do ida-e-volta.
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def _leg_best_validated(
    origin: str, dest: str, leg_date: date, adults: int, cabin: str,
) -> Optional[Dict[str, Any]]:
    """Roda uma busca SÓ-IDA da perna e devolve a melhor opção VALIDADA em
    milhas (award direto à perna OU hidden-city validado pelo bilhete oficial
    que passa pelo destino). Devolve dict com brl/miles/taxes/airline/kind."""
    from backend.app.ai.agents.tools import run_search
    from backend.app.ai.agents.sanitizer import sanitize_offers
    from backend.app.ai.agents.hidden_city_validator import (
        enrich_hidden_city_offers,
        validate_hidden_city_with_supplementary,
    )

    try:
        r = run_search(
            origin=origin, destination=dest, date_start=leg_date,
            adults=adults, cabin=cabin, top_n=10,
        )
    except Exception as e:
        logger.warning("leg %s→%s falhou: %s", origin, dest, e)
        return None
    if not r.get("ok"):
        return None

    # Sanitiza pra setar `category` a partir do `scenario` — senão o validador
    # de hidden city (que filtra por "hidden" in category) ignora as ofertas
    # cruas (que só têm `scenario`) e a perna nunca pega a hidden city.
    money = sanitize_offers(r.get("money_offers") or [])
    miles = sanitize_offers(r.get("miles_offers") or [])

    # Hidden city desta perna: valida o bilhete oficial que passa pelo destino.
    money = enrich_hidden_city_offers(money, miles, real_destination=dest)
    money = validate_hidden_city_with_supplementary(
        money, real_destination=dest, adults=adults, cabin=cabin, max_validations=1,
    )

    candidates: list[Dict[str, Any]] = []
    # 1) Awards diretos em milhas da própria perna.
    for o in miles:
        eq = o.get("equivalent_brl")
        if eq:
            candidates.append({
                "kind": "miles_direct", "brl": float(eq),
                "miles": o.get("miles"), "taxes_brl": o.get("taxes_brl"),
                "airline": o.get("airline"), "segments": (o.get("outbound") or {}).get("segments"),
            })
    # 2) Hidden city validado (bilhete oficial via a cidade onde o pax desce).
    for o in money:
        mst = o.get("miles_same_ticket") or {}
        eq = mst.get("equivalent_brl")
        if eq:
            candidates.append({
                "kind": "hidden_city", "brl": float(eq),
                "miles": mst.get("miles"), "taxes_brl": mst.get("taxes_brl"),
                "airline": mst.get("airline"), "segments": (o.get("outbound") or {}).get("segments"),
                "hidden_city": True,
            })

    if not candidates:
        return None
    best = min(candidates, key=lambda c: c["brl"])
    best["origin"] = origin
    best["destination"] = dest
    best["date"] = leg_date.isoformat()
    return best


def quote_roundtrip_two_oneways(
    *,
    origin: str, destination: str,
    ida_date: date, volta_date: date,
    adults: int = 1, cabin: str = "economy",
) -> Optional[Dict[str, Any]]:
    """Cota ida (origem→destino) e volta (destino→origem) em paralelo, soma a
    melhor opção validada de cada perna. Devolve None se qualquer perna falhar.
    """
    with ThreadPoolExecutor(max_workers=2) as ex:
        f_ida = ex.submit(_leg_best_validated, origin, destination, ida_date, adults, cabin)
        f_volta = ex.submit(_leg_best_validated, destination, origin, volta_date, adults, cabin)
        ida = f_ida.result()
        volta = f_volta.result()

    if not ida or not volta:
        logger.info(
            "roundtrip_two_oneways: perna faltando (ida=%s, volta=%s)",
            bool(ida), bool(volta),
        )
        return None

    total_miles = int((ida.get("miles") or 0) + (volta.get("miles") or 0))
    total_taxes = float((ida.get("taxes_brl") or 0) + (volta.get("taxes_brl") or 0))
    total_brl = round(float(ida["brl"]) + float(volta["brl"]), 2)
    return {
        "ida": ida,
        "volta": volta,
        "total_miles": total_miles,
        "total_taxes_brl": round(total_taxes, 2),
        "total_brl": total_brl,
        "ida_date": ida_date.isoformat(),
        "volta_date": volta_date.isoformat(),
        "any_hidden_city": bool(ida.get("hidden_city") or volta.get("hidden_city")),
    }
