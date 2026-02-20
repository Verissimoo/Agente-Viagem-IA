from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import requests
from dotenv import load_dotenv


def _env(key: str, default: Optional[str] = None) -> Optional[str]:
    v = os.getenv(key)
    if v is None or str(v).strip() == "":
        return default
    return v


def _sleep_rps(rps: float):
    if rps and rps > 0:
        time.sleep(1.0 / float(rps))


@dataclass
class MoblixClient:
    base_url: str
    api_key: str
    connect_timeout: int = 10
    read_timeout: int = 60
    max_attempts: int = 3
    rps: float = 0.0

    def __post_init__(self):
        self.base_url = (self.base_url or "").rstrip("/")
        if not self.base_url:
            raise RuntimeError("MOBLIX_BASE_URL não configurada no .env")
        if not self.api_key:
            raise RuntimeError("MOBLIX_API_KEY não configurada no .env")
        self._session = requests.Session()

    @property
    def search_url(self) -> str:
        return f"{self.base_url}/flights/search"

    def flight_search(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        last_err: Optional[Exception] = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                if self.rps:
                    _sleep_rps(self.rps)

                r = self._session.post(
                    self.search_url,
                    json=payload,
                    headers=headers,
                    timeout=(self.connect_timeout, self.read_timeout),
                )

                if r.status_code == 429:
                    body = (r.text or "")[:2000]
                    raise RuntimeError(f"Moblix HTTP 429 (rate/limit). Body: {body}")

                if r.status_code >= 500:
                    # retry em erro servidor
                    body = (r.text or "")[:2000]
                    raise RuntimeError(f"Moblix HTTP {r.status_code} (server). Body: {body}")

                if r.status_code >= 400:
                    body = (r.text or "")[:2000]
                    raise RuntimeError(f"Moblix HTTP {r.status_code}. Body: {body}")

                return r.json() if r.content else {}

            except Exception as e:
                last_err = e
                # backoff simples
                time.sleep(min(1.5 * attempt, 6.0))

        raise RuntimeError(f"Falha Moblix após {self.max_attempts} tentativas: {last_err}")


def build_payload(
    origin: str,
    destination: str,
    departure_date: str,
    return_date: Optional[str],
    adults: int = 1,
    cabin_class: str = "economy",
    suppliers: Optional[List[str]] = None,
    search_type: Optional[str] = None,
) -> Dict[str, Any]:
    trip_type = "round_trip" if return_date else "one_way"

    slices = [{"origin": origin, "destination": destination, "departureDate": departure_date}]
    if return_date:
        slices.append({"origin": destination, "destination": origin, "departureDate": return_date})

    payload: Dict[str, Any] = {
        "type": trip_type,
        "slices": slices,
        "passengers": [{"type": "adult", "count": int(adults or 1)}],
        "cabinClass": (cabin_class or "economy").lower(),
        "enableDeduplication": True,
    }

    if suppliers:
        payload["suppliers"] = [str(s).strip().lower() for s in suppliers if str(s).strip()]

    # por segurança: não setar searchType por padrão
    if search_type:
        payload["searchType"] = search_type

    return payload


def search_flights(
    origin: str,
    destination: str,
    departure_date: str,
    return_date: Optional[str] = None,
    adults: int = 1,
    cabin_class: str = "economy",
    suppliers: Optional[List[str]] = None,
    search_type: Optional[str] = None,
) -> Dict[str, Any]:
    load_dotenv(override=False)

    base_url = _env("MOBLIX_BASE_URL", "https://app.apidevoos.dev/api/v1") or ""
    api_key = _env("MOBLIX_API_KEY", "") or ""

    connect_timeout = int(_env("MOBLIX_CONNECT_TIMEOUT", "10") or "10")
    read_timeout = int(_env("MOBLIX_READ_TIMEOUT", "60") or "60")
    max_attempts = int(_env("MOBLIX_MAX_ATTEMPTS", "3") or "3")
    rps = float(_env("MOBLIX_RPS", "0") or "0")

    client = MoblixClient(
        base_url=base_url,
        api_key=api_key,
        connect_timeout=connect_timeout,
        read_timeout=read_timeout,
        max_attempts=max_attempts,
        rps=rps,
    )

    payload = build_payload(
        origin=origin,
        destination=destination,
        departure_date=departure_date,
        return_date=return_date,
        adults=adults,
        cabin_class=cabin_class,
        suppliers=suppliers,
        search_type=search_type,
    )

    return client.flight_search(payload)




