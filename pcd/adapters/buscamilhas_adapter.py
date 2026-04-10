import json
import os
import time
from datetime import datetime
from typing import List

from pcd.core.schema import SearchRequest, UnifiedOffer, SourceType, TripType, Itinerary, Segment, LayoverCategory
from pcd.adapters.base import BaseSearchAdapter
from pcd.core.config import config
from pcd.core.errors import OfflineModeError

from miles_app.buscamilhas_client import search_flights_buscamilhas
from miles_app.buscamilhas_offer_parser import extract_rows_from_buscamilhas

def search_from_fixture(fixture_path: str, trip_type: str) -> List[UnifiedOffer]:
    """Helper para carregar ofertas de um arquivo fixo (mock)"""
    if not os.path.exists(fixture_path):
        return []
    with open(fixture_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return extract_rows_from_buscamilhas(data, "LATAM", trip_type)

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

class BaseBuscaMilhasAdapter(BaseSearchAdapter):
    def __init__(self, companhia: str, source_type: SourceType, airline_code: str, somente_milhas: bool = True, somente_pagante: bool = False):
        self.companhia = companhia
        self.source_type = source_type
        self.airline_code = airline_code
        self.somente_milhas = somente_milhas
        self.somente_pagante = somente_pagante

    def search(self, request: SearchRequest, use_fixtures: bool = False, debug_dump: bool = False) -> List[UnifiedOffer]:
        if use_fixtures:
            # Using our standard test fixture
            fixture_path = os.path.join(os.getcwd(), "pcd", "fixtures", "buscamilhas_roundtrip.json")
            if not os.path.exists(fixture_path):
                return []
            with open(fixture_path, "r", encoding="utf-8") as f:
                raw_data = json.load(f)
        else:
            if config.PCD_OFFLINE:
                raise OfflineModeError("BuscaMilhas")
                
            raw_data = search_flights_buscamilhas(
                companhia=self.companhia,
                origem=request.origin[0],
                destino=request.destination[0],
                data_ida=request.date_start.strftime("%d/%m/%Y"),
                data_volta=request.return_start.strftime("%d/%m/%Y") if request.return_start else None,
                adultos=request.adults,
                classe=request.cabin.value,
                somente_milhas=self.somente_milhas,
                somente_pagante=self.somente_pagante
            )

            # Debug Dump
            if debug_dump:
                try:
                    os.makedirs("debug_dumps", exist_ok=True)
                    ts = int(time.time())
                    if request.return_start:
                        filename = f"debug_dumps/buscamilhas_{self.companhia.lower()}_rt_{request.origin[0]}_{request.destination[0]}_{request.date_start}_{request.return_start}_{ts}.json"
                    else:
                        filename = f"debug_dumps/buscamilhas_{self.companhia.lower()}_ow_{request.origin[0]}_{request.destination[0]}_{request.date_start}_{ts}.json"
                    with open(filename, "w", encoding="utf-8") as f:
                        json.dump(raw_data, f, indent=2, ensure_ascii=False)
                except Exception as e:
                    print(f"Error dumping buscamilhas debug: {e}")
            
        trip_type_str = "RT" if request.return_start else "OW"
        parsed_rows = extract_rows_from_buscamilhas(raw_data, self.companhia, trip_type_str)
        
        def build_itinerary(row):
            if not row: return None
            escalas = int(row.get("Escalas") or 0)
            dur_str = row.get("Duração", "")
            dur_min = 0
            for part in dur_str.split():
                if 'h' in part: dur_min += int(part.replace('h','')) * 60
                elif 'm' in part: dur_min += int(part.replace('m',''))
            
            segs = row.get("segments_raw") or []
            if not segs:
                dep_dt = _parse_time(row.get("Data", ""), row.get("Saída", ""))
                arr_dt = _parse_time(row.get("Data", ""), row.get("Chegada", ""))
                seg = Segment(origin=row.get("Origem", ""), destination=row.get("Destino", ""), departure_dt=dep_dt, arrival_dt=arr_dt, carrier=self.airline_code)
                segs = [seg] * (escalas + 1)
                
            return Itinerary(segments=segs, duration_min=dur_min if dur_min > 0 else None)


        unified_offers = []

        def process_group(rows_group, is_miles):
            idas = [r for r in rows_group if r.get("Trecho") == "IDA"]
            voltas = [r for r in rows_group if r.get("Trecho") == "VOLTA"]

            if trip_type_str == "RT":
                for ida, volta in zip(idas, voltas):
                    outbound = build_itinerary(ida)
                    inbound = build_itinerary(volta)
                    if not outbound or not inbound: continue

                    kwargs = {
                        "source": self.source_type,
                        "airline": self.companhia,
                        "trip_type": TripType.ROUNDTRIP,
                        "outbound": outbound,
                        "inbound": inbound,
                        "layover_out": LayoverCategory.DIRECT,
                        "taxes_brl_out": ida.get("Taxas (R$)"),
                        "taxes_brl_in": volta.get("Taxas (R$)"),
                        "taxes_brl": (ida.get("Taxas (R$)", 0.0) or 0.0) + (volta.get("Taxas (R$)", 0.0) or 0.0),
                    }
                    if is_miles:
                        kwargs.update({
                            "miles_out": ida.get("Milhas"),
                            "miles_in": volta.get("Milhas"),
                            "miles": (ida.get("Milhas", 0) or 0) + (volta.get("Milhas", 0) or 0),
                            "baggage_miles_out": ida.get("Bagagem") if isinstance(ida.get("Bagagem"), (int, float)) else None,
                            "baggage_miles_in": volta.get("Bagagem") if isinstance(volta.get("Bagagem"), (int, float)) else None,
                        })
                    else:
                        price_out = ida.get("Preço", 0.0) or 0.0
                        price_in = volta.get("Preço", 0.0) or 0.0
                        kwargs.update({
                            "price_brl_out": price_out,
                            "price_brl_in": price_in,
                            "price_brl": price_out + price_in,
                            "price_amount": price_out + price_in,
                            "price_currency": "BRL"
                        })
                    unified_offers.append(UnifiedOffer(**kwargs))
            else:
                for ida in idas:
                    outbound = build_itinerary(ida)
                    if not outbound: continue
                    kwargs = {
                        "source": self.source_type,
                        "airline": self.companhia,
                        "trip_type": TripType.ONEWAY,
                        "outbound": outbound,
                        "layover_out": LayoverCategory.DIRECT,
                        "taxes_brl": ida.get("Taxas (R$)"),
                    }
                    if is_miles:
                        kwargs.update({
                            "miles": ida.get("Milhas"),
                            "baggage_miles_out": ida.get("Bagagem") if isinstance(ida.get("Bagagem"), (int, float)) else None
                        })
                    else:
                        price = ida.get("Preço", 0.0) or 0.0
                        kwargs.update({"price_brl": price, "price_amount": price, "price_currency": "BRL"})
                    
                    unified_offers.append(UnifiedOffer(**kwargs))

        rows_miles = [r for r in parsed_rows if r.get("IsMiles")]
        rows_money = [r for r in parsed_rows if not r.get("IsMiles")]
        
        process_group(rows_miles, is_miles=True)
        process_group(rows_money, is_miles=False)

        return unified_offers

class BuscaMilhasLatamAdapter(BaseBuscaMilhasAdapter):
    def __init__(self):
        super().__init__("LATAM", SourceType.BUSCAMILHAS_LATAM, "LA")

class BuscaMilhasGolAdapter(BaseBuscaMilhasAdapter):
    def __init__(self):
        super().__init__("GOL", SourceType.BUSCAMILHAS_GOL, "G3")

class BuscaMilhasAzulAdapter(BaseBuscaMilhasAdapter):
    def __init__(self):
        super().__init__("AZUL", SourceType.BUSCAMILHAS_AZUL, "AD", somente_milhas=False, somente_pagante=False)

