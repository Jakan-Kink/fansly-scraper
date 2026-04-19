"""Unit tests for daemon/dashboard.py — DaemonDashboard and NullDashboard.

Tests use the real ProgressManager to verify integration (no mocks of
internal rich_progress objects). The Live display is suppressed because
we never enter an actual session in test mode via stdout; we exercise the
dashboard's add_task/update_task calls and verify state through the
ProgressManager's public API.
"""

from __future__ import annotations

import asyncio

import pytest

from daemon.dashboard import (
    TASK_FOLLOWING,
    TASK_SIMULATOR,
    TASK_STORY,
    TASK_TIMELINE,
    DaemonDashboard,
    NullDashboard,
    make_dashboard,
)
from helpers.rich_progress import ProgressManager


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def progress_manager():
    """Fresh ProgressManager per test to avoid bleed-over of task state.

    The module-level _progress_manager singleton accumulates tasks across
    tests and sessions, which would make "no tasks leaked" assertions
    unreliable. A fresh instance per test gives each case its own slate.
    """
    return ProgressManager()


# ---------------------------------------------------------------------------
# make_dashboard factory
# ---------------------------------------------------------------------------


class TestMakeDashboard:
    """make_dashboard returns DaemonDashboard when enabled, NullDashboard when not."""

    def test_returns_dashboard_when_enabled(self):
        result = make_dashboard(enabled=True)
        assert isinstance(result, DaemonDashboard)

    def test_returns_null_when_disabled(self):
        result = make_dashboard(enabled=False)
        assert isinstance(result, NullDashboard)


# ---------------------------------------------------------------------------
# DaemonDashboard — context manager lifecycle
# ---------------------------------------------------------------------------


class TestDashboardLifecycle:
    """DaemonDashboard opens/closes its ProgressManager session cleanly."""

    @pytest.mark.asyncio
    async def test_enter_creates_state_task(self, progress_manager):
        dashboard = DaemonDashboard(progress=progress_manager)
        async with dashboard:
            # The state task should exist inside the session
            assert progress_manager.get_active_count() >= 1

    @pytest.mark.asyncio
    async def test_exit_removes_all_tasks(self, progress_manager):
        dashboard = DaemonDashboard(progress=progress_manager)
        async with dashboard:
            # Create an extra countdown task to verify session cleanup
            stop_event = asyncio.Event()
            stop_event.set()  # ensures instant return
            await dashboard.wait_with_countdown(
                TASK_TIMELINE,
                "Timeline poll",
                1.0,
                stop_event,
            )
        # After exit: session auto-cleanup should have removed everything
        assert progress_manager.get_active_count() == 0

    @pytest.mark.asyncio
    async def test_exception_inside_session_still_cleans_up(self, progress_manager):
        """The session must drop tasks even if the body raises."""
        dashboard = DaemonDashboard(progress=progress_manager)

        async def _run_with_exception() -> None:
            async with dashboard:
                assert progress_manager.get_active_count() >= 1
                raise RuntimeError("boom")

        with pytest.raises(RuntimeError, match="boom"):
            await _run_with_exception()
        # Session's finally still ran
        assert progress_manager.get_active_count() == 0


# ---------------------------------------------------------------------------
# DaemonDashboard — state updates
# ---------------------------------------------------------------------------


class TestDashboardState:
    """set_simulator_state / set_ws_state update the status line correctly."""

    @pytest.mark.asyncio
    async def test_simulator_state_active_shows_green(self, progress_manager):
        dashboard = DaemonDashboard(progress=progress_manager)
        async with dashboard:
            dashboard.set_simulator_state("active")
            rendered = dashboard._format_status_line()
        assert "ACTIVE" in rendered
        assert "bold green" in rendered

    @pytest.mark.asyncio
    async def test_simulator_state_idle_shows_yellow(self, progress_manager):
        dashboard = DaemonDashboard(progress=progress_manager)
        async with dashboard:
            dashboard.set_simulator_state("idle")
            rendered = dashboard._format_status_line()
        assert "IDLE" in rendered
        assert "bold yellow" in rendered

    @pytest.mark.asyncio
    async def test_simulator_state_hidden_shows_dim(self, progress_manager):
        dashboard = DaemonDashboard(progress=progress_manager)
        async with dashboard:
            dashboard.set_simulator_state("hidden")
            rendered = dashboard._format_status_line()
        assert "HIDDEN" in rendered
        assert "dim" in rendered

    @pytest.mark.asyncio
    async def test_unknown_state_falls_back_to_red(self, progress_manager):
        """An unexpected state is shown in bold red — a regression signal."""
        dashboard = DaemonDashboard(progress=progress_manager)
        async with dashboard:
            dashboard.set_simulator_state("quantum_superposition")
            rendered = dashboard._format_status_line()
        assert "QUANTUM_SUPERPOSITION" in rendered
        assert "bold red" in rendered

    @pytest.mark.asyncio
    async def test_ws_connected_shows_green(self, progress_manager):
        dashboard = DaemonDashboard(progress=progress_manager)
        async with dashboard:
            dashboard.set_ws_state(connected=True)
            rendered = dashboard._format_status_line()
        assert "connected" in rendered
        assert "[green]" in rendered

    @pytest.mark.asyncio
    async def test_ws_disconnected_shows_red(self, progress_manager):
        dashboard = DaemonDashboard(progress=progress_manager)
        async with dashboard:
            dashboard.set_ws_state(connected=False)
            rendered = dashboard._format_status_line()
        assert "disconnected" in rendered
        assert "[red]" in rendered


