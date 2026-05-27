"""SeatsAeroAdapter — busca award availability via API seats.aero.

Cobertura (Pro+ plan):
  • Aeroplan (Air Canada)
  • Lifemiles (Avianca)
  • Iberia Avios / British Avios / Qatar Avios / Aer Lingus Avios
  • Smiles (GOL)
  • LATAM Pass
  • TudoAzul / Azul Pelo Mundo
  • Copa ConnectMiles
  • Atmos (Air Europa) — em onboarding
  • United MileagePlus, Alaska MileagePlan, Delta SkyMiles
  • Air France/KLM Flying Blue
  • Emirates Skywards, Etihad Guest
  • ANA Mileage Club, Cathay Asia Miles, JAL Mileage Bank, Singapore KrisFlyer
  • E muitos outros (~30 programas no total)

Comportamento sem credencial:
  • `SEATS_AERO_API_KEY` ausente → retorna [] sem erro.
  • Permite o pipeline rodar e o usuário ver o restante mesmo sem o seats.aero
    contratado.
"""
from __future__ import annotations

import os
from typing import List

from backend.app.domain.models import (
    SearchRequest,
    UnifiedOffer,
)
from backend.app.providers.base import BaseSearchAdapter


def _enabled() -> bool:
    """True quando o seats.aero está configurado E habilitado.
    Permite corte rápido via `SEATS_AERO_ENABLED=0` mesmo com key presente."""
    if os.getenv("SEATS_AERO_ENABLED", "1") in ("0", "false", "False", ""):
        return False
    return bool(os.getenv("SEATS_AERO_API_KEY"))


# Mapa: programa → label exibida no source_label da UI.
# Quando integrarmos, o parser usará isso para preencher offer.miles_program.
PROGRAM_DISPLAY = {
    "aeroplan":       "Aeroplan (Air Canada)",
    "lifemiles":      "Lifemiles (Avianca)",
    "iberia":         "Iberia Avios",
    "british":        "British Avios",
    "qatar":          "Qatar Privilege Club",
    "aerlingus":      "Aer Lingus AerClub",
    "smiles":         "Smiles (GOL)",
    "latampass":      "LATAM Pass",
    "tudoazul":       "TudoAzul (Azul)",
    "azulpelomundo":  "Azul Pelo Mundo",
    "copa":           "Copa ConnectMiles",
    "atmos":          "Atmos (Air Europa)",
    "united":         "United MileagePlus",
    "alaska":         "Alaska MileagePlan",
    "delta":          "Delta SkyMiles",
    "flyingblue":     "Flying Blue (AF/KLM)",
    "emirates":       "Emirates Skywards",
    "etihad":         "Etihad Guest",
    "ana":            "ANA Mileage Club",
    "cathay":         "Cathay Asia Miles",
    "jal":            "JAL Mileage Bank",
    "singapore":      "Singapore KrisFlyer",
}


class SeatsAeroAdapter(BaseSearchAdapter):
    """Esqueleto. Implementar quando SEATS_AERO_API_KEY estiver setada.

    Estrutura prevista da API (a confirmar contra docs oficiais após assinatura):
        GET https://seats.aero/api/search
          ?origin=GRU&destination=MIA
          &startDate=2026-06-01&endDate=2026-06-01
          &cabin=economy
          &source=aeroplan,lifemiles,iberia,...
        Headers: Authorization: Partner <SEATS_AERO_API_KEY>

    Resposta esperada (formato típico v2):
        {
          "data": [{
            "ID": "...",
            "Route": {"OriginAirport": "GRU", "DestinationAirport": "MIA"},
            "Date": "2026-06-01",
            "YMileageCost": 60000,
            "YDirect": true,
            "YAvailable": true,
            "Source": "aeroplan",
            ...
          }]
        }
    """

    source_type = None  # será definido como SourceType.SEATS_AERO quando integrarmos

    def search(
        self,
        request: SearchRequest,
        use_fixtures: bool = False,
        debug_dump: bool = False,
    ) -> List[UnifiedOffer]:
        if not _enabled():
            return []

        # TODO: integrar quando a API key estiver disponível.
        # 1. Montar params (origin/destination/date/cabin/sources).
        # 2. Chamar requests.get(SEATS_AERO_URL, headers={"Authorization": f"Partner {key}"}).
        # 3. Parsear resposta para list[UnifiedOffer] com o source e miles_program corretos.
        # 4. Aplicar cache (cached_call) com TTL de 180s — mesmo padrão de milhas do projeto.
        # 5. Filtrar por cabin se necessário (a API retorna várias cabines no mesmo objeto).
        #
        # Em todos os pontos: try/except amplo + return [] para não derrubar o pipeline.

        # Por enquanto, devolve [] mesmo com key presente — só ativa quando o
        # parser estiver pronto. Trocar para a implementação real após:
        #   pip install httpx tenacity (se quiser retry exponencial)
        #   credenciar SEATS_AERO_API_KEY no Railway / .env
        return []
