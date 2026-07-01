"""Teto GLOBAL de navegadores Playwright/Chromium simultâneos.

Cada Chromium consome muita RAM (~200-250MB) e MUITAS threads/processos. Sob
carga (busca multi-data × pernas), AwardTool + Kayak + Skiplagged lançavam
navegadores em paralelo sem teto comum → o container esgotava thread/RAM e o
backend caía com `RuntimeError: can't start new thread`.

Este semáforo é ÚNICO no processo: TODO ponto que lança Chromium adquire um slot
antes do `sync_playwright()`. Assim o nº total de navegadores vivos ao mesmo
tempo nunca passa de `PLAYWRIGHT_MAX_BROWSERS` (default 2), independente de
quantas buscas/provedores rodem.

Uso:
    from backend.app.infrastructure.browser import browser_slot
    with browser_slot(), sync_playwright() as p:
        ...

Se não houver slot dentro do timeout, levanta `BrowserBusy` — os adapters já
absorvem exceção e degradam pra [] (melhor que derrubar o processo).
"""
from __future__ import annotations

import os
import threading
from contextlib import contextmanager
from typing import Iterator


class BrowserBusy(RuntimeError):
    """Sem slot de navegador disponível dentro do timeout."""


def _max_browsers() -> int:
    try:
        return max(1, int(os.getenv("PLAYWRIGHT_MAX_BROWSERS", "2")))
    except ValueError:
        return 2


_SEM = threading.BoundedSemaphore(_max_browsers())
# Espera máx. por um slot. Curto o bastante pra não segurar thread eternamente
# (o orçamento de busca abandona o adapter antes); longo o bastante pra um
# navegador em uso terminar e liberar. Override via env.
_ACQUIRE_TIMEOUT_S = float(os.getenv("PLAYWRIGHT_SLOT_TIMEOUT_S", "100"))


@contextmanager
def browser_slot(timeout: float | None = None) -> Iterator[None]:
    """Segura um slot global de navegador pelo tempo de vida do bloco."""
    got = _SEM.acquire(timeout=timeout if timeout is not None else _ACQUIRE_TIMEOUT_S)
    if not got:
        raise BrowserBusy("sem slot de navegador (teto PLAYWRIGHT_MAX_BROWSERS atingido)")
    try:
        yield
    finally:
        try:
            _SEM.release()
        except ValueError:
            pass  # release a mais (defensivo) — ignora