# ---------------------------------------------------------------------------
# DaemonDashboard — wait_with_countdown semantics
# ---------------------------------------------------------------------------


class TestWaitWithCountdown:
    """wait_with_countdown wakes on stop_event, refresh_event, or timeout."""

    @pytest.mark.asyncio
    async def test_wakes_immediately_on_stop_event_already_set(self, progress_manager):
        dashboard = DaemonDashboard(progress=progress_manager)
        stop_event = asyncio.Event()
        stop_event.set()

        async with dashboard:
            loop = asyncio.get_running_loop()
            start = loop.time()
            triggered = await dashboard.wait_with_countdown(
                TASK_TIMELINE, "Timeline poll", 60.0, stop_event
            )
            elapsed = loop.time() - start

        assert triggered is True
        # Should return near-instantly (much less than the 60s timeout)
        assert elapsed < 1.0, f"Took {elapsed:.2f}s to return on pre-set stop_event"

    @pytest.mark.asyncio
    async def test_wakes_on_stop_event_set_mid_wait(self, progress_manager):
        dashboard = DaemonDashboard(progress=progress_manager)
        stop_event = asyncio.Event()

        async def _set_after(delay: float) -> None:
            await asyncio.sleep(delay)
            stop_event.set()

        async with dashboard:
            setter = asyncio.create_task(_set_after(0.2))
            loop = asyncio.get_running_loop()
            start = loop.time()
            triggered = await dashboard.wait_with_countdown(
                TASK_STORY, "Story poll", 60.0, stop_event
            )
            elapsed = loop.time() - start
            await setter  # settle task

        assert triggered is True
        assert 0.15 <= elapsed <= 1.0, (
            f"Expected ~0.2s wake after stop_event.set(), got {elapsed:.2f}s"
        )

    @pytest.mark.asyncio
    async def test_wakes_on_refresh_event(self, progress_manager):
        """refresh_event (optional second event) also triggers early wake."""
        dashboard = DaemonDashboard(progress=progress_manager)
        stop_event = asyncio.Event()
        refresh_event = asyncio.Event()

        async def _refresh_after(delay: float) -> None:
            await asyncio.sleep(delay)
            refresh_event.set()

        async with dashboard:
            setter = asyncio.create_task(_refresh_after(0.2))
            loop = asyncio.get_running_loop()
            start = loop.time()
            triggered = await dashboard.wait_with_countdown(
                TASK_FOLLOWING,
                "Following refresh",
                60.0,
                stop_event,
                refresh_event,
            )
            elapsed = loop.time() - start
            await setter

        assert triggered is True
        assert 0.15 <= elapsed <= 1.0
        assert not stop_event.is_set()
        assert refresh_event.is_set()

    @pytest.mark.asyncio
    async def test_returns_false_on_natural_timeout(self, progress_manager):
        """When neither event fires and duration elapses, returns False."""
        dashboard = DaemonDashboard(progress=progress_manager)
        stop_event = asyncio.Event()

        async with dashboard:
            loop = asyncio.get_running_loop()
            start = loop.time()
            triggered = await dashboard.wait_with_countdown(
                TASK_SIMULATOR, "Simulator tick", 0.2, stop_event
            )
            elapsed = loop.time() - start

        assert triggered is False
        assert 0.18 <= elapsed <= 0.8, f"Expected ~0.2s duration, got {elapsed:.2f}s"

    @pytest.mark.asyncio
    async def test_waiters_are_cleaned_up_after_return(self, progress_manager):
        """The internal stop/refresh waiters are cancelled once the wait returns.

        If waiters leak, each iteration of a poll loop would add a new pending
        wait() on the same Event — after hours of daemon runtime, Event._waiters
        would balloon. Verify by counting event._waiters before/after.
        """
        dashboard = DaemonDashboard(progress=progress_manager)
        stop_event = asyncio.Event()
        refresh_event = asyncio.Event()

        async with dashboard:
            for _ in range(5):
                refresh_event.set()  # each iteration triggers early wake
                await dashboard.wait_with_countdown(
                    TASK_TIMELINE,
                    "Timeline poll",
                    0.5,
                    stop_event,
                    refresh_event,
                )
                refresh_event.clear()

            # After 5 iterations, neither event should have leftover waiters
            # beyond what Python's internal bookkeeping normally keeps. Event
            # internals vary but the count should be bounded (not N iterations).
            assert len(stop_event._waiters) <= 1, (
                f"stop_event accumulated {len(stop_event._waiters)} waiters"
            )
            assert len(refresh_event._waiters) <= 1, (
                f"refresh_event accumulated {len(refresh_event._waiters)} waiters"
            )


