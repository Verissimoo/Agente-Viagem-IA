import os
import time
import random
import requests
from dotenv import load_dotenv

load_dotenv()

RETRY_STATUS_CODES = {408, 429, 500, 502, 503, 504}


def _compute_wait_seconds(attempt: int, retry_after_header: str | None) -> float:
    if retry_after_header:
        try:
            ra = float(retry_after_header)
            return max(ra, 0) + random.uniform(0.1, 0.5)
        except ValueError:
            pass
    base = min(2 ** (attempt - 1), 30)
    jitter = random.uniform(0.1, 0.8)
    return base + jitter


def _extract_rate_headers(headers: dict) -> dict:
    out = {}
    for k, v in headers.items():
        lk = k.lower()
        if "rate" in lk or "retry" in lk or "limit" in lk or "remaining" in lk or "reset" in lk:
            out[lk] = v
    return out


def _is_search_complete(payload: dict) -> bool:
    """
    Kayak/RapidAPI costuma sinalizar via:
    - data.searchStatus
    - data.status
    Alguns providers variam o texto. Aqui aceitamos vários.
    """
    data = (payload or {}).get("data") or {}
    ss = str(data.get("searchStatus") or "").lower()
    st = str(data.get("status") or "").lower()

    # valores comuns: "complete", "completed", "done", "finished"
    if ss in {"complete", "completed", "done", "finished"}:
        return True
    if st in {"complete", "completed", "done", "finished"}:
        return True

    # Se vier "in_progress" / "pending" / "partial", não completa
    return False


def _poll_search_results(base_url: str, headers: dict, search_url: str, timeout_seconds: int) -> dict:
    """
    Polling do searchUrl retornado pela API.
    """
    if search_url.startswith("http://") or search_url.startswith("https://"):
        url = search_url
    else:
        # searchUrl geralmente é relativo
        url = f"{base_url}{search_url}"

    r = requests.get(url, headers=headers, timeout=timeout_seconds)
    r.raise_for_status()
    return r.json()


def search_flights(
    origin: str,
    destination: str,
    departure_date: str,
    return_date: str | None = None,
    adults: int = 1,
    cabin: str = "e",
    page: int = 1,
    sort_mode: str = "price_a",
):
    api_key = os.getenv("RAPIDAPI_KEY")
    host = os.getenv("RAPIDAPI_HOST", "kayak-api.p.rapidapi.com")
    base_url = os.getenv("KAYAK_BASE_URL", "https://kayak-api.p.rapidapi.com")

    if not api_key:
        raise RuntimeError("RAPIDAPI_KEY não encontrada no .env")

    url = f"{base_url}/search-flights"
    headers = {
        "Content-Type": "application/json",
        "x-rapidapi-key": api_key,
        "x-rapidapi-host": host,
    }

    payload = {
        "origin": origin,
        "destination": destination,
        "departure_date": departure_date,
        "searchMetaData": {"pageNumber": page, "priceMode": "per-person"},
        "filterParams": {"fs": f"cabin={cabin}"},
        "userSearchParams": {
            "passengers": ["ADT"] * adults,
            "sortMode": sort_mode,
        },
    }

    if return_date:
        payload["return_date"] = return_date

    max_attempts = int(os.getenv("RAPIDAPI_MAX_ATTEMPTS", "6"))
    timeout_seconds = int(os.getenv("RAPIDAPI_TIMEOUT", "90"))

    # Polling settings
    poll_enabled = os.getenv("KAYAK_POLL_ENABLED", "1") == "1"
    poll_max = int(os.getenv("KAYAK_POLL_MAX", "8"))          # quantas “puxadas” no searchUrl
    poll_sleep = float(os.getenv("KAYAK_POLL_SLEEP", "1.5"))  # segundos entre polls

    last_exc = None

    for attempt in range(1, max_attempts + 1):
        try:
            r = requests.post(url, headers=headers, json=payload, timeout=timeout_seconds)

            if 200 <= r.status_code < 300:
                data = r.json()

                # Se polling ativado e ainda não completou, usa searchUrl
                if poll_enabled:
                    try:
                        if not _is_search_complete(data):
                            search_url = (data.get("data") or {}).get("searchUrl")
                            if isinstance(search_url, str) and search_url.strip():
                                # faz polling até completar ou estourar poll_max
                                for i in range(poll_max):
                                    time.sleep(poll_sleep)
                                    data = _poll_search_results(base_url, headers, search_url, timeout_seconds)
                                    if _is_search_complete(data):
                                        break
                    except Exception:
                        # Se polling falhar, devolve o que já temos (melhor que quebrar)
                        pass

                return data

            if r.status_code in RETRY_STATUS_CODES:
                if r.status_code == 429:
                    dbg = _extract_rate_headers(r.headers)
                    print(f"[429] Rate headers: {dbg}")

                retry_after = r.headers.get("Retry-After")
                wait_s = _compute_wait_seconds(attempt, retry_after)

                if attempt == max_attempts:
                    try:
                        body = r.json()
                    except Exception:
                        body = r.text[:800]
                    raise RuntimeError(
                        f"Falha após {max_attempts} tentativas. "
                        f"Status {r.status_code}. Wait={wait_s:.2f}s. Body: {body}"
                    )

                time.sleep(wait_s)
                continue

            try:
                body = r.json()
            except Exception:
                body = r.text[:800]
            raise RuntimeError(f"Erro HTTP {r.status_code}. Body: {body}")

        except (requests.Timeout, requests.ConnectionError) as e:
            last_exc = e
            if attempt == max_attempts:
                raise RuntimeError(f"Falha de rede/timeout após {max_attempts} tentativas: {e}") from e
            wait_s = _compute_wait_seconds(attempt, None)
            time.sleep(wait_s)

    raise RuntimeError(f"Falha inesperada em search_flights. Última exceção: {last_exc}")



