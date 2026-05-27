"""Parser pro payload do scraper.py — converte dicts crus do DOM em UnifiedOffer.

Recebe o output de `scraper.search_kayak_scrape` (lista de cards normalizados)
e devolve `List[UnifiedOffer]` prontas pro pipeline.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import List, Optional

from backend.app.domain.models import (
    Itinerary,
    LayoverCategory,
    Segment,
    SourceType,
    TripType,
    UnifiedOffer,
)

# Códigos IATA dos nomes mais comuns — fallback quando o card só traz o nome
# por extenso (sem logo com alt). Mantém só as cias que costumam aparecer em
# rotas BR; pra qualquer outra deixamos o nome cru e o pipeline lida.
_AIRLINE_NAME_TO_IATA = {
    "LATAM": "LA",
    "LATAM AIRLINES": "LA",
    "GOL": "G3",
    "AZUL": "AD",
    "TAP": "TP",
    "TAP AIR PORTUGAL": "TP",
    "IBERIA": "IB",
    "LUFTHANSA": "LH",
    "AIR FRANCE": "AF",
    "KLM": "KL",
    "AMERICAN": "AA",
    "AMERICAN AIRLINES": "AA",
    "DELTA": "DL",
    "UNITED": "UA",
    "UNITED AIRLINES": "UA",
    "AVIANCA": "AV",
    "COPA": "CM",
    "COPA AIRLINES": "CM",
    "BRITISH": "BA",
    "BRITISH AIRWAYS": "BA",
    "EMIRATES": "EK",
    "QATAR": "QR",
    "QATAR AIRWAYS": "QR",
    "TURKISH": "TK",
    "TURKISH AIRLINES": "TK",
}


def _normalize_carrier(airline_raw: Optional[str]) -> tuple[str, str]:
    """Devolve (airline_label, iata_code). Se conhecida → IATA mapeado;
    senão usa primeiras 3 letras como fallback bruto."""
    if not airline_raw:
        return ("Desconhecida", "UNK")
    cleaned = airline_raw.strip()
    upper = cleaned.upper()
    iata = _AIRLINE_NAME_TO_IATA.get(upper)
    if iata is None:
        # Tenta match por prefixo (ex: "LATAM Airlines Brasil" → LATAM)
        for name, code in _AIRLINE_NAME_TO_IATA.items():
            if upper.startswith(name):
                iata = code
                break
    if iata is None:
        iata = upper[:3]
    return (cleaned, iata)


def _parse_time(date_iso: str, time_str: Optional[str]) -> Optional[datetime]:
    """date_iso = 'YYYY-MM-DD', time_str = '14:25' ou '14h25'. Devolve datetime."""
    if not time_str:
        return None
    t = time_str.replace("h", ":").strip()
    if ":" not in t:
        return None
    try:
        hh, mm = t.split(":")
        base = datetime.fromisoformat(date_iso)
        return base.replace(hour=int(hh), minute=int(mm))
    except (ValueError, AttributeError):
        return None


def extract_offers(raw: dict, request_origin: str, request_dest: str) -> List[UnifiedOffer]:
    """Converte o payload do scraper em UnifiedOffer list.

    `raw` segue o shape devolvido por `scraper.fetch_via_playwright` —
    com `offers: list[dict]`, `trip_type`, `depart_date`, `return_date`.
    """
    if not raw or not isinstance(raw, dict):
        return []
    offers_raw = raw.get("offers") or []
    if not offers_raw:
        return []

    trip_type = TripType.ROUNDTRIP if raw.get("trip_type") == "roundtrip" else TripType.ONEWAY
    depart_date = raw.get("depart_date") or ""
    return_date = raw.get("return_date") or None

    out: List[UnifiedOffer] = []
    seen_signatures: set[tuple] = set()

    for card in offers_raw:
        price_brl = card.get("price_brl")
        if not price_brl or price_brl <= 0:
            continue

        airline_label, carrier_iata = _normalize_carrier(card.get("airline"))
        # Endurece: só aceita cias MAPEADAS (IATA real). Sem isso, scraper às
        # vezes pega "Existem outras..." → "EX" como cia fake. Se não é cia
        # conhecida, descarta — melhor menos resultado do que dado errado.
        if carrier_iata == "UNK" or carrier_iata not in set(_AIRLINE_NAME_TO_IATA.values()):
            continue

        # Dedup por (cia + preço + depart_time + stops) — evita duplicatas
        # do scrape quando o card aparece em múltiplos containers do DOM.
        sig = (
            carrier_iata,
            round(price_brl, 2),
            card.get("depart_time") or "",
            card.get("stops") or 0,
        )
        if sig in seen_signatures:
            continue
        seen_signatures.add(sig)

        # Origem/destino: prioriza IATAs detectados no card; senão usa o request.
        orig_iata = (card.get("origin_iata") or request_origin).upper()
        dest_iata = (card.get("dest_iata") or request_dest).upper()
        # Sanity: usa os do request se os detectados forem suspeitos (ex: 3 letras
        # genéricas como "BRL", "USD", "BRS"). Se forem diferentes dos esperados
        # E não pareçam IATA real, override.
        if len(orig_iata) != 3 or not orig_iata.isalpha():
            orig_iata = request_origin.upper()
        if len(dest_iata) != 3 or not dest_iata.isalpha():
            dest_iata = request_dest.upper()

        # Horários — se não veio depart_time, marcamos meia-noite como
        # placeholder pra Segment não rejeitar (datetime obrigatório).
        dep_dt = _parse_time(depart_date, card.get("depart_time"))
        arr_dt = _parse_time(depart_date, card.get("arrival_time"))
        if dep_dt is None:
            try:
                dep_dt = datetime.fromisoformat(depart_date)
            except (ValueError, TypeError):
                dep_dt = datetime.now()
        if arr_dt is None and card.get("duration_min"):
            arr_dt = dep_dt + timedelta(minutes=int(card["duration_min"]))
        elif arr_dt is None:
            arr_dt = dep_dt + timedelta(hours=2)
        # Se arr_dt vem antes do dep_dt e há duration_min, ajusta pro dia seguinte
        # (vôos noturnos cruzando meia-noite).
        if arr_dt < dep_dt:
            arr_dt = arr_dt + timedelta(days=1)

        stops = int(card.get("stops") or 0)

        out_seg = Segment(
            origin=orig_iata,
            destination=dest_iata,
            departure_dt=dep_dt,
            arrival_dt=arr_dt,
            carrier=carrier_iata,
        )
        # Stops simulado — segments duplicados pra acertar contagem de paradas.
        # Kayak não nos dá os segmentos intermediários no card; é uma aproximação.
        out_segments = [out_seg] * (stops + 1)
        outbound = Itinerary(segments=out_segments, duration_min=card.get("duration_min"))

        inbound = None
        if trip_type == TripType.ROUNDTRIP and return_date:
            # Para roundtrip o scrape de Kayak.com.br devolve voos combinados
            # (ida+volta no mesmo card). Sem horário discreto da volta vamos
            # criar um inbound placeholder com a data da volta.
            try:
                in_base = datetime.fromisoformat(return_date)
            except (ValueError, TypeError):
                in_base = dep_dt + timedelta(days=7)
            in_seg = Segment(
                origin=dest_iata,
                destination=orig_iata,
                departure_dt=in_base,
                arrival_dt=in_base + timedelta(hours=2),
                carrier=carrier_iata,
            )
            in_segments = [in_seg] * (stops + 1)
            inbound = Itinerary(segments=in_segments)

        uo = UnifiedOffer(
            source=SourceType.KAYAK,
            airline=airline_label,
            trip_type=trip_type,
            outbound=outbound,
            inbound=inbound,
            price_brl=float(price_brl),
            price_amount=float(price_brl),
            price_currency="BRL",
            equivalent_brl=float(price_brl),
            deeplink=card.get("deeplink") or "",
            layover_out=LayoverCategory.DIRECT if stops == 0 else LayoverCategory.CONNECTION,
            captured_at=datetime.utcnow(),
        )
        out.append(uo)

    return out
