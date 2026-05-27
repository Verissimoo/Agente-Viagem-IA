"""
backend.app.ai.summarizer
-------------------------
Único ponto autorizado a invocar LLM no pipeline. Recebe ofertas já
rankeadas e normalizadas (não JSON cru) e devolve texto curto em PT-BR
para o vendedor entender rapidamente a melhor opção, economia estimada
e riscos operacionais.

Usa litellm + Groq (stack já existente no projeto). Não chama em coleta
ou parsing — apenas no pós-ranking.

Controlado por feature flag `ENABLE_AI_SUMMARY=1`. Se desligado ou se a
chamada falhar, retorna None (frontend lida com ausência de resumo).
"""
from __future__ import annotations

import os
from typing import Iterable, Optional

from backend.app.domain.models import UnifiedOffer

# Custo desnecessário em dev/teste: importação preguiçosa do litellm
# (importar litellm é caro: ~300ms).
_litellm_completion = None


def _get_completion():
    """Importa litellm.completion sob demanda para evitar custo de startup."""
    global _litellm_completion
    if _litellm_completion is None:
        from litellm import completion as _c
        _litellm_completion = _c
    return _litellm_completion


def _is_enabled() -> bool:
    return os.getenv("ENABLE_AI_SUMMARY", "0") not in ("0", "false", "False", "")


def _format_offer_line(offer: UnifiedOffer) -> str:
    """Resume uma oferta em uma linha compacta para o prompt — economiza tokens."""
    parts: list[str] = [f"src={offer.source.value}", f"airline={offer.airline}"]

    if offer.price_brl is not None:
        parts.append(f"R${offer.price_brl:.0f}")
    if offer.miles is not None:
        taxes = f"+R${offer.taxes_brl:.0f}" if offer.taxes_brl else ""
        parts.append(f"{offer.miles}mi{taxes}")
    if offer.equivalent_brl is not None and offer.equivalent_brl != offer.price_brl:
        parts.append(f"eq=R${offer.equivalent_brl:.0f}")
    if offer.scenario:
        parts.append(f"scenario={offer.scenario.value}")
    if offer.layover_city:
        parts.append(f"layover={offer.layover_city}")
    if offer.outbound and offer.outbound.segments:
        stops = max(0, len(offer.outbound.segments) - 1)
        parts.append("direto" if stops == 0 else f"{stops}para")

    return " | ".join(parts)


def _build_prompt(
    offers: list[UnifiedOffer],
    origin: str,
    destination: str,
    date: str,
) -> str:
    """Monta um prompt compacto — top-N como bullets, dados já reduzidos."""
    lines = [_format_offer_line(o) for o in offers[:12]]
    bullets = "\n".join(f"- {ln}" for ln in lines)
    return (
        f"Rota: {origin}→{destination} em {date}.\n"
        f"Top-{len(lines)} ofertas (já rankeadas):\n{bullets}\n\n"
        "Em até 4 frases curtas em português do Brasil, recomende a melhor opção "
        "para um vendedor de viagens. Destaque: (1) economia estimada vs. a opção "
        "mais cara mostrada, (2) riscos operacionais (ex.: hidden-city perde "
        "bagagem despachada), (3) se há alternativa em milhas que vale a pena. "
        "Não invente preços que não estão na lista. Resposta direta, sem "
        "bullets ou JSON."
    )


def summarize(
    offers: Iterable[UnifiedOffer],
    *,
    origin: str,
    destination: str,
    date: str,
    model: Optional[str] = None,
    timeout_s: int = 12,
) -> Optional[str]:
    """Gera um resumo textual para o vendedor. Retorna None se:
      - feature flag desligada,
      - lista de ofertas vazia,
      - falha na chamada LLM (timeout, key inválida, etc.).
    """
    if not _is_enabled():
        return None

    offer_list = [o for o in offers if o is not None]
    if not offer_list:
        return None

    model = model or os.getenv("GROQ_MODEL", "groq/llama-3.3-70b-versatile")
    if not os.getenv("GROQ_API_KEY"):
        return None

    prompt = _build_prompt(offer_list, origin, destination, date)

    try:
        completion = _get_completion()
        response = completion(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            timeout=timeout_s,
            temperature=0.2,
            max_tokens=300,
        )
        content = response.choices[0].message.content
        return (content or "").strip() or None
    except Exception as e:
        print(f"[summarizer] LLM falhou: {e}")
        return None
