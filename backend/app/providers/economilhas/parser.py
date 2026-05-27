"""
economilhas_offer_parser.py
---------------------------
Normaliza a resposta da API Economilhas para o MESMO formato de rows
que o `buscamilhas_offer_parser` produz, de modo que o pipeline e a UI
existentes não precisem mudar.

Como cada companhia retorna `data` no formato bruto da API nativa do
programa, há um parser por programa:

  SMILES  → _parse_smiles_data
  LATAM   → _parse_latam_data
  AZUL    → _parse_azul_data
  AZUL_INTERLINE / COPA / IBERIA / BRITISH → _parse_generic_data
  Cash (LATAM/AZUL/GOL) → _parse_cash_data

Os parsers de SMILES e LATAM foram implementados a partir do payload
real capturado em debug_dumps/economilhas_sample_*. Para os demais usei
heurísticas defensivas: se a estrutura não bate, devolvem uma "row
informativa" indicando que a busca veio mas o parser ainda não suporta
aquele programa — o vendedor não fica sem feedback.

Cada row tem o formato esperado pelo restante do pipeline:

  {
    "Programa": "SMILES",
    "Companhia": "GOL",
    "Tipo": "OW" | "RT",
    "Trecho": "IDA" | "VOLTA",
    "Origem", "Destino", "Data", "Saída", "Chegada", "Duração",
    "Escalas", "Local Escala",
    "departure_dt", "arrival_dt",
    "outbound_segments_raw" / "inbound_segments_raw",
    "segments_raw",
    "Milhas" (ou "Preço"), "Taxas (R$)", "Bagagem",
    "IsMiles", "TipoMilhas",
    "GroupId", "Link",
  }
"""
from __future__ import annotations

import os
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from backend.app.domain.models import Segment


# ──────────────────────────────────────────────────────────────────
# Mapa Programa → companhia "amigável" + IATA padrão
# ──────────────────────────────────────────────────────────────────
PROGRAM_AIRLINE_INFO: Dict[str, Dict[str, str]] = {
    "SMILES":         {"airline": "GOL",     "iata": "G3", "label": "GOL (Smiles)"},
    "LATAM":          {"airline": "LATAM",   "iata": "LA", "label": "LATAM Pass"},
    "AZUL":           {"airline": "AZUL",    "iata": "AD", "label": "Azul Fidelidade"},
    "AZUL_INTERLINE": {"airline": "AZUL",    "iata": "AD", "label": "Azul Pelo Mundo"},
    "COPA":           {"airline": "COPA",    "iata": "CM", "label": "Copa ConnectMiles"},
    "IBERIA":         {"airline": "IBERIA",  "iata": "IB", "label": "Iberia Plus"},
    "BRITISH":        {"airline": "BRITISH", "iata": "BA", "label": "British Airways Avios"},
    # Cash
    "CASH_LATAM": {"airline": "LATAM", "iata": "LA", "label": "LATAM (dinheiro)"},
    "CASH_AZUL":  {"airline": "AZUL",  "iata": "AD", "label": "Azul (dinheiro)"},
    "CASH_GOL":   {"airline": "GOL",   "iata": "G3", "label": "GOL (dinheiro)"},
}


# ──────────────────────────────────────────────────────────────────
# Helpers de data/hora
# ──────────────────────────────────────────────────────────────────
def _parse_iso(s: str | None) -> Optional[datetime]:
    if not isinstance(s, str) or not s:
        return None
    s2 = s.replace("Z", "+00:00") if s.endswith("Z") else s
    try:
        return datetime.fromisoformat(s2)
    except ValueError:
        for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M",
                    "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M",
                    "%Y-%m-%d"):
            try:
                return datetime.strptime(s, fmt)
            except ValueError:
                continue
    return None


def _fmt_date_iso(dt: Optional[datetime]) -> str:
    return dt.strftime("%Y-%m-%d") if dt else ""


def _fmt_clock(dt: Optional[datetime]) -> str:
    return dt.strftime("%H:%M") if dt else ""


def _dur_str_from_min(total_min: Optional[int]) -> str:
    if total_min is None or total_min <= 0:
        return ""
    h, m = divmod(int(total_min), 60)
    if h == 0:
        return f"{m}m"
    if m == 0:
        return f"{h}h"
    return f"{h}h {m}m"


