from datetime import date, datetime
from enum import Enum
from typing import List, Optional
from pydantic import BaseModel, ConfigDict, Field, model_validator

class TripType(str, Enum):
    ONEWAY = "oneway"
    ROUNDTRIP = "roundtrip"

class CabinClass(str, Enum):
    ECONOMY = "economy"
    BUSINESS = "business"
    FIRST = "first"

class LayoverCategory(str, Enum):
    DIRECT = "direct"
    CONNECTION = "connection"

class SourceType(str, Enum):
    KAYAK = "kayak"
    MOBLIX_LATAM = "moblix_latam"


class SearchRequest(BaseModel):
    """
    Representa a intenção de pesquisa unificada, vinda do Parser.
    Datas adotam date 'naive' para fuso local implícito.
    """
    origin: List[str] = Field(..., description="Lista de aeroportos (IATAs) de origem")
    destination: List[str] = Field(..., description="Lista de aeroportos (IATAs) de destino")
    date_start: date = Field(..., description="Data de ida (exata ou início do range)")
    date_end: date = Field(..., description="Data de ida fim do range flexível (igual a date_start se fixa)")
    return_start: Optional[date] = Field(None, description="Data de volta início do range")
    return_end: Optional[date] = Field(None, description="Data de volta fim do range")
    
    trip_type: TripType = TripType.ONEWAY
    adults: int = Field(1, ge=1)
    cabin: CabinClass = CabinClass.ECONOMY
    baggage_checked: bool = False
    
    direct_only: bool = False
    
    flex_days: Optional[int] = Field(None, description="Dias extras permitidos caso flex")
    currency: str = Field("BRL", description="Moeda padrão da busca")
    
    debug_dump_moblix: bool = False


class Segment(BaseModel):
    """
    Um trecho individual de voo, sem escalas no meio.
    Para datas/horas de voos, utilizamos datetime com info de timezone (aware) preferencialmente,
    ou se assumirá UTC para cálculos quando timezone ausente.
    """
    origin: str
    destination: str
    departure_dt: datetime
    arrival_dt: datetime
    carrier: str = Field(..., description="Código IATA da Cia Aérea")
    flight_number: Optional[str] = None


class Itinerary(BaseModel):
    """
    Agrupa uma sequência de segmentos, representando uma "Perna/Leg" (a Ida inteira ou a Volta inteira).
    """
    segments: List[Segment]
    duration_min: Optional[int] = None
    
    @property
    def stops(self) -> int:
        return max(0, len(self.segments) - 1)


class UnifiedOffer(BaseModel):
    """
    Oferta de voo já padronizada, pronta pra ser analisada pela engine de layover/scoring.
    """
    source: SourceType
    airline: str
    trip_type: TripType
    
    outbound: Itinerary
    inbound: Optional[Itinerary] = None
    
    stops_out: Optional[int] = None
    stops_in: Optional[int] = None
    
    layover_out: Optional[LayoverCategory] = None
    layover_in: Optional[LayoverCategory] = None
    
    # Preços
    price_brl: Optional[float] = None
    price_amount: Optional[float] = Field(None, description="Preço na moeda original")
    price_currency: Optional[str] = Field(None, description="Código da moeda original (BRL, USD, etc.)")
    miles: Optional[int] = None
    taxes_brl: Optional[float] = None
    
    # Detalhes por perna (para Roundtrip)
    price_brl_out: Optional[float] = None
    price_brl_in: Optional[float] = None
    miles_out: Optional[int] = None
    miles_in: Optional[int] = None
    taxes_brl_out: Optional[float] = None
    taxes_brl_in: Optional[float] = None
    
    equivalent_brl: Optional[float] = Field(None, description="Valor BRL convertido no scoring (após aplicar valor do milheiro + taxa)")
    deeplink: Optional[str] = None

    @model_validator(mode='after')
    def validate_price_or_miles(self) -> "UnifiedOffer":
        if self.price_brl is None and self.miles is None:
            raise ValueError("Uma oferta deve ter 'price_brl' (dinheiro) ou 'miles' (para parceiros de milhas)")
        return self

    @model_validator(mode='after')
    def validate_inbound_on_roundtrip(self) -> "UnifiedOffer":
        if self.trip_type == TripType.ROUNDTRIP and self.inbound is None:
            raise ValueError("Viagem de Ida e Volta (ROUNDTRIP) deve possuir o itinerário de volta (inbound)")
        return self

    @model_validator(mode='after')
    def derive_missing_layover_data(self) -> "UnifiedOffer":
        # Derivando layover_category caso não tenha sido passado
        if self.outbound:
            if self.stops_out is None:
                self.stops_out = self.outbound.stops
            if self.layover_out is None:
                self.layover_out = LayoverCategory.CONNECTION if (self.stops_out or 0) > 0 else LayoverCategory.DIRECT
        
        if self.inbound:
            if self.stops_in is None:
                self.stops_in = self.inbound.stops
            if self.layover_in is None:
                self.layover_in = LayoverCategory.CONNECTION if (self.stops_in or 0) > 0 else LayoverCategory.DIRECT
        return self
class PipelineResult(BaseModel):
    """Objeto final consolidado do pipeline para consumo na UI/API"""
    request_id: str
    best_overall: Optional[UnifiedOffer] = None
    best_money: Optional[UnifiedOffer] = None
    best_miles: Optional[UnifiedOffer] = None
    
    ranked_offers: List[UnifiedOffer] = []
    money_offers: List[UnifiedOffer] = []
    miles_offers: List[UnifiedOffer] = []
    
    justification: List[str] = []
    table_rows: List[dict] = []
    trace_path: Optional[str] = None

class ParsedIntent(BaseModel):
    """Representa a intenção extraída de um texto livre"""
    origin_city: Optional[str] = None
    origin_iata: Optional[str] = None
    destination_city: Optional[str] = None
    destination_iata: Optional[str] = None
    trip_type: TripType = TripType.ONEWAY
    date_start: Optional[date] = None
    date_return: Optional[date] = None
    adults: int = 1
    cabin: CabinClass = CabinClass.ECONOMY
    direct_only: bool = False
    confidence: float = 0.0
    notes: Optional[str] = None
