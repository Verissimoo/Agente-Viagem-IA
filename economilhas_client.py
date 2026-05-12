"""
economilhas_client.py
---------------------
Cliente HTTP para a API Economilhas. Contraste com BuscaMilhas:

  BuscaMilhas: 1 chamada por companhia (N requisições para N programas).
  Economilhas: 1 chamada com array `airlineLoyalty` cobre todos os programas
               de uma vez.

A resposta vem com um campo `data` por companhia contendo o RAW da API
nativa do programa (Smiles, LATAM, Azul, ...). O parser específico vive
em `economilhas_offer_parser.py`.

Erros tratados:
  401 → credencial inválida
  402 → quota esgotada (Payment Required)
  422 → payload inválido
  429 → rate limit (com retry)
  5xx → erro transiente do upstream (com retry)
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import requests
from dotenv import load_dotenv

from pcd.cache import (
    SEM_ECONOMILHAS, make_key, get as _cache_get, set_ as _cache_set,
)

_CACHE_PREFIX = "economilhas"


# ──────────────────────────────────────────────────────────────────
# Constantes / metadados
# ──────────────────────────────────────────────────────────────────
ECONOMILHAS_ENDPOINT = "https://api.economilha.com/flights/search"
ECONOMILHAS_QUOTA_ENDPOINT = "https://api.economilha.com/quota"

ACCEPT_VERSION = "application/vnd.economilha.v1+json"

ECONOMILHAS_PROGRAMS_MILES: Dict[str, str] = {
    "SMILES":         "GOL (Smiles)",
    "LATAM":          "LATAM Pass",
    "AZUL":           "Azul Fidelidade",
    "AZUL_INTERLINE": "Azul Pelo Mundo (Interline)",
    "COPA":           "Copa ConnectMiles",
    "IBERIA":         "Iberia Plus",
    "BRITISH":        "British Airways Avios",
}

ECONOMILHAS_PROGRAMS_CASH: Dict[str, str] = {
    "LATAM": "LATAM (dinheiro)",
    "AZUL":  "Azul (dinheiro)",
    "GOL":   "GOL (dinheiro)",
}


# ──────────────────────────────────────────────────────────────────
# Exceções específicas — facilitam o tratamento amigável na UI
# ──────────────────────────────────────────────────────────────────
class EconomilhasError(RuntimeError):
    """Base para falhas Economilhas que precisam de mensagem amigável."""


class EconomilhasAuthError(EconomilhasError):
    """401 — chave inválida ou ausente."""


class EconomilhasQuotaExceeded(EconomilhasError):
    """402 — quota esgotada na conta Economilhas."""


class EconomilhasValidationError(EconomilhasError):
    """422 — payload inválido."""


# ──────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────
def _env(key: str, default: Optional[str] = None) -> Optional[str]:
    v = os.getenv(key)
    if v is None or str(v).strip() == "":
        return default
    return v


def _short(body: str, n: int = 800) -> str:
    body = body or ""
    return body if len(body) <= n else body[:n] + "…"


# ──────────────────────────────────────────────────────────────────
# Client
# ──────────────────────────────────────────────────────────────────
@dataclass
class EconomilhasClient:
    api_key: str
    endpoint: str = ECONOMILHAS_ENDPOINT
    quota_endpoint: str = ECONOMILHAS_QUOTA_ENDPOINT
    connect_timeout: int = 10
    read_timeout: int = 90
    max_attempts: int = 3

    def __post_init__(self):
        self.endpoint = (self.endpoint or "").rstrip("/")
        self.quota_endpoint = (self.quota_endpoint or "").rstrip("/")
        if not self.api_key:
            raise EconomilhasAuthError("ECONOMILHAS_API_KEY não configurada no .env")
        self._session = requests.Session()

    # ── Headers padrão ──────────────────────────────────────────
    def _headers(self) -> Dict[str, str]:
        return {
            "x-api-key": self.api_key,
            "Accept": ACCEPT_VERSION,
            "Content-Type": "application/json",
        }

    # ── Tradução status → exceção ───────────────────────────────
    @staticmethod
    def _raise_for_status(status: int, body: str):
        if status == 401:
            raise EconomilhasAuthError(
                f"Economilhas HTTP 401 — chave inválida ou expirada. Body: {_short(body)}"
            )
        if status == 402:
            raise EconomilhasQuotaExceeded(
                f"Economilhas HTTP 402 — quota esgotada. Body: {_short(body)}"
            )
        if status == 422:
            raise EconomilhasValidationError(
                f"Economilhas HTTP 422 — payload inválido. Body: {_short(body)}"
            )
        if status == 429:
            # rate-limit é retentável; sinaliza para o caller
            raise EconomilhasError(f"Economilhas HTTP 429 (rate limit). Body: {_short(body)}")
        if status >= 500:
            raise EconomilhasError(f"Economilhas HTTP {status} (server). Body: {_short(body)}")
        if status >= 400:
            raise EconomilhasError(f"Economilhas HTTP {status}. Body: {_short(body)}")

    # ── Requisição com retry ────────────────────────────────────
    def _post(self, url: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        last_err: Optional[Exception] = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                r = self._session.post(
                    url,
                    json=payload,
                    headers=self._headers(),
                    timeout=(self.connect_timeout, self.read_timeout),
                )
                if r.status_code in (401, 402, 422):
                    # Erros não-retentáveis: levanta imediatamente.
                    self._raise_for_status(r.status_code, r.text)
                if r.status_code == 429 or r.status_code >= 500:
                    # Retentáveis.
                    self._raise_for_status(r.status_code, r.text)
                if r.status_code >= 400:
                    self._raise_for_status(r.status_code, r.text)
                return r.json() if r.content else {}
            except (EconomilhasAuthError, EconomilhasQuotaExceeded, EconomilhasValidationError):
                # Não retentar.
                raise
            except Exception as e:
                last_err = e
                if attempt < self.max_attempts:
                    # 429 → backoff exponencial 2s/4s/8s (defesa-em-profundidade
                    # ao SEM_ECONOMILHAS). Outros transientes mantêm o 1.5*attempt.
                    wait_s = (2.0 ** attempt) if "429" in str(e) else min(1.5 * attempt, 6.0)
                    time.sleep(wait_s)
        raise EconomilhasError(
            f"Falha Economilhas após {self.max_attempts} tentativas: {last_err}"
        )

    def _get(self, url: str) -> Dict[str, Any]:
        last_err: Optional[Exception] = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                r = self._session.get(
                    url,
                    headers=self._headers(),
                    timeout=(self.connect_timeout, self.read_timeout),
                )
                if r.status_code in (401, 402, 422):
                    self._raise_for_status(r.status_code, r.text)
                if r.status_code == 429 or r.status_code >= 500:
                    self._raise_for_status(r.status_code, r.text)
                if r.status_code >= 400:
                    self._raise_for_status(r.status_code, r.text)
                return r.json() if r.content else {}
            except (EconomilhasAuthError, EconomilhasQuotaExceeded, EconomilhasValidationError):
                raise
            except Exception as e:
                last_err = e
                if attempt < self.max_attempts:
                    time.sleep(min(1.5 * attempt, 6.0))
        raise EconomilhasError(
            f"Falha Economilhas (quota) após {self.max_attempts} tentativas: {last_err}"
        )

    # ── API pública ─────────────────────────────────────────────
    def search(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Faz POST /flights/search e devolve o JSON cru da Economilhas."""
        return self._post(self.endpoint, payload)

    def get_quota(self) -> Dict[str, Any]:
        """GET /quota — retorna `{limit, consumed, remaining, usageByCompany}`
        ou estrutura equivalente. A UI cacheia por 5 min para não consumir
        request a cada visualização do popover."""
        return self._get(self.quota_endpoint)