def _dur_str_from_hm(hours: Any, minutes: Any) -> str:
    try:
        h = int(hours or 0)
        m = int(minutes or 0)
    except Exception:
        return ""
    return _dur_str_from_min(h * 60 + m)


def _safe_float(x) -> Optional[float]:
    if x is None or x == "":
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _safe_int(x) -> Optional[int]:
    if x is None or x == "":
        return None
    try:
        return int(float(x))
    except (TypeError, ValueError):
        return None


# ──────────────────────────────────────────────────────────────────
# Estrutura da row base
# ──────────────────────────────────────────────────────────────────
def _make_base_row(
    program: str,
    airline: str,
    trip_type: str,
    leg_label: str,                      # "IDA" | "VOLTA"
    origin: str,
    destination: str,
    dep_dt: Optional[datetime],
    arr_dt: Optional[datetime],
    duration_str: str,
    stops: int,
    segments: List[Segment],
    flight_no: str = "",
) -> Dict[str, Any]:
    local_escala = (
        ", ".join(s.destination for s in segments[:-1] if s.destination)
        if stops > 0 and len(segments) > 1
        else "Direto"
    )
    data_iso = _fmt_date_iso(dep_dt)
    return {
        "Programa":     program.upper(),
        "Companhia":    airline.upper(),
        "Tipo":         trip_type,
        "Trecho":       leg_label,
        "Origem":       origin or "",
        "Destino":      destination or "",
        "Data":         data_iso,
        "Saída":        _fmt_clock(dep_dt),
        "Chegada":      _fmt_clock(arr_dt),
        "Duração":      duration_str,
        "Escalas":      stops,
        "Local Escala": local_escala,
        "departure_dt": dep_dt,
        "arrival_dt":   arr_dt,
        # Segmentos a nível de row, separados por sentido (mesmo contrato BM).
        "outbound_segments_raw": segments if leg_label == "IDA"   else [],
        "inbound_segments_raw":  segments if leg_label == "VOLTA" else [],
        "segments_raw":          segments,
        "Conexoes":     [],
        "NumeroVoo":    flight_no or "",
        "GroupId":      f"{flight_no}_{data_iso}_{leg_label}".strip("_"),
        "Link":         "",
        "_sort_trecho": 0 if leg_label == "IDA" else 1,
        "_provider":    "economilhas",
    }


# ──────────────────────────────────────────────────────────────────
# SMILES — `data.requestedFlightSegmentList[].flightList[]`
# ──────────────────────────────────────────────────────────────────
def _smiles_segments_from_legs(leg_list: List[Dict[str, Any]], default_carrier: str) -> List[Segment]:
    out: List[Segment] = []
    for leg in (leg_list or []):
        dep = (leg.get("departure") or {})
        arr = (leg.get("arrival") or {})
        dep_dt = _parse_iso(dep.get("date"))
        arr_dt = _parse_iso(arr.get("date"))
        if dep_dt is None or arr_dt is None:
            continue
        carrier_obj = (leg.get("marketingAirline") or leg.get("operationAirline") or {})
        carrier = (carrier_obj.get("code") or default_carrier or "G3").upper()
        out.append(Segment(
            origin=(dep.get("airport") or {}).get("code") or "",
            destination=(arr.get("airport") or {}).get("code") or "",
            departure_dt=dep_dt,
            arrival_dt=arr_dt,
            carrier=carrier,
            flight_number=str(leg.get("flightNumber") or ""),
        ))
    return out