# ---------------------------------------------------------------------------
# NullDashboard — API surface matches, no-op where expected
# ---------------------------------------------------------------------------


class TestNullDashboard:
    """NullDashboard silently ignores state updates but still does composite wait."""

    @pytest.mark.asyncio
    async def test_state_methods_are_noops(self):
        dashboard = NullDashboard()
        async with dashboard:
            # These should not raise or print — nothing to verify beyond "no error"
            dashboard.set_simulator_state("active")
            dashboard.set_ws_state(connected=True)
            dashboard.set_simulator_state("hidden")
            dashboard.set_ws_state(connected=False)

    @pytest.mark.asyncio
    async def test_wait_with_countdown_wakes_on_stop_event(self):
        """Even without a visual bar, the composite wait still works."""
        dashboard = NullDashboard()
        stop_event = asyncio.Event()

        async def _set_after(delay: float) -> None:
            await asyncio.sleep(delay)
            stop_event.set()

        async with dashboard:
            setter = asyncio.create_task(_set_after(0.15))
            loop = asyncio.get_running_loop()
            start = loop.time()
            triggered = await dashboard.wait_with_countdown(
                "ignored", "ignored", 60.0, stop_event
            )
            elapsed = loop.time() - start
            await setter

        assert triggered is True
        assert elapsed < 1.0

    @pytest.mark.asyncio
    async def test_wait_with_countdown_respects_refresh_event(self):
        dashboard = NullDashboard()
        stop_event = asyncio.Event()
        refresh_event = asyncio.Event()
        refresh_event.set()

        async with dashboard:
            triggered = await dashboard.wait_with_countdown(
                "ignored", "ignored", 60.0, stop_event, refresh_event
            )

        assert triggered is True

    @pytest.mark.asyncio
    async def test_wait_with_countdown_returns_false_on_timeout(self):
        dashboard = NullDashboard()
        stop_event = asyncio.Event()

        async with dashboard:
            triggered = await dashboard.wait_with_countdown(
                "ignored", "ignored", 0.1, stop_event
            )

        assert triggered is False


# ---------------------------------------------------------------------------
# Polymorphism — both dashboards satisfy the same contract
# ---------------------------------------------------------------------------


class TestDashboardPolymorphism:
    """DaemonDashboard and NullDashboard expose the same method surface."""

    @pytest.mark.parametrize(
        "dashboard_factory",
        [
            pytest.param(
                lambda: DaemonDashboard(progress=ProgressManager()), id="real"
            ),
            pytest.param(NullDashboard, id="null"),
        ],
    )
    @pytest.mark.asyncio
    async def test_full_cycle_works(self, dashboard_factory):
        """Both dashboards handle the full enter -> state -> countdown -> exit cycle."""
        dashboard = dashboard_factory()
        stop_event = asyncio.Event()
        stop_event.set()

        async with dashboard:
            dashboard.set_simulator_state("active")
            dashboard.set_ws_state(connected=True)
            triggered = await dashboard.wait_with_countdown(
                TASK_TIMELINE, "Timeline poll", 60.0, stop_event
            )

        assert triggered is True
