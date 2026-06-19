"""In-memory TTL cache (single-process, thread-safe) for provider HTTP calls.

**Pricing is volatile** — fares can change in minutes. To avoid quoting a
stale price to a customer, this cache uses **short, differentiated TTLs**:

  - Cash sources (Kayak, Skiplagged):  90 s
  - Miles sources (BuscaMilhas, MCP, Economilhas, Award): 180 s
  - Static reference data (FX rates, etc.): 6 h (configured at call site)

The TTL can be overridden per call via `cached_call(..., ttl_seconds=N)`,
and the whole cache can be bypassed for a given search by calling
`invalidate(prefix=...)` before the search. Use `CACHE_DISABLED=1` to
turn caching off entirely (regression/debug mode).

Each stored value is timestamped, so callers can also ask `age(key)` to
display "consulted N seconds ago" in the UI.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from typing import Any, Callable, Optional

# Default TTLs (seconds) — env-tunable.
DEFAULT_TTL_S = int(os.getenv("CACHE_DEFAULT_TTL_S", "120"))
CASH_TTL_S = int(os.getenv("CACHE_CASH_TTL_S", "90"))
MILES_TTL_S = int(os.getenv("CACHE_MILES_TTL_S", "180"))
DISABLED = os.getenv("CACHE_DISABLED", "0") not in ("0", "false", "False", "")

# Prefix → TTL mapping. Anything not listed falls back to DEFAULT_TTL_S.
_TTL_BY_PREFIX: dict[str, int] = {
    "kayak":             CASH_TTL_S,
    "skiplagged":        CASH_TTL_S,
    "buscamilhas":       MILES_TTL_S,
    "economilhas":       MILES_TTL_S,
    "seats_aero":        MILES_TTL_S,
    "mcp_award":         MILES_TTL_S,
    "fx_rates":          int(os.getenv("CACHE_FX_TTL_S", "21600")),  # 6h
}

_lock = threading.Lock()
# Each entry: (timestamp, value, ttl_seconds)
_store: dict[str, tuple[float, Any, int]] = {}
_stats = {"hits": 0, "misses": 0, "sets": 0, "expired": 0}


def _ttl_for(prefix: str) -> int:
    return _TTL_BY_PREFIX.get(prefix, DEFAULT_TTL_S)


def make_key(prefix: str, params: dict) -> str:
    """Stable key from `prefix` + serialized `params`. MD5 truncated to 12 hex."""
    try:
        params_str = json.dumps(params, sort_keys=True, default=str)
    except TypeError:
        params_str = repr(sorted(params.items()))
    h = hashlib.md5(params_str.encode("utf-8")).hexdigest()[:12]
    return f"{prefix}:{h}"


def get(key: str) -> Optional[Any]:
    """Returns the cached value if still within its TTL, else None."""
    if DISABLED:
        return None
    now = time.time()
    with _lock:
        item = _store.get(key)
        if item is None:
            _stats["misses"] += 1
            return None
        ts, value, ttl = item
        if now - ts >= ttl:
            _store.pop(key, None)
            _stats["misses"] += 1
            _stats["expired"] += 1
            return None
        _stats["hits"] += 1
        return value


def set_(key: str, value: Any, ttl_seconds: Optional[int] = None) -> None:
    """Stores `value` with a per-entry TTL. Defaults to the prefix-aware TTL."""
    if DISABLED:
        return
    prefix = key.split(":", 1)[0] if ":" in key else ""
    ttl = ttl_seconds if ttl_seconds is not None else _ttl_for(prefix)
    with _lock:
        _store[key] = (time.time(), value, ttl)
        _stats["sets"] += 1


def age(key: str) -> Optional[float]:
    """Seconds since the entry was stored. None if absent/expired."""
    with _lock:
        item = _store.get(key)
        if item is None:
            return None
        ts, _, ttl = item
        elapsed = time.time() - ts
        if elapsed >= ttl:
            return None
        return elapsed


def cached_call(
    prefix: str,
    params: dict,
    fn: Callable[..., Any],
    *args: Any,
    ttl_seconds: Optional[int] = None,
    force_refresh: bool = False,
    **kwargs: Any,
) -> Any:
    """Wrap a producer function with cache.

    Usage:
        return cached_call("kayak", {"o": "GRU", "d": "MIA"}, _do_search)

    Pass `ttl_seconds=N` to override the prefix-default. Pass `force_refresh=True`
    to skip the cache lookup but still store the result for subsequent calls.
    """
    key = make_key(prefix, params)
    if not force_refresh:
        hit = get(key)
        if hit is not None:
            return hit
    result = fn(*args, **kwargs)
    set_(key, result, ttl_seconds=ttl_seconds)
    return result


def invalidate(prefix: Optional[str] = None) -> int:
    """Removes entries whose key starts with `<prefix>:`. None → clear all.
    Returns the number of entries removed."""
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
    """Snapshot of hit/miss counters + current size + TTL config."""
    with _lock:
        total = _stats["hits"] + _stats["misses"]
        hit_rate = (_stats["hits"] / total) if total else 0.0
        return {
            "hits": _stats["hits"],
            "misses": _stats["misses"],
            "sets": _stats["sets"],
            "expired": _stats["expired"],
            "hit_rate": round(hit_rate, 3),
            "entries": len(_store),
            "disabled": DISABLED,
            "ttls": {**_TTL_BY_PREFIX, "_default": DEFAULT_TTL_S},
        }


# Per-provider concurrency limits — independent of caching.
SEM_KAYAK = threading.BoundedSemaphore(5)
SEM_BUSCAMILHAS = threading.BoundedSemaphore(3)
SEM_ECONOMILHAS = threading.BoundedSemaphore(5)
# seats.aero: quota Pro baixa (1000/dia/key). Serializa para não esgotar quota
# em buscas com flex de datas (cada data = 1 /search + N /trips).
SEM_SEATS_AERO = threading.BoundedSemaphore(int(os.getenv("SEATS_AERO_MAX_CONCURRENCY", "3")))
