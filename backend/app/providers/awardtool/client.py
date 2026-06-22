"""Client Playwright do AwardTool — dirige a conta Pro própria pra coletar award.

Fluxo:
  1. Login Cognito por formulário (/signin → "Login with Password" → Amplify),
     persistindo a sessão em storage_state (reusa entre buscas).
  2. Busca: abre a home com os params na URL (pré-preenche o form), clica
     "Search" (MuiButton-contained), que dispara o crawl em tempo real.
  3. Intercepta /search_result_v2, faz POLLING decodificando o envelope até
     `finish`/`has_next=false`, acumulando `result[]`.

Decode dos resultados via cipher.decode_v3. Sem credencial → levanta
AwardToolAuthError (o adapter trata e devolve []).

ATENÇÃO: ToS proíbe automação → uso gentil + cache (risco de ban). Concorrência
serializada por SEM_AWARDTOOL.
"""
from __future__ import annotations

import os
import threading
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from backend.app.providers.awardtool.cipher import decode_v3

# Serializa o uso do browser (1 sessão, evita ban / corrida no storage_state).
SEM_AWARDTOOL = threading.BoundedSemaphore(int(os.getenv("AWARDTOOL_MAX_CONCURRENCY", "1")))

_BASE = "https://www.awardtool.com"
_STATE_PATH = os.getenv("AWARDTOOL_STATE_PATH", "debug/awardtool_state.json")
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")


class AwardToolError(RuntimeError):
    """Falha genérica do AwardTool."""


class AwardToolAuthError(AwardToolError):
    """Credenciais ausentes/ inválidas."""


def _creds() -> tuple[str, str]:
    email = os.getenv("AWARDTOOL_EMAIL", "").strip()
    pw = os.getenv("AWARDTOOL_PASSWORD", "").strip()
    if not email or not pw:
        raise AwardToolAuthError("AWARDTOOL_EMAIL/PASSWORD ausentes")
    return email, pw


def _ts(d: date) -> int:
    return int(datetime(d.year, d.month, d.day).timestamp())


def _headless() -> bool:
    return os.getenv("AWARDTOOL_HEADFUL", "0") in ("0", "false", "False", "")


def _login(p, email: str, pw: str) -> None:
    """Loga e persiste storage_state em _STATE_PATH."""
    browser = p.chromium.launch(headless=_headless())
    ctx = browser.new_context(user_agent=_UA, locale="pt-BR",
                              viewport={"width": 1366, "height": 850})
    try:
        page = ctx.new_page()
        page.goto(f"{_BASE}/signin", wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(2000)
        page.click("button:has-text('Login with Password')")
        page.wait_for_selector("input[type=password]", timeout=15000)
        page.fill("input[type=email], input[placeholder='Email']", email)
        page.fill("input[type=password]", pw)
        page.locator("button[type=submit]").first.click(timeout=8000)
        # espera token Cognito no localStorage
        for _ in range(30):
            page.wait_for_timeout(1000)
            keys = page.evaluate("() => Object.keys(localStorage)")
            if any("idToken" in k for k in keys):
                break
        else:
            raise AwardToolAuthError("login não confirmou sessão (sem idToken)")
        Path(_STATE_PATH).parent.mkdir(parents=True, exist_ok=True)
        ctx.storage_state(path=_STATE_PATH)
    finally:
        ctx.close(); browser.close()


def _search_url(origin: str, destination: str, ds: date, de: date,
                programs: List[str], cabin: str) -> str:
    from urllib.parse import quote
    # cabins múltiplas vão separadas por '&' DENTRO do valor (encode p/ %26)
    cabins = quote(cabin, safe="")
    progs = quote(",".join(programs), safe="")
    return (
        f"{_BASE}/?flightWay=oneway&pax=1&children=0"
        f"&cabins={cabins}&range=true&rangeV2=false"
        f"&from={origin.upper()}&to={destination.upper()}"
        f"&programs={progs}&targetId="
        f"&oneWayRangeStartDate={_ts(ds)}&oneWayRangeEndDate={_ts(de)}"
    )


def _run_search(p, url: str, budget_s: float) -> List[Dict[str, Any]]:
    """Abre o form, clica Search, faz polling e devolve result[] acumulado."""
    browser = p.chromium.launch(headless=_headless())
    ctx = browser.new_context(storage_state=_STATE_PATH, user_agent=_UA,
                              locale="pt-BR", viewport={"width": 1366, "height": 900})
    captured: List[str] = []
    finished = {"done": False}

    def on_response(resp):
        if "search_result_v2" in resp.url and resp.request.method == "POST":
            try:
                body = resp.text()
                if "ciphered_data_v3" in body:
                    import json as _json
                    captured.append(_json.loads(body)["ciphered_data_v3"])
            except Exception:
                pass

    ctx.on("response", on_response)

    def _enable_realtime(pg):
        """Ativa 'Real-time Search' — sem isso o AwardTool só faz lookup em
        cache (retorna vazio rápido); com isso dispara o crawl ao vivo."""
        try:
            rt = pg.locator("text=/Real-?time Search/i")
            if rt.count() > 0:
                rt.first.click(timeout=3000)
        except Exception:
            pass

    try:
        page = ctx.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(2500)
        _enable_realtime(page)              # liga real-time na home
        page.wait_for_timeout(600)
        # Clica o submit AZUL (contained); pode abrir nova aba (/flight).
        result_page = page
        try:
            with ctx.expect_page(timeout=8000) as pop:
                page.locator("button.MuiButton-contained", has_text="Search").first.click(timeout=8000)
            result_page = pop.value
            result_page.wait_for_timeout(2000)
            _enable_realtime(result_page)   # garante real-time na aba de resultados
        except Exception:
            # sem popup (o clique já disparou dentro do `with`): roda na mesma aba
            result_page = page

        page = result_page
        merged: Dict[str, Dict[str, Any]] = {}
        waited = 0.0
        seen = 0
        while waited < budget_s:
            page.wait_for_timeout(1000)
            waited += 1.0
            # decodifica o que chegou de novo e checa o envelope
            while seen < len(captured):
                try:
                    env = decode_v3(captured[seen])
                except Exception:
                    env = None
                seen += 1
                if isinstance(env, dict):
                    for it in env.get("result") or []:
                        if isinstance(it, dict) and it.get("id"):
                            merged[it["id"]] = it
                    if env.get("finish") or env.get("has_next") is False:
                        finished["done"] = True
            if finished["done"] and seen >= len(captured):
                break
        return list(merged.values())
    finally:
        ctx.close(); browser.close()


def search_awardtool(
    origin: str,
    destination: str,
    date_start: date,
    date_end: date,
    *,
    programs: Optional[List[str]] = None,
    cabin: str = "Economy",
    budget_s: Optional[float] = None,
) -> List[Dict[str, Any]]:
    """Busca award no AwardTool. Devolve a lista crua de `result[]` (itens de
    voo) — o parser converte em UnifiedOffer. Faz login se necessário."""
    from playwright.sync_api import sync_playwright

    email, pw = _creds()
    programs = programs or []
    budget = budget_s if budget_s is not None else float(os.getenv("AWARDTOOL_BUDGET_S", "60"))
    url = _search_url(origin, destination, date_start, date_end, programs, cabin)

    with SEM_AWARDTOOL, sync_playwright() as p:
        if not Path(_STATE_PATH).exists():
            _login(p, email, pw)
        try:
            return _run_search(p, url, budget)
        except Exception:
            # sessão pode ter expirado → relog uma vez
            _login(p, email, pw)
            return _run_search(p, url, budget)
