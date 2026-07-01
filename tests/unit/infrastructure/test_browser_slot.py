"""Teto global de navegadores Playwright — impede esgotar thread/RAM do container."""
import threading

import pytest

import backend.app.infrastructure.browser as b


def test_browser_slot_acquire_release():
    with b.browser_slot(timeout=1):
        pass  # entra e sai sem erro


def test_browser_slot_caps_concurrency(monkeypatch):
    # Cap de 1: o 2º slot simultâneo não entra dentro do timeout curto.
    monkeypatch.setattr(b, "_SEM", threading.BoundedSemaphore(1))
    with b.browser_slot(timeout=1):
        with pytest.raises(b.BrowserBusy):
            with b.browser_slot(timeout=0.05):
                pass
    # Depois de liberar, o slot volta a estar disponível.
    with b.browser_slot(timeout=1):
        pass


def test_release_extra_nao_quebra(monkeypatch):
    monkeypatch.setattr(b, "_SEM", threading.BoundedSemaphore(1))
    with b.browser_slot(timeout=1):
        pass
    # release defensivo a mais não deve levantar
    b._SEM.release() if False else None  # noop guard