def _smiles_best_fare(fare_list: List[Dict[str, Any]]) -> Tuple[Optional[int], Optional[float], str, Optional[int]]:
    """Retorna (miles, taxa_brl, tipo, miles_bag).

    A SMILES devolve várias tarifas (`SMILES`, `SMILES_CLUB`,
    `SMILES_MONEY`, `MONEY`, ...). Selecionamos a mais barata em
    milhas (excluindo `MONEY`) como base, e a próxima como bag (Smiles
    não traz preço com bagagem em campo próprio — tratamos só como
    referência da próxima opção)."""
    pure_miles: List[Tuple[int, float, str]] = []
    for f in fare_list or []:
        ftype = str(f.get("type") or "").upper()
        if ftype == "MONEY":
            continue
        # Filtra tarifas mistas (SMILES_MONEY*): exigem milhas + dinheiro
        # adicional, não são "pure miles". Mantém apenas SMILES, SMILES_CLUB
        # e similares onde `money == 0`.
        money = _safe_float(f.get("money")) or 0.0
        if money > 0:
            continue
        miles = _safe_int(f.get("miles"))
        if not miles or miles <= 0:
            continue
        tax = _safe_float((f.get("g3") or {}).get("costTax")) or 0.0
        pure_miles.append((miles, tax, ftype))
    if not pure_miles:
        return None, None, "", None
    pure_miles.sort(key=lambda t: (t[0], t[1]))
    base_miles, base_tax, base_type = pure_miles[0]
    bag = None
    for m, _, _ in pure_miles[1:]:
        if m > base_miles:
            bag = m
            break
    return base_miles, base_tax, base_type, bag


def _parse_smiles_data_with_legs(data: Dict[str, Any], trip_type: str) -> List[Dict[str, Any]]:
    """Em OW: usa apenas requestedFlightSegmentList[0] como IDA.
    Em RT: requestedFlightSegmentList[0] é IDA, [1] é VOLTA."""
    rows: List[Dict[str, Any]] = []
    seg_list = data.get("requestedFlightSegmentList") or []
    if not isinstance(seg_list, list) or not seg_list:
        return rows

    info = PROGRAM_AIRLINE_INFO["SMILES"]
    airline_code = info["airline"]
    iata_default = info["iata"]

    for idx, seg in enumerate(seg_list):
        leg_label = "VOLTA" if (idx == 1 and trip_type == "RT") else "IDA"
        flights = seg.get("flightList") or []
        for fl in flights:
            dep_obj = (fl.get("departure") or {})
            arr_obj = (fl.get("arrival") or {})
            dep_dt = _parse_iso(dep_obj.get("date"))
            arr_dt = _parse_iso(arr_obj.get("date"))
            if dep_dt is None or arr_dt is None:
                continue

            origin = (dep_obj.get("airport") or {}).get("code") or ""
            destination = (arr_obj.get("airport") or {}).get("code") or ""

            dur = fl.get("duration") or {}
            dur_str = _dur_str_from_hm(dur.get("hours"), dur.get("minutes"))
            stops = int(fl.get("stops") or 0)
            flight_no = ""
            legs_raw = fl.get("legList") or []
            if legs_raw:
                flight_no = str((legs_raw[0] or {}).get("flightNumber") or "")
            segs = _smiles_segments_from_legs(legs_raw, iata_default)
            if not segs:
                # fallback: 1 segmento usando os campos do flight
                segs = [Segment(
                    origin=origin, destination=destination,
                    departure_dt=dep_dt, arrival_dt=arr_dt,
                    carrier=iata_default, flight_number=flight_no,
                )]

            base = _make_base_row(
                program="SMILES", airline=airline_code, trip_type=trip_type,
                leg_label=leg_label, origin=origin, destination=destination,
                dep_dt=dep_dt, arr_dt=arr_dt,
                duration_str=dur_str, stops=stops, segments=segs,
                flight_no=flight_no,
            )

            miles, tax, fare_type, bag = _smiles_best_fare(fl.get("fareList") or [])
            if miles is None:
                continue
            base.update({
                "IsMiles":    True,
                "Milhas":     miles,
                "Taxas (R$)": tax,
                "Bagagem":    bag if bag is not None else "—",
                "TipoMilhas": fare_type,
                "_sort_compare": miles,
            })
            rows.append(base)
    return rows


# ──────────────────────────────────────────────────────────────────
# LATAM — `data.outbound.content[].summary.brands[]` + `itinerary[]`
# ──────────────────────────────────────────────────────────────────
def _latam_segments_from_itinerary(itin_list: List[Dict[str, Any]], default_carrier: str = "LA") -> List[Segment]:
    out: List[Segment] = []
    for it in (itin_list or []):
        dep_dt = _parse_iso(it.get("departure"))
        arr_dt = _parse_iso(it.get("arrival"))
        if dep_dt is None or arr_dt is None:
            continue
        flight = it.get("flight") or {}
        carrier = str(flight.get("airlineCode") or default_carrier).upper()
        out.append(Segment(
            origin=str(it.get("origin") or "").upper(),
            destination=str(it.get("destination") or "").upper(),
            departure_dt=dep_dt,
            arrival_dt=arr_dt,
            carrier=carrier,
            flight_number=str(flight.get("flightNumber") or ""),
        ))
    return out


