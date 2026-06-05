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
import os
import re
import time as _time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from backend.app.ai.agents.hidden_city_validator import (
    enrich_hidden_city_offers,
    validate_hidden_city_with_supplementary,
    validate_split_with_supplementary,
)
from backend.app.ai.agents.airlines import carrier_to_program
from backend.app.ai.agents.kayak_split_optimizer import optimize_split_dates_via_kayak
from backend.app.ai.agents.routes import classify_route
from backend.app.ai.agents.llm import get_cached_chat_model
from backend.app.ai.agents.pricing import estimate_pax_breakdown, format_breakdown_text
from backend.app.ai.agents.prompts import presenter_system_prompt
from backend.app.ai.agents.sanitizer import sanitize_offers
from backend.app.ai.agents.state import ChatState
from backend.app.chat.security.output_filter import sanitize_assistant_output

logger = logging.getLogger(__name__)

# Orçamento TOTAL (s) das revalidações do presenter (hidden city / split /
# otimizador). Elas fazem novas chamadas a provedores; se estourarem, seguimos
# apresentando as ofertas que já temos (sem enriquecer) em vez de travar a
# resposta. Ajuste via env PRESENTER_VALIDATION_BUDGET_S.
try:
    _PRESENTER_VALIDATION_BUDGET_S = float(os.getenv("PRESENTER_VALIDATION_BUDGET_S", "18"))
except ValueError:
    _PRESENTER_VALIDATION_BUDGET_S = 18.0

# A validação de HIDDEN CITY tem orçamento PRÓPRIO e maior: ela busca o bilhete
# oficial em milhas (ex.: BSB→SSA via FOR) e esse valor é o PREÇO EM EVIDÊNCIA
# do card — então precisa completar de forma confiável, não pode ser cortado
# pelo orçamento curto das demais revalidações.
try:
    _HIDDEN_CITY_VALIDATION_BUDGET_S = float(os.getenv("HIDDEN_CITY_VALIDATION_BUDGET_S", "35"))
except ValueError:
    _HIDDEN_CITY_VALIDATION_BUDGET_S = 35.0


