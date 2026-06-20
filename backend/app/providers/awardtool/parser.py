"""Parser AwardTool: JSON decodificado (`result[]`) → List[UnifiedOffer].

Cada item de `result` é um voo (uma cabine) com:
  p_c   programa de milhas (código IATA, ex "TP", "AC")
  a_p   milhas (award points)
  sc    taxa · c  moeda da taxa (ex "USD")
  a_n   nome da cia · a_c código · c_t cabine · date data · url booking
  fare.ps[]  segmentos (or, de, d_t/a_t ISO, a_c carrier, f_n flight no, ac aircraft)

Taxa convertida pra BRL via fx_rates. Milhas → BRL fica pro ranking (tarifa
base internacional 0,05 via SourceType.AWARDTOOL em conversion.py).
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from backend.app.domain.models import (
    Itinerary,
    Scenario,
    Segment,
    SourceType,
    TripType,
    UnifiedOffer,
)
from backend.app.infrastructure import fx_rates

# Código do programa (p_c) → label exibida em miles_program.
PROGRAM_LABEL: Dict[str, str] = {
    "AC": "Aeroplan (Air Canada)",
    "AV": "LifeMiles (Avianca)",
    "KL": "Flying Blue (AF/KLM)",
    "AF": "Flying Blue (AF/KLM)",
    "AY": "Finnair Plus",
    "IB": "Iberia Avios",
    "BA": "British Avios",
    "QR": "Qatar Privilege Club",
    "AS": "Alaska Mileage Plan",
    "CM": "Copa ConnectMiles",
    "TP": "TAP Miles&Go",
    "VS": "Virgin Atlantic Flying Club",
    "EK": "Emirates Skywards",
    "EY": "Etihad Guest",
    "UA": "United MileagePlus",
    "AA": "American AAdvantage",
    "TK": "Turkish Miles&Smiles",
    "SK": "SAS EuroBonus",
    "DL": "Delta SkyMiles",
    "QF": "Qantas Frequent Flyer",
    "G3": "Smiles (GOL)",
    "AM": "Aeromexico Club Premier",
}


def _to_int(v: Any) -> int:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return 0


def _to_float(v: Any) -> Optional[float]:
    try:
        f = float(v)
        return f if f >= 0 else None
    except (TypeError, ValueError):
        return None


def _parse_dt(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00").split("+")[0])
    except ValueError:
        return None


def _taxes_brl(amount: Optional[float], currency: Optional[str]) -> Optional[float]:
    if not amount:
        return 0.0
    ccy = (currency or "USD").upper()
    if ccy == "BRL":
        return round(amount, 2)
    try:
        return round(fx_rates.convert(amount, ccy, "BRL"), 2)
    except Exception:
        return None  # taxa em moeda não conversível → não fabrica valor


def _segments(item: Dict[str, Any]) -> List[Segment]:
    ps = ((item.get("fare") or {}).get("ps")) or []
    segs: List[Segment] = []
    for s in ps:
        if not isinstance(s, dict):
            continue
        dep = _parse_dt(s.get("d_t"))
        arr = _parse_dt(s.get("a_t"))
        origin = (s.get("or") or "").upper()
        dest = (s.get("de") or "").upper()
        if not (dep and arr and origin and dest):
            continue
        segs.append(Segment(
            origin=origin, destination=dest,
            departure_dt=dep, arrival_dt=arr,
            carrier=(s.get("a_c") or "??").upper(),
            flight_number=(str(s.get("f_n")) if s.get("f_n") else None),
        ))
    return segs


def _item_to_offer(item: Dict[str, Any]) -> Optional[UnifiedOffer]:
    miles = _to_int(item.get("a_p")) or _to_int((item.get("payment") or {}).get("miles"))
    if miles <= 0:
        return None
    segs = _segments(item)
    if not segs:
        return None

    program_code = (item.get("p_c") or item.get("a_c") or "").upper()
    label = PROGRAM_LABEL.get(program_code, program_code or "AwardTool")
    tax_amount = _to_float(item.get("sc"))
    if tax_amount is None:
        tax_amount = _to_float((item.get("payment") or {}).get("tax"))

    return UnifiedOffer(
        source=SourceType.AWARDTOOL,
        airline=item.get("a_n") or segs[0].carrier,
        trip_type=TripType.ONEWAY,
        outbound=Itinerary(segments=segs),
        miles=miles,
        miles_program=label,
        taxes_brl=_taxes_brl(tax_amount, item.get("c")),
        scenario=Scenario.MILES_DIRECT,
        deeplink=item.get("url") or None,
        risk_notes="Award via AwardTool — confirmar assento/taxa na emissão.",
    )


def parse_search_result(payload: Dict[str, Any]) -> List[UnifiedOffer]:
    """Converte o JSON decodificado de /search_result_v2 em UnifiedOffers.
    Absorve itens malformados individualmente (nunca levanta)."""
    offers: List[UnifiedOffer] = []
    for item in (payload.get("result") or []):
        if not isinstance(item, dict):
            continue
        try:
            offer = _item_to_offer(item)
        except Exception:
            offer = None
        if offer is not None:
            offers.append(offer)
    return offers
