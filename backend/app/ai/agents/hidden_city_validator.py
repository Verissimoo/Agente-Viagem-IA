"""Validação de hidden city — cruza ofertas hidden city com alternativas em milhas.

Quando o Skiplagged retorna uma oferta hidden city (ex.: BSB→CNF é hidden city
de um bilhete BSB→GIG via CNF), o vendedor quer saber:
  - "Existe alternativa em milhas pro trecho real do passageiro (BSB→CNF)?"
  - "Quanto sai em milhas vs. em dinheiro?"

Esta função cruza a oferta hidden city com as ofertas em milhas que JÁ FORAM
buscadas pelos providers BuscaMilhas/Economilhas/MCP no orchestrator (mesma
busca, paralela ao Skiplagged). Não faz request extra — só consolida.

Critérios de match:
  - Mesma origem e mesmo destino (intermediário do hidden city)
  - Mesma data de partida
  - Preferência: mesma companhia operadora do hidden city (se existir)
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def _first_segment(offer: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    segs = (offer.get("outbound") or {}).get("segments") or []
    return segs[0] if segs else None


def _last_segment_real_destination(offer: Dict[str, Any]) -> Optional[str]:
    """Pra hidden city, o destino REAL é o último que o passageiro voa antes
    de descartar. Como sanitizer pode esconder esse detalhe, pegamos o último
    segmento da `outbound` (o passageiro voa todos os segmentos até desembarcar
    em algum intermediário). Para a busca cruzada, o destino "real" é o que o
    vendedor pediu — que é a origem do segmento que ele DESCARTA. Como não
    temos esse flag no sanitized offer, usamos o `destination_iata` do request.
    """
    # Esse será sobrescrito pelo caller que tem acesso ao slot.
    segs = (offer.get("outbound") or {}).get("segments") or []
    return segs[-1].get("destination") if segs else None


def _depart_date(offer: Dict[str, Any]) -> Optional[str]:
    seg = _first_segment(offer)
    if not seg:
        return None
    dep = str(seg.get("departure_dt", ""))
    return dep[:10] if len(dep) >= 10 else None


def _carrier(offer: Dict[str, Any]) -> str:
    """Retorna nome NORMALIZADO da cia (GOL, LATAM, AZUL) — funciona tanto pra
    ofertas raw (com código G3, LA) quanto sanitized (já com nome amigável)."""
    from backend.app.ai.agents.airlines import prettify_carrier
    # Tenta airline_code primeiro (sanitized guarda esse campo)
    code = offer.get("airline_code") or offer.get("airline") or ""
    normalized = prettify_carrier(code) or code
    return str(normalized).upper()


def find_miles_alternative(
    hidden_offer: Dict[str, Any],
    miles_pool: List[Dict[str, Any]],
    *,
    real_destination: str,
) -> Optional[Dict[str, Any]]:
    """Procura no pool de ofertas em milhas a melhor opção pro trecho real.

    `real_destination` = destino que o vendedor PEDIU (não o destino vendido
    no bilhete hidden city). É o destino do segmento onde o passageiro
    desembarca de fato. Vem do slot da intake.
    """
    if not miles_pool:
        return None

    seg = _first_segment(hidden_offer)
    if not seg:
        return None

    origin = seg.get("origin")
    target_date = _depart_date(hidden_offer)
    target_carrier = _carrier(hidden_offer)   # já normalizado pra nome amigável
    if not (origin and real_destination and target_date):
        return None

    # 1ª passada: rota+data+carrier (match perfeito)
    # 2ª passada: rota+data (qualquer carrier)
    # 3ª passada: rota apenas (caso a data diferir por +/- 1 dia)
    def _matches_route_date_carrier(o: Dict[str, Any]) -> bool:
        s = _first_segment(o)
        if not s:
            return False
        if s.get("origin") != origin:
            return False
        last_segs = (o.get("outbound") or {}).get("segments") or []
        if not last_segs or last_segs[-1].get("destination") != real_destination:
            return False
        if _depart_date(o) != target_date:
            return False
        return _carrier(o) == target_carrier

    def _matches_route_date(o: Dict[str, Any]) -> bool:
        s = _first_segment(o)
        if not s:
            return False
        if s.get("origin") != origin:
            return False
        last_segs = (o.get("outbound") or {}).get("segments") or []
        if not last_segs or last_segs[-1].get("destination") != real_destination:
            return False
        return _depart_date(o) == target_date

    def _matches_route(o: Dict[str, Any]) -> bool:
        s = _first_segment(o)
        if not s:
            return False
        if s.get("origin") != origin:
            return False
        last_segs = (o.get("outbound") or {}).get("segments") or []
        return last_segs and last_segs[-1].get("destination") == real_destination

    def _sort_key(o: Dict[str, Any]) -> float:
        return float(
            o.get("equivalent_brl")
            or ((o.get("taxes_brl") or 0) + (o.get("miles") or 0) * 0.02)
            or 9e9
        )

    for predicate in (_matches_route_date_carrier, _matches_route_date, _matches_route):
        candidates = [o for o in miles_pool if predicate(o)]
        if candidates:
            best = min(candidates, key=_sort_key)
            logger.info(
                "hidden_city_validator: encontrei alternativa em milhas "
                "(rota %s→%s, carrier=%s) → %s mi + R$ %s",
                origin, real_destination,
                best.get("airline"), best.get("miles"), best.get("taxes_brl"),
            )
            return best

    logger.info(
        "hidden_city_validator: NENHUMA alternativa em milhas pra %s→%s em %s",
        origin, real_destination, target_date,
    )
    return None


def mark_hidden_city_disembark(
    offer: Dict[str, Any], real_destination: str,
) -> Dict[str, Any]:
    """Marca os segmentos do hidden city como `used` ou `discarded`.

    Tudo até (e incluindo) o segmento que tem `destination == real_destination`
    é USADO pelo passageiro. Tudo depois é DESCARTADO (o bilhete leva o pax
    pra mais longe, mas ele desembarca antes).

    Adiciona campos:
      - segment.used / segment.discarded (per segment)
      - offer.passenger_disembark_at (IATA)
      - offer.discarded_segments_count
    """
    out = dict(offer)
    for itin_key in ("outbound", "inbound"):
        itin = out.get(itin_key)
        if not isinstance(itin, dict):
            continue
        segs = itin.get("segments") or []
        if not segs:
            continue
        new_segs = []
        disembark_idx = None
        # Acha o índice do segmento onde o passageiro desembarca
        for i, seg in enumerate(segs):
            if seg.get("destination") == real_destination:
                disembark_idx = i
                break
        for i, seg in enumerate(segs):
            seg_copy = dict(seg)
            if disembark_idx is not None:
                if i <= disembark_idx:
                    seg_copy["used"] = True
                    seg_copy["discarded"] = False
                else:
                    seg_copy["used"] = False
                    seg_copy["discarded"] = True
            new_segs.append(seg_copy)
        out[itin_key] = {**itin, "segments": new_segs}
        if itin_key == "outbound" and disembark_idx is not None:
            out["passenger_disembark_at"] = real_destination
            out["discarded_segments_count"] = max(0, len(segs) - disembark_idx - 1)
    return out


def enrich_hidden_city_offers(
    offers: List[Dict[str, Any]],
    miles_pool: List[Dict[str, Any]],
    *,
    real_destination: str,
) -> List[Dict[str, Any]]:
    """Pra cada oferta hidden city no `offers`, adiciona `miles_alternative`
    com a melhor oferta em milhas pra mesmo trecho real.

    `real_destination` = IATA do destino do passageiro (slot do intake).
    """
    out = []
    for offer in offers:
        cat = (offer.get("category") or "").lower()
        if "hidden" not in cat:
            out.append(offer)
            continue

        # Marca segmentos usados vs. descartados (todo hidden city ganha isso)
        offer = mark_hidden_city_disembark(offer, real_destination)

        alt = find_miles_alternative(
            offer, miles_pool, real_destination=real_destination,
        )
        if alt:
            offer = dict(offer)
            offer["miles_alternative"] = {
                "airline": alt.get("airline"),
                "miles": alt.get("miles"),
                "taxes_brl": alt.get("taxes_brl"),
                "equivalent_brl": alt.get("equivalent_brl"),
                "offer_id": alt.get("offer_id"),
            }
        out.append(offer)
    return out


# ─── Busca SUPLEMENTAR — destino do bilhete (não do passageiro) ─────────
def _ticket_destination(offer: Dict[str, Any]) -> Optional[str]:
    """Destino oficial do bilhete hidden city = último segmento da outbound."""
    segs = (offer.get("outbound") or {}).get("segments") or []
    return segs[-1].get("destination") if segs else None


def _connects_through(offer: Dict[str, Any], hub: str) -> bool:
    """True se o itinerário faz escala no hub indicado (destino real do passageiro)."""
    segs = (offer.get("outbound") or {}).get("segments") or []
    if len(segs) < 2:
        return False
    # Hub aparece como destino intermediário (não final) OU origem intermediária
    intermediates = [s.get("destination") for s in segs[:-1]] + [s.get("origin") for s in segs[1:]]
    return hub in intermediates


def supplementary_miles_search_for_hidden_city(
    hidden_offer: Dict[str, Any],
    *,
    real_destination: str,
    adults: int,
    cabin: str = "economy",
) -> Optional[Dict[str, Any]]:
    """Faz busca SUPLEMENTAR em milhas pelo MESMO bilhete do hidden city.

    Lógica:
    - Hidden city é vendido como BSB→CNF, mas o passageiro só vai até SSA (escala).
    - Buscamos em milhas o bilhete BSB→CNF (destino oficial do bilhete).
    - Dos resultados, filtramos só os que fazem escala em SSA (mesma rota física).
    - Retorna o melhor em milhas, com flag `via_hub` indicando que valida hidden city.

    Custo: 1 chamada adicional a `run_search` (latência ~5-30s). Limitar uso a
    1-2 ofertas hidden city no top pra controlar.
    """
    seg = _first_segment(hidden_offer)
    if not seg:
        return None
    ticket_dest = _ticket_destination(hidden_offer)
    if not ticket_dest or ticket_dest == real_destination:
        return None
    target_date = _depart_date(hidden_offer)
    if not target_date:
        return None
    origin = seg.get("origin")
    if not origin:
        return None

    from datetime import date as _date
    try:
        target_date_obj = _date.fromisoformat(target_date)
    except ValueError:
        return None

    logger.info(
        "supplementary_miles_search: hidden city %s→%s (via %s), buscando "
        "bilhete em milhas %s→%s em %s",
        origin, real_destination, ticket_dest, origin, ticket_dest, target_date,
    )

    # Faz a busca suplementar usando os mesmos providers/orchestrator
    from backend.app.ai.agents.tools import run_search
    try:
        result = run_search(
            origin=origin, destination=ticket_dest,
            date_start=target_date_obj,
            adults=adults, cabin=cabin,
            top_n=10,
        )
    except Exception as e:
        logger.warning("supplementary search falhou: %s", e)
        return None
    if not result.get("ok"):
        logger.info("supplementary search retornou vazia ou erro")
        return None

    miles_offers = result.get("miles_offers") or []
    if not miles_offers:
        return None

    # Filtra ofertas que fazem escala no destino REAL do passageiro (valida que
    # é o mesmo bilhete físico do hidden city, não outra rota qualquer).
    via_hub = [o for o in miles_offers if _connects_through(o, real_destination)]
    pool_filtered_by_hub = via_hub if via_hub else miles_offers

    # PRIORIDADE 1: mesma cia operadora do hidden city + passa pelo hub.
    # Ex.: hidden G3 BSB→CNF via SSA → preferir Smiles (G3) BSB→CNF via SSA.
    hidden_carrier = _carrier(hidden_offer)
    same_carrier = [
        o for o in pool_filtered_by_hub
        if _carrier(o) == hidden_carrier
    ]
    candidates = same_carrier or pool_filtered_by_hub

    def _sort_key(o: Dict[str, Any]) -> float:
        return float(
            o.get("equivalent_brl")
            or ((o.get("taxes_brl") or 0) + (o.get("miles") or 0) * 0.025)
            or 9e9
        )

    best = min(candidates, key=_sort_key)
    # Marca origem do match pro frontend mostrar contexto
    best = dict(best)
    best["_supplementary_match"] = {
        "via_hub": real_destination,
        "ticket_destination": ticket_dest,
        "exact_route_match": bool(via_hub),
        "same_carrier_match": bool(same_carrier),
    }
    return best


# ─── Validação SPLIT — busca cada perna em milhas separadamente ─────────
def supplementary_miles_search_for_split(
    split_offer: Dict[str, Any],
    *,
    adults: int,
    cabin: str = "economy",
    max_workers: int = 2,
) -> Optional[Dict[str, Any]]:
    """Pra uma oferta split, busca cada perna em milhas separadamente.

    Split de trecho = N bilhetes separados (BSB→GRU + GRU→LIS). Validamos
    fazendo 1 busca por perna na sua data e cia operadora.
    Retorna breakdown com cada perna + totais ou None se alguma falhar.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from datetime import date as _date

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
            dep_date = _date.fromisoformat(dep_str)
        except ValueError:
            return None
        leg_specs.append({
            "origin": origin, "destination": dest,
            "dep_date": dep_date,
            "carrier_hint": seg.get("carrier") or "",
        })

    from backend.app.ai.agents.tools import run_search as _run_search

    def _search_leg(leg: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        try:
            r = _run_search(
                origin=leg["origin"], destination=leg["destination"],
                date_start=leg["dep_date"],
                adults=adults, cabin=cabin, top_n=5,
            )
        except Exception as e:
            logger.warning("split leg search %s→%s falhou: %s",
                           leg["origin"], leg["destination"], e)
            return None
        if not r.get("ok"):
            return None
        miles_pool = r.get("miles_offers") or []
        if not miles_pool:
            return None

        def _sort_key(o: Dict[str, Any]) -> float:
            return float(
                o.get("equivalent_brl")
                or ((o.get("taxes_brl") or 0) + (o.get("miles") or 0) * 0.025)
                or 9e9
            )
        # Preferência: mesma cia do hint, senão a mais barata
        same_carrier = [
            o for o in miles_pool
            if str(o.get("airline") or "").upper() == leg["carrier_hint"].upper()
        ]
        candidates = same_carrier or miles_pool
        return min(candidates, key=_sort_key)

    # Roda buscas em paralelo
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
        logger.info("split validation incompleta — alguma perna sem milhas")
        return None

    # Monta breakdown e totais
    breakdown = []
    total_miles = 0
    total_taxes = 0.0
    total_eq = 0.0
    for leg_spec, leg_result in zip(leg_specs, leg_results):
        if not leg_result:
            continue
        miles = int(leg_result.get("miles") or 0)
        taxes = float(leg_result.get("taxes_brl") or 0)
        eq = float(leg_result.get("equivalent_brl") or 0)
        total_miles += miles
        total_taxes += taxes
        total_eq += eq
        breakdown.append({
            "origin": leg_spec["origin"],
            "destination": leg_spec["destination"],
            "dep_date": leg_spec["dep_date"].isoformat(),
            "airline": leg_result.get("airline"),
            "miles": miles,
            "taxes_brl": round(taxes, 2),
            "equivalent_brl": round(eq, 2) if eq else None,
        })

    return {
        "validated": True,
        "split_breakdown": breakdown,
        "total_miles": total_miles,
        "total_taxes_brl": round(total_taxes, 2),
        "total_equivalent_brl": round(total_eq, 2) if total_eq else None,
    }


def validate_split_with_supplementary(
    offers: List[Dict[str, Any]],
    *,
    adults: int,
    cabin: str = "economy",
    max_validations: int = 1,
) -> List[Dict[str, Any]]:
    """Pra ATÉ N splits no `offers`, faz busca suplementar em milhas pra cada
    perna (N requests por split). Anexa `miles_alternative` com breakdown.
    """
    out = []
    validations_done = 0
    for offer in offers:
        cat = (offer.get("category") or "").lower()
        if "split" not in cat or validations_done >= max_validations:
            out.append(offer)
            continue
        if offer.get("miles_alternative"):  # já preenchido
            out.append(offer)
            continue

        validated = supplementary_miles_search_for_split(
            offer, adults=adults, cabin=cabin,
        )
        if validated:
            offer = dict(offer)
            offer["miles_alternative"] = {
                "validated": True,
                "is_split": True,
                "split_breakdown": validated["split_breakdown"],
                "miles": validated["total_miles"],
                "taxes_brl": validated["total_taxes_brl"],
                "equivalent_brl": validated.get("total_equivalent_brl"),
                "airline": "Split (cias separadas)",
            }
            validations_done += 1
        out.append(offer)
    return out


def validate_hidden_city_with_supplementary(
    offers: List[Dict[str, Any]],
    *,
    real_destination: str,
    adults: int,
    cabin: str = "economy",
    max_validations: int = 1,
) -> List[Dict[str, Any]]:
    """Pra ATÉ N hidden cities no `offers`, faz busca suplementar (1 request/oferta)
    e anexa `miles_alternative` validada (com flag `via_hub` se rota bate exato).

    Pra evitar latência, processa só as N primeiras hidden city encontradas
    (geralmente 1 — a do top).
    """
    out = []
    validations_done = 0
    for offer in offers:
        cat = (offer.get("category") or "").lower()
        if "hidden" not in cat or validations_done >= max_validations:
            out.append(offer)
            continue

        # SOBRESCREVE miles_alternative existente (cross-reference simples
        # busca o TRECHO REAL — suplementar busca o BILHETE OFICIAL, mais preciso).
        # Não pulamos se já tem — substituímos.
        validated = supplementary_miles_search_for_hidden_city(
            offer,
            real_destination=real_destination,
            adults=adults,
            cabin=cabin,
        )
        if validated:
            offer = dict(offer)
            match_info = validated.get("_supplementary_match") or {}
            # Pra ofertas em milhas, usa o NOME DO PROGRAMA (Smiles, LATAM Pass)
            # em vez do código da cia (G3, LA).
            from backend.app.ai.agents.airlines import (
                miles_program_name, prettify_carrier,
            )
            carrier = validated.get("airline") or ""
            program = miles_program_name(carrier) or prettify_carrier(carrier) or carrier
            offer["miles_alternative"] = {
                "airline": program,   # mostra nome do programa diretamente
                "miles": validated.get("miles"),
                "taxes_brl": validated.get("taxes_brl"),
                "equivalent_brl": validated.get("equivalent_brl"),
                "offer_id": validated.get("offer_id"),
                "validated": True,
                "exact_route_match": match_info.get("exact_route_match", False),
                "same_carrier_match": match_info.get("same_carrier_match", False),
            }
            validations_done += 1
        out.append(offer)
    return out
