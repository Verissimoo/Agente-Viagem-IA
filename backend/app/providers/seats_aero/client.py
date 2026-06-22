"""Cliente HTTP da Partner API do seats.aero.

Autenticação por **API key estática** no header `Partner-Authorization` — o
login por magic-link do site é irrelevante aqui (a key é gerada uma vez na aba
"API" das settings e usada sem sessão).

Padrão multi-programa (igual ao Economilhas): UMA chamada a `/search` cobre
vários programas de milhas de uma vez via `sources=aeroplan,lifemiles,...`.

Dois endpoints usados:
  GET /partnerapi/search        → availability cacheada (só milhas, sem horário/taxa)
  GET /partnerapi/trips/{id}    → detalhe de voo (segmentos com horário + TotalTaxes)

Sem `SEATS_AERO_API_KEY` o `_make_*` levanta SeatsAeroAuthError — o adapter
trata e devolve []. Quota Pro: 1000 chamadas/dia/key (reset meia-noite UTC),
então cacheamos agressivamente e o adapter limita o nº de /trips por busca.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import requests

from backend.app.infrastructure.cache import (
    SEM_SEATS_AERO,
    get as _cache_get,
    make_key,
    set_ as _cache_set,
)

_CACHE_PREFIX = "seats_aero"

SEATS_AERO_BASE = "https://seats.aero/partnerapi"


# ──────────────────────────────────────────────────────────────────
# Exceções
# ──────────────────────────────────────────────────────────────────
class SeatsAeroError(RuntimeError):
    """Base para falhas seats.aero."""


class SeatsAeroAuthError(SeatsAeroError):
    """401/403 — key inválida, ausente, ou sem acesso à Partner API."""


class SeatsAeroQuotaExceeded(SeatsAeroError):
    """429 — quota diária (1000/dia) esgotada ou rate-limit."""


# ──────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────
def _env(key: str, default: Optional[str] = None) -> Optional[str]:
    v = os.getenv(key)
    if v is None or str(v).strip() == "":
        return default
    return v


def _short(body: str, n: int = 600) -> str:
    body = body or ""
    return body if len(body) <= n else body[:n] + "…"


# ──────────────────────────────────────────────────────────────────
# Client
# ──────────────────────────────────────────────────────────────────
@dataclass
class SeatsAeroClient:
    api_key: str
    base: str = SEATS_AERO_BASE
    connect_timeout: int = 10
    read_timeout: int = 30
    max_attempts: int = 3

    def __post_init__(self) -> None:
        self.base = (self.base or SEATS_AERO_BASE).rstrip("/")
        if not self.api_key:
            raise SeatsAeroAuthError("SEATS_AERO_API_KEY não configurada no .env")
        self._session = requests.Session()

    def _headers(self) -> Dict[str, str]:
        return {
            "Partner-Authorization": self.api_key,
            "Accept": "application/json",
        }

    @staticmethod
    def _raise_for_status(status: int, body: str) -> None:
        if status in (401, 403):
            raise SeatsAeroAuthError(
                f"seats.aero HTTP {status} — key inválida/sem acesso. Body: {_short(body)}"
            )
        if status == 429:
            raise SeatsAeroQuotaExceeded(
                f"seats.aero HTTP 429 — quota/rate-limit. Body: {_short(body)}"
            )
        if status >= 500:
            raise SeatsAeroError(f"seats.aero HTTP {status} (server). Body: {_short(body)}")
        if status >= 400:
            raise SeatsAeroError(f"seats.aero HTTP {status}. Body: {_short(body)}")

    def _get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        url = f"{self.base}/{path.lstrip('/')}"
        last_err: Optional[Exception] = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                r = self._session.get(
                    url, params=params, headers=self._headers(),
                    timeout=(self.connect_timeout, self.read_timeout),
                )
                if r.status_code in (401, 403):
                    # Não-retentável.
                    self._raise_for_status(r.status_code, r.text)
                if r.status_code == 429 or r.status_code >= 500:
                    self._raise_for_status(r.status_code, r.text)
                if r.status_code >= 400:
                    self._raise_for_status(r.status_code, r.text)
                return r.json() if r.content else {}
            except SeatsAeroAuthError:
                raise
            except Exception as e:
                last_err = e
                if attempt < self.max_attempts:
                    wait_s = (2.0 ** attempt) if "429" in str(e) else min(1.5 * attempt, 6.0)
                    time.sleep(wait_s)
        raise SeatsAeroError(
            f"Falha seats.aero após {self.max_attempts} tentativas: {last_err}"
        )

    # ── API pública (cru) ───────────────────────────────────────
    def search(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """GET /search — availability cacheada multi-programa."""
        return self._get("search", params)

    def trip(self, availability_id: str) -> Dict[str, Any]:
        """GET /trips/{id} — segmentos de voo + taxas de uma availability."""
        return self._get(f"trips/{availability_id}")


def _make_client_from_env() -> SeatsAeroClient:
    return SeatsAeroClient(
        api_key=_env("SEATS_AERO_API_KEY", "") or "",
        base=_env("SEATS_AERO_BASE", SEATS_AERO_BASE) or SEATS_AERO_BASE,
        connect_timeout=int(_env("SEATS_AERO_CONNECT_TIMEOUT", "10") or "10"),
        read_timeout=int(_env("SEATS_AERO_READ_TIMEOUT", "30") or "30"),
        max_attempts=int(_env("SEATS_AERO_MAX_ATTEMPTS", "3") or "3"),
    )


# ──────────────────────────────────────────────────────────────────
# Funções de alto nível (lêem a key do .env, cacheiam, serializam)
# ──────────────────────────────────────────────────────────────────
def search_availability(
    origin: str,
    destination: str,
    depart_date: str,                 # YYYY-MM-DD
    sources: List[str],
    *,
    cabin: str = "economy",
    only_direct: bool = False,
    take: int = 500,
) -> Dict[str, Any]:
    """Busca availability (uma data, uma direção) na Partner API do seats.aero.

    Devolve o JSON cru (com `data[]`). O parser normaliza para UnifiedOffer.
    Cacheado pelo TTL de milhas (180s).
    """
    src = sorted({s.strip().lower() for s in (sources or []) if s.strip()})
    ck_params = {
        "o": origin.upper(), "d": destination.upper(), "dd": depart_date,
        "src": src, "cab": cabin.lower(), "dir": bool(only_direct),
    }
    ck = make_key(_CACHE_PREFIX, ck_params)
    hit = _cache_get(ck)
    if hit is not None:
        return hit

    params: Dict[str, Any] = {
        "origin_airport": origin.upper(),
        "destination_airport": destination.upper(),
        "start_date": depart_date,
        "end_date": depart_date,
        "sources": ",".join(src),
        "cabin": cabin.lower(),
        "take": take,
        "order_by": "lowest_mileage",
    }
    if only_direct:
        params["only_direct_flights"] = "true"

    with SEM_SEATS_AERO:
        result = _make_client_from_env().search(params)
    _cache_set(ck, result)
    return result


def get_trip(availability_id: str) -> Dict[str, Any]:
    """GET /trips/{id} — detalhe de voo. Cacheado (180s)."""
    ck = make_key(_CACHE_PREFIX, {"trip": availability_id})
    hit = _cache_get(ck)
    if hit is not None:
        return hit
    with SEM_SEATS_AERO:
        result = _make_client_from_env().trip(availability_id)
    _cache_set(ck, result)
    return result
