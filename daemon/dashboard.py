"""Live Rich dashboard for the monitoring daemon.

Shows simulator state and per-loop countdown bars while the daemon runs.
Designed so someone popping into a long-running tmux pane can see at a
glance what stage the daemon is in, how long until the next poll, and
whether the WebSocket is connected.

Integration contract with daemon/runner.py:
  - ``DaemonDashboard.__aenter__`` opens a ProgressManager session and
    creates the persistent state and countdown tasks.
  - ``wait_with_countdown(name, duration, stop_event, refresh_event=None)``
    replaces the raw ``asyncio.wait_for(stop_event.wait(), timeout)``
    pattern: it drives the named countdown bar while awaiting either a
    timeout, stop_event, or optional refresh_event.
  - ``set_simulator_state(state)`` / ``set_ws_state(connected)`` update
    the status line; call when the state actually changes.
  - ``__aexit__`` exits the session — auto-cleanup removes all tasks.

When ``config.monitoring_dashboard_enabled`` is False, ``NullDashboard``
is used instead — a no-op implementation with identical method surface.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Iterable
from types import TracebackType
from typing import TYPE_CHECKING

from helpers.rich_progress import get_progress_manager


if TYPE_CHECKING:
    from helpers.rich_progress import ProgressManager


# State task name — single persistent task in the daemon_state group.
_STATE_TASK = "daemon-status-line"

# Countdown task names — one per cadenced poll loop.
TASK_TIMELINE = "daemon-countdown-timeline"
TASK_STORY = "daemon-countdown-story"
TASK_FOLLOWING = "daemon-countdown-following"
TASK_SIMULATOR = "daemon-countdown-simulator"

# Color mapping for simulator states. Falls back to "red" for unknown
# states so a regression is visually loud.
_STATE_COLORS = {
    "active": "bold green",
    "idle": "bold yellow",
    "hidden": "dim white",
}


class DaemonDashboard:
    """Rich live dashboard bound to a ProgressManager session.

    Usage:
        async with DaemonDashboard() as dashboard:
            dashboard.set_simulator_state("active")
            await dashboard.wait_with_countdown(
                TASK_TIMELINE,
                description="Timeline poll",
                duration=180.0,
                stop_event=stop_event,
            )
    """

    def __init__(self, progress: ProgressManager | None = None) -> None:
        self._progress = progress if progress is not None else get_progress_manager()
        self._session_cm: contextlib.AbstractContextManager | None = None
        self._simulator_state: str = "initializing"
        self._ws_connected: bool = False

    async def __aenter__(self) -> DaemonDashboard:
        # ProgressManager.session is sync — no need for asynccontextmanager
        self._session_cm = self._progress.session(auto_cleanup=True)
        self._session_cm.__enter__()
        self._progress.add_task(
            _STATE_TASK,
            description=self._format_status_line(),
            total=None,
            group="daemon_state",
        )
        # Pre-create all four countdown tasks at session entry so Rich's
        # Live display has a stable-height panel from the first refresh.
        # Per-iteration add_task calls work via the "update if exists"
        # fallback but produce slightly different write patterns that
        # interact badly with tmux scrollback capture. mymember-downloader
        # (source of rich_progress.py) creates tasks once and only
        # update()s afterward — matching that pattern here.
        for task_name, desc in (
            (TASK_TIMELINE, "Timeline poll"),
            (TASK_STORY, "Story poll"),
            (TASK_SIMULATOR, "Simulator tick"),
            (TASK_FOLLOWING, "Following refresh"),
        ):
            self._progress.add_task(
                task_name,
                desc,
                total=1,
                group="daemon",
                show_elapsed=False,
            )
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._session_cm is not None:
            self._session_cm.__exit__(exc_type, exc, tb)
            self._session_cm = None

    # ── State updates ────────────────────────────────────────────────

    def set_simulator_state(self, state: str) -> None:
        """Update the simulator state shown on the status line."""
        self._simulator_state = state
        self._refresh_status_line()

    def set_ws_state(self, connected: bool) -> None:
        """Update the WebSocket connection indicator."""
        self._ws_connected = connected
        self._refresh_status_line()

    def _format_status_line(self) -> str:
        color = _STATE_COLORS.get(self._simulator_state, "bold red")
        state_text = f"[{color}]{self._simulator_state.upper()}[/]"
        ws_color = "green" if self._ws_connected else "red"
        ws_text = (
            f"[{ws_color}]{'connected' if self._ws_connected else 'disconnected'}[/]"
        )
        return f"Daemon: {state_text}  ·  WS: {ws_text}"

    def _refresh_status_line(self) -> None:
        self._progress.update_task(
            _STATE_TASK, advance=0, description=self._format_status_line()
        )

    # ── Poll-in-progress transition ──────────────────────────────────

    def mark_active(self, task_name: str, description: str) -> None:
        """Swap in an "actively working" description on the countdown bar.

        Called by poll loops between ``wait_with_countdown`` returning
        and the actual API call firing. The bar stays at 100% (the state
        wait_with_countdown left it in) while the description changes to
        indicate work is running, then the next ``wait_with_countdown``
        call resets it to 0% + original description.

        Mirrors the poll_scheduler pattern in mymember-downloader where
        the bar visibly transitions from "timer expired" to "work
        happening" to "next countdown" as distinct states.
        """
        self._progress.update_task(task_name, advance=0, description=description)

    # ── Countdown helper ─────────────────────────────────────────────

    async def wait_with_countdown(
        self,
        task_name: str,
        description: str,
        duration: float,
        stop_event: asyncio.Event,
        refresh_event: asyncio.Event | None = None,
    ) -> bool:
        """Drive a countdown bar for ``duration`` seconds or until any event fires.

        Returns True when stop_event or refresh_event was set (early wake);
        False when the timeout expired naturally. The caller inspects the
        returned value and/or the events to decide next action.

        The progress bar renders in the "daemon" GROUP_COLUMNS group
        (description + bar + TimeRemainingColumn). Resolution is 100ms
        per tick; bars advance smoothly at the Live display's 30Hz.
        """
        # Centiseconds for 100ms resolution without integer truncation
        total_ticks = max(1, int(duration * 10))

        # Task was pre-created in __aenter__. Reset its counters + swap
        # in the current description/total in one update call. No
        # add_task here — that path creates churn in Rich's Live diff
        # that produces duplicate-bar artifacts in tmux scrollback.
        self._progress.update_task(
            task_name,
            advance=0,
            description=description,
            total=total_ticks,
            completed=0,
        )

        events: Iterable[asyncio.Event] = (
            (stop_event, refresh_event) if refresh_event is not None else (stop_event,)
        )
        waiters = [asyncio.create_task(e.wait()) for e in events]

        loop = asyncio.get_running_loop()
        start = loop.time()
        triggered_early = False

        try:
            while True:
                elapsed = loop.time() - start
                if elapsed >= duration:
                    # Natural timeout — paint the bar at 100% and return
                    self._progress.update_task(
                        task_name, advance=0, completed=total_ticks
                    )
                    return triggered_early

                completed_ticks = min(int(elapsed * 10), total_ticks)
                self._progress.update_task(
                    task_name, advance=0, completed=completed_ticks
                )

                # Sleep until next 100ms tick or an event fires, whichever is first
                tick_budget = min(0.1, duration - elapsed)
                done, _ = await asyncio.wait(
                    waiters,
                    timeout=tick_budget,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if done:
                    # An event fired — mark the bar complete and return
                    self._progress.update_task(
                        task_name, advance=0, completed=total_ticks
                    )
                    triggered_early = True
                    return True
        finally:
            for w in waiters:
                if not w.done():
                    w.cancel()
            # Let cancelled waiters settle without spamming "Task was
            # destroyed but it is pending!" warnings at shutdown.
            await asyncio.gather(*waiters, return_exceptions=True)


class NullDashboard:
    """No-op dashboard for when ``monitoring_dashboard_enabled`` is False.

    Same method surface as DaemonDashboard, every call is a cheap no-op
    except wait_with_countdown, which falls back to the plain composite
    wait (stop_event OR refresh_event OR timeout) — the same semantic as
    the dashboard version, just without the visual bar.
    """

    async def __aenter__(self) -> NullDashboard:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        return None

    def set_simulator_state(self, state: str) -> None:  # noqa: ARG002
        return None

    def set_ws_state(self, connected: bool) -> None:  # noqa: ARG002
        return None

    def mark_active(
        self,
        task_name: str,  # noqa: ARG002
        description: str,  # noqa: ARG002
    ) -> None:
        return None

    async def wait_with_countdown(
        self,
        task_name: str,  # noqa: ARG002
        description: str,  # noqa: ARG002
        duration: float,
        stop_event: asyncio.Event,
        refresh_event: asyncio.Event | None = None,
    ) -> bool:
        events = (
            (stop_event, refresh_event) if refresh_event is not None else (stop_event,)
        )
        waiters = [asyncio.create_task(e.wait()) for e in events]
        try:
            done, _ = await asyncio.wait(
                waiters,
                timeout=duration,
                return_when=asyncio.FIRST_COMPLETED,
            )
            return bool(done)
        finally:
            for w in waiters:
                if not w.done():
                    w.cancel()
            await asyncio.gather(*waiters, return_exceptions=True)


def make_dashboard(enabled: bool) -> DaemonDashboard | NullDashboard:
    """Return the appropriate dashboard implementation.

    The two classes share the same async-context + method surface, so
    ``daemon/runner.py`` can use the return value polymorphically.
    """
    return DaemonDashboard() if enabled else NullDashboard()