def _run_bounded(fn: Callable[[], Any], fallback: Any, timeout_s: float) -> Any:
    """Roda `fn` com teto de tempo. Se estourar/falhar, devolve `fallback`
    (as ofertas sem enriquecimento). Threads internas que vazarem terminam em
    background e o resultado é descartado."""
    if timeout_s <= 0:
        return fallback
    ex = ThreadPoolExecutor(max_workers=1)
    try:
        return ex.submit(fn).result(timeout=timeout_s)
    except FuturesTimeoutError:
        logger.warning("presenter: validação excedeu o orçamento (%.0fs) — apresentando sem enriquecer", timeout_s)
        return fallback
    except Exception as e:
        logger.warning("presenter: validação falhou (%s) — apresentando sem enriquecer", e)
        return fallback
    finally:
        ex.shutdown(wait=False, cancel_futures=True)

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
        # Inclui o equivalente em BRL JÁ CALCULADO (milhas × valor do milheiro +
        # taxas). Sem isso o LLM faz a conta de cabeça e erra o valor.
        eq = offer.get("equivalent_brl")
        eq_txt = f" ≈ R$ {eq:.0f}" if eq else ""
        # Programa de milhas ÚNICO e correto (LATAM→LATAM Pass, GOL→Smiles) —
        # sem isso o LLM inventa/combina ("Smiles/LATAM Pass" num voo LATAM).
        prog = offer.get("miles_program") or carrier_to_program(offer.get("airline"))
        prog_txt = f" [{prog}]" if prog else ""
        parts.append(f"{offer['miles']} mi{tax}{eq_txt}{prog_txt}")
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
            f"⚡ MAIS BARATO em milhas: ≈ R$ {alt_eq:.0f} "
            f"({alt.get('miles'):,} mi + R$ {alt.get('taxes_brl', 0):.0f} · {program}) "
            f"— economia R$ {cash - alt_eq:.0f} vs cash do skip".replace(",", ".")
        )

    # Hidden city VALIDADO em milhas (bilhete oficial passando pela escala): é
    # ESTE o valor da oferta em milhas — costuma ser o mais barato e deve poder
    # LIDERAR a recomendação (não o cash do skip).
    mst = offer.get("miles_same_ticket") or {}
    if mst.get("equivalent_brl"):
        parts.append(
            f"⚡ VALIDADO em milhas: ≈ R$ {mst['equivalent_brl']:.0f} "
            f"({(mst.get('miles') or 0):,} mi + R$ {(mst.get('taxes_brl') or 0):.0f} · "
            f"{mst.get('airline') or 'milhas'})".replace(",", ".")
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


def _intl_leg_txt(leg: Dict[str, Any]) -> str:
    """Linha pré-calculada de uma perna/rota internacional (milhas ou cash+10%)."""
    air = leg.get("airline") or "—"
    brl = float(leg.get("brl") or leg.get("total_brl") or 0)
    if leg.get("kind") == "miles":
        prog = leg.get("program")
        ptxt = f" [{prog}]" if prog else ""
        return (f"{air}: {int(leg.get('miles') or 0):,} mi + R$ "
                f"{float(leg.get('taxes_brl') or 0):.0f} ≈ R$ {brl:.0f}{ptxt}"
                ).replace(",", ".")
    return f"{air}: ≈ R$ {brl:.0f} (tarifa de mercado +10%)"


def _hub_leg_dto(leg: Dict[str, Any], label: str) -> Dict[str, Any]:
    """DTO de uma perna do hub-split pro card: milhas + programa próprios e, se
    o cash dela for mais barato, a dica de emissão melhor."""
    dto: Dict[str, Any] = {
        "label": label,
        "airline": leg.get("airline") or "—",
        "kind": leg.get("kind") or "miles",
        "miles": leg.get("miles"),
        "taxes_brl": leg.get("taxes_brl"),
        "program": leg.get("program"),
        "equivalent_brl": leg.get("brl"),
    }
    cc = leg.get("cash_cheaper")
    if cc:
        dto["cash_cheaper"] = {
            "cash_brl": cc.get("cash_brl"),
            "savings_brl": cc.get("savings_brl"),
            "airline": cc.get("airline"),
        }
    return dto


def _intl_option_card(opt: Dict[str, Any]) -> Dict[str, Any]:
    """Converte uma opção de quote_international em card pro frontend."""
    typ = opt.get("type")
    day = opt.get("date") or ""
    total = float(opt.get("total_brl") or 0)
    if typ == "hub_split":
        intl = opt.get("intl_leg") or {}
        dom = opt.get("domestic_leg") or {}
        hub = opt.get("hub") or "GRU"
        segs = (dom.get("segments") or []) + (intl.get("segments") or [])
        # Milhas NÃO são somadas (programas diferentes — Smiles + Miles&Go não
        # se misturam). Cada perna vai separada em `split_legs`, com seu programa
        # e, se o cash dela for mais barato, a dica de procurar emissão melhor.
        return {
            "offer_id": f"intl_hub_{hub}_{day}",
            "category": f"Quebra de trecho via {hub} · 2 bilhetes",
            "category_why": (
                f"Bilhete doméstico até {hub} + bilhete internacional a partir de "
                f"{hub}, comprados separados — soma costuma sair mais barata que o direto."
            ),
            "airline": f"{dom.get('airline') or '—'} + {intl.get('airline') or '—'}",
            "miles": None,        # ver split_legs (cada perna tem programa próprio)
            "equivalent_brl": total,
            "outbound": {"segments": segs},
            "split_legs": {
                "domestic": _hub_leg_dto(dom, "Trecho nacional"),
                "international": _hub_leg_dto(intl, "Trecho internacional"),
            },
            "risk_notes": (
                "Dois bilhetes separados (doméstico + internacional): sem reproteção "
                "se a conexão atrasar. Reserve folga entre os voos."
            ),
        }
    if typ == "skip_split":
        return {
            "offer_id": f"intl_skip_{day}_{round(total)}",
            "category": "Split · rota alternativa (milhas)",
            "category_why": "Trecho dividido em bilhetes separados — mais barato, porém arriscado.",
            "airline": opt.get("airline") or "—",
            "miles": opt.get("miles"),
            "taxes_brl": opt.get("taxes_brl"),
            "equivalent_brl": total,
            "outbound": {"segments": opt.get("segments") or []},
            "risk_notes": "Bilhetes separados por trecho, sem reproteção. Mais barato, mas não recomendado.",
        }
    if typ == "direct_cash":
        return {
            "offer_id": f"intl_cash_{day}",
            "category": "Direto · sem milhas (cash +10%)",
            "category_why": (
                "Voo mais barato encontrado, mas de companhia sem programa de "
                "milhas plugado na nossa rede — preço de mercado com +10%."
            ),
            "airline": opt.get("airline") or "—",
            "price_brl": opt.get("cash_brl"),
            "equivalent_brl": total,
            "outbound": {"segments": opt.get("segments") or []},
            "risk_notes": "Sem programa de milhas plugado pra essa cia (preço de mercado +10%).",
        }
    # direct_miles
    return {
        "offer_id": f"intl_miles_{day}_{(opt.get('airline') or '').upper()}",
        "category": "Direto · milhas",
        "airline": opt.get("airline") or "—",
        "miles": opt.get("miles"),
        "taxes_brl": opt.get("taxes_brl"),
        "equivalent_brl": total,
        "miles_program": opt.get("program"),
        "outbound": {"segments": opt.get("segments") or []},
    }


def _present_international(state: ChatState, options: List[Dict[str, Any]], *,
                          reference: Optional[Dict[str, Any]] = None,
                          market_signal: Optional[Dict[str, Any]] = None) -> ChatState:
    """Apresenta as opções de quebra de trecho internacional (direto + hub-split
    + skip-split), liderando pela mais barata VALIDADA. Valores já vêm
    pré-calculados de quote_international — o LLM só repassa, nunca recalcula."""
    slots = state.get("slots") or {}
    o_iata = slots.get("origin_iata", "?")
    d_iata = slots.get("destination_iata", "?")
    rota = f"{o_iata} → {d_iata}"

    # Ordena por preço, MAS separa o skip (Tipo 2 / Skiplagged): mesmo quando é
    # numericamente o mais barato, ele NÃO lidera nem é recomendado — vai por
    # último, marcado como arriscado. A RECOMENDADA é a mais barata "segura"
    # (milhas / cash sem programa / quebra de trecho nacional).
    by_price = sorted(options, key=lambda o: float(o.get("total_brl") or 9e9))
    safe = [o for o in by_price if o.get("type") != "skip_split"]
    skip = [o for o in by_price if o.get("type") == "skip_split"]
    ordered = safe + skip

    _LABEL = {
        "direct_miles": "DIRETO (milhas)",
        "direct_cash": "DIRETO (cash +10% — sem milhas plugadas pra essa cia)",
        "hub_split": "QUEBRA DE TRECHO NACIONAL (bilhete nacional até o hub + internacional)",
        "skip_split": "SPLIT alternativo (bilhetes separados — mais barato, porém ARRISCADO)",
    }
    lines: List[str] = []
    for i, opt in enumerate(ordered, 1):
        typ = opt.get("type")
        total = float(opt.get("total_brl") or 0)
        day = opt.get("date") or "—"
        tag = " ⭐ RECOMENDADA" if i == 1 and typ != "skip_split" else ""
        lines.append(f"  {i}. {_LABEL.get(typ, typ)} · partida {day} · TOTAL ≈ R$ {total:.0f}{tag}")
        if typ == "hub_split":
            intl = opt.get("intl_leg") or {}
            dom = opt.get("domestic_leg") or {}
            # Pernas separadas (cada uma com seu programa) — contexto pro LLM;
            # o detalhe de milhas e a dica de cash-mais-barato vivem NO CARD.
            _hub = opt.get("hub") or "GRU"
            lines.append(f"       • nacional {o_iata}→{_hub}: {_intl_leg_txt(dom)}")
            lines.append(f"       • internacional {_hub}→{d_iata}: {_intl_leg_txt(intl)}")
        elif typ == "skip_split":
            for b in (opt.get("breakdown") or []):
                lines.append(
                    f"       • {b.get('airline') or '—'} {b.get('origin','')}→{b.get('destination','')}: "
                    f"{int(b.get('miles') or 0):,} mi + R$ {float(b.get('taxes_brl') or 0):.0f}".replace(",", ".")
                )
            lines.append("       ⚠️ mais barato, MAS bilhetes separados sem reproteção — NÃO recomende.")
        elif typ == "direct_cash":
            lines.append(f"       • {_intl_leg_txt({'kind': 'cash', **opt})}")
            lines.append("       ⚠️ é a mais barata achada, MAS não temos programa de milhas plugado pra essa cia.")
        else:  # direct_miles
            lines.append(f"       • {_intl_leg_txt({'kind': 'miles', **opt})}")

    cheapest = ordered[0] if ordered else {}
    cheapest_date = cheapest.get("date") or "—"
    dates_found = sorted({o.get("date") for o in ordered if o.get("date")})

    # Nota de MERCADO (só texto): o Kayak achou um preço que NÃO conseguimos em
    # milhas — o vendedor precisa saber pra buscar emissão melhor.
    market_note = ""
    if market_signal and market_signal.get("price_brl"):
        ms_d = market_signal.get("date") or ""
        try:
            ms_d = datetime.fromisoformat(ms_d).strftime("%d/%m") if ms_d else ""
        except Exception:
            pass
        market_note = (
            f"\nMERCADO (avise no texto): o voo direto aparece por ~R$ "
            f"{float(market_signal['price_brl']):.0f}"
            + (f" em {ms_d}" if ms_d else "")
            + " no mercado, mas NÃO encontramos esse preço nos nossos programas de "
            f"milhas. Referência com nosso markup (+10%): ~R$ "
            f"{float(market_signal.get('price_markup_brl') or 0):.0f}. Diga isso pro "
            "vendedor (sem citar fonte/site)."
        )

    # Referência de OUTRAS datas (regra: sempre mostrar) — só cash, sem milhas.
    ref_note = ""
    ref_by = (reference or {}).get("direct_by_date") or {}
    if ref_by:
        others = sorted((iso for iso in ref_by if iso not in dates_found),
                        key=lambda k: ref_by[k])[:4]
        if others:
            def _fmt(iso):
                try:
                    return datetime.fromisoformat(iso).strftime("%d/%m")
                except Exception:
                    return iso
            ref_note = "\nREFERÊNCIA de outras datas (mercado): " + " · ".join(
                f"{_fmt(iso)} ~R$ {float(ref_by[iso]):.0f}" for iso in others
            ) + " — MOSTRE como referência pro vendedor comparar."

    sections = (
        f"Rota: {rota} (internacional, só-ida)\n"
        f"Data mais barata encontrada: {cheapest_date}. "
        f"Datas analisadas nos cards: {', '.join(dates_found) or '—'}.\n"
        "OPÇÕES VALIDADAS (já ordenadas; valores PRÉ-CALCULADOS, NÃO recalcule):\n"
        + "\n".join(lines) + market_note + ref_note + "\n\n"
        "Como apresentar (markdown conciso):\n"
        f"- COMECE a mensagem dizendo que a data mais barata encontrada foi {cheapest_date}.\n"
        "- NÃO fale de milhas no texto (nem quantidade, nem nome de programa, nem "
        "'em milhas') — isso aparece SÓ nos cards. No texto, use o valor em R$.\n"
        "- LIDERE pela ⭐ RECOMENDADA (a 1ª — a mais barata segura), citando cia e R$.\n"
        "- Destaque a QUEBRA DE TRECHO NACIONAL (bilhete nacional até GRU + "
        "internacional separado) quando existir — é a opção que o vendedor quer ver.\n"
        "- Se houver DIRETO cash +10%, diga que é a mais barata mas que não temos "
        "como encontrá-la pela nossa rede (preço de mercado).\n"
        "- O SPLIT alternativo (se aparecer) é só pra registro: cite por último, "
        "avise que é arriscado (bilhetes separados) e NÃO o recomende, mesmo barato.\n"
        "- Se houver nota de MERCADO, repasse-a: existe um preço de mercado que "
        "não conseguimos nos programas de milhas (cite o valor de referência +10%). "
        "Esta é a ÚNICA situação em que você menciona 'milhas' no texto.\n"
        "- Se houver REFERÊNCIA de outras datas, liste-as pro vendedor comparar.\n"
        "- Mencione que comparamos várias datas se houver mais de uma."
    )

    try:
        model = get_cached_chat_model(primary=True)
        sys = SystemMessage(content=presenter_system_prompt())
        resp = model.invoke([sys, HumanMessage(content=sections)])
        text = resp.content if isinstance(resp.content, str) else str(resp.content)
    except Exception as e:
        logger.warning("LLM falhou no presenter internacional (%s) — fallback", e)
        text = f"**{rota}** — opções (recomendada primeiro):\n" + "\n".join(
            f"{i}. {_LABEL.get(o.get('type'), o.get('type'))}: ≈ R$ {float(o.get('total_brl') or 0):.0f} ({o.get('date')})"
            for i, o in enumerate(ordered, 1)
        )

    cards = [_intl_option_card(o) for o in ordered]
    safe_text = sanitize_assistant_output(text)
    msg = AIMessage(
        content=safe_text,
        additional_kwargs={
            "offers": cards, "rota": rota,
            "presented_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    return {
        **state,
        "presented_offers": cards,
        "presented_at": datetime.now(timezone.utc).isoformat(),
        "messages": [msg],
        "next_node": "end",
    }


def presenter_node(state: ChatState) -> ChatState:
    results = state.get("search_results") or {}

    # Quebra de trecho INTERNACIONAL: opções já validadas/ordenadas pelo
    # orchestrator. Branch dedicado — não passa pelo fluxo de validação doméstico.
    intl_options = results.get("international_options")
    if intl_options:
        return _present_international(
            state, intl_options,
            reference=results.get("intl_reference"),
            market_signal=results.get("intl_market_signal"),
        )

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

    # ─── IDA-E-VOLTA: só apresenta ofertas COERENTES (com volta) ──────
    # Skiplagged (hidden city / cash / split) é só-ida — cobriria só metade da
    # viagem. Numa busca ida-e-volta, ofertas só-ida (sem inbound) são removidas
    # dos cards/recomendação; o hidden city ida-e-volta entra SÓ via o card
    # combinado montado a partir de results["roundtrip_two_oneways"]. Sem este
    # filtro, uma one-way barata (metade da viagem) dominava o ranking.
    is_roundtrip = (
        slots_for_pax.get("trip_type") == "roundtrip"
        or bool(slots_for_pax.get("return_from") or slots_for_pax.get("date_return"))
    )
    if is_roundtrip:
        def _has_inbound(o: Dict[str, Any]) -> bool:
            return bool((o.get("inbound") or {}).get("segments"))
        sanitized_ranked = [o for o in sanitized_ranked if _has_inbound(o)]
        sanitized_money = [o for o in sanitized_money if _has_inbound(o)]
        sanitized_miles = [o for o in sanitized_miles if _has_inbound(o)]

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

        validations_timing = {}
        # Orçamento total compartilhado: cada validação roda só com o tempo que
        # sobra. Se acabar, as ofertas seguem pra apresentação sem enriquecer.
        deadline = _time.monotonic() + _PRESENTER_VALIDATION_BUDGET_S

        def _remaining() -> float:
            return deadline - _time.monotonic()

        # HIDDEN CITY — orçamento PRÓPRIO (maior): o valor validado é o preço em
        # evidência do card, então precisa completar.
        t0 = _time.monotonic()
        hc_deadline = _time.monotonic() + _HIDDEN_CITY_VALIDATION_BUDGET_S
        sanitized_ranked = _run_bounded(
            lambda: validate_hidden_city_with_supplementary(
                sanitized_ranked, real_destination=real_destination,
                adults=adults_for_search, cabin=cabin_for_search, max_validations=1,
            ), sanitized_ranked, hc_deadline - _time.monotonic(),
        )
        sanitized_money = _run_bounded(
            lambda: validate_hidden_city_with_supplementary(
                sanitized_money, real_destination=real_destination,
                adults=adults_for_search, cabin=cabin_for_search, max_validations=1,
            ), sanitized_money, hc_deadline - _time.monotonic(),
        )
        validations_timing["hidden_city"] = _time.monotonic() - t0

        # SPLIT — só pra internacional. Doméstico pula (perda de tempo).
        if not is_domestic:
            t0 = _time.monotonic()
            sanitized_ranked = _run_bounded(
                lambda: validate_split_with_supplementary(
                    sanitized_ranked, adults=adults_for_search,
                    cabin=cabin_for_search, max_validations=1,
                ), sanitized_ranked, _remaining(),
            )
            sanitized_money = _run_bounded(
                lambda: validate_split_with_supplementary(
                    sanitized_money, adults=adults_for_search,
                    cabin=cabin_for_search, max_validations=1,
                ), sanitized_money, _remaining(),
            )
            validations_timing["split_miles"] = _time.monotonic() - t0

        # KAYAK SPLIT OPTIMIZER — só pra internacional com flex.
        if not is_domestic and flex_active:
            t0 = _time.monotonic()
            sanitized_ranked = _run_bounded(
                lambda: optimize_split_dates_via_kayak(
                    sanitized_ranked, adults=adults_for_search,
                    cabin=cabin_for_search, flex_days=3, max_optimizations=1,
                ), sanitized_ranked, _remaining(),
            )
            sanitized_money = _run_bounded(
                lambda: optimize_split_dates_via_kayak(
                    sanitized_money, adults=adults_for_search,
                    cabin=cabin_for_search, flex_days=3, max_optimizations=1,
                ), sanitized_money, _remaining(),
            )
            validations_timing["kayak_split"] = _time.monotonic() - t0

        logger.info(
            "presenter validations: route=%s domestic=%s flex=%s budget=%.0fs timings=%s",
            route_type, is_domestic, flex_active, _PRESENTER_VALIDATION_BUDGET_S,
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

    # ─── DATAS COMPARADAS — flex de ida+volta resolvido via radar ─────
    # Quando rodamos o radar (ida × volta), o vendedor PRECISA saber qual
    # combinação venceu e que comparamos várias.
    md_info = results.get("multi_date_info") or {}
    best_per_pair = md_info.get("best_per_pair") or {}
    if best_per_pair:
        ranked_combos = sorted(best_per_pair.items(), key=lambda kv: kv[1])
        scanned = md_info.get("candidates_scanned")
        sections.append("---")
        header = "DATAS COMPARADAS (cruzamos ida × volta"
        if scanned:
            header += f"; {scanned} combinações varridas"
        header += " — DIGA a combinação vencedora e o preço ao vendedor):"
        sections.append(header)
        for combo, price in ranked_combos[:5]:
            sections.append(f"  - {combo}: a partir de R$ {price:.0f}")

    # ─── HIDDEN CITY IDA-E-VOLTA = 2 bilhetes só-ida somados ──────────
    # Hidden city é one-way; o ida-e-volta real é ida (O→D) + volta (D→O)
    # validadas e SOMADAS. Mostra o breakdown e manda o LLM recomendar o mais
    # barato validado entre ISTO e o RT normal acima.
    rt2 = results.get("roundtrip_two_oneways")
    if rt2 and rt2.get("total_miles"):
        ida = rt2["ida"]; volta = rt2["volta"]
        def _legtxt(leg):
            hc = " (hidden city)" if leg.get("hidden_city") else ""
            return (f"{leg.get('airline') or '—'}{hc}: {int(leg.get('miles') or 0):,} mi "
                    f"+ R$ {float(leg.get('taxes_brl') or 0):.0f} ≈ R$ {float(leg['brl']):.0f}").replace(",", ".")
        sections.append("---")
        sections.append(
            "IDA-E-VOLTA EM MILHAS (dois bilhetes só-ida somados — necessário p/ "
            "hidden city). Se isto for MAIS BARATO que o RT normal acima, é a "
            "RECOMENDAÇÃO; senão, cite como alternativa:"
        )
        sections.append(f"  - Ida ({rt2.get('ida_date')}): {_legtxt(ida)}")
        sections.append(f"  - Volta ({rt2.get('volta_date')}): {_legtxt(volta)}")
        sections.append(
            f"  - TOTAL ida+volta: {int(rt2['total_miles']):,} mi + R$ "
            f"{float(rt2['total_taxes_brl']):.0f} ≈ R$ {float(rt2['total_brl']):.0f}".replace(",", ".")
        )

        # Vira também um CARD: junção ida+volta visível, com itinerário das duas
        # pernas e o breakdown por trecho.
        ida_segs = ida.get("segments") or []
        volta_segs = volta.get("segments") or []
        if ida_segs and volta_segs:
            same_airline = ida.get("airline") == volta.get("airline")
            any_hc = bool(rt2.get("any_hidden_city"))
            combined_card = {
                "offer_id": f"rt2_{rt2.get('ida_date')}_{rt2.get('volta_date')}",
                # Só rotula "Hidden City" se ALGUMA perna for de fato hidden city;
                # senão é só uma junção de 2 bilhetes só-ida normais.
                "category": "Hidden City · ida e volta" if any_hc else "Ida e volta · 2 bilhetes",
                "category_why": (
                    "Ida e volta montado como DOIS bilhetes só-ida"
                    + (" (inclui hidden city — bilhete vai além de onde o cliente desce)"
                       if any_hc else "")
                    + ": ida e volta pesquisadas separadamente e somadas."
                ),
                "airline": ida.get("airline") if same_airline
                else f"{ida.get('airline') or '—'} / {volta.get('airline') or '—'}",
                "miles": rt2["total_miles"],
                "taxes_brl": rt2["total_taxes_brl"],
                "equivalent_brl": rt2["total_brl"],
                "outbound": {"segments": ida_segs},
                "inbound": {"segments": volta_segs},
                "roundtrip_legs": {
                    "ida": {
                        "airline": ida.get("airline"), "miles": ida.get("miles"),
                        "taxes_brl": ida.get("taxes_brl"), "equivalent_brl": ida.get("brl"),
                        "hidden_city": bool(ida.get("hidden_city")), "date": ida.get("date"),
                    },
                    "volta": {
                        "airline": volta.get("airline"), "miles": volta.get("miles"),
                        "taxes_brl": volta.get("taxes_brl"), "equivalent_brl": volta.get("brl"),
                        "hidden_city": bool(volta.get("hidden_city")), "date": volta.get("date"),
                    },
                },
                "risk_notes": (
                    "Dois bilhetes separados (ida e volta)."
                    + (" Inclui hidden city — sem bagagem despachada, fora de programa de milhagem."
                       if rt2.get("any_hidden_city") else "")
                ),
            }
            presented = presented + [combined_card]

    # ─── BAGAGEM DESPACHADA — só quando o cliente pediu mala (23kg) ────
    # Regra real em services/baggage.py: dado da nossa rede quando há, Smiles
    # doméstico = R$130/trecho, internacional sem dado = incerto, hidden city =
    # impossível. O LLM precisa AVISAR isso (sobretudo o caso hidden city).
    baggage_requested = bool(slots.get("baggage_checked"))
    if baggage_requested:
        from backend.app.services.baggage import baggage_from_dict, NOT_ALLOWED, UNKNOWN
        bag_lines: List[str] = []
        seen_keys = set()
        for o in top_for_prompt:
            cat = o.get("category") or ("milhas" if o.get("miles") else "cash")
            key = (o.get("airline"), cat, o.get("miles"), o.get("price_brl"))
            if key in seen_keys:
                continue
            seen_keys.add(key)
            info = baggage_from_dict(o, "IDA")
            flag = ""
            if info.status == NOT_ALLOWED:
                flag = " ⛔"
            elif info.status == UNKNOWN:
                flag = " ⚠️"
            bag_lines.append(f"  - {o.get('airline') or '—'} {cat}:{flag} {info.note}")
        if bag_lines:
            sections.append("---")
            sections.append(
                "BAGAGEM DESPACHADA (o cliente PEDIU mala de 23kg — você DEVE comentar isto):"
            )
            sections.extend(bag_lines)

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

    # NOTA: a comparação cash-hidden vs MESMO bilhete em milhas NÃO entra aqui —
    # ela já é o destaque da Recomendação (marcador "MAIS BARATO em milhas"). Repetir
    # no Insight gera redundância. Insight fica pra algo NOVO (data alt., total p/ pax).
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

    # Preferência de horário (SUAVE): prioriza/destaca, não exclui.
    time_pref = (slots_for_pax.get("time_preference") or "").strip()
    time_note = ""
    if time_pref:
        _tl = {"manha": "manhã", "tarde": "tarde", "noite": "noite", "madrugada": "madrugada"}.get(time_pref, time_pref)
        time_note = (
            f"\n*** PREFERÊNCIA DE HORÁRIO: o cliente prefere voar de {_tl}. "
            "PRIORIZE e destaque as ofertas que partem nesse período (veja os horários "
            "nos trechos); mencione isso. NÃO exclua as demais — é preferência suave. ***\n"
        )

    try:
        model = get_cached_chat_model(primary=True)
        sys = SystemMessage(content=presenter_system_prompt())
        user = HumanMessage(content=(
            f"Rota: {rota} · {pax_label}\n"
            f"Total de ofertas analisadas: {len(presented)} "
            f"(dinheiro: {len(sanitized_money)}, milhas: {len(sanitized_miles)})\n"
            f"{filter_note}{pax_note}{time_note}{pax_breakdowns_text}\n"
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

    # O 1º card vira "RECOMENDADA" no frontend (isBest = idx 0). Ordena pelo
    # MESMO custo que a mensagem usa pra recomendar (menor valor validado:
    # equivalente em milhas, hidden city validado, ou cash) → o mais barato
    # fica em 1º e bate com o texto.
    def _reco_cost(o: Dict[str, Any]) -> float:
        mst = o.get("miles_same_ticket") or {}
        cands = [o.get("equivalent_brl"), mst.get("equivalent_brl"), o.get("price_brl")]
        vals = [float(c) for c in cands if c]
        return min(vals) if vals else 9e9
    presented = sorted(presented, key=_reco_cost)

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