# ──────────────────────────────────────────────────────────────────
# Build payload
# ──────────────────────────────────────────────────────────────────
def build_payload_economilhas(
    airlines: List[str],
    origin: str,
    destination: str,
    departure_date: str,                  # YYYY-MM-DD
    return_date: Optional[str] = None,    # YYYY-MM-DD; None = ida
    adults: int = 1,
    children: int = 0,
    infants: int = 0,
    cabin: str = "ECONOMY",
    price_type: str = "MILES",            # ou "CASH"
) -> Dict[str, Any]:
    trip_type = "ROUND_TRIP" if return_date else "ONE_WAY"
    payload: Dict[str, Any] = {
        "airlineLoyalty": [a.upper() for a in airlines],
        "priceType": price_type.upper(),
        "tripType": trip_type,
        "cabinType": cabin.upper(),
        "origin": origin.upper(),
        "destination": destination.upper(),
        "departureDate": departure_date,
        "passengers": {
            "adults":   int(adults or 1),
            "children": int(children or 0),
            "infants":  int(infants or 0),
        },
    }
    if return_date:
        payload["returnDate"] = return_date
    return payload


# ──────────────────────────────────────────────────────────────────
# Função de alto nível (lê api_key do .env)
# ──────────────────────────────────────────────────────────────────
def _make_client_from_env() -> EconomilhasClient:
    load_dotenv(override=False)
    api_key = _env("ECONOMILHAS_API_KEY", "") or ""
    endpoint = _env("ECONOMILHAS_ENDPOINT", ECONOMILHAS_ENDPOINT) or ECONOMILHAS_ENDPOINT
    quota_endpoint = _env("ECONOMILHAS_QUOTA_ENDPOINT", ECONOMILHAS_QUOTA_ENDPOINT) or ECONOMILHAS_QUOTA_ENDPOINT
    connect_timeout = int(_env("ECONOMILHAS_CONNECT_TIMEOUT", "10") or "10")
    read_timeout = int(_env("ECONOMILHAS_READ_TIMEOUT", "90") or "90")
    max_attempts = int(_env("ECONOMILHAS_MAX_ATTEMPTS", "3") or "3")
    return EconomilhasClient(
        api_key=api_key,
        endpoint=endpoint,
        quota_endpoint=quota_endpoint,
        connect_timeout=connect_timeout,
        read_timeout=read_timeout,
        max_attempts=max_attempts,
    )


