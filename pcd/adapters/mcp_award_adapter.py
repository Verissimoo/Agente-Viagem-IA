"""
pcd/adapters/mcp_award_adapter.py
==================================
Adapter para o Award Travel Finder via REST API.

Integra o mcp_offer_parser ao sistema de adapters do PCD,
retornando UnifiedOffer com source=SourceType.MCP_AWARD.

Fixture padrao (use_fixtures=True):
  Carrega debug_dumps/mcp_all_airlines_GRU_JFK_sample.json
  (gerado por scripts/fetch_mcp_sample.py)

Busca real (use_fixtures=False):
  Chama call_rest_availability() para cada airline suportada
  usando os parametros reais do SearchRequest (origem, destino, data).
  Nao ha valores hardcoded de rota.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import List

# Garante que a raiz do projeto está no sys.path
_ROOT = Path(__file__).parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from pcd.adapters.base import BaseSearchAdapter
from pcd.core.schema import (
    Itinerary,
    LayoverCategory,
    SearchRequest,
    Segment,
    SourceType,
    TripType,
    UnifiedOffer,
)

# Parser de ofertas brutas do MCP
from mcp_offer_parser import extract_mcp_offers

# Cliente REST
from mcp_client import call_rest_availability

# Fixture padrão para uso em testes / use_fixtures=True
_DEFAULT_FIXTURE = _ROOT / "debug_dumps" / "mcp_all_airlines_GRU_JFK_sample.json"

# Airlines suportadas pela REST API do Award Travel Finder (plano Free)
_SUPPORTED_AIRLINES = [
    "british_airways",
    "qatar_airways",
    "cathay_pacific",
    "virgin_atlantic",
    "american_airlines",
]


def _make_stub_itinerary(
    origin: str,
    destination: str,
    search_date: str,
    departure_time: str | None,
    arrival_time: str | None,
    flight_number: str | None,
) -> Itinerary:
    """
    Constrói um Itinerary mínimo a partir dos dados disponíveis.
    Como o Award Travel Finder retorna disponibilidade (não horários exatos),
    usamos datetime do início do dia como placeholder quando não há horário.
    """
    try:
        dep_dt = datetime.fromisoformat(departure_time) if departure_time else datetime.strptime(search_date, "%Y-%m-%d")
    except Exception:
        dep_dt = datetime.strptime(search_date[:10], "%Y-%m-%d")

    try:
        arr_dt = datetime.fromisoformat(arrival_time) if arrival_time else dep_dt
    except Exception:
        arr_dt = dep_dt

    seg = Segment(
        origin=origin,
        destination=destination,
        departure_dt=dep_dt,
        arrival_dt=arr_dt,
        carrier=flight_number[:2] if (flight_number and len(flight_number) >= 2) else "??",
        flight_number=flight_number,
    )
    return Itinerary(segments=[seg])


class McpAwardAdapter(BaseSearchAdapter):
    """
    Adapter que busca disponibilidade de milhas internacionais
    via Award Travel Finder REST API.

    Retorna UnifiedOffer com source=SourceType.MCP_AWARD,
    miles=<pontos>, miles_program=<nome do programa>, taxes_brl=<taxas em BRL>.

    price_brl e equivalent_brl permanecem None — o cálculo pelo
    valor do milheiro é responsabilidade da tabela de configuração do sistema.
    """

    def __init__(self, fixture_path: str | None = None, source_type: SourceType = SourceType.MCP_AWARD):
        self._fixture_path = fixture_path or str(_DEFAULT_FIXTURE)
        self._source_type = source_type
        self._airlines_to_fetch = _SUPPORTED_AIRLINES

    # ------------------------------------------------------------------
    # Interface obrigatória
    # ------------------------------------------------------------------

    def search(
        self,
        request: SearchRequest,
        use_fixtures: bool = False,
        debug_dump: bool = False,
    ) -> List[UnifiedOffer]:

        # Extrai parametros do SearchRequest — SEM valores padrao hardcoded
        if not request.origin:
            raise ValueError("[McpAwardAdapter] SearchRequest.origin esta vazio.")
        if not request.destination:
            raise ValueError("[McpAwardAdapter] SearchRequest.destination esta vazio.")

        origin      = request.origin[0].upper()
        destination = request.destination[0].upper()
        date_str    = request.date_start.strftime("%Y-%m-%d")

        print(f"[McpAwardAdapter] Busca: {origin} -> {destination} | {date_str} | fixture={use_fixtures}")

        # ------------------------------------------------------------------
        # Modo fixture
        # ------------------------------------------------------------------
        if use_fixtures:
            raw_json = self._load_fixture()
        else:
            raw_json = self._fetch_all_airlines(origin, destination, date_str)

        if not raw_json:
            return []

        # Extrai ofertas normalizadas via mcp_offer_parser
        raw_offers = extract_mcp_offers(raw_json)

        # Converte para UnifiedOffer
        unified: List[UnifiedOffer] = []
        for o in raw_offers:
            try:
                u = self._to_unified_offer(o, origin, destination, date_str)
                if u is not None:
                    unified.append(u)
            except Exception as exc:
                print(f"[McpAwardAdapter] Erro ao converter oferta {o.get('airline')}/{o.get('cabin_class')}: {exc}")

        print(f"[McpAwardAdapter] {len(unified)} UnifiedOffers geradas para {origin}->{destination} em {date_str}")
        return unified

    # ------------------------------------------------------------------
    # Helpers privados
    # ------------------------------------------------------------------

    def _load_fixture(self) -> dict | None:
        path = Path(self._fixture_path)
        if not path.exists():
            print(f"[McpAwardAdapter] Fixture nao encontrado: {path}")
            return None
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        print(f"[McpAwardAdapter] [FIXTURE] Carregado: {path.name}")
        return data

    def _fetch_all_airlines(self, origin: str, destination: str, date_str: str) -> dict:
        """Chama a REST API para cada airline suportada e monta estrutura consolidada."""
        airlines_data = {}
        for airline in self._airlines_to_fetch:
            try:
                result = call_rest_availability(
                    airline=airline,
                    departure=origin,
                    arrival=destination,
                    date=date_str,
                )
                airlines_data[airline] = result.get("data", result)
            except Exception as exc:
                print(f"[McpAwardAdapter] Falha em {airline}: {exc}")
                airlines_data[airline] = {"http_error": 500, "detail": str(exc)}

        return {
            "tool":    "search_all_airlines",
            "source":  "mcp_award_adapter",
            "query":   {"origin": origin, "destination": destination, "date": date_str},
            "airlines": airlines_data,
        }

    def _to_unified_offer(
        self,
        o: dict,
        origin: str,
        destination: str,
        date_str: str,
    ) -> UnifiedOffer | None:
        """Converte um dict do mcp_offer_parser em UnifiedOffer."""
        miles = o.get("miles")
        if not miles or miles <= 0:
            return None

        outbound = _make_stub_itinerary(
            origin=origin,
            destination=destination,
            search_date=o.get("search_date") or date_str,
            departure_time=o.get("departure_time"),
            arrival_time=o.get("arrival_time"),
            flight_number=o.get("flight_number"),
        )

        return UnifiedOffer(
            source=self._source_type,
            airline=_fmt_airline_name(o.get("airline", "")),
            trip_type=TripType.ONEWAY,
            outbound=outbound,
            layover_out=LayoverCategory.DIRECT,
            miles=miles,
            miles_program=o.get("miles_program"),
            taxes_brl=o.get("taxes_brl") or 0.0,
            deeplink=o.get("booking_link"),
            # price_brl e equivalent_brl ficam None — calculados pela tabela de configuração
        )


def _fmt_airline_name(slug: str) -> str:
    """Converte slug para nome legível. Ex: 'qatar_airways' -> 'Qatar Airways'"""
    return slug.replace("_", " ").title()


class McpQatarAdapter(McpAwardAdapter):
    """Adapter dedicado exclusivamente para Qatar Airways via Award Travel Finder."""
    def __init__(self, fixture_path: str | None = None):
        super().__init__(fixture_path=fixture_path, source_type=SourceType.MCP_QATAR)
        self._airlines_to_fetch = ["qatar_airways"]

