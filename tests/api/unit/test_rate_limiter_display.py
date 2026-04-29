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


class _StubProgress:
    """Records every progress-manager call without doing any rendering.

    Used to test RateLimiterDisplay's update methods directly (no background
    thread, no timing race) so coverage of the state-machine branches is
    deterministic.
    """

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def add_task(self, **kwargs) -> None:
        self.calls.append(("add", kwargs))

    def update_task(self, name, **kwargs) -> None:
        self.calls.append(("update", name, kwargs))

    def remove_task(self, name) -> None:
        self.calls.append(("remove", name))


class TestRateLimiterDisplayDirectMethods:
    """Direct-call tests of _update / _update_backoff / _update_tokens / _cleanup.

    Bypasses the threaded display loop so each branch is exercised
    deterministically (the existing TestRateLimiterDisplayUpdate tests reach
    these branches via the polling thread but coverage is flaky because the
    loop relies on tight timing — 0.4s/0.05s interval = 8 ticks).
    """

    def _make_display(self):
        limiter = _make_limiter([_stats(enabled=False)])
        return RateLimiterDisplay(limiter, update_interval=0.05)

    # ── _cleanup (lines 173-180) ────────────────────────────────────

    def test_cleanup_no_active_tasks_is_noop(self):
        """No active tasks → _cleanup makes zero progress calls."""
        display = self._make_display()
        progress = _StubProgress()

        display._cleanup(progress)

        assert progress.calls == []
        assert display._backoff_active is False
        assert display._tokens_active is False

    def test_cleanup_only_backoff_active_removes_just_backoff(self):
        """Lines 175-177: _backoff_active=True → remove backoff task only."""
        display = self._make_display()
        display._backoff_active = True
        progress = _StubProgress()

        display._cleanup(progress)

        assert progress.calls == [("remove", "rate_limit_backoff")]
        assert display._backoff_active is False
        assert display._tokens_active is False

    def test_cleanup_only_tokens_active_removes_just_tokens(self):
        """Lines 178-180: _tokens_active=True → remove token task only."""
        display = self._make_display()
        display._tokens_active = True
        progress = _StubProgress()

        display._cleanup(progress)

        assert progress.calls == [("remove", "rate_limit_tokens")]
        assert display._tokens_active is False

    def test_cleanup_both_active_removes_both(self):
        """Lines 175-180: both flags True → both tasks removed, both flags reset."""
        display = self._make_display()
        display._backoff_active = True
        display._tokens_active = True
        progress = _StubProgress()

        display._cleanup(progress)

        assert ("remove", "rate_limit_backoff") in progress.calls
        assert ("remove", "rate_limit_tokens") in progress.calls
        assert display._backoff_active is False
        assert display._tokens_active is False

    # ── _update_backoff (lines 112-141) ─────────────────────────────

    def test_update_backoff_first_entry_adds_and_updates(self):
        """Lines 123-130: first time in backoff → add_task + set _backoff_active.
        Lines 132-138: also issues an update_task on the same call.
        """
        display = self._make_display()
        progress = _StubProgress()
        stats = _stats(
            is_in_backoff=True,
            current_backoff_seconds=10,
            backoff_remaining=4,
        )

        display._update_backoff(progress, stats)

        # First call adds the task and immediately updates.
        ops = [c[0] for c in progress.calls]
        assert ops == ["add", "update"]
        add_kwargs = progress.calls[0][1]
        assert add_kwargs["name"] == "rate_limit_backoff"
        assert add_kwargs["total"] == 10
        # Update sets completed = total - remaining = 10 - 4 = 6.
        update_kwargs = progress.calls[1][2]
        assert update_kwargs["completed"] == pytest.approx(6.0)
        assert display._backoff_active is True

    def test_update_backoff_already_active_only_updates(self):
        """Lines 123-130 false branch: _backoff_active already True → no add_task."""
        display = self._make_display()
        display._backoff_active = True
        progress = _StubProgress()
        stats = _stats(
            is_in_backoff=True,
            current_backoff_seconds=20,
            backoff_remaining=5,
        )

        display._update_backoff(progress, stats)

        # Only update fires; no add.
        ops = [c[0] for c in progress.calls]
        assert ops == ["update"]

    def test_update_backoff_minimum_total_floor(self):
        """Line 119: max(total, 0.1) — current_backoff_seconds=0 floors at 0.1."""
        display = self._make_display()
        progress = _StubProgress()
        stats = _stats(
            is_in_backoff=True,
            current_backoff_seconds=0,
            backoff_remaining=0,
        )

        display._update_backoff(progress, stats)

        add_kwargs = progress.calls[0][1]
        # int(0.1) == 0 — but the stored total in update is the float floor.
        update_kwargs = progress.calls[1][2]
        assert update_kwargs["total"] == pytest.approx(0.1)
        assert add_kwargs["total"] == 0  # int(0.1)

    def test_update_backoff_ended_removes_task(self):
        """Lines 139-141: not in backoff but _backoff_active=True → remove task."""
        display = self._make_display()
        display._backoff_active = True
        progress = _StubProgress()
        stats = _stats(is_in_backoff=False)

        display._update_backoff(progress, stats)

        assert progress.calls == [("remove", "rate_limit_backoff")]
        assert display._backoff_active is False

    def test_update_backoff_not_active_no_change(self):
        """Lines 118 + 139 false: not in backoff and never was → no calls."""
        display = self._make_display()
        progress = _StubProgress()
        stats = _stats(is_in_backoff=False)

        display._update_backoff(progress, stats)

        assert progress.calls == []
        assert display._backoff_active is False

    # ── _update_tokens (lines 143-171) ──────────────────────────────

    def test_update_tokens_high_utilization_adds_and_updates(self):
        """Lines 154-168: utilization >=80 + burst_size>0 → add task and update."""
        display = self._make_display()
        progress = _StubProgress()
        stats = _stats(
            utilization_percent=85,
            burst_size=100,
            available_tokens=15,
        )

        display._update_tokens(progress, stats)

        ops = [c[0] for c in progress.calls]
        assert ops == ["add", "update"]
        add_kwargs = progress.calls[0][1]
        assert add_kwargs["name"] == "rate_limit_tokens"
        assert add_kwargs["total"] == 100
        update_kwargs = progress.calls[1][2]
        # completed = min(available, burst_size) = min(15, 100) = 15
        assert update_kwargs["completed"] == pytest.approx(15.0)
        assert display._tokens_active is True

    def test_update_tokens_already_active_only_updates(self):
        """Lines 154 false branch: already active → no add_task."""
        display = self._make_display()
        display._tokens_active = True
        progress = _StubProgress()
        stats = _stats(
            utilization_percent=90,
            burst_size=50,
            available_tokens=5,
        )

        display._update_tokens(progress, stats)

        ops = [c[0] for c in progress.calls]
        assert ops == ["update"]

    def test_update_tokens_below_threshold_removes_when_active(self):
        """Lines 169-171: utilization <80 but _tokens_active → remove task."""
        display = self._make_display()
        display._tokens_active = True
        progress = _StubProgress()
        stats = _stats(
            utilization_percent=50,
            burst_size=100,
            available_tokens=50,
        )

        display._update_tokens(progress, stats)

        assert progress.calls == [("remove", "rate_limit_tokens")]
        assert display._tokens_active is False

    def test_update_tokens_zero_burst_size_no_action(self):
        """Line 153: burst_size=0 means no token bucket → don't show task."""
        display = self._make_display()
        progress = _StubProgress()
        stats = _stats(
            utilization_percent=99,
            burst_size=0,
            available_tokens=0,
        )

        display._update_tokens(progress, stats)

        assert progress.calls == []
        assert display._tokens_active is False

    # ── _update (lines 101-110) ────────────────────────────────────

    def test_update_disabled_triggers_cleanup_only(self):
        """Lines 105-107: stats.enabled=False → _cleanup, return without dispatch."""
        # Limiter returns disabled stats once.
        limiter = _make_limiter([_stats(enabled=False)])
        display = RateLimiterDisplay(limiter, update_interval=0.05)
        # Pre-set both flags True so _cleanup will issue removes.
        display._backoff_active = True
        display._tokens_active = True
        progress = _StubProgress()

        display._update(progress)

        # Both removes happened; flags reset.
        ops = [c[0] for c in progress.calls]
        assert ops == ["remove", "remove"]
        assert display._backoff_active is False
        assert display._tokens_active is False

    def test_update_enabled_dispatches_to_both(self):
        """Lines 109-110: enabled=True → _update_backoff and _update_tokens called.

        Verified by stats that should produce calls in BOTH paths.
        """
        limiter = _make_limiter(
            [
                _stats(
                    is_in_backoff=True,
                    current_backoff_seconds=8,
                    backoff_remaining=3,
                    utilization_percent=85,
                    burst_size=100,
                    available_tokens=15,
                )
            ]
        )
        display = RateLimiterDisplay(limiter, update_interval=0.05)
        progress = _StubProgress()

        display._update(progress)

        # Backoff branch added a task; tokens branch added a task.
        added_names = [c[1]["name"] for c in progress.calls if c[0] == "add"]
        assert "rate_limit_backoff" in added_names
        assert "rate_limit_tokens" in added_names


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
