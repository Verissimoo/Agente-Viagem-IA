"""Parser seats.aero: JSON cru (availability + trips) → UnifiedOffer.

Fluxo em dois passos da Partner API:
  1. /search devolve "availability objects" (rota+data+programa, por cabine) —
     só `*MileageCost`, sem horário nem taxa.
  2. /trips/{id} devolve os voos reais (segmentos com horário) + `TotalTaxes`.

Regras de normalização:
  • Milhas vêm da availability (ou do trip, se presente).
  • TAXA: seats.aero NÃO entrega taxa confiável (ausente p/ Qatar/Turkish/
    Singapore; em moeda variável nos demais). Por decisão de produto, NÃO
    fabricamos `taxes_brl` — fica None e sinalizamos em `risk_notes`.
  • Cenário sempre MILES_DIRECT.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date as _date, datetime, time
from typing import Any, Dict, List, Optional

from backend.app.domain.models import (
    Itinerary,
    Scenario,
    Segment,
    SourceType,
    TripType,
    UnifiedOffer,
)

# Cabine do nosso domínio → prefixo do campo na availability do seats.aero.
# (W/premium não existe no nosso CabinClass; mapeado p/ Y por segurança.)
_CABIN_PREFIX = {"economy": "Y", "business": "J", "first": "F"}

_TAXES_NOTE = "Award via seats.aero — taxas não incluídas (confirmar na emissão)."


@dataclass
class Availability:
    id: str
    source: str
    origin: str
    destination: str
    date: str           # YYYY-MM-DD
    miles: int
    direct: bool
    airlines: List[str] = field(default_factory=list)


def _to_int(v: Any) -> int:
    try:
        return int(float(str(v).replace(",", "").strip()))
    except (TypeError, ValueError):
        return 0


def _airlines_list(v: Any) -> List[str]:
    if isinstance(v, list):
        return [str(x).strip() for x in v if str(x).strip()]
    if isinstance(v, str):
        return [a.strip() for a in v.split(",") if a.strip()]
    return []


def _parse_dt(value: Any, fallback_date: Optional[str] = None) -> datetime:
    """ISO 8601 → datetime (aware vira naive UTC). Fallback: meio-dia da data."""
    if isinstance(value, str) and value:
        s = value.strip().replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(s)
            return dt.replace(tzinfo=None) if dt.tzinfo else dt
        except ValueError:
            pass
    if fallback_date:
        try:
            return datetime.combine(_date.fromisoformat(fallback_date), time(12, 0))
        except ValueError:
            pass
    return datetime(1970, 1, 1, 12, 0)


def _carrier_from_flight_no(flight_no: Any) -> str:
    m = re.match(r"\s*([A-Za-z]{2,3})", str(flight_no or ""))
    return m.group(1).upper() if m else ""


# ──────────────────────────────────────────────────────────────────
# /search → availabilities
# ──────────────────────────────────────────────────────────────────
def parse_availabilities(raw: Dict[str, Any], cabin: str) -> List[Availability]:
    """Extrai availabilities da cabine pedida que tenham assento + milhas > 0."""
    prefix = _CABIN_PREFIX.get((cabin or "economy").lower(), "Y")
    data = (raw or {}).get("data") or []
    out: List[Availability] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        if not item.get(f"{prefix}Available"):
            continue
        miles = _to_int(item.get(f"{prefix}MileageCost"))
        if miles <= 0:
            continue
        route = item.get("Route") or {}
        origin = (route.get("OriginAirport") or item.get("OriginAirport") or "").upper()
        destination = (route.get("DestinationAirport") or item.get("DestinationAirport") or "").upper()
        source = (route.get("Source") or item.get("Source") or "").lower()
        avail_id = str(item.get("ID") or item.get("id") or "")
        if not (avail_id and origin and destination and source):
            continue
        out.append(Availability(
            id=avail_id,
            source=source,
            origin=origin,
            destination=destination,
            date=str(item.get("Date") or item.get("ParsedDate") or "")[:10],
            miles=miles,
            direct=bool(item.get(f"{prefix}Direct")),
            airlines=_airlines_list(item.get(f"{prefix}Airlines")),
        ))
    return out


# ──────────────────────────────────────────────────────────────────
# /trips → Itinerary
# ──────────────────────────────────────────────────────────────────
def _trips_list(raw: Dict[str, Any]) -> List[Dict[str, Any]]:
    if isinstance(raw, list):
        return [t for t in raw if isinstance(t, dict)]
    data = (raw or {}).get("data")
    if isinstance(data, list):
        return [t for t in data if isinstance(t, dict)]
    return []


def _cabin_matches(trip: Dict[str, Any], cabin: str) -> bool:
    c = str(trip.get("Cabin") or "").lower()
    return (not c) or c.startswith((cabin or "economy").lower()[:4])


def itinerary_from_trip(
    trip_raw: Dict[str, Any], cabin: str, avail: Availability,
) -> Optional[tuple[Itinerary, int, str]]:
    """Escolhe o trip mais barato na cabine e monta o Itinerary.

    Retorna (itinerary, miles, carrier) ou None se não há trip utilizável.
    """
    trips = [t for t in _trips_list(trip_raw) if _cabin_matches(t, cabin)]
    if not trips:
        return None
    trips.sort(key=lambda t: _to_int(t.get("MileageCost")) or avail.miles)
    trip = trips[0]

    segs_raw = trip.get("AvailabilitySegments") or trip.get("Segments") or []
    segments: List[Segment] = []
    for s in segs_raw:
        if not isinstance(s, dict):
            continue
        flight_no = s.get("FlightNumber") or s.get("FlightNumbers")
        carrier = (s.get("Carrier") or _carrier_from_flight_no(flight_no)
                   or (avail.airlines[0] if avail.airlines else "")).upper()
        segments.append(Segment(
            origin=(s.get("OriginAirport") or "").upper(),
            destination=(s.get("DestinationAirport") or "").upper(),
            departure_dt=_parse_dt(s.get("DepartsAt"), avail.date),
            arrival_dt=_parse_dt(s.get("ArrivesAt"), avail.date),
            carrier=carrier or "??",
            flight_number=str(flight_no) if flight_no else None,
        ))
    if not segments:
        return None
    miles = _to_int(trip.get("MileageCost")) or avail.miles
    carrier = segments[0].carrier
    return Itinerary(segments=segments), miles, carrier


def synthetic_itinerary(avail: Availability) -> tuple[Itinerary, int, str]:
    """Fallback quando /trips falha: 1 segmento direto sem horário preciso."""
    dep = _parse_dt(None, avail.date)
    carrier = (avail.airlines[0] if avail.airlines else "??").upper()
    seg = Segment(
        origin=avail.origin, destination=avail.destination,
        departure_dt=dep, arrival_dt=dep, carrier=carrier or "??",
    )
    return Itinerary(segments=[seg]), avail.miles, carrier


# ──────────────────────────────────────────────────────────────────
# UnifiedOffer builders
# ──────────────────────────────────────────────────────────────────
def build_oneway_offer(
    itin: Itinerary, miles: int, carrier: str, program_label: str,
    *, approximate_times: bool = False,
) -> UnifiedOffer:
    notes = _TAXES_NOTE
    if approximate_times:
        notes += " Horários aproximados (detalhe de voo indisponível)."
    return UnifiedOffer(
        source=SourceType.SEATS_AERO,
        airline=carrier or program_label,
        trip_type=TripType.ONEWAY,
        outbound=itin,
        miles=int(miles),
        miles_program=program_label,
        taxes_brl=None,
        scenario=Scenario.MILES_DIRECT,
        risk_notes=notes,
    )


def build_roundtrip_offer(
    out_itin: Itinerary, out_miles: int,
    in_itin: Itinerary, in_miles: int,
    carrier: str, program_label: str,
    *, approximate_times: bool = False,
) -> UnifiedOffer:
    notes = _TAXES_NOTE
    if approximate_times:
        notes += " Horários aproximados (detalhe de voo indisponível)."
    return UnifiedOffer(
        source=SourceType.SEATS_AERO,
        airline=carrier or program_label,
        trip_type=TripType.ROUNDTRIP,
        outbound=out_itin,
        inbound=in_itin,
        miles=int(out_miles) + int(in_miles),
        miles_out=int(out_miles),
        miles_in=int(in_miles),
        miles_program=program_label,
        taxes_brl=None,
        scenario=Scenario.MILES_DIRECT,
        risk_notes=notes,
    )
