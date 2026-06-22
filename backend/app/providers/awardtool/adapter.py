"""AwardToolAdapter — award multi-programa via scraping da conta Pro (Playwright).

Multi-programa como o Economilhas: uma busca cobre vários programas (Aeroplan,
LifeMiles, Flying Blue, Finnair, Iberia, Qatar, British, Alaska, Copa, TAP…).
Cada voo vira um UnifiedOffer com seu miles_program e entra no ranking.

GATED por `AWARDTOOL_ENABLED` (default 0) — provider PESADO (abre navegador,
~30-60s) e ToS-sensível. Sem flag ou sem credencial → retorna [] na hora.

Limite do AwardTool: 36 entradas (dias×programas×cabines). Como o orchestrator
chama por DATA (1 dia), usamos 1 cabine + N programas (N≤~30) por chamada.
Cache de 180s (cada chamada é cara). Falha sempre absorvida → [].
"""
from __future__ import annotations

import os
from typing import List

from backend.app.domain.errors import OfflineModeError
from backend.app.domain.models import SearchRequest, UnifiedOffer
from backend.app.infrastructure.cache import cached_call
from backend.app.infrastructure.config import config
from backend.app.providers.awardtool.client import (
    AwardToolAuthError,
    AwardToolError,
    search_awardtool,
)
from backend.app.providers.awardtool.parser import parse_search_result
from backend.app.providers.base import BaseSearchAdapter

# Programas-alvo (códigos IATA do AwardTool). Override: AWARDTOOL_PROGRAMS="AC,AV,IB".
_DEFAULT_PROGRAMS = ["AC", "AV", "KL", "AY", "IB", "BA", "QR", "AS", "CM", "TP"]

# Cabine do domínio → rótulo do AwardTool.
_CABIN_MAP = {"economy": "Economy", "business": "Business", "first": "First"}

# Máx. de entradas por busca (dias×programas×cabines) imposto pelo AwardTool.
_MAX_ENTRIES = 36


def _enabled() -> bool:
    if os.getenv("AWARDTOOL_ENABLED", "0") in ("0", "false", "False", ""):
        return False
    return bool(os.getenv("AWARDTOOL_EMAIL") and os.getenv("AWARDTOOL_PASSWORD"))


def _programs() -> List[str]:
    env = os.getenv("AWARDTOOL_PROGRAMS", "")
    progs = [p.strip().upper() for p in env.split(",") if p.strip()] if env.strip() else list(_DEFAULT_PROGRAMS)
    # 1 dia × 1 cabine × N programas ≤ 36
    return progs[:_MAX_ENTRIES]


class AwardToolAdapter(BaseSearchAdapter):
    """Adapter síncrono — roda no ThreadPoolExecutor do orchestrator.

    Indisponibilidade do AwardTool nunca degrada o resto da busca (→ []).
    """

    def search(
        self,
        request: SearchRequest,
        use_fixtures: bool = False,
        debug_dump: bool = False,
    ) -> List[UnifiedOffer]:
        if not _enabled():
            return []
        if config.PCD_OFFLINE:
            raise OfflineModeError("awardtool")

        origin = request.origin[0]
        destination = request.destination[0]
        day = request.date_start
        cabin = _CABIN_MAP.get((request.cabin.value if request.cabin else "economy"), "Economy")
        programs = _programs()

        try:
            raw = cached_call(
                "awardtool",
                {"o": origin.upper(), "d": destination.upper(),
                 "dd": day.isoformat(), "prog": sorted(programs), "cab": cabin},
                search_awardtool,
                origin, destination, day, day,
                programs=programs, cabin=cabin,
            )
            return parse_search_result({"result": raw})
        except AwardToolAuthError as e:
            print(f"[awardtool] auth: {str(e)[:150]}")
            return []
        except (AwardToolError, Exception) as e:
            print(f"[awardtool] {type(e).__name__}: {str(e)[:150]}")
            return []
