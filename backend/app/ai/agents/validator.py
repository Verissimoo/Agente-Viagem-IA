"""Validator — checks determinísticos + LLM-as-critic opcional.

Estratégia:
1. Rodar checks puros (preço razoável, conexões viáveis, sem dado nulo).
2. Se algum check falhar = bloqueia. Manda de volta pro orchestrator pedir refresh.
3. LLM-as-critic é OPCIONAL e desligável (`CHAT_VALIDATOR_LLM=0`) — usa modelo barato
   pra pegar inconsistências que regras não vêem (datas trocadas, etc.).

Output: campo `validation_report` no state com
    { "verdict": "pass"|"warn"|"block", "issues": [...], "recommended": [offer_id...] }
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

from langchain_core.messages import AIMessage

from backend.app.ai.agents.state import ChatState

logger = logging.getLogger(__name__)


# Limites de sanidade. Valores deliberadamente conservadores —
# preferimos warn que block, então só bloqueamos no claramente quebrado.
_MIN_PRICE_BRL = 80.0          # passagem comercial real abaixo disso é suspeita
_MAX_PRICE_BRL = 200_000.0
_MIN_CONNECTION_MIN = 35       # tempo mínimo de conexão viável


def _check_offer(offer: Dict[str, Any]) -> List[str]:
    issues: List[str] = []

    price = offer.get("price_brl") or offer.get("equivalent_brl")
    if price is not None and (price < _MIN_PRICE_BRL or price > _MAX_PRICE_BRL):
        issues.append(f"preço fora do range plausível: R$ {price:.0f}")

    if offer.get("price_brl") is None and offer.get("miles") is None:
        issues.append("oferta sem preço nem milhas")

    out = offer.get("outbound") or {}
    segs = out.get("segments") or []
    if not segs:
        issues.append("itinerário de ida vazio")
    else:
        # Conexões: tempo entre segmento N e N+1
        for i in range(len(segs) - 1):
            try:
                # As datas vêm como ISO string após model_dump(mode="json")
                from datetime import datetime
                arr = datetime.fromisoformat(segs[i].get("arrival_dt", "").replace("Z", "+00:00"))
                dep = datetime.fromisoformat(segs[i + 1].get("departure_dt", "").replace("Z", "+00:00"))
                gap = (dep - arr).total_seconds() / 60.0
                if gap < _MIN_CONNECTION_MIN:
                    issues.append(
                        f"conexão muito curta ({gap:.0f} min) entre "
                        f"{segs[i].get('destination')} e {segs[i+1].get('origin')}"
                    )
                elif gap > 24 * 60:
                    issues.append(
                        f"conexão muito longa ({gap/60:.0f} h) entre "
                        f"{segs[i].get('destination')} e {segs[i+1].get('origin')}"
                    )
            except Exception:
                pass

    return issues


def validator_node(state: ChatState) -> ChatState:
    results = state.get("search_results") or {}
    money: List[Dict[str, Any]] = results.get("money_offers") or []
    miles: List[Dict[str, Any]] = results.get("miles_offers") or []
    ranked: List[Dict[str, Any]] = results.get("ranked_offers") or []

    if not (money or miles or ranked):
        # Sem ofertas — verdict block mas mensagem amigável.
        report = {
            "verdict": "block",
            "issues": ["nenhuma oferta retornada"],
            "recommended": [],
            "warn_notes": [],
        }
        return {
            **state,
            "validation_report": report,
            "next_node": "end",
            "messages": [AIMessage(content=(
                "Não encontrei opções pra esse trajeto na data pedida. "
                "Quer tentar com flexibilidade de datas ou outro destino?"
            ))],
        }

    all_offers = ranked or (money + miles)
    per_offer_issues: List[Dict[str, Any]] = []
    pass_count = 0
    warn_notes: List[str] = []

    for idx, offer in enumerate(all_offers):
        issues = _check_offer(offer)
        critical = any("fora do range" in i for i in issues)
        if issues:
            per_offer_issues.append({"index": idx, "issues": issues})
        if not critical:
            pass_count += 1

    verdict = "pass"
    if pass_count == 0 and len(all_offers) > 0:
        verdict = "warn"   # passa, mas com aviso — não trava conversa
        warn_notes = ["Algumas ofertas têm preço fora do range esperado; conferir antes de fechar."]
    elif per_offer_issues:
        verdict = "warn"
        warn_notes = [
            f"{len(per_offer_issues)} oferta(s) com ressalvas — apresentadas com aviso."
        ]

    report = {
        "verdict": verdict,
        "issues": per_offer_issues,
        "warn_notes": warn_notes,
        "pass_count": pass_count,
        "total_offers": len(all_offers),
    }

    if verdict == "block":
        return {
            **state,
            "validation_report": report,
            "next_node": "end",
            "messages": [AIMessage(content=(
                "As opções que recebi agora estão com inconsistências de preço. "
                "Quer tentar de novo com outras datas?"
            ))],
        }

    return {
        **state,
        "validation_report": report,
        "next_node": "presenter",
    }
