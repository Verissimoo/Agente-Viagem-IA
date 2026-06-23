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
from datetime import timedelta
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

# PROGRAMAS explícitos (não vazio!). O param vazio (=todos) × todas as cabines ×
# range estoura o limite de 36 entradas e o AwardTool volta VAZIO — era o bug de
# "AwardTool sem tarifa" intermitente. Lista curada dos programas resgatáveis do
# Brasil (mapeados em parser.PROGRAM_LABEL); UA fica de fora (United MileagePlus
# é suprimido no ranking). ~16 programas × range(2) × 1 cabine = ~32 ≤ 36.
# Override: AWARDTOOL_PROGRAMS="AC,AV,TK".
_DEFAULT_PROGRAMS: List[str] = [
    "AC",  # Aeroplan
    "AV",  # LifeMiles
    "AF",  # Flying Blue
    "AY",  # Finnair Plus
    "IB",  # Iberia Avios
    "BA",  # British Avios
    "QR",  # Qatar Privilege Club
    "AS",  # Alaska Mileage Plan
    "CM",  # Copa ConnectMiles
    "TP",  # TAP Miles&Go
    "EK",  # Emirates Skywards
    "EY",  # Etihad Guest
    "AA",  # American AAdvantage
    "TK",  # Turkish Miles&Smiles
    "G3",  # Smiles (GOL)
    "VS",  # Virgin Atlantic Flying Club
]

# AwardTool é um buscador de RANGE: range de 1 dia (start==end) volta vazio.
# Crawleamos uma janela curta e filtramos pra data pedida.
_RANGE_DAYS = int(os.getenv("AWARDTOOL_RANGE_DAYS", "2"))

# Cabine: SÓ Economy por padrão pra caber no limite de 36 entradas (todas as
# cabines × N programas × range estourava → vazio). Override via AWARDTOOL_CABINS,
# ex.: "Economy&Business" (lembre de reduzir AWARDTOOL_PROGRAMS pra não passar 36).
_DEFAULT_CABINS = "Economy"
_ALL_CABINS = "Economy&Premium Economy&Business&First"

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
        cabin = os.getenv("AWARDTOOL_CABINS", "").strip() or _DEFAULT_CABINS
        programs = _programs()

        # Janela PEDIDA (o orchestrator passa [win_start, win_end] do plano).
        want_start = request.date_start
        want_end = request.date_end or want_start
        if want_end < want_start:
            want_end = want_start
        # cap defensivo (flex gigante não vira crawl eterno)
        max_win = int(os.getenv("AWARDTOOL_MAX_WINDOW_DAYS", "10"))
        if (want_end - want_start).days > max_win:
            want_end = want_start + timedelta(days=max_win)
        # Crawl precisa de range ≥2 dias (range de 1 dia volta vazio).
        crawl_end = max(want_end, want_start + timedelta(days=_RANGE_DAYS))

        try:
            raw = cached_call(
                "awardtool",
                {"o": origin.upper(), "d": destination.upper(),
                 "ds": want_start.isoformat(), "de": want_end.isoformat(),
                 "prog": sorted(programs), "cab": cabin},
                search_awardtool,
                origin, destination, want_start, crawl_end,
                programs=programs, cabin=cabin,
            )
            offers = parse_search_result({"result": raw})
            # devolve só as ofertas DENTRO da janela pedida (descarta o padding do crawl)
            return [o for o in offers
                    if o.outbound.segments
                    and want_start <= o.outbound.segments[0].departure_dt.date() <= want_end]
        except AwardToolAuthError as e:
            print(f"[awardtool] auth: {str(e)[:150]}")
            return []
        except (AwardToolError, Exception) as e:
            print(f"[awardtool] {type(e).__name__}: {str(e)[:150]}")
            return []
