"""Unit tests for daemon.runner orchestration loops (TIER 4B).

Targets the previously-uncovered orchestration-layer branches in:
- _process_timeline_candidate exception path (lines 545-551)
- _timeline_poll_loop hidden state + happy path + error + DaemonUnrecoverable (587-637)
- _story_poll_loop similar surface (664-710)
- _following_refresh_loop early-return + idle/hidden + happy + error (845-887)
- _simulator_tick_loop unhide + ws-reconnect-error (910-947)

Pattern: each test wires real ActivitySimulator + real ErrorBudget + real
asyncio.Event + a fast dashboard stub (no real waits). The poll/refresh
underlying functions are monkeypatched at the daemon.runner local
binding to control results and trigger stop_event after N iterations.

Internal-mock disclosure: tests monkeypatch poll_home_timeline,
poll_story_states, get_following_accounts, should_process_creator,
_is_creator_in_scope at the daemon.runner binding (the local use site,
canonical scope for testing the loop's behavior). Real-pipeline tests
for the underlying functions live in test_polling.py / test_filters.py.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import pytest

from daemon.handlers import (
    DownloadStoriesOnly,
    DownloadTimelineOnly,
)
from daemon.runner import (
    ErrorBudget,
    _following_refresh_loop,
    _process_timeline_candidate,
    _simulator_tick_loop,
    _story_poll_loop,
    _timeline_poll_loop,
)
from daemon.simulator import ActivitySimulator
from errors import DaemonUnrecoverableError
from tests.fixtures.utils.test_isolation import snowflake_id


def _logged(caplog, level: str) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.levelname == level]


class _FastDashboard:
    """Test dashboard with no real waits.

    wait_with_countdown returns immediately so loop iterations don't pay
    the production countdown durations (300s for following-refresh, 30s
    for simulator tick, etc.). All other methods are no-ops.
    """

    def __init__(self) -> None:
        self.simulator_states: list[str] = []
        self.ws_states: list[bool] = []
        self.active_marks: list[tuple[str, str]] = []

    def set_simulator_state(self, state: str) -> None:
        self.simulator_states.append(state)

    def set_ws_state(self, connected: bool) -> None:
        self.ws_states.append(connected)

    def mark_active(self, task_name: str, description: str) -> None:
        self.active_marks.append((task_name, description))

    async def wait_with_countdown(
        self,
        task_name: str,
        description: str,
        duration: float,
        stop_event: asyncio.Event,
        refresh_event: asyncio.Event | None = None,
    ) -> bool:
        # Yield to the event loop so other tasks make progress, but do not
        # actually wait on the event/timeout — keeps the loop body running
        # at full speed while letting the caller control termination via
        # stop_event from inside its monkeypatched poll function.
        await asyncio.sleep(0)
        return False


def _make_simulator(state: str = "active") -> ActivitySimulator:
    """Build an ActivitySimulator forced into the requested state."""
    sim = ActivitySimulator()
    # ActivitySimulator's state defaults to "active" with non-zero intervals.
    if state == "hidden":
        # Hidden state has both intervals at 0 (paused).
        sim.state = "hidden"
    elif state == "idle":
        sim.state = "idle"
    return sim


def _make_budget() -> ErrorBudget:
    """Build an ErrorBudget with a 1-hour window (well above test runtime)."""
    return ErrorBudget(timeout_seconds=3600)


# ---------------------------------------------------------------------------
# _process_timeline_candidate — line 545-551 exception branch
# ---------------------------------------------------------------------------


class TestProcessTimelineCandidateException:
    """Lines 545-551: should_process_creator raises → log warning, default should=True."""

    @pytest.mark.asyncio
    async def test_exception_defaults_to_should_process(
        self, config, entity_store, monkeypatch, caplog
    ):
        caplog.set_level(logging.WARNING)
        creator_id = snowflake_id()

        async def _scope_check(_config, _cid):
            return True  # in scope

        async def _raises(*_a, **_k):
            raise RuntimeError("filter check boom")

        monkeypatch.setattr("daemon.runner._is_creator_in_scope", _scope_check)
        monkeypatch.setattr("daemon.runner.should_process_creator", _raises)

        queue: asyncio.Queue = asyncio.Queue()
        budget = _make_budget()

        await _process_timeline_candidate(
            config,
            creator_id,
            prefetched=[],
            session_baseline=None,
            baseline_consumed=set(),
            queue=queue,
            budget=budget,
        )

        # Default should=True → item enqueued.
        assert queue.qsize() == 1
        item = queue.get_nowait()
        assert isinstance(item, DownloadTimelineOnly)
        assert item.creator_id == creator_id

        warnings = _logged(caplog, "WARNING")
        assert any(
            "should_process_creator error" in m and "filter check boom" in m
            for m in warnings
        )


# ---------------------------------------------------------------------------
# _timeline_poll_loop — lines 587-637
# ---------------------------------------------------------------------------


class TestTimelinePollLoop:
    """Hidden-state branch + happy + error + DaemonUnrecoverable re-raise."""

    @pytest.mark.asyncio
    async def test_hidden_state_skips_poll_and_continues(self, config, monkeypatch):
        """Lines 589-597: simulator.timeline_interval <= 0 (hidden) → wait + continue.

        Verified by checking poll_home_timeline is never called.
        """
        sim = _make_simulator("hidden")
        # ActivitySimulator.timeline_interval is a property — 0 when hidden.
        assert sim.timeline_interval <= 0.0

        poll_calls = 0

        async def _poll(_config):
            nonlocal poll_calls
            poll_calls += 1
            return [], {}

        monkeypatch.setattr("daemon.runner.poll_home_timeline", _poll)

        stop_event = asyncio.Event()
        refresh_event = asyncio.Event()
        queue: asyncio.Queue = asyncio.Queue()
        budget = _make_budget()
        dashboard = _FastDashboard()

        # Stop after one iteration via a background task.
        async def _stop_soon():
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            stop_event.set()

        await asyncio.gather(
            _timeline_poll_loop(
                config,
                sim,
                queue,
                None,
                set(),
                stop_event,
                budget,
                refresh_event,
                dashboard,
            ),
            _stop_soon(),
        )

        # Hidden state never enters the poll branch.
        assert poll_calls == 0

    @pytest.mark.asyncio
    async def test_happy_path_processes_new_creators(
        self, config, entity_store, monkeypatch
    ):
        """Lines 612-637: poll returns creators → simulator on_new_content + enqueue."""
        sim = _make_simulator("active")
        poll_count = 0
        creator_ids = [snowflake_id(), snowflake_id()]

        async def _poll(_config):
            nonlocal poll_count
            poll_count += 1
            if poll_count >= 1:
                stop_event.set()
            return creator_ids, {cid: [] for cid in creator_ids}

        async def _scope_check(_config, _cid):
            return True

        async def _should_process(*_a, **_k):
            return True

        monkeypatch.setattr("daemon.runner.poll_home_timeline", _poll)
        monkeypatch.setattr("daemon.runner._is_creator_in_scope", _scope_check)
        monkeypatch.setattr("daemon.runner.should_process_creator", _should_process)

        stop_event = asyncio.Event()
        refresh_event = asyncio.Event()
        queue: asyncio.Queue = asyncio.Queue()
        budget = _make_budget()

        await _timeline_poll_loop(
            config,
            sim,
            queue,
            None,
            set(),
            stop_event,
            budget,
            refresh_event,
            _FastDashboard(),
        )

        # Both creators enqueued.
        assert queue.qsize() == 2
        items = [queue.get_nowait() for _ in range(2)]
        assert {item.creator_id for item in items} == set(creator_ids)

    @pytest.mark.asyncio
    async def test_poll_error_logs_and_continues(self, config, monkeypatch, caplog):
        """Lines 618-621: poll raises generic Exception → log + budget.on_error + continue."""
        caplog.set_level(logging.WARNING)
        sim = _make_simulator("active")

        poll_count = 0

        async def _poll(_config):
            nonlocal poll_count
            poll_count += 1
            stop_event.set()
            raise RuntimeError("transient poll boom")

        monkeypatch.setattr("daemon.runner.poll_home_timeline", _poll)

        stop_event = asyncio.Event()
        refresh_event = asyncio.Event()
        queue: asyncio.Queue = asyncio.Queue()
        budget = _make_budget()

        await _timeline_poll_loop(
            config,
            sim,
            queue,
            None,
            set(),
            stop_event,
            budget,
            refresh_event,
            _FastDashboard(),
        )

        warnings = _logged(caplog, "WARNING")
        assert any(
            "timeline poll error" in m and "transient poll boom" in m for m in warnings
        )
        # No items enqueued because poll failed.
        assert queue.qsize() == 0

    @pytest.mark.asyncio
    async def test_daemon_unrecoverable_re_raised(self, config, monkeypatch):
        """Lines 616-617: DaemonUnrecoverableError bypasses the generic except."""
        sim = _make_simulator("active")

        async def _poll(_config):
            raise DaemonUnrecoverableError("hard fatal")

        monkeypatch.setattr("daemon.runner.poll_home_timeline", _poll)

        stop_event = asyncio.Event()
        refresh_event = asyncio.Event()
        queue: asyncio.Queue = asyncio.Queue()
        budget = _make_budget()

        with pytest.raises(DaemonUnrecoverableError, match="hard fatal"):
            await _timeline_poll_loop(
                config,
                sim,
                queue,
                None,
                set(),
                stop_event,
                budget,
                refresh_event,
                _FastDashboard(),
            )


# ---------------------------------------------------------------------------
# _story_poll_loop — lines 664-710 (mirror structure)
# ---------------------------------------------------------------------------


class TestStoryPollLoop:
    """Hidden-state branch + happy + error + DaemonUnrecoverable re-raise."""

    @pytest.mark.asyncio
    async def test_hidden_state_skips_poll(self, config, monkeypatch):
        """Lines 666-673: simulator.story_interval <= 0 → wait + continue."""
        sim = _make_simulator("hidden")
        poll_calls = 0

        async def _poll(_config):
            nonlocal poll_calls
            poll_calls += 1
            return []

        monkeypatch.setattr("daemon.runner.poll_story_states", _poll)

        stop_event = asyncio.Event()
        refresh_event = asyncio.Event()
        queue: asyncio.Queue = asyncio.Queue()
        budget = _make_budget()

        async def _stop_soon():
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            stop_event.set()

        await asyncio.gather(
            _story_poll_loop(
                config, sim, queue, stop_event, budget, refresh_event, _FastDashboard()
            ),
            _stop_soon(),
        )

        assert poll_calls == 0

    @pytest.mark.asyncio
    async def test_happy_path_enqueues_in_scope_creators_only(
        self, config, monkeypatch
    ):
        """Lines 695-710: poll returns ids → in-scope ones enqueued, out-of-scope skipped."""
        sim = _make_simulator("active")
        in_scope_id = snowflake_id()
        out_of_scope_id = snowflake_id()

        async def _poll(_config):
            stop_event.set()
            return [in_scope_id, out_of_scope_id]

        async def _scope_check(_config, cid):
            return cid == in_scope_id

        monkeypatch.setattr("daemon.runner.poll_story_states", _poll)
        monkeypatch.setattr("daemon.runner._is_creator_in_scope", _scope_check)

        stop_event = asyncio.Event()
        refresh_event = asyncio.Event()
        queue: asyncio.Queue = asyncio.Queue()
        budget = _make_budget()

        await _story_poll_loop(
            config, sim, queue, stop_event, budget, refresh_event, _FastDashboard()
        )

        # Only the in-scope creator's item lands on the queue.
        assert queue.qsize() == 1
        item = queue.get_nowait()
        assert isinstance(item, DownloadStoriesOnly)
        assert item.creator_id == in_scope_id

    @pytest.mark.asyncio
    async def test_poll_error_logs_and_continues(self, config, monkeypatch, caplog):
        """Lines 692-697: poll raises → log + budget.on_error + continue."""
        caplog.set_level(logging.WARNING)
        sim = _make_simulator("active")

        async def _poll(_config):
            stop_event.set()
            raise RuntimeError("story boom")

        monkeypatch.setattr("daemon.runner.poll_story_states", _poll)

        stop_event = asyncio.Event()
        refresh_event = asyncio.Event()
        queue: asyncio.Queue = asyncio.Queue()
        budget = _make_budget()

        await _story_poll_loop(
            config, sim, queue, stop_event, budget, refresh_event, _FastDashboard()
        )

        warnings = _logged(caplog, "WARNING")
        assert any("story poll error" in m and "story boom" in m for m in warnings)

    @pytest.mark.asyncio
    async def test_daemon_unrecoverable_re_raised(self, config, monkeypatch):
        """Lines 690-691: DaemonUnrecoverableError bypasses the generic except."""
        sim = _make_simulator("active")

        async def _poll(_config):
            raise DaemonUnrecoverableError("story fatal")

        monkeypatch.setattr("daemon.runner.poll_story_states", _poll)

        stop_event = asyncio.Event()
        refresh_event = asyncio.Event()
        queue: asyncio.Queue = asyncio.Queue()
        budget = _make_budget()

        with pytest.raises(DaemonUnrecoverableError, match="story fatal"):
            await _story_poll_loop(
                config, sim, queue, stop_event, budget, refresh_event, _FastDashboard()
            )


# ---------------------------------------------------------------------------
# _following_refresh_loop — lines 845-887
# ---------------------------------------------------------------------------


class TestFollowingRefreshLoop:
    """Early-return + hidden/idle skip + happy + error + DaemonUnrecoverable."""

    @pytest.mark.asyncio
    async def test_use_following_false_returns_immediately(self, config):
        """Lines 845-846: not config.use_following → early return, no loop."""
        config.use_following = False
        sim = _make_simulator("active")
        stop_event = asyncio.Event()
        refresh_event = asyncio.Event()
        budget = _make_budget()

        # Should return immediately even though stop_event isn't set.
        await _following_refresh_loop(
            config, sim, stop_event, refresh_event, budget, _FastDashboard()
        )

    @pytest.mark.asyncio
    async def test_hidden_state_skips_refresh(self, config, monkeypatch):
        """Lines 867-868: simulator.state == 'hidden' → continue without refresh."""
        config.use_following = True
        sim = _make_simulator("hidden")

        refresh_calls = 0

        async def _refresh(_config, _state):
            nonlocal refresh_calls
            refresh_calls += 1
            return set()

        monkeypatch.setattr("daemon.runner.get_following_accounts", _refresh)

        stop_event = asyncio.Event()
        refresh_event = asyncio.Event()
        budget = _make_budget()

        async def _stop_soon():
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            stop_event.set()

        await asyncio.gather(
            _following_refresh_loop(
                config, sim, stop_event, refresh_event, budget, _FastDashboard()
            ),
            _stop_soon(),
        )

        assert refresh_calls == 0

    @pytest.mark.asyncio
    async def test_happy_path_with_new_names_updates_config(
        self, config, monkeypatch, caplog
    ):
        """Lines 870-880: refresh returns new_names → updates config + info log."""
        caplog.set_level(logging.INFO)
        config.use_following = True
        config.user_names = {"old_user"}
        sim = _make_simulator("active")

        refresh_count = 0

        async def _refresh(_config, _state):
            nonlocal refresh_count
            refresh_count += 1
            stop_event.set()
            return {"new_user1", "new_user2"}

        monkeypatch.setattr("daemon.runner.get_following_accounts", _refresh)

        stop_event = asyncio.Event()
        refresh_event = asyncio.Event()
        budget = _make_budget()

        await _following_refresh_loop(
            config, sim, stop_event, refresh_event, budget, _FastDashboard()
        )

        assert config.user_names == {"new_user1", "new_user2"}
        info = _logged(caplog, "INFO")
        assert any(
            "following list refreshed (periodic) - 2 creators" in m for m in info
        )

    @pytest.mark.asyncio
    async def test_refresh_event_set_clears_after_wait(self, config, monkeypatch):
        """Lines 861-862: refresh_event was set when wait returned → clear it."""
        config.use_following = True
        sim = _make_simulator("active")

        async def _refresh(_config, _state):
            stop_event.set()
            return set()

        monkeypatch.setattr("daemon.runner.get_following_accounts", _refresh)

        stop_event = asyncio.Event()
        refresh_event = asyncio.Event()
        refresh_event.set()  # caller asked for an immediate refresh
        budget = _make_budget()

        await _following_refresh_loop(
            config, sim, stop_event, refresh_event, budget, _FastDashboard()
        )

        # After processing the refresh, the event should be cleared.
        assert not refresh_event.is_set()

    @pytest.mark.asyncio
    async def test_refresh_error_logged_continues(self, config, monkeypatch, caplog):
        """Lines 883-887: refresh raises → log + budget.on_error + continue."""
        caplog.set_level(logging.WARNING)
        config.use_following = True
        sim = _make_simulator("active")

        async def _raises(_config, _state):
            stop_event.set()
            raise RuntimeError("refresh boom")

        monkeypatch.setattr("daemon.runner.get_following_accounts", _raises)

        stop_event = asyncio.Event()
        refresh_event = asyncio.Event()
        budget = _make_budget()

        await _following_refresh_loop(
            config, sim, stop_event, refresh_event, budget, _FastDashboard()
        )

        warnings = _logged(caplog, "WARNING")
        assert any(
            "following list refresh failed (periodic)" in m and "refresh boom" in m
            for m in warnings
        )

    @pytest.mark.asyncio
    async def test_refresh_daemon_unrecoverable_re_raised(self, config, monkeypatch):
        """Lines 881-882: DaemonUnrecoverableError bypasses the generic except."""
        config.use_following = True
        sim = _make_simulator("active")

        async def _raises(_config, _state):
            raise DaemonUnrecoverableError("refresh fatal")

        monkeypatch.setattr("daemon.runner.get_following_accounts", _raises)

        stop_event = asyncio.Event()
        refresh_event = asyncio.Event()
        budget = _make_budget()

        with pytest.raises(DaemonUnrecoverableError, match="refresh fatal"):
            await _following_refresh_loop(
                config, sim, stop_event, refresh_event, budget, _FastDashboard()
            )


# ---------------------------------------------------------------------------
# _simulator_tick_loop — lines 925-944 (unhide + ws-reconnect-error)
# ---------------------------------------------------------------------------


class _FakeWs:
    """Minimal WS stub for _simulator_tick_loop unhide path."""

    def __init__(self, stop_raises: BaseException | None = None) -> None:
        self.stop_calls = 0
        self.start_calls = 0
        self._stop_raises = stop_raises

    async def stop_thread(self, *_a: Any, **_k: Any) -> None:
        self.stop_calls += 1
        if self._stop_raises is not None:
            raise self._stop_raises

    def start_in_thread(self, *_a: Any, **_k: Any) -> None:
        self.start_calls += 1


class TestSimulatorTickLoopUnhideErrors:
    """Lines 935-947: ws.stop_thread raises during unhide → log + dashboard.set_ws_state(False)."""

    @pytest.mark.asyncio
    async def test_unhide_ws_reconnect_error_logged(self, monkeypatch, caplog):
        caplog.set_level(logging.WARNING)
        sim = _make_simulator("hidden")

        # Force tick() to return "unhide" so we hit the reconnect path.
        tick_calls = 0

        def _tick():
            nonlocal tick_calls
            tick_calls += 1
            sim.state = "active"
            stop_event.set()
            return "unhide"

        sim.tick = _tick  # type: ignore[method-assign]

        ws = _FakeWs(stop_raises=RuntimeError("ws stop boom"))
        stop_event = asyncio.Event()
        refresh_event = asyncio.Event()
        dashboard = _FastDashboard()

        await _simulator_tick_loop(sim, stop_event, ws, refresh_event, dashboard)

        # The ws.stop_thread error was caught — refresh_event still set,
        # dashboard.set_ws_state(False) called.
        assert refresh_event.is_set()
        assert ws.stop_calls == 1
        assert False in dashboard.ws_states
        warnings = _logged(caplog, "WARNING")
        assert any("WebSocket reconnect failed after unhide" in m for m in warnings)

    @pytest.mark.asyncio
    async def test_unhide_ws_reconnect_success_sets_dashboard_true(self, monkeypatch):
        """Lines 938-941 happy path: stop+start succeed → dashboard.set_ws_state(True)."""
        sim = _make_simulator("hidden")

        def _tick():
            sim.state = "active"
            stop_event.set()
            return "unhide"

        sim.tick = _tick  # type: ignore[method-assign]

        ws = _FakeWs()  # no error on stop
        stop_event = asyncio.Event()
        refresh_event = asyncio.Event()
        dashboard = _FastDashboard()

        await _simulator_tick_loop(sim, stop_event, ws, refresh_event, dashboard)

        assert ws.stop_calls == 1
        assert ws.start_calls == 1
        assert True in dashboard.ws_states


# ---------------------------------------------------------------------------
# TIER 5B — _worker_loop exception paths + _refresh_following gaps
#
# Targets:
#   - _worker_loop CancelledError on queue.get (line 746-747)
#   - _worker_loop generic Exception path (755-768) + budget.on_error
#   - _worker_loop DaemonUnrecoverableError re-raise (751-754)
#   - _worker_loop post-processing for non-timeline items (788-791)
#   - _refresh_following success log + exception (814-820)
# ---------------------------------------------------------------------------


from daemon.handlers import (  # noqa: E402 — deferred import to keep TIER 4B + 5B imports separate
    CheckCreatorAccess,
    FullCreatorDownload,
    RedownloadCreatorMedia,
)
from daemon.runner import _refresh_following, _worker_loop  # noqa: E402


class TestWorkerLoopExceptionPaths:
    """Lines 742-768: queue.get cancellation + per-item exception handling."""

    @pytest.mark.asyncio
    async def test_cancelled_error_on_queue_get_breaks_loop(self, config, monkeypatch):
        """Lines 746-747: asyncio.CancelledError from queue.get → break."""
        queue: asyncio.Queue = asyncio.Queue()
        stop_event = asyncio.Event()

        # Patch queue.get to raise CancelledError on first call.
        async def _cancelled_get():
            raise asyncio.CancelledError

        async def _wait_for(_coro, **_kwargs):
            # Production calls asyncio.wait_for(queue.get(), timeout=1.0) —
            # the timeout kwarg is part of asyncio.wait_for's contract.
            # Accept it via **kwargs so we don't bind to the literal "timeout"
            # parameter name (which trips ASYNC109) yet still match the
            # caller's keyword-arg shape.
            raise asyncio.CancelledError

        monkeypatch.setattr("daemon.runner.asyncio.wait_for", _wait_for)

        # Should exit cleanly without hanging.
        await _worker_loop(config, queue, stop_event, use_following=False)

    @pytest.mark.asyncio
    async def test_handler_generic_exception_logged_and_continues(
        self, config, entity_store, monkeypatch, caplog
    ):
        """Lines 755-768: handler raises generic Exception → ERROR log + budget.on_error + continue."""
        caplog.set_level(logging.ERROR)
        creator_id = snowflake_id()

        async def _raises(_config, _item):
            raise RuntimeError("worker boom")

        monkeypatch.setattr("daemon.runner._handle_work_item", _raises)

        queue: asyncio.Queue = asyncio.Queue()
        await queue.put(FullCreatorDownload(creator_id=creator_id))
        stop_event = asyncio.Event()
        budget = _make_budget()

        # Stop after one item so the loop exits.
        async def _stop_when_drained():
            await queue.join()
            stop_event.set()

        await asyncio.gather(
            _worker_loop(config, queue, stop_event, use_following=False, budget=budget),
            _stop_when_drained(),
        )

        errors = _logged(caplog, "ERROR")
        assert any(
            "worker error on FullCreatorDownload" in m and "worker boom" in m
            for m in errors
        )

    @pytest.mark.asyncio
    async def test_handler_daemon_unrecoverable_raised(self, config, monkeypatch):
        """Lines 751-754: DaemonUnrecoverableError bypasses generic except, propagates."""

        async def _raises(_config, _item):
            raise DaemonUnrecoverableError("worker fatal")

        monkeypatch.setattr("daemon.runner._handle_work_item", _raises)

        queue: asyncio.Queue = asyncio.Queue()
        await queue.put(FullCreatorDownload(creator_id=snowflake_id()))
        stop_event = asyncio.Event()

        with pytest.raises(DaemonUnrecoverableError, match="worker fatal"):
            await _worker_loop(config, queue, stop_event, use_following=False)

    @pytest.mark.asyncio
    async def test_post_processing_marks_redownload_creator_processed(
        self, config, entity_store, monkeypatch
    ):
        """Lines 788-791: RedownloadCreatorMedia → mark_creator_processed (no following refresh)."""
        creator_id = snowflake_id()

        marked: list[int] = []

        async def _noop_handler(_config, _item):
            return None

        async def _mark(cid):
            marked.append(cid)

        monkeypatch.setattr("daemon.runner._handle_work_item", _noop_handler)
        monkeypatch.setattr("daemon.runner.mark_creator_processed", _mark)

        queue: asyncio.Queue = asyncio.Queue()
        await queue.put(RedownloadCreatorMedia(creator_id=creator_id))
        stop_event = asyncio.Event()

        async def _stop_when_drained():
            await queue.join()
            stop_event.set()

        await asyncio.gather(
            _worker_loop(config, queue, stop_event, use_following=False),
            _stop_when_drained(),
        )

        assert marked == [creator_id]

    @pytest.mark.asyncio
    async def test_post_processing_marks_check_access_creator_processed(
        self, config, entity_store, monkeypatch
    ):
        """Lines 788-791: CheckCreatorAccess → mark_creator_processed."""
        creator_id = snowflake_id()
        marked: list[int] = []

        async def _noop_handler(_config, _item):
            return None

        async def _mark(cid):
            marked.append(cid)

        monkeypatch.setattr("daemon.runner._handle_work_item", _noop_handler)
        monkeypatch.setattr("daemon.runner.mark_creator_processed", _mark)

        queue: asyncio.Queue = asyncio.Queue()
        await queue.put(CheckCreatorAccess(creator_id=creator_id))
        stop_event = asyncio.Event()

        async def _stop_when_drained():
            await queue.join()
            stop_event.set()

        await asyncio.gather(
            _worker_loop(config, queue, stop_event, use_following=False),
            _stop_when_drained(),
        )

        assert marked == [creator_id]


class TestRefreshFollowing:
    """Lines 795-820: helper called from _worker_loop on FullCreatorDownload completion."""

    @pytest.mark.asyncio
    async def test_happy_path_updates_user_names_and_logs(
        self, config, monkeypatch, caplog
    ):
        """Lines 814-817: get_following_accounts returns names → updates config + info log."""
        caplog.set_level(logging.INFO)
        config.user_names = {"old"}

        async def _info_ok(_c, _s):
            return None

        async def _get_following(_c, _s):
            return {"new1", "new2", "new3"}

        monkeypatch.setattr("daemon.runner.get_creator_account_info", _info_ok)
        monkeypatch.setattr("daemon.runner.get_following_accounts", _get_following)

        await _refresh_following(config)

        assert config.user_names == {"new1", "new2", "new3"}
        info = _logged(caplog, "INFO")
        assert any("following list refreshed - 3 creators" in m for m in info)

    @pytest.mark.asyncio
    async def test_empty_new_names_does_not_clobber_user_names(
        self, config, monkeypatch
    ):
        """Lines 813 false branch: empty new_names → don't overwrite existing user_names."""
        config.user_names = {"unchanged"}

        async def _info_ok(_c, _s):
            return None

        async def _get_following(_c, _s):
            return set()  # empty

        monkeypatch.setattr("daemon.runner.get_creator_account_info", _info_ok)
        monkeypatch.setattr("daemon.runner.get_following_accounts", _get_following)

        await _refresh_following(config)

        assert config.user_names == {"unchanged"}

    @pytest.mark.asyncio
    async def test_exception_logged_does_not_raise(self, config, monkeypatch, caplog):
        """Lines 818-820: any exception → warning log + return without raising."""
        caplog.set_level(logging.WARNING)
        config.user_names = {"unchanged"}

        async def _raises(_c, _s):
            raise RuntimeError("refresh boom")

        monkeypatch.setattr("daemon.runner.get_creator_account_info", _raises)

        # Must not raise.
        await _refresh_following(config)

        warnings = _logged(caplog, "WARNING")
        assert any(
            "following list refresh failed" in m and "refresh boom" in m
            for m in warnings
        )
        # user_names unchanged on error.
        assert config.user_names == {"unchanged"}
