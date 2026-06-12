"""Health-check de provedores de MILHAS via rotas-canário (diagnóstico interno).

Dispara uma busca real (hoje+30, só-ida, 1 adulto) em cada programa de milhas
reusando o worker do orchestrator (`_run_one_adapter`) e classifica o status:
ok / empty / error / timeout. NÃO inclui fontes CASH (Kayak/Skiplagged/AzulCash).

Pra flagrar quando uma cia (American, Interline, etc.) para de responder.
"""
from __future__ import annotations

import logging
import os
from concurrent.futures import (
    ThreadPoolExecutor,
    TimeoutError as FuturesTimeoutError,
    as_completed,
)
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

from backend.app.domain.models import CabinClass, SearchRequest, TripType
from backend.app.services.search_orchestrator import _ADAPTER_MAP, _run_one_adapter

logger = logging.getLogger(__name__)

# Fontes CASH — NÃO entram no teste de MILHAS.
_CASH_ONLY = {"KAYAK", "SKIPLAGGED", "AZUL_CASH"}

# Rótulos legíveis e SourceType por programa (pro painel).
_LABELS: Dict[str, str] = {
    "LATAM": "LATAM Pass", "GOL": "Smiles (GOL)", "AZUL": "TudoAzul",
    "TAP": "TAP Miles&Go", "IBERIA": "Iberia Plus", "AMERICAN": "AAdvantage (American)",
    "AMERICAN AIRLINES": "AAdvantage (American)", "INTERLINE": "Interline",
    "COPA": "ConnectMiles (Copa)", "MCP_AWARD": "MCP Award",
    "QATAR": "Qatar Privilege Club", "ECONOMILHAS": "Economilhas (agregador)",
}
_SOURCE_TYPE: Dict[str, str] = {
    "LATAM": "buscamilhas_latam", "GOL": "buscamilhas_gol", "AZUL": "buscamilhas_azul",
    "TAP": "buscamilhas_tap", "IBERIA": "buscamilhas_iberia",
    "AMERICAN": "buscamilhas_american", "AMERICAN AIRLINES": "buscamilhas_american",
    "INTERLINE": "buscamilhas_interline", "COPA": "buscamilhas_copa",
    "MCP_AWARD": "mcp_award", "QATAR": "mcp_qatar", "ECONOMILHAS": "economilhas",
}

# Rotas-canário (trechos com voo garantido). Override por env:
#   MILES_CANARY_AMERICAN="GRU>JFK"
_DEFAULT_CANARY: Dict[str, Tuple[str, str]] = {
    "LATAM": ("GRU", "GIG"), "GOL": ("BSB", "GRU"), "AZUL": ("GRU", "GIG"),
    "TAP": ("GRU", "LIS"), "IBERIA": ("GRU", "MAD"), "AMERICAN": ("GRU", "MIA"),
    "AMERICAN AIRLINES": ("GRU", "MIA"), "INTERLINE": ("GRU", "MIA"),
    "COPA": ("GRU", "PTY"), "MCP_AWARD": ("GRU", "LIS"), "QATAR": ("GRU", "DOH"),
    "ECONOMILHAS": ("GRU", "GIG"),
}


def _canary(program: str) -> Tuple[str, str]:
    env = os.getenv(f"MILES_CANARY_{program.replace(' ', '_')}")
    if env and ">" in env:
        o, d = (x.strip().upper() for x in env.split(">", 1))
        if len(o) == 3 and len(d) == 3 and o.isalpha() and d.isalpha():
            return o, d
    return _DEFAULT_CANARY.get(program, ("GRU", "GIG"))


@dataclass
class ProgramHealth:
    program: str
    label: str
    source_type: str
    status: str            # ok | empty | error | timeout
    offers_count: int
    latency_ms: float
    route: str             # "GRU→MIA"
    error_kind: Optional[str]
    error_detail: Optional[str]
    checked_at: str


def miles_programs() -> List[str]:
    """Programas de MILHAS testáveis (exclui as fontes só-cash)."""
    return [p for p in _ADAPTER_MAP if p not in _CASH_ONLY]


def _budget_s() -> float:
    try:
        return float(os.getenv("MILES_HEALTHCHECK_BUDGET_S", "35"))
    except ValueError:
        return 35.0


def run_miles_healthcheck(programs: Optional[List[str]] = None, *, adults: int = 1,
                          date_override: Optional[date] = None) -> List[ProgramHealth]:
    """Roda o health-check em paralelo (orçamento de tempo) e devolve o status de
    cada programa. Nunca levanta — `_run_one_adapter` já captura tudo."""
    avail = miles_programs()
    if programs:
        wanted = [p for p in avail if p in {x.upper() for x in programs}]
    else:
        wanted = list(avail)
    if not wanted:
        return []

    d = date_override or (date.today() + timedelta(days=30))
    budget = _budget_s()
    now_iso = datetime.now(timezone.utc).isoformat()

    def _mk(prog: str, route: str, status: str, offers: int, latency: float,
            err) -> ProgramHealth:
        return ProgramHealth(
            program=prog, label=_LABELS.get(prog, prog),
            source_type=_SOURCE_TYPE.get(prog, prog.lower()),
            status=status, offers_count=offers, latency_ms=round(latency, 1),
            route=route, error_kind=type(err).__name__ if err else None,
            error_detail=str(err)[:300] if err else None, checked_at=now_iso,
        )

    results: Dict[str, ProgramHealth] = {}
    futs = {}
    with ThreadPoolExecutor(max_workers=min(len(wanted), 12)) as ex:
        for prog in wanted:
            o, dst = _canary(prog)
            route = f"{o}→{dst}"
            try:
                req = SearchRequest(
                    origin=[o], destination=[dst], date_start=d, date_end=d,
                    trip_type=TripType.ONEWAY, adults=adults, cabin=CabinClass.ECONOMY,
                )
            except Exception as e:
                results[prog] = _mk(prog, route, "error", 0, 0.0, e)
                continue
            f = ex.submit(_run_one_adapter, prog, _ADAPTER_MAP[prog], req, False, False)
            futs[f] = (prog, route)

        try:
            for f in as_completed(futs, timeout=budget):
                prog, route = futs[f]
                _, offers, error, elapsed = f.result()
                if error is not None:
                    status = "error"
                elif offers:
                    status = "ok"
                else:
                    status = "empty"
                results[prog] = _mk(prog, route, status, len(offers), elapsed, error)
        except FuturesTimeoutError:
            pass  # os que não voltaram viram "timeout" abaixo

    for (prog, route) in futs.values():
        if prog not in results:
            results[prog] = _mk(prog, route, "timeout", 0, budget * 1000.0, None)
            results[prog].error_detail = "orçamento de tempo esgotado"

    return [results[p] for p in wanted if p in results]
