"""POST /api/v1/validate-flight — confirms a quoted flight against BuscaMilhas.

Workflow (the only deeplink to the airline can't be trusted blindly):
  1. Receives carrier, flight_number, route, departure datetime, and the
     quoted cash price (if any).
  2. Hits BuscaMilhas (the carrier's own program: G3→Smiles, LA→LATAM Pass,
     AD→TudoAzul) for the same route+date.
  3. Walks the returned offers looking for a flight that matches the same
     carrier + departure time (±10min).
  4. Reports status: `found_with_match` / `found_no_match` / `no_offers` /
     `unsupported_carrier`. The UI shows a green check / amber warning.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from backend.app.services.miles_match import (
    CARRIER_TO_OWN_PROGRAM,
    EXACT_MATCH_MINUTES_TOLERANCE,
    PROGRAM_TO_BUSCAMILHAS_NAME,
)
from backend.app.providers.buscamilhas.client import search_flights_buscamilhas
from backend.app.providers.buscamilhas.parser import extract_rows_from_buscamilhas

router = APIRouter(tags=["validate"])


class ValidateFlightRequest(BaseModel):
    carrier: str = Field(..., min_length=2, max_length=3, description="IATA do operating carrier (G3, LA, AD, ...)")
    origin: str = Field(..., min_length=3, max_length=3)
    destination: str = Field(..., min_length=3, max_length=3)
    departure_dt: str = Field(..., description="ISO datetime do voo a validar")
    adults: int = Field(1, ge=1, le=9)
    quoted_price_brl: float | None = Field(None, description="Preço cash citado (para comparar com milhas)")
    quoted_miles: int | None = Field(None, description="Milhas citadas (para comparar)")


class MatchedFlightDTO(BaseModel):
    flight_number: str | None = None
    carrier: str | None = None
    departure_dt: str | None = None
    arrival_dt: str | None = None
    miles: int
    taxes_brl: float


Status = Literal[
    "found_with_match",          # voo casa no BuscaMilhas (cia + horário)
    "found_no_match",            # BuscaMilhas tem voos na rota mas nenhum casa com este voo
    "no_offers",                 # BuscaMilhas não retornou nada
    "unsupported_carrier",       # carrier não tem programa próprio mapeado
    "error",                     # falha técnica
]


class ValidateFlightResponse(BaseModel):
    status: Status
    message: str
    carrier: str
    program: str | None = None
    queried_date: str
    matches: list[MatchedFlightDTO] = []
    nearby: list[MatchedFlightDTO] = []
    cheapest_miles: int | None = None
    cheapest_total_brl: float | None = None
    price_comparison: str | None = None


def _parse_dt(s: str) -> datetime | None:
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def _row_to_dto(row: dict, dep_dt: datetime | None) -> MatchedFlightDTO:
    miles = int(row.get("Milhas") or 0)
    taxes = float(row.get("Taxas (R$)") or 0.0)
    flight_no = row.get("NumeroVoo") or row.get("Voo")
    carrier = (row.get("Companhia") or "").upper() or None
    arr_dt = row.get("arrival_dt")
    return MatchedFlightDTO(
        flight_number=str(flight_no) if flight_no else None,
        carrier=carrier,
        departure_dt=dep_dt.isoformat() if dep_dt else None,
        arrival_dt=arr_dt.isoformat() if isinstance(arr_dt, datetime) else None,
        miles=miles,
        taxes_brl=taxes,
    )


@router.post("/validate-flight", response_model=ValidateFlightResponse)
def validate_flight(payload: ValidateFlightRequest) -> ValidateFlightResponse:
    carrier_iata = payload.carrier.upper().strip()
    program = CARRIER_TO_OWN_PROGRAM.get(carrier_iata)
    if not program:
        return ValidateFlightResponse(
            status="unsupported_carrier",
            message=(
                f"Carrier {carrier_iata} não tem programa próprio mapeado "
                f"(suportados: G3 Smiles, LA LATAM Pass, AD TudoAzul). "
                f"Não temos como validar via BuscaMilhas."
            ),
            carrier=carrier_iata,
            queried_date=payload.departure_dt[:10],
        )

    target_dt = _parse_dt(payload.departure_dt)
    if target_dt is None:
        raise HTTPException(status_code=400, detail="departure_dt inválido")

    comp = PROGRAM_TO_BUSCAMILHAS_NAME.get(program)
    if not comp:
        return ValidateFlightResponse(
            status="unsupported_carrier",
            message=f"Programa {program} sem mapeamento BuscaMilhas.",
            carrier=carrier_iata,
            program=program,
            queried_date=target_dt.date().isoformat(),
        )

    try:
        data_ida_br = target_dt.strftime("%d/%m/%Y")
        raw = search_flights_buscamilhas(
            companhia=comp,
            origem=payload.origin.upper(),
            destino=payload.destination.upper(),
            data_ida=data_ida_br,
            adultos=payload.adults,
            somente_milhas=True,
        )
        rows = extract_rows_from_buscamilhas(raw, comp, "OW")
        rows = [r for r in rows if r.get("IsMiles")]
    except Exception as e:
        return ValidateFlightResponse(
            status="error",
            message=f"Falha ao consultar BuscaMilhas: {str(e)[:200]}",
            carrier=carrier_iata,
            program=program,
            queried_date=target_dt.date().isoformat(),
        )

    if not rows:
        return ValidateFlightResponse(
            status="no_offers",
            message=(
                f"BuscaMilhas não retornou nenhuma oferta {comp} para "
                f"{payload.origin}→{payload.destination} em {target_dt.date()}. "
                f"O voo pode existir mas estar fora do inventário de milhas — "
                f"confira direto na cia."
            ),
            carrier=carrier_iata,
            program=program,
            queried_date=target_dt.date().isoformat(),
        )

    # Find exact-time matches (±10min) and "nearby" candidates (same day).
    matches: list[tuple[dict, datetime]] = []
    nearby: list[tuple[dict, datetime]] = []
    for row in rows:
        dep = row.get("departure_dt")
        if not isinstance(dep, datetime):
            continue
        if dep.date() != target_dt.date():
            continue
        delta_min = abs((dep - target_dt).total_seconds()) / 60.0
        if delta_min <= EXACT_MATCH_MINUTES_TOLERANCE:
            matches.append((row, dep))
        else:
            nearby.append((row, dep))

    matches.sort(key=lambda t: (int(t[0].get("Milhas") or 0) + float(t[0].get("Taxas (R$)") or 0)))
    nearby.sort(key=lambda t: abs((t[1] - target_dt).total_seconds()))

    matches_dto = [_row_to_dto(r, d) for r, d in matches[:5]]
    nearby_dto = [_row_to_dto(r, d) for r, d in nearby[:5]]

    cheapest = matches_dto[0] if matches_dto else None
    cheapest_miles = cheapest.miles if cheapest else None
    cheapest_total_brl: float | None = None
    if cheapest:
        # Rough miles → BRL just so the UI can show context. Not the source of
        # truth: the seller cares about miles+taxes here.
        from backend.app.services.conversion import cost_per_mile
        rate = cost_per_mile(program=comp, miles=cheapest.miles)
        cheapest_total_brl = round(cheapest.miles * rate + cheapest.taxes_brl, 2)

    if not matches_dto:
        return ValidateFlightResponse(
            status="found_no_match",
            message=(
                f"BuscaMilhas retornou {len(rows)} ofertas {comp} na rota+data, "
                f"mas nenhuma com horário próximo a {target_dt.strftime('%H:%M')} "
                f"(±{EXACT_MATCH_MINUTES_TOLERANCE}min). Esse voo específico pode "
                f"não estar disponível neste programa hoje."
            ),
            carrier=carrier_iata,
            program=program,
            queried_date=target_dt.date().isoformat(),
            nearby=nearby_dto,
        )

    # Build optional price comparison line.
    price_cmp: str | None = None
    if payload.quoted_price_brl and cheapest_total_brl:
        diff = cheapest_total_brl - payload.quoted_price_brl
        pct = abs(diff) / payload.quoted_price_brl * 100
        if diff > 0:
            price_cmp = (
                f"Milhas saem ~R$ {cheapest_total_brl:.0f} "
                f"({pct:.0f}% acima do cash citado de R$ {payload.quoted_price_brl:.0f})."
            )
        else:
            price_cmp = (
                f"Milhas saem ~R$ {cheapest_total_brl:.0f} "
                f"({pct:.0f}% ABAIXO do cash citado de R$ {payload.quoted_price_brl:.0f})."
            )

    return ValidateFlightResponse(
        status="found_with_match",
        message=(
            f"Voo confirmado em {comp}: {len(matches_dto)} opção(ões) na mesma "
            f"rota, mesma data, mesmo horário (±{EXACT_MATCH_MINUTES_TOLERANCE}min)."
        ),
        carrier=carrier_iata,
        program=program,
        queried_date=target_dt.date().isoformat(),
        matches=matches_dto,
        nearby=nearby_dto,
        cheapest_miles=cheapest_miles,
        cheapest_total_brl=cheapest_total_brl,
        price_comparison=price_cmp,
    )
