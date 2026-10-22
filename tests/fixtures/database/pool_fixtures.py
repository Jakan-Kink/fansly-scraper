"""Spy on asyncpg pool creation; the real ``asyncpg.create_pool`` still runs."""

from __future__ import annotations

from typing import Any

import asyncpg
import pytest


@pytest.fixture
def asyncpg_create_pool_spy(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Record the kwargs of every ``asyncpg.create_pool`` call, then make the real call."""
    calls: list[dict[str, Any]] = []
    real_create_pool = asyncpg.create_pool

    def create_pool(*args: Any, **kwargs: Any) -> asyncpg.Pool:
        calls.append(kwargs)
        return real_create_pool(*args, **kwargs)

    monkeypatch.setattr(asyncpg, "create_pool", create_pool)
    return calls
