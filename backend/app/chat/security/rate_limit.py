"""Rate limit por usuário, em memória, com janela deslizante simples.

Não substitui rate-limit no edge (recomendado: Cloudflare/Railway proxy).
É a segunda linha — protege contra abuso de usuário autenticado mesmo se
o atacante passou pela primeira.

Limites configuráveis em `settings`:
- `rate_limit_per_minute` mensagens/min (total no chat).
- `rate_limit_searches_per_hour` buscas reais (chamadas no /search).

Buscar é caro (ThreadPoolExecutor + N providers); proteger esse limite
é o mais importante.
"""
from __future__ import annotations

import threading
import time
from collections import deque
from functools import lru_cache
from typing import Deque, Dict, Tuple

from backend.app.chat.config import settings


class RateLimitExceeded(Exception):
    def __init__(self, kind: str, retry_in_s: float):
        super().__init__(f"Rate limit excedido para {kind}. Tente em {retry_in_s:.0f}s.")
        self.kind = kind
        self.retry_in_s = retry_in_s


class RateLimiter:
    """Sliding-window in-memory. Limites por (user_id, kind)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # (user_id, kind) → deque[timestamps]
        self._hits: Dict[Tuple[str, str], Deque[float]] = {}

    def _check(self, user_id: str, kind: str, *, max_hits: int, window_s: float) -> None:
        now = time.monotonic()
        key = (user_id, kind)
        with self._lock:
            dq = self._hits.setdefault(key, deque())
            # Expira hits velhos
            while dq and (now - dq[0]) > window_s:
                dq.popleft()
            if len(dq) >= max_hits:
                retry_in = window_s - (now - dq[0])
                raise RateLimitExceeded(kind=kind, retry_in_s=max(retry_in, 1.0))
            dq.append(now)

    def check_message(self, user_id: str) -> None:
        self._check(
            user_id,
            "message",
            max_hits=settings.rate_limit_per_minute,
            window_s=60.0,
        )

    def check_search(self, user_id: str) -> None:
        self._check(
            user_id,
            "search",
            max_hits=settings.rate_limit_searches_per_hour,
            window_s=3600.0,
        )


@lru_cache(maxsize=1)
def get_rate_limiter() -> RateLimiter:
    return RateLimiter()
