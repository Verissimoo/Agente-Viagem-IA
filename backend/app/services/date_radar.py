"""Radar de datas — usa o Kayak como ferramenta barata pra varrer combinações
(ida, volta) e descobrir as datas de melhor valor ANTES de gastar as buscas
caras de milhas + hidden city.

Fluxo:
  1. Pra cada par (ida, volta), 1 chamada round-trip ao Kayak (cash de mercado).
  2. Ranqueia os pares pelo menor cash.
  3. Se o Kayak não cobrir a rota (zero preços), cai pra uma AMOSTRA de milhas
     (1 provider) em poucos pares pra ainda escolher uma data.

Degrada com elegância: pares que falham são ignorados, nunca derrubam a varredura.
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import date
from typing import List, Optional, Tuple

from backend.app.domain.models import CabinClass, SearchRequest, TripType, UnifiedOffer

logger = logging.getLogger(__name__)

Pair = Tuple[date, Optional[date]]


@dataclass
class RadarResult:
    ranked_pairs: List[Pair] = field(default_factory=list)   # melhor → pior
    price_by_pair: dict = field(default_factory=dict)         # "ida → volta": preço
    source: str = "none"                                      # "kayak" | "miles_sample" | "none"


def _coerce_cabin(cabin) -> CabinClass:
    if isinstance(cabin, CabinClass):
        return cabin
    try:
        return CabinClass((cabin or "economy").lower())
    except ValueError:
        return CabinClass.ECONOMY


def _safe_search(adapter_cls, req: SearchRequest) -> List[UnifiedOffer]:
    """Roda um adapter sem propagar exceção; garante equivalent_brl em milhas."""
    try:
        offers = adapter_cls().search(req, use_fixtures=False) or []
    except TypeError:
        try:
            offers = adapter_cls().search(req) or []
        except Exception as e:
            logger.warning("radar: %s falhou: %s", adapter_cls.__name__, e)
            return []
    except Exception as e:
        logger.warning("radar: %s falhou: %s", adapter_cls.__name__, e)
        return []
    from backend.app.services.conversion import offer_equivalent_brl
    for o in offers:
        if o.equivalent_brl is None or o.equivalent_brl == 0:
            try:
                v = offer_equivalent_brl(o)
                if v and v > 0:
                    o.equivalent_brl = float(v)
            except Exception:
                pass
    return offers


def _req_for_pair(origin: str, destination: str, ida: date, volta: Optional[date],
                  adults: int, cabin: CabinClass) -> SearchRequest:
    # volta=None → busca SÓ-IDA (radar de flex de ida).
    return SearchRequest(
        origin=[origin.upper()],
        destination=[destination.upper()],
        date_start=ida,
        date_end=ida,
        return_start=volta,
        return_end=volta,
        trip_type=TripType.ROUNDTRIP if volta else TripType.ONEWAY,
        adults=adults,
        cabin=cabin,
    )


def _pair_label(ida: date, volta: Optional[date]) -> str:
    return f"{ida.isoformat()} → {volta.isoformat()}" if volta else ida.isoformat()


def _cheapest_cash(offers: List[UnifiedOffer]) -> Optional[float]:
    prices = [float(o.price_brl) for o in offers if o.price_brl is not None]
    return min(prices) if prices else None


def _cheapest_equiv(offers: List[UnifiedOffer]) -> Optional[float]:
    vals = [float(o.equivalent_brl) for o in offers if o.equivalent_brl]
    return min(vals) if vals else None


def scan_dates(
    pairs: List[Pair],
    *,
    origin: str,
    destination: str,
    adults: int = 1,
    cabin="economy",
    max_workers: int = 8,
) -> RadarResult:
    """Varre os pares no Kayak e devolve-os ranqueados pelo menor cash.
    Se o Kayak vier vazio, cai pra amostra de milhas."""
    from backend.app.providers.kayak.adapter import KayakAdapter

    cab = _coerce_cabin(cabin)
    if not pairs:
        return RadarResult()

    price_by_pair: dict = {}

    def _one(pair: Pair) -> Tuple[Pair, Optional[float]]:
        ida, volta = pair
        offers = _safe_search(KayakAdapter, _req_for_pair(origin, destination, ida, volta, adults, cab))
        return pair, _cheapest_cash(offers)

    with ThreadPoolExecutor(max_workers=min(len(pairs), max_workers) or 1) as ex:
        futures = [ex.submit(_one, p) for p in pairs]
        for f in as_completed(futures):
            pair, price = f.result()
            if price is not None:
                price_by_pair[pair] = price

    if price_by_pair:
        ranked = sorted(price_by_pair.keys(), key=lambda p: price_by_pair[p])
        return RadarResult(
            ranked_pairs=ranked,
            price_by_pair={_pair_label(i, v): price_by_pair[(i, v)] for i, v in ranked},
            source="kayak",
        )

    # ── Fallback: Kayak não cobriu a rota → amostra de milhas ──
    return _scan_miles_sample(pairs, origin=origin, destination=destination, adults=adults, cabin=cab)


def _scan_miles_sample(
    pairs: List[Pair], *, origin: str, destination: str, adults: int, cabin: CabinClass,
    sample: int = 4,
) -> RadarResult:
    """Sem cash do Kayak: usa Economilhas (1 provider) em poucos pares pra ranquear."""
    from backend.app.providers.economilhas.adapter import EconomilhasAdapter

    # Amostra uniforme dos pares pra não rodar milhas em todos.
    if len(pairs) > sample:
        step = (len(pairs) - 1) / (sample - 1) if sample > 1 else 1
        idxs = sorted({round(i * step) for i in range(sample)})
        chosen = [pairs[i] for i in idxs if i < len(pairs)]
    else:
        chosen = pairs

    equiv_by_pair: dict = {}

    def _one(pair: Pair) -> Tuple[Pair, Optional[float]]:
        ida, volta = pair
        offers = _safe_search(EconomilhasAdapter, _req_for_pair(origin, destination, ida, volta, adults, cabin))
        return pair, _cheapest_equiv(offers)

    with ThreadPoolExecutor(max_workers=min(len(chosen), 4) or 1) as ex:
        futures = [ex.submit(_one, p) for p in chosen]
        for f in as_completed(futures):
            pair, val = f.result()
            if val is not None:
                equiv_by_pair[pair] = val

    if not equiv_by_pair:
        # Nem milhas — devolve os pares centrais sem ranking real.
        return RadarResult(ranked_pairs=chosen, source="none")

    ranked = sorted(equiv_by_pair.keys(), key=lambda p: equiv_by_pair[p])
    return RadarResult(
        ranked_pairs=ranked,
        price_by_pair={_pair_label(i, v): equiv_by_pair[(i, v)] for i, v in ranked},
        source="miles_sample",
    )


def scan_skip_pairs(
    pairs: List[Pair], *, origin: str, destination: str,
    adults: int = 1, cabin="economy", max_workers: int = 4,
) -> dict:
    """Comparativo de mercado via SKIPLAGGED (hidden city/split, SEM validação de
    milhas) das combinações ida-e-volta: Skip ida (O→D) + Skip volta (D→O), pega
    o mais barato de cada perna e soma. Só pra REFERÊNCIA, não pra fechar venda.

    Deduplica as pernas (combos deslizantes compartilham datas) pra não raspar à
    toa. Devolve {label_da_combo: total_skip_brl}."""
    from backend.app.providers.skiplagged.adapter import SkiplaggedAdapter
    cab = _coerce_cabin(cabin)

    legs = {}
    for ida, volta in pairs:
        legs[(origin, destination, ida)] = None
        legs[(destination, origin, volta)] = None

    def _one(leg):
        o, d, day = leg
        offers = _safe_search(SkiplaggedAdapter, _req_for_pair(o, d, day, None, adults, cab))
        prices = [float(x.price_brl) for x in offers if x.price_brl]
        return leg, (min(prices) if prices else None)

    cheapest: dict = {}
    with ThreadPoolExecutor(max_workers=min(len(legs), max_workers) or 1) as ex:
        for f in as_completed([ex.submit(_one, lg) for lg in legs]):
            leg, price = f.result()
            cheapest[leg] = price

    out: dict = {}
    for ida, volta in pairs:
        pi = cheapest.get((origin, destination, ida))
        pv = cheapest.get((destination, origin, volta))
        if pi is not None and pv is not None:
            out[_pair_label(ida, volta)] = round(pi + pv, 2)
    return out
