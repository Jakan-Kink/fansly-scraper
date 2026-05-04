"""Controllable monotonic-clock fixtures for cache TTL / expiry tests."""

from __future__ import annotations

import pytest


@pytest.fixture
def fake_monotonic_clock(monkeypatch):
    """Patch ``metadata.entity_store.time.monotonic`` with a controllable clock.

    Yields a single-key dict ``{"now": float}`` — tests advance time by
    mutating ``state["now"] += seconds``. Avoids real ``sleep`` in TTL
    tests so they're fast and deterministic.
    """
    state = {"now": 1000.0}

    def fake_monotonic() -> float:
        return state["now"]

    monkeypatch.setattr("metadata.entity_store.time.monotonic", fake_monotonic)
    return state