def _latam_best_brand(brands: List[Dict[str, Any]]) -> Tuple[Optional[int], Optional[float], str, Optional[int]]:
    """Acha a tarifa em LOYALTY_POINTS mais barata + a próxima
    (geralmente STANDARD com bagagem) como referência de bagagem."""
    miles_brands: List[Tuple[int, float, str]] = []
    for b in brands or []:
        price = b.get("price") or {}
        ccy = str(price.get("currency") or "").upper()
        if ccy != "LOYALTY_POINTS":
            continue
        amt = _safe_int(price.get("amount"))
        if not amt or amt <= 0:
            continue
        tax = _safe_float((b.get("taxes") or {}).get("amount")) or 0.0
        brand_text = str(b.get("brandText") or "").upper()
        miles_brands.append((amt, tax, brand_text))
    if not miles_brands:
        return None, None, "", None
    miles_brands.sort(key=lambda t: (t[0], t[1]))
    base_miles, base_tax, base_brand = miles_brands[0]
    # Bagagem: STANDARD acima de LIGHT (se houver) ou a próxima
    bag = None
    for m, _, brand in miles_brands[1:]:
        if m > base_miles and ("STANDARD" in brand or "PLUS" in brand or bag is None):
            bag = m
            break
    return base_miles, base_tax, base_brand, bag


def _parse_latam_outbound_or_inbound(content_list: List[Dict[str, Any]], leg_label: str, trip_type: str) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    info = PROGRAM_AIRLINE_INFO["LATAM"]
    for c in (content_list or []):
        summary = c.get("summary") or {}
        itin = c.get("itinerary") or []
        if not summary or not itin:
            continue
        origin_obj = summary.get("origin") or {}
        dest_obj = summary.get("destination") or {}
        dep_dt = _parse_iso(origin_obj.get("departure"))
        arr_dt = _parse_iso(dest_obj.get("arrival"))
        if dep_dt is None or arr_dt is None:
            continue
        origin = str(origin_obj.get("iataCode") or "").upper()
        destination = str(dest_obj.get("iataCode") or "").upper()
        stops = int(summary.get("stopOvers") or 0)
        dur_str = _dur_str_from_min(_safe_int(summary.get("duration")))
        flight_no = str(summary.get("flightCode") or "")
        segs = _latam_segments_from_itinerary(itin, info["iata"])
        if not segs:
            segs = [Segment(
                origin=origin, destination=destination,
                departure_dt=dep_dt, arrival_dt=arr_dt,
                carrier=info["iata"], flight_number=flight_no,
            )]
        base = _make_base_row(
            program="LATAM", airline=info["airline"], trip_type=trip_type,
            leg_label=leg_label, origin=origin, destination=destination,
            dep_dt=dep_dt, arr_dt=arr_dt,
            duration_str=dur_str, stops=stops, segments=segs,
            flight_no=flight_no,
        )

        miles, tax, brand_text, bag = _latam_best_brand(summary.get("brands") or [])
        if miles is None:
            continue
        base.update({
            "IsMiles":    True,
            "Milhas":     miles,
            "Taxas (R$)": tax,
            "Bagagem":    bag if bag is not None else "—",
            "TipoMilhas": brand_text,
            "_sort_compare": miles,
        })
        rows.append(base)
    return rows


def _parse_latam_data(data: Dict[str, Any], trip_type: str) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    out_obj = data.get("outbound") or {}
    in_obj = data.get("inbound") or {}
    rows += _parse_latam_outbound_or_inbound(out_obj.get("content") or [], "IDA", trip_type)
    if trip_type == "RT":
        rows += _parse_latam_outbound_or_inbound(in_obj.get("content") or [], "VOLTA", trip_type)
    return rows