def search_flights_economilhas(
    airlines: List[str],
    origin: str,
    destination: str,
    departure_date: str,                 # YYYY-MM-DD
    return_date: Optional[str] = None,
    adults: int = 1,
    children: int = 0,
    infants: int = 0,
    cabin: str = "ECONOMY",
    price_type: str = "MILES",
) -> Dict[str, Any]:
    """Busca voos na Economilhas usando ECONOMILHAS_API_KEY do .env.

    Devolve o JSON original da API (com `results[]` e `summary`). O parser
    em economilhas_offer_parser.py é quem normaliza para rows.
    """
    # Cache antes do semáforo / cliente: airlines normalizado + ordenado para
    # bater entre chamadas equivalentes.
    _ck_params = {
        "ai": sorted(a.upper() for a in (airlines or [])),
        "o": origin.upper(), "d": destination.upper(),
        "dd": departure_date, "rd": return_date or "",
        "ad": adults, "ch": children, "in": infants,
        "cab": cabin.upper(), "pt": price_type.upper(),
    }
    _ck = make_key(_CACHE_PREFIX, _ck_params)
    _hit = _cache_get(_ck)
    if _hit is not None:
        return _hit

    client = _make_client_from_env()
    payload = build_payload_economilhas(
        airlines=airlines,
        origin=origin, destination=destination,
        departure_date=departure_date, return_date=return_date,
        adults=adults, children=children, infants=infants,
        cabin=cabin, price_type=price_type,
    )
    with SEM_ECONOMILHAS:
        _result = client.search(payload)
    _cache_set(_ck, _result)
    return _result


# ──────────────────────────────────────────────────────────────────
# Quota com cache de 5 min — evita gastar requisição a cada render
# ──────────────────────────────────────────────────────────────────
_QUOTA_CACHE: Dict[str, Any] = {"ts": 0.0, "data": None}
_QUOTA_TTL_S = 300


def get_quota_cached(force: bool = False) -> Dict[str, Any]:
    """Retorna a quota mais recente, com TTL de 5 minutos por padrão.

    `force=True` ignora o cache e busca de novo (útil quando o vendedor
    clica explicitamente em 'Verificar quota')."""
    now = time.time()
    if (
        not force
        and _QUOTA_CACHE["data"] is not None
        and (now - _QUOTA_CACHE["ts"]) < _QUOTA_TTL_S
    ):
        return _QUOTA_CACHE["data"]
    data = _make_client_from_env().get_quota()
    _QUOTA_CACHE["ts"] = now
    _QUOTA_CACHE["data"] = data
    return data
