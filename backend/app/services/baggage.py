"""Regras de bagagem despachada (23kg).

Fonte de verdade: os dados que a nossa rede de cotação (BuscaMilhas) devolve
quando a tarifa traz o tier com mala. Quando o dado não vem, aplicamos
fallbacks por programa:

  • Smiles (GOL) doméstico — não traz tier de bagagem nos dados: R$ 130 por
    trecho e por passageiro em qualquer voo nacional.
  • Internacional sem dado confiável: NÃO afirmamos o valor — sinalizamos que
    precisa ser confirmado na emissão.

Hidden city é caso à parte: bagagem despachada é IMPOSSÍVEL — a mala seguiria
para o destino oficial do bilhete, não para a cidade onde o passageiro desce.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from backend.app.domain.models import Itinerary, Scenario, UnifiedOffer
from backend.app.services.segment_split import BR_AIRPORTS

# Smiles (GOL) não expõe tier de mala — fee fixo doméstico por trecho/passageiro.
SMILES_DOMESTIC_BAG_BRL: float = 130.0

# Status possíveis de BaggageInfo.status:
INCLUDED = "included"        # tarifa já inclui mala despachada
ADDABLE = "addable"          # pode adicionar e temos o valor (dado real ou regra Smiles)
NOT_ALLOWED = "not_allowed"  # hidden city: impossível despachar
UNKNOWN = "unknown"          # não temos certeza do valor (ex.: internacional sem dado)


@dataclass
class BaggageInfo:
    """Resultado da regra de bagagem para UM trecho (ida ou volta), por passageiro."""
    status: str
    extra_brl: Optional[float] = None     # custo adicional por passageiro neste trecho
    extra_miles: Optional[int] = None     # custo adicional em milhas neste trecho (se aplicável)
    certain: bool = True                  # False quando é estimativa não confirmada
    note: str = ""


def leg_is_domestic(itinerary: Optional[Itinerary]) -> bool:
    """True se TODOS os aeroportos do trecho são brasileiros."""
    if not itinerary or not itinerary.segments:
        return False
    for seg in itinerary.segments:
        if (seg.origin or "").upper() not in BR_AIRPORTS:
            return False
        if (seg.destination or "").upper() not in BR_AIRPORTS:
            return False
    return True


def _carrier_of(itinerary: Optional[Itinerary]) -> str:
    if not itinerary or not itinerary.segments:
        return ""
    return (itinerary.segments[0].carrier or "").upper()[:3]


def _decide(
    *,
    is_hidden: bool,
    domestic: bool,
    carrier: str,
    leg_miles: Optional[int],
    leg_baggage_miles: Optional[int],
    rate_per_mile: Optional[float],
) -> BaggageInfo:
    """Núcleo da regra — independente de o input ser UnifiedOffer ou dict."""
    # 1. Hidden city — bagagem despachada é impossível.
    if is_hidden:
        return BaggageInfo(
            status=NOT_ALLOWED,
            note=(
                "Não dá pra despachar bagagem (23kg) nesta tarifa hidden city: a mala "
                "iria para o destino final do bilhete, não para a cidade onde o cliente "
                "desce. Só bagagem de mão."
            ),
        )

    # 2. Dado real da nossa rede — tier com mala explícito.
    if leg_baggage_miles is not None:
        base = leg_miles or 0
        incremental = max(0, int(leg_baggage_miles) - base)
        if incremental == 0:
            return BaggageInfo(
                status=INCLUDED,
                extra_brl=0.0,
                extra_miles=0,
                note="Mala despachada (23kg) já inclusa na tarifa.",
            )
        extra_brl = round(incremental * rate_per_mile, 2) if rate_per_mile else None
        brl_txt = f" (≈ R$ {extra_brl:.0f})" if extra_brl else ""
        return BaggageInfo(
            status=ADDABLE,
            extra_brl=extra_brl,
            extra_miles=incremental,
            note=f"Mala despachada (23kg): + {incremental:,} mi{brl_txt} por trecho.".replace(",", "."),
        )

    # 3. Smiles (GOL) doméstico — sem tier nos dados: fee fixo conhecido.
    if domestic and carrier == "G3":
        return BaggageInfo(
            status=ADDABLE,
            extra_brl=SMILES_DOMESTIC_BAG_BRL,
            note=f"Mala despachada (23kg) Smiles: R$ {SMILES_DOMESTIC_BAG_BRL:.0f} por trecho.",
        )

    # 4. Sem dado confiável — não afirmamos o valor.
    where = "internacional" if not domestic else "desta tarifa"
    return BaggageInfo(
        status=UNKNOWN,
        certain=False,
        note=(
            f"Não conseguimos confirmar o valor da bagagem despachada (23kg) {where} — "
            "precisa ser verificado na emissão."
        ),
    )


def _rate_per_mile(offer: UnifiedOffer, real_cost: Optional[float]) -> Optional[float]:
    """Valor do milheiro implícito (BRL por milha), a partir do custo real e taxas."""
    if offer.miles and offer.miles > 0 and real_cost is not None:
        net = real_cost - float(offer.taxes_brl or 0)
        if net > 0:
            return net / offer.miles
    return None


def baggage_for_row(
    offer: UnifiedOffer,
    itinerary: Optional[Itinerary],
    leg: str,
    *,
    real_cost: Optional[float],
) -> BaggageInfo:
    """Conveniência para a tabela de cotação: resolve milhas/baggage do trecho
    (IDA usa *_out, VOLTA usa *_in) e calcula a bagagem."""
    if (leg or "").upper() == "VOLTA":
        leg_miles = offer.miles_in
        leg_bag = offer.baggage_miles_in
    else:
        leg_miles = offer.miles_out if offer.miles_out is not None else offer.miles
        leg_bag = offer.baggage_miles_out
    return _decide(
        is_hidden=offer.scenario == Scenario.HIDDEN_CITY,
        domestic=leg_is_domestic(itinerary),
        carrier=_carrier_of(itinerary),
        leg_miles=leg_miles,
        leg_baggage_miles=leg_bag,
        rate_per_mile=_rate_per_mile(offer, real_cost),
    )


def _segments_domestic(segments: list) -> bool:
    if not segments:
        return False
    for s in segments:
        if str(s.get("origin") or "").upper() not in BR_AIRPORTS:
            return False
        if str(s.get("destination") or "").upper() not in BR_AIRPORTS:
            return False
    return True


def baggage_from_dict(offer: dict, leg: str = "IDA") -> BaggageInfo:
    """Versão dict-based para o chat (presenter), onde ofertas já são dicts
    serializados de UnifiedOffer (model_dump)."""
    scenario = str(offer.get("scenario") or "")
    is_hidden = scenario == Scenario.HIDDEN_CITY.value or "hidden" in scenario.lower()

    itin = offer.get("inbound" if (leg or "").upper() == "VOLTA" else "outbound") or {}
    segs = itin.get("segments") or []
    carrier = str((segs[0].get("carrier") if segs else "") or "").upper()[:3]

    if (leg or "").upper() == "VOLTA":
        leg_miles = offer.get("miles_in")
        leg_bag = offer.get("baggage_miles_in")
    else:
        leg_miles = offer.get("miles_out") if offer.get("miles_out") is not None else offer.get("miles")
        leg_bag = offer.get("baggage_miles_out")

    miles_total = offer.get("miles")
    real_cost = offer.get("equivalent_brl") or offer.get("price_brl")
    rate = None
    if miles_total and real_cost:
        net = float(real_cost) - float(offer.get("taxes_brl") or 0)
        if net > 0:
            rate = net / miles_total

    return _decide(
        is_hidden=is_hidden,
        domestic=_segments_domestic(segs),
        carrier=carrier,
        leg_miles=leg_miles,
        leg_baggage_miles=leg_bag,
        rate_per_mile=rate,
    )
