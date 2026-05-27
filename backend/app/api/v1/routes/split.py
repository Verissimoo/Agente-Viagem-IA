"""POST /api/v1/split — runs the segment-split agent (GRU break) for a route.

Returns up to N domestic legs (origin→hub) + N international legs (hub→destination)
plus a direct flight for comparison. UI consumes these and lets the user combine.

Fase 2 — POST /api/v1/split/fit
Given a specific international leg the user picked, returns the domestic legs
that fit the connection window (150min/240min depending on baggage) on the
correct date (same day or day before/after, computed from intl departure).

Fase 3 — POST /api/v1/split/miles-search
Direct miles search hub→destination, without anchoring to a Kayak flight.
This is the primary workflow: user picks a real miles offer (with actual
program availability), then fits the domestic leg around it.
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import date as _date, datetime
from typing import Literal, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from backend.app.domain.models import UnifiedOffer
from backend.app.services.search_orchestrator import run_pipeline
from backend.app.services.segment_split import KayakOffer, SegmentSplitAgent

router = APIRouter(tags=["split"])


class SplitRequest(BaseModel):
    origin: str = Field(..., min_length=3, max_length=3)
    destination: str = Field(..., min_length=3, max_length=3)
    date: _date
    adults: int = Field(1, ge=1, le=9)
    return_date: Optional[_date] = None
    hub: str = Field("GRU", min_length=3, max_length=3)


class KayakLegDTO(BaseModel):
    id: str
    airline: str
    airlines: list[str] = []
    airlines_iata: list[str] = []
    origin: str
    destination: str
    departure_dt: Optional[str] = None
    arrival_dt: Optional[str] = None
    duration_min: int = 0
    stops: int = 0
    price_brl: float = 0.0


class SplitResponse(BaseModel):
    origin: str
    destination: str
    date: str
    route_type: str
    hub: str
    leg_to_hub: list[KayakLegDTO] = []
    leg_from_hub: list[KayakLegDTO] = []
    direct: Optional[KayakLegDTO] = None
    not_applicable_reason: Optional[str] = None
    notes: list[str] = []


def _to_dto(off) -> KayakLegDTO:
    from backend.app.services.segment_split import offer_id
    airlines = list(getattr(off, "airlines", []) or [])
    iata = list(getattr(off, "airlines_iata", []) or [])
    return KayakLegDTO(
        id=offer_id(off),
        airline=(airlines[0] if airlines else (iata[0] if iata else "")),
        airlines=airlines,
        airlines_iata=iata,
        origin=off.origin,
        destination=off.destination,
        departure_dt=off.departure_dt.isoformat() if off.departure_dt else None,
        arrival_dt=off.arrival_dt.isoformat() if off.arrival_dt else None,
        duration_min=off.duration_min,
        stops=off.stops,
        price_brl=off.price_brl,
    )


@router.post("/split", response_model=SplitResponse)
def split(payload: SplitRequest) -> SplitResponse:
    try:
        result = SegmentSplitAgent().run(
            origin=payload.origin,
            destination=payload.destination,
            date=payload.date.isoformat(),
            adults=payload.adults,
            return_date=payload.return_date.isoformat() if payload.return_date else None,
            hub=payload.hub,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Falha no segment split: {e}") from e

    return SplitResponse(
        origin=result.origin,
        destination=result.destination,
        date=result.date,
        route_type=result.route_type,
        hub=result.hub,
        leg_to_hub=[_to_dto(o) for o in (result.leg_to_gru or [])],
        leg_from_hub=[_to_dto(o) for o in (result.leg_from_gru or [])],
        direct=_to_dto(result.direct_offer) if result.direct_offer else None,
        not_applicable_reason=result.not_applicable_reason,
        notes=result.notes,
    )


# ────────────────────────────────────────────────────────────────────
# Fase 2 — encaixe do voo doméstico para um voo internacional escolhido
# ────────────────────────────────────────────────────────────────────
class IntlLegInput(BaseModel):
    """Voo internacional selecionado, no formato do KayakLegDTO devolvido por /split."""
    origin: str
    destination: str
    airline: str = ""
    airlines: list[str] = []
    airlines_iata: list[str] = []
    departure_dt: Optional[str] = None
    arrival_dt: Optional[str] = None
    duration_min: int = 0
    stops: int = 0
    price_brl: float = 0.0


class SplitFitRequest(BaseModel):
    """Pesquisa voos domésticos compatíveis com um voo internacional escolhido.

    `intl_direction`:
      • "from_gru" — voo internacional SAI de GRU; doméstico precisa CHEGAR
        em GRU antes (com folga = janela de conexão)
      • "to_gru"   — voo internacional CHEGA em GRU; doméstico SAI de GRU
        depois (com folga = janela de conexão)
    """
    intl_offer: IntlLegInput
    other_endpoint: str = Field(..., min_length=3, max_length=3, description="Aeroporto da outra ponta (BSB, CGB etc)")
    intl_direction: Literal["from_gru", "to_gru"]
    adults: int = Field(1, ge=1, le=9)
    with_baggage: bool = False


class FitOfferDTO(KayakLegDTO):
    """KayakLegDTO + dados de conexão calculados para o encaixe."""
    layover_minutes: int = 0
    layover_status: str = "ok"  # "ok" | "tight" | "long" | "invalid"


class SplitFitResponse(BaseModel):
    search_date: str
    search_date_offset: str               # "same_day" | "day_before" | "day_after"
    target_window_start: Optional[str] = None
    target_window_end: Optional[str] = None
    compatible_offers: list[FitOfferDTO] = []
    incompatible_offers: list[FitOfferDTO] = []
    no_results: bool = False
    with_baggage: bool = False
    notes: list[str] = []


def _classify_layover(minutes: int, with_baggage: bool) -> str:
    """Espelha as regras visuais do legado (verde/amarelo/cinza/vermelho)."""
    min_conn = 240 if with_baggage else 150
    if minutes < min_conn:
        return "invalid"
    if minutes <= min_conn + 30:
        return "tight"
    if minutes > 720:
        return "long"
    return "ok"


def _fit_offer_to_dto(off: KayakOffer, layover_min: int, with_baggage: bool) -> FitOfferDTO:
    from backend.app.services.segment_split import offer_id
    airlines = list(getattr(off, "airlines", []) or [])
    iata = list(getattr(off, "airlines_iata", []) or [])
    return FitOfferDTO(
        id=offer_id(off),
        airline=(airlines[0] if airlines else (iata[0] if iata else "")),
        airlines=airlines,
        airlines_iata=iata,
        origin=off.origin,
        destination=off.destination,
        departure_dt=off.departure_dt.isoformat() if off.departure_dt else None,
        arrival_dt=off.arrival_dt.isoformat() if off.arrival_dt else None,
        duration_min=off.duration_min,
        stops=off.stops,
        price_brl=off.price_brl,
        layover_minutes=layover_min,
        layover_status=_classify_layover(layover_min, with_baggage),
    )


def _layover_with_intl(dom: KayakOffer, intl_dt: Optional[datetime], direction: str) -> int:
    """Tempo de conexão em minutos entre voo doméstico e a perna internacional."""
    if intl_dt is None:
        return 0
    if direction == "from_gru":
        # intl sai DEPOIS do doméstico chegar
        if not dom.arrival_dt:
            return 0
        return int((intl_dt - dom.arrival_dt).total_seconds() / 60)
    # to_gru: intl chega ANTES do doméstico sair
    if not dom.departure_dt:
        return 0
    return int((dom.departure_dt - intl_dt).total_seconds() / 60)


def _input_to_kayak_offer(leg: IntlLegInput) -> KayakOffer:
    def _parse(s: Optional[str]) -> Optional[datetime]:
        if not s:
            return None
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            return None
    return KayakOffer(
        origin=leg.origin.upper(),
        destination=leg.destination.upper(),
        airlines=leg.airlines or ([leg.airline] if leg.airline else []),
        airlines_iata=leg.airlines_iata,
        departure_dt=_parse(leg.departure_dt),
        arrival_dt=_parse(leg.arrival_dt),
        duration_min=leg.duration_min,
        stops=leg.stops,
        price_brl=leg.price_brl,
    )


@router.post("/split/fit", response_model=SplitFitResponse)
def split_fit(payload: SplitFitRequest) -> SplitFitResponse:
    """Fase 2: dado um voo internacional escolhido, retorna voos domésticos
    compatíveis com a janela de conexão (150min/240min) calculando
    automaticamente a data correta (mesmo dia, dia anterior ou seguinte)."""
    intl_offer = _input_to_kayak_offer(payload.intl_offer)
    try:
        fit = SegmentSplitAgent().fit_domestic_leg(
            intl_offer=intl_offer,
            other_endpoint=payload.other_endpoint.upper(),
            intl_direction=payload.intl_direction,
            adults=payload.adults,
            with_baggage=payload.with_baggage,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Falha no split/fit: {e}") from e

    # intl_dt usado para calcular layover:
    intl_dt_ref = (
        intl_offer.departure_dt if payload.intl_direction == "from_gru"
        else intl_offer.arrival_dt
    )

    compat = [
        _fit_offer_to_dto(o, _layover_with_intl(o, intl_dt_ref, payload.intl_direction), payload.with_baggage)
        for o in fit.compatible_offers
    ]
    incompat = [
        _fit_offer_to_dto(o, _layover_with_intl(o, intl_dt_ref, payload.intl_direction), payload.with_baggage)
        for o in fit.incompatible_offers
    ]

    return SplitFitResponse(
        search_date=fit.search_date,
        search_date_offset=fit.search_date_offset,
        target_window_start=fit.target_window_start.isoformat() if fit.target_window_start else None,
        target_window_end=fit.target_window_end.isoformat() if fit.target_window_end else None,
        compatible_offers=compat,
        incompatible_offers=incompat,
        no_results=fit.no_results,
        with_baggage=fit.with_baggage,
        notes=fit.notes,
    )


# ────────────────────────────────────────────────────────────────────
# Fase 3 — busca direta de milhas hub → destino
# ────────────────────────────────────────────────────────────────────
class SplitMilesSearchRequest(BaseModel):
    """Busca milhas reais entre o hub e o destino, sem ancorar em voo Kayak.

    Diferente de /miles-match (que tenta encontrar UM voo específico em milhas),
    este endpoint mostra TODAS as opções de milhas disponíveis hub→destino,
    pra usuário escolher uma real e só depois encaixar o voo doméstico.
    """
    origin: str = Field(..., min_length=3, max_length=3, description="Hub (GRU, GIG, BSB, ...)")
    destination: str = Field(..., min_length=3, max_length=3)
    date: _date
    adults: int = Field(1, ge=1, le=9)
    return_date: Optional[_date] = None


class SplitMilesOfferDTO(BaseModel):
    """Oferta de milhas hub→destino, normalizada pra UI escolher."""
    id: str
    source: str                          # buscamilhas / economilhas / mcp_award
    program: Optional[str] = None        # SMILES, LATAM_PASS, AZUL_FIDELIDADE...
    carrier: str                         # IATA (LA, G3, AD, JJ, ...)
    carrier_name: str
    flight_number: Optional[str] = None
    origin: str
    destination: str
    departure_dt: Optional[str] = None
    arrival_dt: Optional[str] = None
    duration_min: int = 0
    stops: int = 0
    miles: int
    taxes_brl: float
    equivalent_brl: float                # custo total (milhas valuated + taxas)
    deeplink: Optional[str] = None


class SplitMilesSearchResponse(BaseModel):
    origin: str
    destination: str
    date: str
    offers: list[SplitMilesOfferDTO] = []
    programs_seen: list[str] = []
    carriers_seen: list[str] = []
    notes: list[str] = []


_MAX_MILES_OFFERS = 30


def _offer_to_miles_dto(offer: UnifiedOffer) -> Optional[SplitMilesOfferDTO]:
    """Converte UnifiedOffer (miles) → DTO. Retorna None se faltar dado crítico."""
    if offer.miles is None:
        return None
    seg = offer.outbound.segments[0] if offer.outbound and offer.outbound.segments else None
    last_seg = offer.outbound.segments[-1] if offer.outbound and offer.outbound.segments else None
    if seg is None or last_seg is None:
        return None

    carrier_iata = seg.carrier or ""
    flight_number = seg.flight_number
    deep_id = f"{offer.source.value}:{carrier_iata}:{flight_number or 'NA'}:{seg.departure_dt.isoformat()}"

    return SplitMilesOfferDTO(
        id=deep_id,
        source=offer.source.value,
        program=offer.miles_program,
        carrier=carrier_iata,
        carrier_name=offer.airline or carrier_iata,
        flight_number=flight_number,
        origin=seg.origin,
        destination=last_seg.destination,
        departure_dt=seg.departure_dt.isoformat() if seg.departure_dt else None,
        arrival_dt=last_seg.arrival_dt.isoformat() if last_seg.arrival_dt else None,
        duration_min=offer.outbound.duration_min or 0,
        stops=offer.stops_out or 0,
        miles=int(offer.miles),
        taxes_brl=float(offer.taxes_brl or 0),
        equivalent_brl=float(offer.equivalent_brl or 0),
        deeplink=offer.deeplink,
    )


@router.post("/split/miles-search", response_model=SplitMilesSearchResponse)
def split_miles_search(payload: SplitMilesSearchRequest) -> SplitMilesSearchResponse:
    """Roda o pipeline focado em milhas pra perna hub → destino.

    Diferente de /split (que devolve Kayak cash hub→destino), este endpoint
    devolve as ofertas REAIS de milhas (BuscaMilhas + Economilhas + MCP) pro
    vendedor escolher antes de encaixar a perna doméstica.
    """
    try:
        result = run_pipeline(
            prompt="",
            top_n=50,
            origin=payload.origin.upper(),
            destination=payload.destination.upper(),
            date_start=payload.date,
            date_return=payload.return_date,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Falha na busca de milhas: {e}") from e

    miles_dtos: list[SplitMilesOfferDTO] = []
    seen_ids: set[str] = set()
    for offer in result.miles_offers:
        dto = _offer_to_miles_dto(offer)
        if dto is None:
            continue
        if dto.id in seen_ids:
            continue
        seen_ids.add(dto.id)
        miles_dtos.append(dto)

    miles_dtos.sort(key=lambda d: d.equivalent_brl or float("inf"))
    miles_dtos = miles_dtos[:_MAX_MILES_OFFERS]

    programs = sorted({d.program for d in miles_dtos if d.program})
    carriers = sorted({d.carrier for d in miles_dtos if d.carrier})

    notes: list[str] = []
    if not miles_dtos:
        notes.append("Nenhum programa retornou disponibilidade pra essa rota/data.")

    return SplitMilesSearchResponse(
        origin=payload.origin.upper(),
        destination=payload.destination.upper(),
        date=payload.date.isoformat(),
        offers=miles_dtos,
        programs_seen=programs,
        carriers_seen=carriers,
        notes=notes,
    )
