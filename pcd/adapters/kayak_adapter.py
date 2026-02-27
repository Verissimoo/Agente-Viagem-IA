import json
import os
import time
from datetime import datetime
from typing import List

from pcd.core.schema import (
    SearchRequest, UnifiedOffer, TripType, SourceType, 
    Itinerary, Segment, LayoverCategory
)
from pcd.adapters.base import BaseSearchAdapter
from pcd.core.config import config
from pcd.core.errors import OfflineModeError

from kayak_client import search_flights as kayak_search
from offer_parser import extract_offers as kayak_extract
import fx_rates

def search_from_fixture(fixture_path: str) -> List[UnifiedOffer]:
    """Helper para carregar ofertas de um arquivo fixo (mock)"""
    if not os.path.exists(fixture_path):
        return []
    with open(fixture_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return kayak_extract(data)

def _parse_datetime(dt_str: str) -> datetime:
    try:
        # Kayak returns like "2023-10-10T15:00:00"
        return datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
    except Exception:
        return datetime.now()

class KayakAdapter(BaseSearchAdapter):
    def search(self, request: SearchRequest, use_fixtures: bool = False, debug_dump: bool = False) -> List[UnifiedOffer]:
        if use_fixtures:
            fixture_path = os.path.join(os.getcwd(), "pcd", "fixtures", "kayak_oneway.json")
            if not os.path.exists(fixture_path):
                return []
            with open(fixture_path, "r", encoding="utf-8") as f:
                raw_data = json.load(f)
        else:
            if config.PCD_OFFLINE:
                raise OfflineModeError("Kayak")
                
            raw_data = kayak_search(
                origin=request.origin[0],
                destination=request.destination[0],
                departure_date=request.date_start.isoformat(),
                return_date=request.return_start.isoformat() if request.return_start else None,
                adults=request.adults,
                cabin=request.cabin.value[0], # e.g. "economy" -> "e"
            )

            if debug_dump:
                try:
                    os.makedirs("debug_dumps", exist_ok=True)
                    ts = int(time.time())
                    if request.return_start:
                        filename = f"debug_dumps/kayak_rt_{request.origin[0]}_{request.destination[0]}_{request.date_start}_{request.return_start}_{ts}.json"
                    else:
                        filename = f"debug_dumps/kayak_ow_{request.origin[0]}_{request.destination[0]}_{request.date_start}_{ts}.json"
                    with open(filename, "w", encoding="utf-8") as f:
                        json.dump(raw_data, f, indent=2, ensure_ascii=False)
                    
                    # Mapping debug
                    map_file = f"debug_dumps/kayak_price_fields_{ts}.json"
                    price_debug = []
                    results = (raw_data.get("data") or {}).get("results") or []
                    for item in results[:5]: # top 5
                        if item.get("type") == "core":
                            pd = {
                                "id": (item.get("legs") or [{}])[0].get("id"),
                                "bookingOptions": []
                            }
                            for bo in (item.get("bookingOptions") or []):
                                pd["bookingOptions"].append({
                                    "currency": bo.get("currency"),
                                    "displayPrice": bo.get("displayPrice"),
                                    "fees": bo.get("fees")
                                })
                            price_debug.append(pd)
                    
                    with open(map_file, "w", encoding="utf-8") as f:
                        json.dump(price_debug, f, indent=2, ensure_ascii=False)
                except Exception as e:
                    print(f"Error dumping debug: {e}")
                
        parsed_offers = kayak_extract(raw_data)
        unified_offers = []

        for p in parsed_offers:
            airline = p.get("airlines", [""])[0] if isinstance(p.get("airlines"), list) else ""
            trip_type = TripType.ROUNDTRIP if p.get("trip_type") == "roundtrip" else TripType.ONEWAY
            
            # Montar outbound
            stops_out = p.get("out_stops") if trip_type == TripType.ROUNDTRIP else p.get("stops")
            dur_out = p.get("out_duration_min") if trip_type == TripType.ROUNDTRIP else p.get("duration_min")
            dep_time_out = p.get("out_departure_time") if trip_type == TripType.ROUNDTRIP else p.get("departure_time")
            arr_time_out = p.get("out_arrival_time") if trip_type == TripType.ROUNDTRIP else p.get("arrival_time")
            
            out_seg = Segment(
                origin=request.origin[0],
                destination=request.destination[0],
                departure_dt=_parse_datetime(dep_time_out or ""),
                arrival_dt=_parse_datetime(arr_time_out or ""),
                carrier=airline[:3] if airline else "UNK"
            )
            out_segments = [out_seg] * (int(stops_out or 0) + 1)
            outbound = Itinerary(segments=out_segments, duration_min=dur_out)
            
            inbound = None
            if trip_type == TripType.ROUNDTRIP:
                stops_in = p.get("in_stops")
                dur_in = p.get("in_duration_min")
                in_seg = Segment(
                    origin=request.destination[0],
                    destination=request.origin[0],
                    departure_dt=_parse_datetime(p.get("in_departure_time") or ""),
                    arrival_dt=_parse_datetime(p.get("in_arrival_time") or ""),
                    carrier=airline[:3] if airline else "UNK"
                )
                in_segments = [in_seg] * (int(stops_in or 0) + 1)
                inbound = Itinerary(segments=in_segments, duration_min=dur_in)
            
            # Preço e Conversão
            orig_price = p.get("price")
            orig_ccy = p.get("currency") or "BRL"
            
            price_brl = None
            if orig_ccy == "BRL":
                price_brl = orig_price
            else:
                try:
                    price_brl = fx_rates.convert(orig_price, orig_ccy, "BRL")
                except Exception:
                    # Se falhar conversão, mantém None e scoring usará equivalent_brl como last resort? 
                    # Na verdade UnifiedOffer obriga price_brl ou miles. 
                    # Vamos colocar o orig_price como equivalent_brl se falhar.
                    pass

            uo = UnifiedOffer(
                source=SourceType.KAYAK,
                airline=airline,
                trip_type=trip_type,
                outbound=outbound,
                inbound=inbound,
                price_brl=price_brl,
                price_amount=orig_price,
                price_currency=orig_ccy,
                equivalent_brl=price_brl if price_brl else orig_price,
                deeplink=p.get("shareableUrl") or "",
                layover_out=LayoverCategory.DIRECT
            )
            unified_offers.append(uo)
            
        return unified_offers
