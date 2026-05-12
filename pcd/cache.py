"""
pcd/cache.py
------------
Cache em memória com TTL e thread-safety para os clientes HTTP do app
(Kayak, BuscaMilhas, Economilhas). Single-process; o Streamlit Cloud reinicia
a cada deploy e isso é suficiente para o caso de uso (vendedor olhando
mesma rota repetidamente em ±10min).

Por que TTL de 10 min:
  - Tarifas Kayak mudam em minutos, mas dentro do mesmo fluxo o vendedor
    geralmente repete a busca para comparar com outras companhias. 10 min
    cobre essa janela sem servir dado estoacado.
  - O cache é invalidado explicitamente quando o vendedor faz uma nova busca
    (rota/data principal mudou) via `invalidate(prefix=...)` no Streamlit.

Threadsafe via `threading.Lock` interno — todas as chamadas concorrentes
dos pools paralelos passam pelo mesmo dict.
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
from typing import Any, Callable, Optional

_CACHE_TTL_S = 600
_lock = threading.Lock()
_store: dict[str, tuple[float, Any]] = {}

# Telemetria leve — útil em TEMP_PERF para ver hit-rate.
_stats = {"hits": 0, "misses": 0, "sets": 0}


def make_key(prefix: str, params: dict) -> str:
    """Chave estável a partir de um prefixo (cliente) + dict de parâmetros.
    Usa md5 truncado em 12 chars — chance de colisão desprezível para os
    volumes deste app."""
    try:
        params_str = json.dumps(params, sort_keys=True, default=str)
    except TypeError:
        params_str = repr(sorted(params.items()))
    h = hashlib.md5(params_str.encode("utf-8")).hexdigest()[:12]
    return f"{prefix}:{h}"


def get(key: str) -> Optional[Any]:
    """Devolve o valor cacheado se ainda dentro do TTL, senão None.
    Limpa entradas expiradas de forma preguiçosa quando esbarra nelas."""
    now = time.time()
    with _lock:
        item = _store.get(key)
        if item is None:
            _stats["misses"] += 1
            return None
        ts, value = item
        if now - ts >= _CACHE_TTL_S:
            _store.pop(key, None)
            _stats["misses"] += 1
            return None
        _stats["hits"] += 1
        return value


def set_(key: str, value: Any) -> None:
    """Grava `value` com timestamp atual. Não tenta ser LRU — o cache nunca
    cresce muito (no máx ~poucas centenas de entradas por sessão)."""
    with _lock:
        _store[key] = (time.time(), value)
        _stats["sets"] += 1


def cached_call(
    prefix: str,
    params: dict,
    fn: Callable[..., Any],
    *args: Any,
    **kwargs: Any,
) -> Any:
    """Atalho que combina get/set com a função produtora.

    Uso típico:
        return cached_call("kayak", {"o": "GRU", "d": "MIA", "date": "..."},
                           _do_kayak_search, payload)

    `params` é a chave lógica — não inclui sentinelas de runtime (timeouts,
    objetos de sessão, etc.) que tornariam a chave inútil."""
    key = make_key(prefix, params)
    hit = get(key)
    if hit is not None:
        return hit
    result = fn(*args, **kwargs)
    set_(key, result)
    return result


def invalidate(prefix: Optional[str] = None) -> int:
    """Limpa entradas cujo prefixo bate com `prefix`. Se `prefix` for None,
    limpa tudo. Devolve o número de entradas removidas — útil para logs."""
    with _lock:
        if prefix is None:
            n = len(_store)
            _store.clear()
            return n
        to_drop = [k for k in _store if k.startswith(f"{prefix}:")]
        for k in to_drop:
            _store.pop(k, None)
        return len(to_drop)


def stats() -> dict:
    """Snapshot dos contadores — usado pelos logs TEMP_PERF."""
    with _lock:
        total = _stats["hits"] + _stats["misses"]
        hit_rate = (_stats["hits"] / total) if total else 0.0
        return {
            "hits": _stats["hits"],
            "misses": _stats["misses"],
            "sets": _stats["sets"],
            "hit_rate": round(hit_rate, 3),
            "entries": len(_store),
        }


# ──────────────────────────────────────────────────────────────────
# Semáforos por cliente — controla concorrência cross-call por
# provedor, independente de quantos pools paralelos estejam disparando.
# ──────────────────────────────────────────────────────────────────
SEM_KAYAK = threading.BoundedSemaphore(5)
SEM_BUSCAMILHAS = threading.BoundedSemaphore(3)
SEM_ECONOMILHAS = threading.BoundedSemaphore(5)
