"""Cache TTL + freshness tests.

The cache is the single biggest pricing-correctness risk: a stale entry can
cause us to quote yesterday's fare to today's buyer. These tests pin down
the behaviors that protect us.
"""
import importlib
import time

import pytest


@pytest.fixture
def cache(monkeypatch):
    """Fresh cache module per test (resets env-driven config + counters)."""
    monkeypatch.delenv("CACHE_DISABLED", raising=False)
    from backend.app.infrastructure import cache as cache_mod
    importlib.reload(cache_mod)
    cache_mod.invalidate()
    return cache_mod


def test_default_ttls_are_short(cache):
    s = cache.stats()
    # Cash TTL must stay short — fares change in minutes.
    assert s["ttls"]["kayak"] <= 120
    assert s["ttls"]["skiplagged"] <= 120
    # Miles can be slightly longer (availability changes more slowly than price).
    assert s["ttls"]["buscamilhas"] <= 300
    assert s["ttls"]["mcp_award"] <= 300


def test_hit_within_ttl(cache):
    cache.set_("kayak:abc", {"price": 1000})
    assert cache.get("kayak:abc") == {"price": 1000}


def test_miss_after_ttl_expires(cache, monkeypatch):
    """Entry must be evicted once age exceeds its TTL."""
    cache.set_("kayak:abc", {"price": 1000}, ttl_seconds=1)
    time.sleep(1.1)
    assert cache.get("kayak:abc") is None


def test_force_refresh_bypasses_cache_but_stores_result(cache):
    calls = {"n": 0}

    def producer():
        calls["n"] += 1
        return {"v": calls["n"]}

    # First call: miss, stored.
    cache.cached_call("kayak", {"k": 1}, producer)
    assert calls["n"] == 1

    # Second call without refresh: served from cache.
    cache.cached_call("kayak", {"k": 1}, producer)
    assert calls["n"] == 1

    # Third call with force_refresh: producer runs again, result replaces stored.
    result = cache.cached_call("kayak", {"k": 1}, producer, force_refresh=True)
    assert calls["n"] == 2
    assert result == {"v": 2}


def test_invalidate_clears_only_matching_prefix(cache):
    cache.set_("kayak:a", 1)
    cache.set_("buscamilhas:b", 2)
    dropped = cache.invalidate("kayak")
    assert dropped == 1
    assert cache.get("kayak:a") is None
    assert cache.get("buscamilhas:b") == 2


def test_cache_disabled_env_skips_storage(monkeypatch):
    monkeypatch.setenv("CACHE_DISABLED", "1")
    from backend.app.infrastructure import cache as cache_mod
    importlib.reload(cache_mod)
    cache_mod.set_("kayak:abc", {"price": 1000})
    assert cache_mod.get("kayak:abc") is None


def test_per_call_ttl_overrides_prefix_default(cache):
    cache.set_("kayak:abc", "fast", ttl_seconds=0)
    # ttl 0 should immediately make it stale on the next get
    time.sleep(0.05)
    assert cache.get("kayak:abc") is None


def test_age_reports_seconds_for_live_entry(cache):
    cache.set_("kayak:abc", "x", ttl_seconds=10)
    age = cache.age("kayak:abc")
    assert age is not None
    assert 0 <= age < 10


def test_age_returns_none_for_expired_or_missing(cache):
    assert cache.age("nope:nope") is None
    cache.set_("kayak:expired", "x", ttl_seconds=0)
    time.sleep(0.05)
    assert cache.age("kayak:expired") is None
