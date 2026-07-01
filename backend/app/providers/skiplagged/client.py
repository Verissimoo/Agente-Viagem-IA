"""Skiplagged scraping client.

The site renders `https://skiplagged.com/flights/<from>/<to>/<date>` in the
browser, which then calls the JSON endpoint `https://skiplagged.com/api/search.php`.
We go straight to the JSON endpoint via Playwright (Cloudflare blocks raw httpx).

A single response from `/api/search.php` already contains:
- Regular routes (destination = requested destination)
- Hidden-city routes (destination = a city *beyond* the requested one,
  with the requested destination appearing as a connection)

So there's no separate endpoint for hidden city — detection happens in the
parser by inspecting `segments[-1].destination`.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlencode

import httpx

SKIPLAGGED_HOST = "https://skiplagged.com"

DEBUG_DIR = Path("debug") / "skiplagged"
DEBUG_DIR.mkdir(parents=True, exist_ok=True)

DEFAULT_TIMEOUT_S = float(os.getenv("SKIPLAGGED_TIMEOUT", "25"))
PLAYWRIGHT_WAIT_MS = int(os.getenv("SKIPLAGGED_WAIT_MS", "12000"))

_RESULT_KEYS = ("flights", "itineraries")


def _api_url(from_: str, to: str, date_: str, adults: int) -> str:
    params = {
        "from": from_,
        "to": to,
        "depart": date_,
        "return": "",
        "format": "v3",
        "counts[adults]": str(adults),
        "counts[children]": "0",
        "counts[infants_lap]": "0",
        "counts[infants_seat]": "0",
        "fare_class": "economy",
        "sort": "cost",
    }
    return f"{SKIPLAGGED_HOST}/api/search.php?{urlencode(params)}"


def _debug_dump(payload: Any, from_: str, to: str, date_: str, suffix: str) -> None:
    try:
        ts = int(time.time())
        fname = DEBUG_DIR / f"raw_{from_}_{to}_{date_}_{suffix}_{ts}.json"
        fname.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def _looks_like_results(data: Any) -> bool:
    return isinstance(data, dict) and all(k in data for k in _RESULT_KEYS)


def fetch_via_httpx(
    from_: str,
    to: str,
    date_: str,
    adults: int = 1,
    timeout: float = DEFAULT_TIMEOUT_S,
) -> Optional[dict]:
    """Tries the JSON endpoint directly. Usually blocked by Cloudflare (403),
    kept as a cheap first attempt before falling back to Playwright."""
    url = _api_url(from_, to, date_, adults)
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json,text/plain,*/*",
        "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
        "Referer": f"{SKIPLAGGED_HOST}/flights/{from_}/{to}/{date_}",
    }
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            resp = client.get(url, headers=headers)
        if resp.status_code != 200:
            return None
        if "json" not in resp.headers.get("content-type", ""):
            return None
        data = resp.json()
        if _looks_like_results(data):
            _debug_dump(data, from_, to, date_, "httpx")
            return data
    except (httpx.HTTPError, ValueError):
        pass
    return None


def fetch_via_playwright(
    from_: str,
    to: str,
    date_: str,
    adults: int = 1,
    wait_ms: int = PLAYWRIGHT_WAIT_MS,
) -> Optional[dict]:
    """Loads `/api/search.php` directly through a headless browser to bypass
    Cloudflare. Captures the JSON either from the page body (when Skiplagged
    serves JSON straight) or from network responses.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None

    target_url = _api_url(from_, to, date_, adults)
    captured: list[dict] = []

    def _on_response(response):  # noqa: ANN001
        try:
            if "skiplagged.com/api/search.php" not in response.url:
                return
            ct = response.headers.get("content-type", "")
            if "json" not in ct or response.status != 200:
                return
            data = response.json()
            if _looks_like_results(data):
                captured.append(data)
        except Exception:
            pass

    try:
        from backend.app.infrastructure.browser import browser_slot
        with browser_slot(), sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                context = browser.new_context(
                    user_agent=(
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/120.0.0.0 Safari/537.36"
                    ),
                    locale="pt-BR",
                )
                page = context.new_page()
                page.on("response", _on_response)
                # `/api/search.php` serves JSON directly — Playwright renders it
                # as `<pre>...</pre>`. We capture from the response listener.
                try:
                    page.goto(target_url, wait_until="domcontentloaded", timeout=wait_ms + 5000)
                except Exception:
                    pass
                # Pricing data is filled in incrementally — wait for richer results.
                page.wait_for_timeout(wait_ms)

                # Fallback: read the JSON straight from the rendered <pre>.
                if not captured:
                    try:
                        body_text = page.evaluate("document.body.innerText")
                        if body_text and body_text.strip().startswith("{"):
                            data = json.loads(body_text)
                            if _looks_like_results(data):
                                captured.append(data)
                    except Exception:
                        pass
            finally:
                browser.close()
    except Exception as e:
        _debug_dump({"error": str(e), "target_url": target_url}, from_, to, date_, "playwright_err")
        return None

    if not captured:
        return None

    best = max(captured, key=lambda d: len(json.dumps(d, default=str)))
    _debug_dump(best, from_, to, date_, "playwright")
    return best


def fetch_skiplagged(
    from_: str,
    to: str,
    date_: str,
    adults: int = 1,
) -> Optional[dict]:
    """Cascade: try direct HTTP first (usually blocked), fall back to Playwright."""
    data = fetch_via_httpx(from_, to, date_, adults=adults)
    if data is not None:
        return data
    return fetch_via_playwright(from_, to, date_, adults=adults)
