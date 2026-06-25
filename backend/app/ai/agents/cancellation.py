"""Cancelamento cooperativo de cotação, por thread.

O usuário clica "Interromper" → a rota marca a thread como cancelada. O loop de
fan-out de adapters (o ponto mais caro, onde mora a espera de ~90s) checa o
sinal e para cedo, em vez de esperar todos os provedores.

Mecânica:
- `request_cancel/is_cancelled/clear_cancel`: registro in-memory por thread_id
  (lock). Por processo — multi-réplica exigiria redis (anotado).
- `current_thread` (ContextVar): o `orchestrator_node` seta o thread_id atual no
  início; o loop de busca (que roda NO MESMO thread/contexto, síncrono) lê via
  `should_cancel_current()` sem precisar passar parâmetro por toda a cadeia.
"""
from __future__ import annotations

import threading
from contextvars import ContextVar
from typing import Optional

_LOCK = threading.Lock()
_CANCELLED: set[str] = set()

# thread_id da cotação em execução no contexto atual (setado no orchestrator_node).
current_thread: ContextVar[Optional[str]] = ContextVar("cotacao_thread_id", default=None)


def request_cancel(thread_id: str) -> None:
    if not thread_id:
        return
    with _LOCK:
        _CANCELLED.add(thread_id)


def is_cancelled(thread_id: Optional[str]) -> bool:
    if not thread_id:
        return False
    with _LOCK:
        return thread_id in _CANCELLED


def clear_cancel(thread_id: Optional[str]) -> None:
    if not thread_id:
        return
    with _LOCK:
        _CANCELLED.discard(thread_id)


def should_cancel_current() -> bool:
    """True se a cotação do contexto atual foi cancelada. Chamável de qualquer
    ponto que rode no mesmo thread do `orchestrator_node` (ex.: o loop de busca)."""
    return is_cancelled(current_thread.get())
