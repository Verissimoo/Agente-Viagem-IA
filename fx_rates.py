from __future__ import annotations
import os
import time
import requests

# Cache simples em memória (processo atual do Streamlit)
_RATE_CACHE: dict[tuple[str, str], dict] = {}

DEFAULT_FX_BASE_URL = os.getenv("FX_BASE_URL", "https://api.frankfurter.app")
DEFAULT_FX_TTL_SECONDS = int(os.getenv("FX_TTL_SECONDS", "21600"))  # 6h
DEFAULT_FX_TIMEOUT = int(os.getenv("FX_TIMEOUT", "20"))


def get_rate(from_ccy: str, to_ccy: str) -> float:
    """
    Busca taxa from_ccy -> to_ccy usando Frankfurter:
      GET /latest?from=USD&to=BRL
    Cacheia por TTL para reduzir requisições.
    """
    from_ccy = (from_ccy or "").upper()
    to_ccy = (to_ccy or "").upper()

    if not from_ccy or not to_ccy:
        raise ValueError("Moedas inválidas para conversão.")
    if from_ccy == to_ccy:
        return 1.0

    key = (from_ccy, to_ccy)
    now = time.time()

    cached = _RATE_CACHE.get(key)
    if cached and (now - cached["ts"] < DEFAULT_FX_TTL_SECONDS):
        return float(cached["rate"])

    url = f"{DEFAULT_FX_BASE_URL}/latest"
    params = {"from": from_ccy, "to": to_ccy}

    r = requests.get(url, params=params, timeout=DEFAULT_FX_TIMEOUT)
    r.raise_for_status()
    data = r.json()

    rate = data.get("rates", {}).get(to_ccy)
    if rate is None:
        raise RuntimeError(f"Taxa não encontrada para {from_ccy}->{to_ccy}. Resposta: {data}")

    _RATE_CACHE[key] = {"rate": float(rate), "ts": now}
    return float(rate)


def convert(amount: float, from_ccy: str, to_ccy: str) -> float:
    rate = get_rate(from_ccy, to_ccy)
    return float(amount) * rate
