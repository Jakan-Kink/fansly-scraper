"""Tests for api/rate_limiter_display.py — visual display for rate limiter status.

No external boundaries — uses a stub RateLimiter with controllable get_stats().
"""

import asyncio
import time
from types import SimpleNamespace
from typing import Any

import pytest

from api.rate_limiter_display import RateLimiterDisplay
from helpers.rich_progress import ProgressManager


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


def _make_raising_limiter(exc):
    """Build a stub RateLimiter whose get_stats raises, to test the loop's
    exception-break branch."""

    def get_stats():
        raise exc

    return SimpleNamespace(get_stats=get_stats)


def _stats(
    *,
    enabled: bool = True,
    is_in_backoff: bool = False,
    current_backoff_seconds: float = 0,
    backoff_remaining: float = 0,
    utilization_percent: float = 0,
    burst_size: float = 100,
    available_tokens: float = 100,
) -> dict[str, bool | float]:
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
        limiter = _make_raising_limiter(RuntimeError("stats error"))
        display = RateLimiterDisplay(limiter, update_interval=0.05)

        with display:
            time.sleep(0.2)
        # Should have stopped cleanly despite exception


class _StubProgress(ProgressManager):
    """Records every progress-manager call without doing any rendering.

    Used to test RateLimiterDisplay's update methods directly (no background
    thread, no timing race) so coverage of the state-machine branches is
    deterministic.
    """

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[tuple] = []

    def add_task(  # type: ignore[override]  # records args instead of rendering
        self, name: str, description: str = "", **kwargs: Any
    ) -> str:
        self.calls.append(("add", {"name": name, "description": description, **kwargs}))
        return name

    def update_task(
        self,
        name: str,
        advance: int = 1,
        description: str | None = None,
        **kwargs: Any,
    ) -> None:
        self.calls.append(
            ("update", name, {"advance": advance, "description": description, **kwargs})
        )

    def remove_task(self, name: str) -> None:
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

    @pytest.mark.parametrize(
        ("backoff_active", "tokens_active", "expected_removes"),
        [
            pytest.param(False, False, [], id="no-active-tasks-noop"),
            pytest.param(
                True,
                False,
                [("remove", "rate_limit_backoff")],
                id="only-backoff-removes-just-backoff",
            ),
            pytest.param(
                False,
                True,
                [("remove", "rate_limit_tokens")],
                id="only-tokens-removes-just-tokens",
            ),
            pytest.param(
                True,
                True,
                [
                    ("remove", "rate_limit_backoff"),
                    ("remove", "rate_limit_tokens"),
                ],
                id="both-active-removes-both",
            ),
        ],
    )
    def test_cleanup_truth_table(
        self,
        backoff_active: bool,
        tokens_active: bool,
        expected_removes: list[tuple[str, str]],
    ) -> None:
        """Lines 173-180: each active flag drives exactly one remove; both reset."""
        display = self._make_display()
        display._backoff_active = backoff_active
        display._tokens_active = tokens_active
        progress = _StubProgress()

        display._cleanup(progress)

        for expected in expected_removes:
            assert expected in progress.calls
        assert len(progress.calls) == len(expected_removes)
        assert display._backoff_active is False
        assert display._tokens_active is False

    # ── _update_backoff (lines 112-141) ─────────────────────────────

    @pytest.mark.parametrize(
        (
            "preset_active",
            "stats",
            "expected_ops",
            "expected_active_after",
            "expected_add_total",
            "expected_completed",
            "expected_update_total",
        ),
        [
            pytest.param(
                False,
                _stats(
                    is_in_backoff=True,
                    current_backoff_seconds=10,
                    backoff_remaining=4,
                ),
                ["add", "update"],
                True,
                10,
                # Update sets completed = total - remaining = 10 - 4 = 6.
                6.0,
                None,
                id="first-entry-adds-and-updates",
            ),
            pytest.param(
                True,
                _stats(
                    is_in_backoff=True,
                    current_backoff_seconds=20,
                    backoff_remaining=5,
                ),
                # Lines 123-130 false branch: already active → only update, no add.
                ["update"],
                True,
                None,
                None,
                None,
                id="already-active-only-updates",
            ),
            pytest.param(
                False,
                _stats(
                    is_in_backoff=True,
                    current_backoff_seconds=0,
                    backoff_remaining=0,
                ),
                ["add", "update"],
                True,
                # Line 119: max(total, 0.1) floor — add gets int(0.1) == 0,
                # update carries the float floor 0.1.
                0,
                None,
                0.1,
                id="minimum-total-floor",
            ),
            pytest.param(
                True,
                _stats(is_in_backoff=False),
                # Lines 139-141: backoff ended while active → remove task.
                ["remove"],
                False,
                None,
                None,
                None,
                id="ended-removes-task",
            ),
            pytest.param(
                False,
                _stats(is_in_backoff=False),
                # Lines 118 + 139 false: not in backoff and never was → no calls.
                [],
                False,
                None,
                None,
                None,
                id="not-active-no-change",
            ),
        ],
    )
    def test_update_backoff_state_machine(
        self,
        preset_active: bool,
        stats: dict[str, bool | float],
        expected_ops: list[str],
        expected_active_after: bool,
        expected_add_total: int | None,
        expected_completed: float | None,
        expected_update_total: float | None,
    ) -> None:
        """Lines 112-141: add on first entry, update while active, remove on end."""
        display = self._make_display()
        display._backoff_active = preset_active
        progress = _StubProgress()

        display._update_backoff(progress, stats)

        ops = [c[0] for c in progress.calls]
        assert ops == expected_ops
        assert display._backoff_active is expected_active_after
        if "add" in ops:
            add_kwargs = progress.calls[0][1]
            assert add_kwargs["name"] == "rate_limit_backoff"
            assert add_kwargs["total"] == expected_add_total
        if "remove" in ops:
            assert progress.calls == [("remove", "rate_limit_backoff")]
        if expected_completed is not None:
            update_kwargs = progress.calls[1][2]
            assert update_kwargs["completed"] == pytest.approx(expected_completed)
        if expected_update_total is not None:
            update_kwargs = progress.calls[1][2]
            assert update_kwargs["total"] == pytest.approx(expected_update_total)

    # ── _update_tokens (lines 143-171) ──────────────────────────────

    @pytest.mark.parametrize(
        (
            "preset_active",
            "stats",
            "expected_ops",
            "expected_active_after",
            "expected_add_total",
            "expected_completed",
        ),
        [
            pytest.param(
                False,
                _stats(
                    utilization_percent=85,
                    burst_size=100,
                    available_tokens=15,
                ),
                # Lines 154-168: utilization >=80 + burst_size>0 → add and update.
                ["add", "update"],
                True,
                100,
                # completed = min(available, burst_size) = min(15, 100) = 15
                15.0,
                id="high-utilization-adds-and-updates",
            ),
            pytest.param(
                True,
                _stats(
                    utilization_percent=90,
                    burst_size=50,
                    available_tokens=5,
                ),
                # Lines 154 false branch: already active → no add_task.
                ["update"],
                True,
                None,
                None,
                id="already-active-only-updates",
            ),
            pytest.param(
                True,
                _stats(
                    utilization_percent=50,
                    burst_size=100,
                    available_tokens=50,
                ),
                # Lines 169-171: utilization <80 but active → remove task.
                ["remove"],
                False,
                None,
                None,
                id="below-threshold-removes-when-active",
            ),
            pytest.param(
                False,
                _stats(
                    utilization_percent=99,
                    burst_size=0,
                    available_tokens=0,
                ),
                # Line 153: burst_size=0 means no token bucket → don't show task.
                [],
                False,
                None,
                None,
                id="zero-burst-size-no-action",
            ),
        ],
    )
    def test_update_tokens_state_machine(
        self,
        preset_active: bool,
        stats: dict[str, bool | float],
        expected_ops: list[str],
        expected_active_after: bool,
        expected_add_total: int | None,
        expected_completed: float | None,
    ) -> None:
        """Lines 143-171: add above threshold, update while active, remove below."""
        display = self._make_display()
        display._tokens_active = preset_active
        progress = _StubProgress()

        display._update_tokens(progress, stats)

        ops = [c[0] for c in progress.calls]
        assert ops == expected_ops
        assert display._tokens_active is expected_active_after
        if "add" in ops:
            add_kwargs = progress.calls[0][1]
            assert add_kwargs["name"] == "rate_limit_tokens"
            assert add_kwargs["total"] == expected_add_total
        if "remove" in ops:
            assert progress.calls == [("remove", "rate_limit_tokens")]
        if expected_completed is not None:
            update_kwargs = progress.calls[1][2]
            assert update_kwargs["completed"] == pytest.approx(expected_completed)

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