# ──────────────────────────────────────────────────────────────────
# AZUL — `data.data.trips[].journeys[].fares[]`
# ──────────────────────────────────────────────────────────────────
def _azul_segments_from_legs(legs: List[Dict[str, Any]], default_carrier: str = "AD") -> List[Segment]:
    out: List[Segment] = []
    for lg in legs or []:
        dep_dt = _parse_iso(lg.get("std") or lg.get("departure"))
        arr_dt = _parse_iso(lg.get("sta") or lg.get("arrival"))
        if dep_dt is None or arr_dt is None:
            continue
        out.append(Segment(
            origin=str(lg.get("departureStation") or lg.get("origin") or "").upper(),
            destination=str(lg.get("arrivalStation") or lg.get("destination") or "").upper(),
            departure_dt=dep_dt, arrival_dt=arr_dt,
            carrier=str(lg.get("carrierCode") or default_carrier).upper(),
            flight_number=str(lg.get("flightNumber") or lg.get("flightDesignator") or ""),
        ))
    return out


def _parse_azul_data(data: Dict[str, Any], trip_type: str) -> List[Dict[str, Any]]:
    """Estrutura observada: data.data.trips[].journeys[]. Quando vazio
    (ex.: rota/data sem disponibilidade) devolve []. Quando há voos,
    espera-se journeys[].fares[].fareInfo / journeys[].legs[]."""
    rows: List[Dict[str, Any]] = []
    info = PROGRAM_AIRLINE_INFO["AZUL"]

    azul_payload = data.get("data") or {}
    if isinstance(azul_payload, dict) and "data" in azul_payload:
        azul_inner = azul_payload.get("data") or {}
    else:
        azul_inner = azul_payload

    trips = azul_inner.get("trips") or []
    if not isinstance(trips, list):
        return rows

    for trip_idx, trip in enumerate(trips):
        if not isinstance(trip, dict):
            continue
        leg_label = "VOLTA" if (trip_idx == 1 and trip_type == "RT") else "IDA"
        journeys = trip.get("journeys") or []
        if not isinstance(journeys, list):
            continue
        for j in journeys:
            if not isinstance(j, dict):
                continue
            legs = j.get("legs") or j.get("segments") or []
            segs = _azul_segments_from_legs(legs, info["iata"])
            dep_dt = segs[0].departure_dt if segs else _parse_iso(j.get("std") or j.get("departure"))
            arr_dt = segs[-1].arrival_dt if segs else _parse_iso(j.get("sta") or j.get("arrival"))
            if dep_dt is None or arr_dt is None:
                continue

            stops = max(0, len(segs) - 1)
            dur_min = _safe_int(j.get("duration") or j.get("totalDuration"))
            if dur_min is None and dep_dt and arr_dt:
                dur_min = max(0, int((arr_dt - dep_dt).total_seconds() / 60))
            dur_str = _dur_str_from_min(dur_min)
            origin = (segs[0].origin if segs else trip.get("departureStation")) or ""
            destination = (segs[-1].destination if segs else trip.get("arrivalStation")) or ""
            flight_no = (segs[0].flight_number if segs else "") or ""

            fares = j.get("fares") or []
            best_miles: Optional[int] = None
            best_tax: Optional[float] = None
            fare_type = ""
            for f in fares:
                if not isinstance(f, dict):
                    continue
                fare_info = f.get("fareInfo") or f
                miles = _safe_int(
                    fare_info.get("miles")
                    or fare_info.get("points")
                    or fare_info.get("loyaltyPoints")
                    or (fare_info.get("price") or {}).get("amount")
                    if isinstance(fare_info.get("price"), dict)
                    else fare_info.get("amount")
                )
                if not miles or miles <= 0:
                    continue
                tax = _safe_float(
                    fare_info.get("taxes")
                    or (fare_info.get("totalAmount") or {}).get("taxes")
                    or 0.0
                ) or 0.0
                fname = str(fare_info.get("fareName") or fare_info.get("class") or "").upper()
                if best_miles is None or miles < best_miles:
                    best_miles, best_tax, fare_type = miles, tax, fname
            if best_miles is None:
                continue

            base = _make_base_row(
                program="AZUL", airline=info["airline"], trip_type=trip_type,
                leg_label=leg_label, origin=origin, destination=destination,
                dep_dt=dep_dt, arr_dt=arr_dt,
                duration_str=dur_str, stops=stops, segments=segs,
                flight_no=flight_no,
            )
            base.update({
                "IsMiles":    True,
                "Milhas":     best_miles,
                "Taxas (R$)": best_tax or 0.0,
                "Bagagem":    "—",
                "TipoMilhas": fare_type,
                "_sort_compare": best_miles,
            })
            rows.append(base)
    return rows


