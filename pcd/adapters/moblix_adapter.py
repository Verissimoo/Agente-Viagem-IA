import json
import os
import time
from datetime import datetime
from typing import List

from pcd.core.schema import SearchRequest, UnifiedOffer, SourceType, TripType, Itinerary, Segment, LayoverCategory
from pcd.adapters.base import BaseSearchAdapter
from pcd.core.config import config
from pcd.core.errors import OfflineModeError

from miles_app.moblix_client import search_flights as moblix_search
from miles_app.moblix_offer_parser import extract_latam_miles_rows as moblix_extract

def search_from_fixture(fixture_path: str, trip_type: str) -> List[UnifiedOffer]:
    """Helper para carregar ofertas de um arquivo fixo (mock)"""
    if not os.path.exists(fixture_path):
        return []
    with open(fixture_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return moblix_extract(data, trip_type)

def _parse_time(date_str: str, time_str: str) -> datetime:
    try:
        # data: YYYY-MM-DD
        # time: HH:MM ou HH:MM:SS
        if not date_str:
            return datetime.now()
        dt_base = f"{date_str}T{time_str if time_str else '00:00'}"
        return datetime.fromisoformat(dt_base)
    except Exception:
        return datetime.now()

class MoblixLatamAdapter(BaseSearchAdapter):
    def search(self, request: SearchRequest, use_fixtures: bool = False, debug_dump: bool = False) -> List[UnifiedOffer]:
        if use_fixtures:
            # Using our standard test fixture
            fixture_path = os.path.join(os.getcwd(), "pcd", "fixtures", "moblix_roundtrip.json")
            if not os.path.exists(fixture_path):
                return []
            with open(fixture_path, "r", encoding="utf-8") as f:
                raw_data = json.load(f)
        else:
            if config.PCD_OFFLINE:
                raise OfflineModeError("Moblix")
                
            raw_data = moblix_search(
                origin=request.origin[0],
                destination=request.destination[0],
                departure_date=request.date_start.isoformat(),
                return_date=request.return_start.isoformat() if request.return_start else None,
                adults=request.adults,
                cabin_class=request.cabin.value,
                suppliers=["latam"]
            )

            # Debug Dump
            if debug_dump:
                try:
                    os.makedirs("debug_dumps", exist_ok=True)
                    ts = int(time.time())
                    if request.return_start:
                        filename = f"debug_dumps/moblix_rt_{request.origin[0]}_{request.destination[0]}_{request.date_start}_{request.return_start}_{ts}.json"
                    else:
                        filename = f"debug_dumps/moblix_ow_{request.origin[0]}_{request.destination[0]}_{request.date_start}_{ts}.json"
                    with open(filename, "w", encoding="utf-8") as f:
                        json.dump(raw_data, f, indent=2, ensure_ascii=False)
                except Exception as e:
                    print(f"Error dumping moblix debug: {e}")
            
        trip_type_str = "RT" if request.return_start else "OW"
        parsed_rows = moblix_extract(raw_data, trip_type_str)
        
        # Moblix_offer_parser retorna linhas "achatadas" (ex: 2 linhas p/ 1 grupo ida/volta)
        # Precisamos agrupar pelo GroupId para montar a UnifiedOffer (Outbound + Inbound)
        grouped = {}
        for r in parsed_rows:
            gid = r.get("GroupId")
            if gid not in grouped:
                grouped[gid] = []
            grouped[gid].append(r)
            
        unified_offers = []
        for gid, rows in grouped.items():
            if not rows:
                continue
                
            first = rows[0]
            trip_enum = TripType.ROUNDTRIP if first.get("Tipo") == "RT" else TripType.ONEWAY
            
            # milhas_base com check de sanidade de bagagem já tratado por `_select_base_and_bag_points` no parser local
            # O parser insere "—" se for nulo
            miles = first.get("Milhas")
            taxes = first.get("Taxas (R$)")
            link = first.get("Link")
            
            outbound_row = next((x for x in rows if x.get("Trecho") == "IDA"), first)
            inbound_row = next((x for x in rows if x.get("Trecho") == "VOLTA"), None)
            
            # Helper to build Itinerary from row
            def build_itinerary(row):
                if not row: return None
                escalas = int(row.get("Escalas") or 0)
                dur_str = row.get("Duração", "")
                
                # convert "Xh Ym" to minutes
                dur_min = 0
                for part in dur_str.split():
                    if 'h' in part:
                        dur_min += int(part.replace('h','')) * 60
                    elif 'm' in part:
                        dur_min += int(part.replace('m',''))
                        
                dep_dt = _parse_time(row.get("Data", ""), row.get("Saída", ""))
                arr_dt = _parse_time(row.get("Data", ""), row.get("Chegada", ""))
                
                seg = Segment(
                    origin=row.get("Origem", ""),
                    destination=row.get("Destino", ""),
                    departure_dt=dep_dt,
                    arrival_dt=arr_dt,
                    carrier="AD" # mock is AZUL
                )
                
                return Itinerary(
                    segments=[seg] * (escalas + 1),
                    duration_min=dur_min if dur_min > 0 else None
                )

            outbound = build_itinerary(outbound_row)
            inbound = build_itinerary(inbound_row) if trip_enum == TripType.ROUNDTRIP else None

            if not outbound:
                continue

            uo = UnifiedOffer(
                source=SourceType.MOBLIX_LATAM,
                airline="LATAM",
                trip_type=trip_enum,
                outbound=outbound,
                inbound=inbound,
                miles=miles,
                taxes_brl=taxes,
                # Novos campos
                price_brl_out=first.get("outbound_total"),
                price_brl_in=first.get("inbound_total"),
                miles_out=first.get("miles_out"),
                miles_in=first.get("miles_in"),
                deeplink=link,
                layover_out=LayoverCategory.DIRECT # Will be overridden by classify_offer
            )
            unified_offers.append(uo)

        return unified_offers
