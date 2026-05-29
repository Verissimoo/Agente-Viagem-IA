"""Presenter — gera resposta final em markdown + payload sanitizado pra UI.

A UI renderiza:
- O texto markdown como "fala do assistente".
- Os cards de oferta a partir de `presented_offers` (no metadata da mensagem).

Sanitização de nomes de provider e jargão técnico acontece em duas camadas:
1. Sistema-prompt do LLM diz "nunca cite fonte/técnica".
2. `security/output_filter.py` passa um filtro regex pra garantir.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from backend.app.ai.agents.hidden_city_validator import (
    enrich_hidden_city_offers,
    validate_hidden_city_with_supplementary,
    validate_split_with_supplementary,
)
from backend.app.ai.agents.kayak_split_optimizer import optimize_split_dates_via_kayak
from backend.app.ai.agents.routes import classify_route
from backend.app.ai.agents.llm import get_cached_chat_model
from backend.app.ai.agents.pricing import estimate_pax_breakdown, format_breakdown_text
from backend.app.ai.agents.prompts import presenter_system_prompt
from backend.app.ai.agents.sanitizer import sanitize_offers
from backend.app.ai.agents.state import ChatState
from backend.app.chat.security.output_filter import sanitize_assistant_output

logger = logging.getLogger(__name__)

# Quantas ofertas mandar pro LLM no prompt. Mais que isso é ruído + tokens.
_MAX_OFFERS_IN_PROMPT = 6


# Detector de filtro pedido pelo usuário (split/hidden/direto/cash/milhas).
# Mapeia para predicado que opera sobre oferta sanitizada (campo `category`).
_FILTER_PATTERNS: List[Tuple[str, str, str]] = [
    # (regex, label_humano, category_target)
    (r"\b(split|quebra de trecho|trecho dividido)\b", "split de trecho", "Split"),
    (r"\b(hidden city|skiplagged|rota alternativa|rota otimizada)\b", "hidden city", "Hidden City"),
    (r"\b(direto|sem escala|n[aã]o.*conex)\b", "voo direto", "__direct__"),
    (r"\b(em milhas|usando milhas|pagar em milhas|pontos)\b", "em milhas", "Milhas"),
    (r"\b(em dinheiro|em cash|n[aã]o.*milhas)\b", "em dinheiro", "Cash direto"),
]


def _detect_filter(state: ChatState) -> Optional[Tuple[str, str]]:
    """Procura na última mensagem do usuário um filtro explícito.

    Retorna (label, category_target) ou None se não há filtro claro.
    `category_target` pode ser:
      - prefixo de category (ex.: "Split" pega "Split de trecho" e variantes)
      - "__direct__" sentinela pra filtrar por número de segmentos
    """
    messages = state.get("messages", [])
    for msg in reversed(messages):
        if isinstance(msg, HumanMessage):
            text = msg.content if isinstance(msg.content, str) else str(msg.content)
            lower = text.lower()
            for pattern, label, target in _FILTER_PATTERNS:
                if re.search(pattern, lower):
                    return (label, target)
            return None
    return None


def _apply_filter(offers: List[Dict[str, Any]], target: str) -> List[Dict[str, Any]]:
    if target == "__direct__":
        return [
            o for o in offers
            if len((o.get("outbound") or {}).get("segments") or []) <= 1
        ]
    return [
        o for o in offers
        if (o.get("category") or "").lower().startswith(target.lower())
    ]


def _summary_line(offer: Dict[str, Any]) -> str:
    parts = []
    if offer.get("airline"):
        parts.append(str(offer["airline"]))
    if offer.get("price_brl") is not None:
        parts.append(f"R$ {offer['price_brl']:.0f}")
    if offer.get("miles") is not None:
        tax = f" + R$ {offer['taxes_brl']:.0f}" if offer.get("taxes_brl") else ""
        parts.append(f"{offer['miles']} mi{tax}")
    if offer.get("category"):
        parts.append(offer["category"])
    out = offer.get("outbound") or {}
    segs = out.get("segments") or []
    parts.append("direto" if len(segs) <= 1 else f"{len(segs) - 1} conexão(ões)")

    # Se há miles_alternative MAIS BARATA que o cash, sinaliza explicitamente
    alt = offer.get("miles_alternative") or {}
    cash = offer.get("price_brl")
    alt_eq = alt.get("equivalent_brl")
    if cash and alt_eq and alt_eq < cash:
        program = alt.get("airline") or "milhas"
        parts.append(
            f"⚡ MAIS BARATO em milhas: {alt.get('miles'):,} mi + R$ {alt.get('taxes_brl', 0):.0f} "
            f"≈ R$ {alt_eq:.0f} ({program}) — economia R$ {cash - alt_eq:.0f}".replace(",", ".")
        )

    if offer.get("risk_notes"):
        parts.append(f"aviso: {offer['risk_notes']}")
    return " · ".join(parts)


def _connection_minutes(seg_a: Dict[str, Any], seg_b: Dict[str, Any]) -> Optional[int]:
    """Calcula minutos entre chegada do segmento A e partida do B."""
    try:
        from datetime import datetime
        arr = datetime.fromisoformat(str(seg_a.get("arrival_dt", "")).replace("Z", "+00:00"))
        dep = datetime.fromisoformat(str(seg_b.get("departure_dt", "")).replace("Z", "+00:00"))
        return int((dep - arr).total_seconds() / 60)
    except Exception:
        return None


def _offer_depart_date(offer: Dict[str, Any]) -> Optional[str]:
    """Extrai ISO date (YYYY-MM-DD) da partida do 1º segmento da ida."""
    segs = (offer.get("outbound") or {}).get("segments") or []
    if not segs:
        return None
    dep = str(segs[0].get("departure_dt", ""))
    return dep[:10] if len(dep) >= 10 else None


def _effective_price_brl(offer: Dict[str, Any]) -> float:
    """Preço efetivo = MENOR entre cash da oferta e equivalent_brl da
    alternativa em milhas verificada (se houver).

    Pra hidden city/split com miles_alternative validada, considera os DOIS
    caminhos — se o mesmo bilhete em milhas sai mais barato, esse é o
    preço real que deveria ser apresentado.
    """
    cash = offer.get("equivalent_brl") or offer.get("price_brl")
    if cash is None and offer.get("miles"):
        cash = (offer.get("taxes_brl") or 0) + (offer.get("miles") or 0) * 0.015

    alt = offer.get("miles_alternative") or {}
    alt_eq = alt.get("equivalent_brl")

    if cash is not None and alt_eq is not None:
        return float(min(cash, alt_eq))
    return float(cash if cash is not None else (alt_eq if alt_eq is not None else 9e9))


def _offer_sort_key(offer: Dict[str, Any]) -> float:
    """Critério de comparação universal — usa preço efetivo (considera milhas verificadas)."""
    return _effective_price_brl(offer)


def _category_bucket(offer: Dict[str, Any]) -> str:
    """Normaliza categoria pra grupo macro: cash | milhas | split | hidden | azul_oficial | outro."""
    cat = (offer.get("category") or "").lower()
    if "azul oficial" in cat or "azul_official" in cat:
        return "azul_oficial"
    if "split" in cat:
        return "split"
    if "hidden" in cat:
        return "hidden"
    if "milhas" in cat or offer.get("miles") is not None:
        return "milhas"
    if "cash" in cat or "dinheiro" in cat or offer.get("price_brl") is not None:
        return "cash"
    return "outro"


# Penalties de risco operacional aplicados na recomendação principal.
# Hidden city e split têm riscos reais (bagagem, perda de conexão sem
# reproteção). Mesmo sendo mais baratos, queremos que a RECOMENDADA seja a
# opção segura quando a diferença não compensa o risco.
_RISK_PENALTY = {
    "hidden":        1.15,   # +15% — risco grande (perde PNR, sem bagagem despachada)
    "split":         1.10,   # +10% — risco médio (cias separadas, sem reproteção)
    "milhas":        1.00,   # neutro
    "cash":          1.00,   # neutro — opção mais segura
    "azul_oficial":  0.95,   # −5% bônus — tarifa especial agência, lucro embutido
    "outro":         1.00,
}


def _recommendation_score(offer: Dict[str, Any]) -> float:
    """Score pra escolher a oferta RECOMENDADA: preço × penalty de risco.

    Permite que cash/milhas direto vença split/hidden city ligeiramente mais
    baratos — alinha com a fala do LLM ("prefiro opção segura, mesmo um pouco
    mais cara").
    """
    base = _offer_sort_key(offer)
    bucket = _category_bucket(offer)
    return base * _RISK_PENALTY.get(bucket, 1.0)


def smart_diversify(
    offers: List[Dict[str, Any]],
    *,
    base_date_iso: Optional[str],
    diversify_dates: bool,
    max_total: int = 5,
) -> List[Dict[str, Any]]:
    """Garante mix de CATEGORIAS e (opcionalmente) DATAS no top.

    Ordem de prioridade:
    1. Melhor de cada categoria macro (cash, milhas, split, hidden) — sempre tem mix
    2. Se há flex de datas, melhor de cada data adicional
    3. Preenche com mais baratas sobrando
    """
    if not offers:
        return []
    # Ordena por SCORE DE RECOMENDAÇÃO (preço × penalty de risco), não preço puro.
    # Garante que cash/milhas direto venha antes de hidden/split quando preços
    # forem próximos — alinha card RECOMENDADA com a recomendação do LLM.
    sorted_offers = sorted(offers, key=_recommendation_score)

    selected: List[Dict[str, Any]] = []
    selected_ids: set = set()

    def _add(o: Optional[Dict[str, Any]]) -> bool:
        if not o:
            return False
        oid = o.get("offer_id")
        if oid in selected_ids:
            return False
        selected.append(o)
        selected_ids.add(oid)
        return len(selected) >= max_total

    # 1) Melhor de cada categoria — limita 1 por bucket nessa primeira passada.
    seen_buckets: set = set()
    for o in sorted_offers:
        bucket = _category_bucket(o)
        if bucket in seen_buckets:
            continue
        seen_buckets.add(bucket)
        if _add(o):
            return selected

    # 2) Se há flex de datas, melhor de cada data adicional (pulando datas já presentes).
    if diversify_dates and base_date_iso:
        seen_dates = {_offer_depart_date(o) for o in selected}
        # Para cada data não vista, pega a melhor oferta
        by_date: Dict[str, Dict[str, Any]] = {}
        for o in sorted_offers:
            d = _offer_depart_date(o)
            if not d or d in seen_dates:
                continue
            if d not in by_date:
                by_date[d] = o  # primeira encontrada (mais barata por sort)
        # Ordena datas: base primeiro, depois pelas mais baratas
        sorted_alt_dates = sorted(
            by_date.keys(),
            key=lambda d: (0 if d == base_date_iso else 1, _offer_sort_key(by_date[d])),
        )
        for d in sorted_alt_dates:
            if _add(by_date[d]):
                return selected

    # 3) Preenche com as mais baratas restantes (incluindo 2ª/3ª oferta de buckets já vistos)
    for o in sorted_offers:
        if _add(o):
            return selected

    return selected


def diversify_offers_by_date(
    offers: List[Dict[str, Any]],
    *,
    base_date_iso: Optional[str],
    max_total: int = 5,
) -> List[Dict[str, Any]]:
    """Pega o melhor por data presente, priorizando data base e datas mais baratas.

    Garante que cards mostrem opções de DIAS DIFERENTES quando há flex —
    em vez de 5 opções todas do mesmo dia (sintoma comum quando uma cia
    domina aquele dia).
    """
    if not offers:
        return []
    # Agrupa por data e fica com a mais barata de cada
    by_date: Dict[str, Dict[str, Any]] = {}
    for o in offers:
        d = _offer_depart_date(o)
        if not d:
            continue
        if d not in by_date or _offer_sort_key(o) < _offer_sort_key(by_date[d]):
            by_date[d] = o

    if not by_date:
        return offers[:max_total]

    # Ordena datas: base primeiro, depois pelas mais baratas
    sorted_dates = sorted(
        by_date.keys(),
        key=lambda d: (0 if d == base_date_iso else 1, _offer_sort_key(by_date[d])),
    )
    diversified = [by_date[d] for d in sorted_dates[:max_total]]

    # Se ainda cabe e a base tem múltiplas ofertas baratas, completa com a 2ª/3ª
    # melhor da data base (caso o user só queira opções do dia exato)
    if len(diversified) < max_total and base_date_iso:
        base_offers = sorted(
            (o for o in offers if _offer_depart_date(o) == base_date_iso),
            key=_offer_sort_key,
        )
        for o in base_offers[1:]:
            if len(diversified) >= max_total:
                break
            if o not in diversified:
                diversified.append(o)

    return diversified


def _detail_offer_segments(offer: Dict[str, Any]) -> str:
    """Detalha segmentos da oferta — usado quando é split/multi-trecho.

    Formato (legível, pro LLM repassar ao vendedor):
        Trecho 1: cia HH:MM ORIG → DEST HH:MM
        Conexão: 2h30 em DEST (apertada / confortável / longa)
        Trecho 2: cia HH:MM ORIG → DEST HH:MM
    """
    out = offer.get("outbound") or {}
    segs = out.get("segments") or []
    if len(segs) <= 1:
        return ""

    lines = []
    for i, seg in enumerate(segs):
        carrier = seg.get("carrier") or "—"
        try:
            from datetime import datetime
            dep = datetime.fromisoformat(str(seg.get("departure_dt", "")).replace("Z", "+00:00"))
            arr = datetime.fromisoformat(str(seg.get("arrival_dt", "")).replace("Z", "+00:00"))
            dep_str = dep.strftime("%H:%M")
            arr_str = arr.strftime("%H:%M")
        except Exception:
            dep_str = "—"
            arr_str = "—"
        lines.append(
            f"  Trecho {i+1}: {carrier} {dep_str} {seg.get('origin')} → "
            f"{seg.get('destination')} {arr_str}"
        )
        if i < len(segs) - 1:
            mins = _connection_minutes(seg, segs[i + 1])
            hub = seg.get("destination")
            if mins is None:
                lines.append(f"  Conexão em {hub}: tempo desconhecido")
            else:
                h, m = divmod(mins, 60)
                gap = f"{h}h{m:02d}" if h else f"{m}min"
                # Heurística B2B internacional: <90min apertado, <180min ok, >180min confortável
                if mins < 90:
                    label = " (APERTADA — risco se atrasar)"
                elif mins < 180:
                    label = " (ok pra internacional)"
                elif mins < 480:
                    label = " (confortável)"
                else:
                    label = f" (longa — {h}h de espera)"
                lines.append(f"  Conexão em {hub}: {gap}{label}")
    return "\n".join(lines)


def presenter_node(state: ChatState) -> ChatState:
    results = state.get("search_results") or {}
    ranked = results.get("ranked_offers") or []
    money = results.get("money_offers") or []
    miles = results.get("miles_offers") or []

    sanitized_ranked = sanitize_offers(ranked)
    sanitized_money = sanitize_offers(money)
    sanitized_miles = sanitize_offers(miles)

    # Cross-reference hidden city com alternativa em milhas:
    # pra cada hidden city, anexa a melhor oferta em milhas do MESMO trecho real
    # (busca já feita pelos providers BuscaMilhas/Economilhas no orchestrator).
    slots_for_pax = state.get("slots") or {}
    real_destination = slots_for_pax.get("destination_iata") or ""
    if real_destination and sanitized_miles:
        sanitized_money = enrich_hidden_city_offers(
            sanitized_money, sanitized_miles, real_destination=real_destination,
        )
        sanitized_ranked = enrich_hidden_city_offers(
            sanitized_ranked, sanitized_miles, real_destination=real_destination,
        )

    # ─── REGRAS DE VALIDAÇÃO POR TIPO DE ROTA ─────────────────────────
    # Doméstico: split de trecho raramente vale a pena (sem hubs fortes pra
    #   arbitragem). Pulamos validate_split + kayak_optimizer.
    # Internacional: split vale (ex.: SP como hub pra Europa/EUA). Tudo roda.
    # Hidden city: roda em ambos.
    if real_destination:
        adults_for_search = int(slots_for_pax.get("adults", 1) or 1)
        cabin_for_search = slots_for_pax.get("cabin", "economy") or "economy"
        origin_iata = slots_for_pax.get("origin_iata") or ""
        route_type = classify_route(origin_iata, real_destination)
        is_domestic = route_type == "domestic"

        flex_active = (
            int(slots_for_pax.get("flex_days") or 0) > 0
            or slots_for_pax.get("flex_mode") in ("plusminus", "range", "plus", "minus")
        )

        import time as _time
        validations_timing = {}

        # HIDDEN CITY — sempre roda (rota doméstica também tem hidden city
        # útil, ex.: BSB→SSA com bilhete oficial pra CNF).
        t0 = _time.monotonic()
        sanitized_ranked = validate_hidden_city_with_supplementary(
            sanitized_ranked, real_destination=real_destination,
            adults=adults_for_search, cabin=cabin_for_search, max_validations=1,
        )
        sanitized_money = validate_hidden_city_with_supplementary(
            sanitized_money, real_destination=real_destination,
            adults=adults_for_search, cabin=cabin_for_search, max_validations=1,
        )
        validations_timing["hidden_city"] = _time.monotonic() - t0

        # SPLIT — só pra internacional. Doméstico pula (perda de tempo).
        if not is_domestic:
            t0 = _time.monotonic()
            sanitized_ranked = validate_split_with_supplementary(
                sanitized_ranked, adults=adults_for_search,
                cabin=cabin_for_search, max_validations=1,
            )
            sanitized_money = validate_split_with_supplementary(
                sanitized_money, adults=adults_for_search,
                cabin=cabin_for_search, max_validations=1,
            )
            validations_timing["split_miles"] = _time.monotonic() - t0

        # KAYAK SPLIT OPTIMIZER — só pra internacional com flex.
        if not is_domestic and flex_active:
            t0 = _time.monotonic()
            sanitized_ranked = optimize_split_dates_via_kayak(
                sanitized_ranked, adults=adults_for_search,
                cabin=cabin_for_search, flex_days=3, max_optimizations=1,
            )
            sanitized_money = optimize_split_dates_via_kayak(
                sanitized_money, adults=adults_for_search,
                cabin=cabin_for_search, flex_days=3, max_optimizations=1,
            )
            validations_timing["kayak_split"] = _time.monotonic() - t0

        logger.info(
            "presenter validations: route=%s domestic=%s flex=%s timings=%s",
            route_type, is_domestic, flex_active,
            {k: f"{v:.1f}s" for k, v in validations_timing.items()},
        )

    # Pool universal (ranked + money + miles dedup) — base pra filtros/diversificação
    seen_ids: set = set()
    unique_pool: List[Dict[str, Any]] = []
    for o in sanitized_ranked + list(sanitized_money) + list(sanitized_miles):
        oid = o.get("offer_id")
        if oid in seen_ids:
            continue
        seen_ids.add(oid)
        unique_pool.append(o)
    all_presented = sanitized_ranked or (sanitized_money + sanitized_miles)

    # 1) FILTRO primeiro — se vendedor pediu "só splits", restringe o universo
    #    antes de qualquer diversificação. Senão a diversificação pega o
    #    melhor por data (geralmente não-split) e filtro depois esvazia tudo.
    user_filter = _detect_filter(state)
    if user_filter:
        label, target = user_filter
        filtered_pool = _apply_filter(unique_pool, target)
        filtered_money = _apply_filter(sanitized_money, target)
        filtered_miles = _apply_filter(sanitized_miles, target)
        if not filtered_pool:
            no_match = (
                f"Não encontrei opções **{label}** pra essa rota e data. "
                "Quer que eu mantenha as outras opções que mostrei antes, "
                "ou prefere tentar outra combinação?"
            )
            return {
                **state,
                "presented_offers": [],
                "presented_at": datetime.now(timezone.utc).isoformat(),
                "messages": [AIMessage(content=sanitize_assistant_output(no_match))],
                "next_node": "end",
            }
        logger.info("presenter: filtro=%s aplicado → %d ofertas pool",
                    label, len(filtered_pool))
        unique_pool = filtered_pool
        sanitized_money = filtered_money
        sanitized_miles = filtered_miles
        all_presented = filtered_pool

    # 2) DIVERSIFICAÇÃO INTELIGENTE — categoria + (se há flex) datas.
    # Garante mix automático: usuário vê opções de cash, milhas, split, hidden
    # ao invés de 5 cards do mesmo tipo (que aconteceria se um tipo dominasse
    # o ranking por ser mais barato).
    slots_for_flex = state.get("slots") or {}
    flex_days_q = int(slots_for_flex.get("flex_days") or 0)
    base_date_iso = slots_for_flex.get("date_start")
    if len(unique_pool) > 1 and not user_filter:
        # Quando há filtro explícito, NÃO mixa categoria (usuário pediu uma só);
        # mas ainda diversifica datas se flex está ativo.
        diversified = smart_diversify(
            unique_pool,
            base_date_iso=base_date_iso,
            diversify_dates=(flex_days_q > 0),
            max_total=5,
        )
        if len(diversified) > 1:
            buckets_present = {_category_bucket(o) for o in diversified}
            dates_present = {_offer_depart_date(o) for o in diversified}
            logger.info(
                "presenter: smart_diversify → %d ofertas, categorias=%s, datas=%s",
                len(diversified), sorted(buckets_present), sorted(dates_present),
            )
            all_presented = diversified
    elif flex_days_q > 0 and user_filter and len(unique_pool) > 1:
        # Filtro ativo (ex: só splits) + flex → só diversifica por data
        diversified = diversify_offers_by_date(
            unique_pool, base_date_iso=base_date_iso, max_total=5,
        )
        if len(diversified) > 1:
            all_presented = diversified

    presented = all_presented
    top_for_prompt = presented[:_MAX_OFFERS_IN_PROMPT]

    slots = state.get("slots") or {}
    rota = f"{slots.get('origin_iata', '?')} → {slots.get('destination_iata', '?')}"

    # Composição de passageiros
    adults = int(slots.get("adults", 1) or 1)
    children = int(slots.get("children", 0) or 0)
    infants = int(slots.get("infants", 0) or 0)
    ages = slots.get("children_ages") or []
    pax_parts = [f"{adults} adulto{'s' if adults != 1 else ''}"]
    if children:
        ages_str = f" ({', '.join(f'{a}a' for a in ages)})" if ages else ""
        pax_parts.append(f"{children} criança{'s' if children != 1 else ''}{ages_str}")
    if infants:
        pax_parts.append(f"{infants} bebê{'s' if infants != 1 else ''}")
    pax_label = " + ".join(pax_parts)

    # Separar por categoria pra o LLM enxergar todas as variantes presentes.
    cheapest_money = min(
        (o for o in sanitized_money if o.get("price_brl") is not None),
        key=lambda o: o["price_brl"], default=None,
    )
    cheapest_miles = min(
        (o for o in sanitized_miles if o.get("miles") is not None),
        key=lambda o: o["miles"], default=None,
    )
    direct_offer = next(
        (o for o in presented
         if len((o.get("outbound") or {}).get("segments") or []) <= 1),
        None,
    )

    sections: List[str] = []
    if cheapest_money:
        sections.append(f"MAIS BARATA (dinheiro): {_summary_line(cheapest_money)}")
        # Se for split/multi-trecho, joga o detalhe pro LLM repassar.
        details = _detail_offer_segments(cheapest_money)
        if details:
            sections.append(details)
    if cheapest_miles:
        sections.append(f"MAIS BARATA (milhas): {_summary_line(cheapest_miles)}")
        details = _detail_offer_segments(cheapest_miles)
        if details:
            sections.append(details)
    if direct_offer and direct_offer not in (cheapest_money, cheapest_miles):
        sections.append(f"DIRETA: {_summary_line(direct_offer)}")

    # Top do ranking (pode coincidir com categorias acima — LLM cuida da dedup)
    sections.append("---")
    # Se diversificamos por data, sinaliza no prompt pra LLM aproveitar
    dates_in_top = sorted({_offer_depart_date(o) for o in top_for_prompt if _offer_depart_date(o)})
    if len(dates_in_top) > 1:
        sections.append(
            f"DATAS DIFERENTES nas ofertas abaixo: {', '.join(dates_in_top)}. "
            "Mostre data de partida de cada uma na seção Alternativas."
        )
    sections.append("RANKING (top opções, já diversificadas por data quando há flex):")
    for i, o in enumerate(top_for_prompt, 1):
        d = _offer_depart_date(o)
        date_tag = f" [partida {d}]" if d else ""
        sections.append(f"  {i}. {_summary_line(o)}{date_tag}")
        details = _detail_offer_segments(o)
        if details:
            sections.append(details)

    # Dados auxiliares pro insight
    insight_data = []
    best_date = results.get("best_depart_date")
    best_date_brl = results.get("best_depart_date_equivalent_brl")
    if best_date and best_date_brl:
        insight_data.append(
            f"Melhor data próxima (se cliente flexibilizar): {best_date} "
            f"(equivalente a R$ {best_date_brl:.0f})"
        )
    if cheapest_money and direct_offer and cheapest_money is not direct_offer:
        cmp = cheapest_money.get("price_brl")
        dpr = direct_offer.get("price_brl")
        if cmp and dpr:
            insight_data.append(
                f"Diferença direto vs. mais barata: R$ {abs(dpr - cmp):.0f}"
            )

    # Pra hidden city no top com miles_alternative verificada: comparar
    # cash hidden vs MESMO bilhete em milhas (referência real, não preço médio).
    for off in presented[:3]:
        cat = (off.get("category") or "").lower()
        if "hidden" not in cat:
            continue
        cash_price = off.get("price_brl")
        alt = off.get("miles_alternative") or {}
        alt_eq = alt.get("equivalent_brl")
        alt_miles = alt.get("miles")
        alt_program = alt.get("airline")
        if cash_price and alt_eq:
            diff = cash_price - alt_eq
            sign = "mais barato" if diff > 0 else "mais caro"
            insight_data.append(
                f"Hidden city cash R$ {cash_price:.0f} vs mesmo bilhete em "
                f"{alt_program or 'milhas'} ({alt_miles:,} mi ≈ R$ {alt_eq:.0f}) — "
                f"hidden é R$ {abs(diff):.0f} {sign}".replace(",", ".")
            )
            break   # só pro top hidden — não acumula
    if insight_data:
        sections.append("---")
        sections.append("DADOS PARA INSIGHT:")
        for d in insight_data:
            sections.append(f"  - {d}")

    sections_text = "\n".join(sections)

    filter_note = ""
    if user_filter:
        filter_note = (
            f"\n*** FILTRO ATIVO: vendedor pediu APENAS opções **{user_filter[0]}**. "
            "Mostre só essas — não cite outras categorias. ***\n"
        )

    # Aviso sobre flex truncado (se aplicável)
    flex_clamp = results.get("flex_clamped_info") if isinstance(results, dict) else None
    if flex_clamp:
        filter_note += (
            f"\n*** AVISO DE FLEX: vendedor pediu ±{flex_clamp['requested_days']} dias, "
            f"mas a busca foi limitada a ±{flex_clamp['actual_cap_days']} dias "
            f"(janela máxima da nossa rede de cotação pra manter tempo de resposta). "
            "MENCIONE isso brevemente na resposta — algo como "
            f"'busquei nos {1 + 2 * flex_clamp['actual_cap_days']} dias mais próximos da sua data'. ***\n"
        )

    # Calcula breakdown pra ofertas-destaque (já com convenção de tarifa
    # por faixa etária — bebê 10%, criança 75%, 12+ integral).
    pax_note = ""
    pax_breakdowns_text = ""
    breakdowns_by_offer_id: Dict[str, Any] = {}
    if children or infants or len(ages) > 0:
        breakdown_blocks: List[str] = []
        for label, off in [
            ("RECOMENDADA", presented[0] if presented else None),
            ("MAIS BARATA dinheiro", cheapest_money),
            ("MAIS BARATA milhas", cheapest_miles),
        ]:
            if not off or off in breakdown_blocks:
                continue
            bd = estimate_pax_breakdown(
                adult_price_brl=off.get("price_brl"),
                adult_miles=off.get("miles"),
                adult_taxes_brl=off.get("taxes_brl"),
                adults=adults,
                children_ages=list(ages),
                infants=infants,
            )
            if off.get("offer_id"):
                breakdowns_by_offer_id[off["offer_id"]] = bd
            block = f"{label} ({off.get('airline','—')} · {off.get('category','—')}):\n{format_breakdown_text(bd)}"
            breakdown_blocks.append(block)

        if breakdown_blocks:
            pax_breakdowns_text = (
                "\n*** TOTAIS ESTIMADOS POR OFERTA (calculados pra "
                f"{pax_label}) ***\n"
                + "\n\n".join(breakdown_blocks)
                + "\n"
            )

        pax_note = (
            f"\n*** PASSAGEIROS: {pax_label}. "
            "MOSTRE explicitamente o TOTAL ESTIMADO da seção acima — "
            "esses valores já estão calculados com a convenção de mercado. "
            "Lembre que o valor de criança/bebê é estimativa baseada em "
            "convenção do setor; o vendedor deve confirmar a tarifa final "
            "com a cia antes de emitir. ***\n"
        )

    try:
        model = get_cached_chat_model(primary=True)
        sys = SystemMessage(content=presenter_system_prompt())
        user = HumanMessage(content=(
            f"Rota: {rota} · {pax_label}\n"
            f"Total de ofertas analisadas: {len(presented)} "
            f"(dinheiro: {len(sanitized_money)}, milhas: {len(sanitized_miles)})\n"
            f"{filter_note}{pax_note}{pax_breakdowns_text}\n"
            f"{sections_text}\n\n"
            "Apresente em markdown ao vendedor. Os preços nas ofertas são por "
            "adulto; use os TOTAIS ESTIMADOS já calculados na seção 'TOTAIS "
            "ESTIMADOS POR OFERTA' (se presente)."
        ))
        resp = model.invoke([sys, user])
        text = resp.content if isinstance(resp.content, str) else str(resp.content)
    except Exception as e:
        logger.warning("LLM falhou no presenter (%s) — fallback determinístico", e)
        text = _fallback_presentation(rota, top_for_prompt)

    safe_text = sanitize_assistant_output(text)

    # Calcula breakdown pra TODAS as ofertas — barato e útil pro PDF.
    # Só quando tem pax múltiplo (mais de 1 adulto OU crianças/bebês).
    needs_breakdown = adults > 1 or children > 0 or infants > 0 or len(ages) > 0
    if needs_breakdown:
        presented_with_breakdown = []
        for o in presented:
            o_copy = dict(o)
            bd = estimate_pax_breakdown(
                adult_price_brl=o.get("price_brl"),
                adult_miles=o.get("miles"),
                adult_taxes_brl=o.get("taxes_brl"),
                adults=adults,
                children_ages=list(ages),
                infants=infants,
            )
            o_copy["pax_breakdown"] = {
                "lines": [
                    {
                        "label": l.label, "quantity": l.quantity,
                        "unit_price_brl": l.unit_price_brl,
                        "unit_miles": l.unit_miles,
                        "unit_taxes_brl": l.unit_taxes_brl,
                        "line_total_brl": l.line_total_brl,
                        "line_total_miles": l.line_total_miles,
                        "line_total_taxes_brl": l.line_total_taxes_brl,
                    } for l in bd.lines
                ],
                "grand_total_brl": bd.grand_total_brl,
                "grand_total_miles": bd.grand_total_miles,
                "grand_total_taxes_brl": bd.grand_total_taxes_brl,
                "is_miles": bd.is_miles,
                "has_estimate": bd.has_estimate,
                "pax_label": pax_label,
            }
            presented_with_breakdown.append(o_copy)
        presented = presented_with_breakdown

    msg = AIMessage(
        content=safe_text,
        additional_kwargs={
            "offers": presented,
            "rota": rota,
            "presented_at": datetime.now(timezone.utc).isoformat(),
        },
    )

    return {
        **state,
        "presented_offers": presented,
        "presented_at": datetime.now(timezone.utc).isoformat(),
        "messages": [msg],
        "next_node": "end",
    }


def _fallback_presentation(rota: str, offers: List[Dict[str, Any]]) -> str:
    if not offers:
        return f"**{rota}** — Não encontrei opções no momento. Quer tentar outras datas?"
    lines = [f"**{rota}** — opções encontradas:\n"]
    for i, o in enumerate(offers, 1):
        lines.append(f"{i}. {_summary_line(o)}")
    lines.append("\nQuer que eu siga com alguma dessas? Posso refinar a busca também.")
    return "\n".join(lines)