# ──────────────────────────────────────────────────────────────────
# Genérico — heurística para AZUL_INTERLINE/COPA/IBERIA/BRITISH
# ──────────────────────────────────────────────────────────────────
def _walk_first(value: Any, keys: List[str]) -> Any:
    """Tenta alcançar uma chave em qualquer profundidade (DFS leve)."""
    if isinstance(value, dict):
        for k in keys:
            if k in value:
                return value[k]
        for v in value.values():
            r = _walk_first(v, keys)
            if r is not None:
                return r
    elif isinstance(value, list):
        for v in value:
            r = _walk_first(v, keys)
            if r is not None:
                return r
    return None


def _parse_generic_data(data: Dict[str, Any], program: str, trip_type: str) -> List[Dict[str, Any]]:
    """Parser fallback. Tenta extrair (miles, taxes, dep, arr, segments)
    em qualquer profundidade. Se nada vier, devolve uma row informativa
    para a UI saber que houve sucesso na chamada — só falta parser fino."""
    info = PROGRAM_AIRLINE_INFO.get(program, {"airline": program, "iata": "XX", "label": program})

    miles = _safe_int(_walk_first(data, ["miles", "points", "loyaltyPoints", "totalMiles"]))
    taxes = _safe_float(_walk_first(data, ["taxes", "taxesAmount", "totalTaxes", "fees"])) or 0.0

    dep_str = _walk_first(data, ["departure", "departureDate", "std", "outboundDeparture"])
    arr_str = _walk_first(data, ["arrival", "arrivalDate", "sta", "outboundArrival"])
    dep_dt = _parse_iso(dep_str) if isinstance(dep_str, str) else None
    arr_dt = _parse_iso(arr_str) if isinstance(arr_str, str) else None

    origin = str(_walk_first(data, ["origin", "departureAirport", "departureStation"]) or "")
    destination = str(_walk_first(data, ["destination", "arrivalAirport", "arrivalStation"]) or "")

    if miles is None or dep_dt is None or arr_dt is None:
        # Row informativa — sucesso na API mas parser ainda incompleto
        return [{
            "Programa":   program.upper(),
            "Companhia":  info["airline"].upper(),
            "Tipo":       trip_type,
            "Trecho":     "IDA",
            "Origem":     origin,
            "Destino":    destination,
            "Data":       "",
            "Saída":      "",
            "Chegada":    "",
            "Duração":    "",
            "Escalas":    0,
            "Local Escala": "—",
            "departure_dt": None,
            "arrival_dt":   None,
            "outbound_segments_raw": [],
            "inbound_segments_raw":  [],
            "segments_raw": [],
            "Conexoes":   [],
            "NumeroVoo":  "",
            "GroupId":    f"{program}_unparsed",
            "Link":       "",
            "IsMiles":    True,
            "Milhas":     0,
            "Taxas (R$)": 0.0,
            "Bagagem":    "—",
            "TipoMilhas": "PARSER_PENDENTE",
            "_sort_compare": 10**18,
            "_sort_trecho": 0,
            "_provider":  "economilhas",
            "_unparsed":  True,
            "_parser_note": (
                f"Resposta {program} recebida mas parser específico ainda "
                f"não suporta este formato. Ajustar em fase 2."
            ),
        }]

    segs = [Segment(
        origin=origin or "—", destination=destination or "—",
        departure_dt=dep_dt, arrival_dt=arr_dt,
        carrier=info["iata"], flight_number="",
    )]
    dur_min = max(0, int((arr_dt - dep_dt).total_seconds() / 60))
    base = _make_base_row(
        program=program, airline=info["airline"], trip_type=trip_type,
        leg_label="IDA", origin=origin, destination=destination,
        dep_dt=dep_dt, arr_dt=arr_dt,
        duration_str=_dur_str_from_min(dur_min),
        stops=0, segments=segs, flight_no="",
    )
    base.update({
        "IsMiles":    True,
        "Milhas":     int(miles),
        "Taxas (R$)": taxes,
        "Bagagem":    "—",
        "TipoMilhas": "GENERIC",
        "_sort_compare": int(miles),
    })
    return [base]


