"""Tests for api/rate_limiter_display.py — visual display for rate limiter status.

No external boundaries — uses a stub RateLimiter with controllable get_stats().
"""

import asyncio
import time
from types import SimpleNamespace

import pytest

from api.rate_limiter_display import RateLimiterDisplay


def _make_limiter(stats_sequence):
    """Build a stub RateLimiter that returns stats from a sequence.

    After the sequence is exhausted, returns disabled stats.
    """
    it = iter(stats_sequence)

    def get_stats():
        try:
            return next(it)
        except StopIteration:
            return {"enabled": False}

    return SimpleNamespace(get_stats=get_stats)


def _stats(
    *,
    enabled=True,
    is_in_backoff=False,
    current_backoff_seconds=0,
    backoff_remaining=0,
    utilization_percent=0,
    burst_size=100,
    available_tokens=100,
):
    return {
        "enabled": enabled,
        "is_in_backoff": is_in_backoff,
        "current_backoff_seconds": current_backoff_seconds,
        "backoff_remaining": backoff_remaining,
        "utilization_percent": utilization_percent,
        "burst_size": burst_size,
        "available_tokens": available_tokens,
    }


class TestRateLimiterDisplayLifecycle:
    """Lines 64-82, 185-204: start/stop, context manager, idempotent start."""

    def test_start_stop_and_context_manager(self):
        """Lines 64-82, 185-204: start → thread alive, stop → joined.
        Context manager calls start on enter, stop on exit.
        Nested context increments/decrements depth.
        """
        limiter = _make_limiter([_stats(enabled=False)])
        display = RateLimiterDisplay(limiter, update_interval=0.05)

        # Context manager — start on enter, stop on exit
        with display:
            assert display._display_thread is not None
            assert display._display_thread.is_alive()
            assert display._context_depth == 1

            # Nested entry — depth increments, no new thread
            with display:
                assert display._context_depth == 2

            # Exited inner — depth decremented but still running
            assert display._context_depth == 1
            assert display._display_thread.is_alive()

        # Exited outer — stopped
        assert display._context_depth == 0
        assert display._display_thread is None

    def test_start_idempotent(self):
        """Line 67: calling start() when already running is a no-op."""
        limiter = _make_limiter([_stats(enabled=False)] * 20)
        display = RateLimiterDisplay(limiter, update_interval=0.05)

        display.start()
        thread1 = display._display_thread
        display.start()  # second call — should be no-op
        assert display._display_thread is thread1
        display.stop()


class TestRateLimiterDisplayUpdate:
    """Lines 86-180: display loop, backoff/token tasks, cleanup."""

    def test_backoff_shown_then_removed(self):
        """Lines 118-141: backoff active → add task, backoff ends → remove task."""
        limiter = _make_limiter(
            [
                _stats(
                    is_in_backoff=True, current_backoff_seconds=10, backoff_remaining=5
                ),
                _stats(
                    is_in_backoff=True, current_backoff_seconds=10, backoff_remaining=2
                ),
                _stats(is_in_backoff=False),  # backoff ended → remove
                _stats(enabled=False),  # display loop exits
            ]
        )
        display = RateLimiterDisplay(limiter, update_interval=0.05)

        with display:
            time.sleep(0.4)

        assert display._backoff_active is False

    def test_tokens_shown_then_removed(self):
        """Lines 153-171: utilization ≥80% → add token task, drops below → remove."""
        limiter = _make_limiter(
            [
                _stats(utilization_percent=90, burst_size=100, available_tokens=10),
                _stats(utilization_percent=95, burst_size=100, available_tokens=5),
                _stats(
                    utilization_percent=50, burst_size=100, available_tokens=50
                ),  # below 80 → remove
                _stats(enabled=False),
            ]
        )
        display = RateLimiterDisplay(limiter, update_interval=0.05)

        with display:
            time.sleep(0.4)

        assert display._tokens_active is False

    def test_not_enabled_triggers_cleanup(self):
        """Lines 106-107: stats not enabled → cleanup any active tasks."""
        limiter = _make_limiter(
            [
                _stats(
                    is_in_backoff=True, current_backoff_seconds=5, backoff_remaining=3
                ),
                _stats(enabled=False),  # immediately cleanup
            ]
        )
        display = RateLimiterDisplay(limiter, update_interval=0.05)

        with display:
            time.sleep(0.3)

        assert display._backoff_active is False

    def test_display_loop_exception_breaks(self):
        """Lines 94-95: exception during _update → break out of loop."""

        def exploding_stats():
            raise RuntimeError("stats error")

        limiter = SimpleNamespace(get_stats=exploding_stats)
        display = RateLimiterDisplay(limiter, update_interval=0.05)

        with display:
            time.sleep(0.2)
        # Should have stopped cleanly despite exception


class TestRateLimiterDisplayDecorator:
    """Lines 214-236: __call__ as decorator for sync and async functions."""

    def test_sync_decorator(self):
        """Lines 229-236: sync function decorated → runs inside context."""
        limiter = _make_limiter([_stats(enabled=False)] * 10)
        display = RateLimiterDisplay(limiter, update_interval=0.05)

        @display
        def my_func(x):
            return x * 2

        result = my_func(21)
        assert result == 42

    @pytest.mark.asyncio
    async def test_async_decorator(self):
        """Lines 219-227: async function decorated → runs inside context."""
        limiter = _make_limiter([_stats(enabled=False)] * 10)
        display = RateLimiterDisplay(limiter, update_interval=0.05)

        @display
        async def my_async_func(x):
            await asyncio.sleep(0)
            return x + 1

        result = await my_async_func(41)
        assert result == 42
