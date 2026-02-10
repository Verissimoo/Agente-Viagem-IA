from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional, List

import requests
from dotenv import load_dotenv


def _env(key: str, default: Optional[str] = None) -> Optional[str]:
    return os.getenv(key, default)


def _sleep_rps(rps: float):
    # rps=1.0 => espera ~1s entre chamadas
    if rps and rps > 0:
        time.sleep(1.0 / float(rps))


@dataclass
class MoblixClient:
    base_url: str
    api_key: str
    timeout: int = 90
    max_attempts: int = 6
    rps: float = 0.0

    @property
    def search_path(self) -> str:
        return "/flights/search"

    def __post_init__(self):
        self.base_url = (self.base_url or "").rstrip("/")
        if not self.base_url:
            raise RuntimeError("MOBLIX_BASE_URL não configurada no .env")
        if not self.api_key:
            raise RuntimeError("MOBLIX_API_KEY não encontrada no .env")

    def flight_search(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        url = f"{self.base_url}{self.search_path}"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        last_err = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                if self.rps:
                    _sleep_rps(self.rps)

                r = requests.post(url, json=payload, headers=headers, timeout=self.timeout)
                if r.status_code >= 400:
                    body = (r.text or "")[:2000]
                    raise RuntimeError(f"Moblix erro HTTP {r.status_code} em {url}. Body: {body}")

                return r.json() if r.content else {}

            except Exception as e:
                last_err = e
                # backoff simples
                time.sleep(min(2.0 * attempt, 10.0))

        raise RuntimeError(f"Falha Moblix após {self.max_attempts} tentativas: {last_err}")


def build_payload(
    origin: str,
    destination: str,
    departure_date: str,
    return_date: Optional[str],
    adults: int = 1,
    cabin_class: str = "economy",
    search_type: Optional[str] = None,  # "milhas" | "pagante" | None
    suppliers: Optional[List[str]] = None,
) -> Dict[str, Any]:
    trip_type = "round_trip" if return_date else "one_way"

    slices = [
        {"origin": origin, "destination": destination, "departureDate": departure_date}
    ]
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
        payload["suppliers"] = suppliers

    if search_type:
        payload["searchType"] = search_type  # "milhas" ou "pagante"

    return payload


def search_flights(
    origin: str,
    destination: str,
    departure_date: str,
    return_date: Optional[str] = None,
    adults: int = 1,
    cabin_class: str = "economy",
    search_type: Optional[str] = None,
    suppliers: Optional[List[str]] = None,
) -> Dict[str, Any]:
    # garante que .env foi carregado (rodando via streamlit ou CLI)
    load_dotenv(override=False)

    base_url = _env("MOBLIX_BASE_URL", "https://app.apidevoos.dev/api/v1")
    api_key = _env("MOBLIX_API_KEY", "")
    timeout = int(_env("MOBLIX_TIMEOUT", "90") or "90")
    max_attempts = int(_env("MOBLIX_MAX_ATTEMPTS", "6") or "6")
    rps = float(_env("MOBLIX_RPS", "0") or "0")

    c = MoblixClient(
        base_url=base_url,
        api_key=api_key,
        timeout=timeout,
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
        search_type=search_type,
        suppliers=suppliers,
    )
    return c.flight_search(payload)