# ──────────────────────────────────────────────────────────────────
# CASH — formato {outbound: {...}, inbound: {...}}
# ──────────────────────────────────────────────────────────────────
def _cash_segments(seg_list: List[Dict[str, Any]], default_carrier: str) -> List[Segment]:
    out: List[Segment] = []
    for s in (seg_list or []):
        dep_dt = _parse_iso(s.get("departure") or s.get("departureDate") or s.get("std"))
        arr_dt = _parse_iso(s.get("arrival") or s.get("arrivalDate") or s.get("sta"))
        if dep_dt is None or arr_dt is None:
            continue
        out.append(Segment(
            origin=str(s.get("origin") or s.get("departureStation") or "").upper(),
            destination=str(s.get("destination") or s.get("arrivalStation") or "").upper(),
            departure_dt=dep_dt, arrival_dt=arr_dt,
            carrier=str(s.get("carrier") or s.get("carrierCode") or default_carrier).upper(),
            flight_number=str(s.get("flightNumber") or ""),
        ))
    return out


def _parse_cash_leg(
    leg_data: Dict[str, Any],
    program: str,
    trip_type: str,
    leg_label: str,
) -> Optional[Dict[str, Any]]:
    if not isinstance(leg_data, dict):
        return None
    info = PROGRAM_AIRLINE_INFO.get(f"CASH_{program.upper()}") or PROGRAM_AIRLINE_INFO.get(program) or {
        "airline": program, "iata": "XX",
    }
    price = _safe_float(leg_data.get("price")) or _safe_float(
        (leg_data.get("totalAmount") or {}).get("amount") if isinstance(leg_data.get("totalAmount"), dict) else None
    )
    if price is None:
        return None
    taxes = _safe_float(leg_data.get("taxes")) or _safe_float(
        (leg_data.get("totalAmount") or {}).get("taxes") if isinstance(leg_data.get("totalAmount"), dict) else None
    ) or 0.0
    segs = _cash_segments(leg_data.get("segments") or [], info["iata"])
    if not segs:
        return None
    dep_dt = segs[0].departure_dt
    arr_dt = segs[-1].arrival_dt
    stops = max(0, len(segs) - 1)
    dur_min = _safe_int(leg_data.get("duration"))
    if dur_min is None:
        dur_min = max(0, int((arr_dt - dep_dt).total_seconds() / 60))
    base = _make_base_row(
        program=f"CASH_{program.upper()}", airline=info["airline"], trip_type=trip_type,
        leg_label=leg_label,
        origin=segs[0].origin, destination=segs[-1].destination,
        dep_dt=dep_dt, arr_dt=arr_dt,
        duration_str=_dur_str_from_min(dur_min),
        stops=stops, segments=segs,
        flight_no=segs[0].flight_number or "",
    )
    base.update({
        "IsMiles":    False,
        "Preço":      float(price),
        "Taxas (R$)": float(taxes),
        "Bagagem":    "—",
        "TipoMilhas": "",
        "_sort_compare": float(price),
    })
    return base


def _parse_cash_data(data: Dict[str, Any], program: str, trip_type: str) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    out_row = _parse_cash_leg(data.get("outbound") or {}, program, trip_type, "IDA")
    if out_row:
        rows.append(out_row)
    if trip_type == "RT":
        in_row = _parse_cash_leg(data.get("inbound") or {}, program, trip_type, "VOLTA")
        if in_row:
            rows.append(in_row)
    return rows


# ──────────────────────────────────────────────────────────────────
# Despachante principal
# ──────────────────────────────────────────────────────────────────
def _maybe_debug_dump(airline: str, data: Any, debug: bool) -> None:
    """Em modo debug, escreve o data bruto recebido em debug_dumps/.
    Útil para ajustar parsers de programas ainda incompletos."""
    if not debug:
        return
    try:
        import json as _json
        os.makedirs("debug_dumps", exist_ok=True)
        ts = int(__import__("time").time())
        fname = f"debug_dumps/economilhas_raw_{airline.lower()}_{ts}.json"
        with open(fname, "w", encoding="utf-8") as f:
            _json.dump(data, f, ensure_ascii=False, indent=2, default=str)
    except Exception:
        # Log de debug nunca pode quebrar o fluxo principal.
        pass


def extract_rows_from_economilhas(
    response: Dict[str, Any],
    trip_type: str,                  # "OW" | "RT"
    debug: bool = False,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Itera `response.results[]` e despacha cada `data` para o parser
    específico.

    Retorna (rows, partial_failures):
      - rows: lista no formato do `buscamilhas_offer_parser`.
      - partial_failures: lista `[{airline, message}]` para companhias
        que vieram com `success=false` ou que o parser não conseguiu ler.
    """
    rows: List[Dict[str, Any]] = []
    failures: List[Dict[str, Any]] = []

    results = response.get("results") if isinstance(response, dict) else None
    if not isinstance(results, list):
        return rows, failures

    for item in results:
        if not isinstance(item, dict):
            continue
        airline = str(item.get("airline") or "").upper()
        success = bool(item.get("success"))
        if not success:
            err = item.get("error") or {}
            failures.append({
                "airline": airline,
                "message": (err.get("message") if isinstance(err, dict) else str(err)) or "Falha sem detalhes",
                "providerStatusCode": (err.get("providerStatusCode") if isinstance(err, dict) else None),
            })
            continue

        data = item.get("data") or {}
        _maybe_debug_dump(airline, data, debug)

        try:
            if airline == "SMILES":
                # Em RT, a Smiles devolve `requestedFlightSegmentList` com
                # 2 entradas (ida e volta na mesma ordem do request). O
                # parser deduz o sentido pelo índice do segmento.
                got = _parse_smiles_data_with_legs(data, trip_type)
                rows += got
            elif airline == "LATAM":
                rows += _parse_latam_data(data, trip_type)
            elif airline == "AZUL":
                rows += _parse_azul_data(data, trip_type)
            elif airline in ("AZUL_INTERLINE", "COPA", "IBERIA", "BRITISH"):
                rows += _parse_generic_data(data, airline, trip_type)
            elif airline in ("LATAM_CASH", "AZUL_CASH", "GOL_CASH"):
                # Caso o cliente envie variações com sufixo CASH:
                rows += _parse_cash_data(data, airline.replace("_CASH", ""), trip_type)
            else:
                # Programa não cadastrado ainda — cai no genérico
                rows += _parse_generic_data(data, airline or "UNKNOWN", trip_type)
        except Exception as e:
            failures.append({
                "airline": airline,
                "message": f"parser falhou: {type(e).__name__}: {str(e)[:200]}",
                "providerStatusCode": None,
            })

    rows.sort(key=lambda r: (r.get("_sort_compare") or 0, r.get("_sort_trecho") or 0))
    for r in rows:
        r.pop("_sort_compare", None)
        r.pop("_sort_trecho", None)

    return rows, failures


# ──────────────────────────────────────────────────────────────────
# Dispatcher de cash (api separada, priceType=CASH)
# ──────────────────────────────────────────────────────────────────
def extract_cash_rows_from_economilhas(
    response: Dict[str, Any],
    trip_type: str,
    debug: bool = False,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Variante para `priceType=CASH`. A resposta vem com a mesma forma
    geral mas o `data` traz `{outbound, inbound}` com `price`/`segments`."""
    rows: List[Dict[str, Any]] = []
    failures: List[Dict[str, Any]] = []

    results = response.get("results") if isinstance(response, dict) else None
    if not isinstance(results, list):
        return rows, failures

    for item in results:
        if not isinstance(item, dict):
            continue
        airline = str(item.get("airline") or "").upper()
        if not bool(item.get("success")):
            err = item.get("error") or {}
            failures.append({
                "airline": airline,
                "message": (err.get("message") if isinstance(err, dict) else str(err)) or "Falha sem detalhes",
                "providerStatusCode": (err.get("providerStatusCode") if isinstance(err, dict) else None),
            })
            continue
        data = item.get("data") or {}
        _maybe_debug_dump(f"{airline}_CASH", data, debug)
        try:
            rows += _parse_cash_data(data, airline, trip_type)
        except Exception as e:
            failures.append({
                "airline": airline,
                "message": f"parser cash falhou: {type(e).__name__}: {str(e)[:200]}",
                "providerStatusCode": None,
            })

    rows.sort(key=lambda r: (r.get("_sort_compare") or 0, r.get("_sort_trecho") or 0))
    for r in rows:
        r.pop("_sort_compare", None)
        r.pop("_sort_trecho", None)
    return rows, failures
