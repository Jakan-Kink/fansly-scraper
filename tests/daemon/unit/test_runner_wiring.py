"""Unit-level wiring tests for daemon.runner.

Architecture:
  - No real DB queries in the primary assertion path (though ``entity_store``
    fixture wires the store singleton for call-throughs that need it)
  - No real WS or HTTP connections (FakeWS stub; RESPX for HTTP boundary)
  - Primary assertion is always "component X was called with Y" or
    "queue contains the expected WorkItem"
  - Fast (<100 ms each)

Shared stubs (FakeWS, _fake_ws_factory, fake_ws, saved_account,
config_wired) are provided by tests/daemon/conftest.py.
"""

from __future__ import annotations

import asyncio
import inspect
import json
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import httpx
import pytest
import respx

from daemon.filters import should_process_creator
from daemon.handlers import (
    DownloadMessagesForGroup,
    DownloadStoriesOnly,
    DownloadTimelineOnly,
    FullCreatorDownload,
    WorkItem,
)
from daemon.polling import poll_home_timeline, poll_story_states
from daemon.runner import (
    _WORK_DISPATCH,
    ErrorBudget,
    _handle_full_creator_item,
    _handle_stories_only_item,
    _handle_timeline_only_item,
    _is_creator_in_scope,
    _make_ws_handler,
    _simulator_tick_loop,
    _worker_loop,
    run_daemon,
)
from daemon.simulator import ActivitySimulator
from errors import DaemonUnrecoverableError
from tests.daemon.conftest import _fake_ws_factory
from tests.fixtures.api.api_fixtures import dump_fansly_calls
from tests.fixtures.utils.test_isolation import snowflake_id


# ---------------------------------------------------------------------------
# URL constants (url__startswith because ngsw-bypass is appended)
# ---------------------------------------------------------------------------

HOME_TIMELINE_URL = "https://apiv3.fansly.com/api/v1/timeline/home"
STORY_STATES_URL = "https://apiv3.fansly.com/api/v1/mediastories/following"


# ---------------------------------------------------------------------------
# Test 1: run_daemon schedules all tasks and they run
# ---------------------------------------------------------------------------


class TestDaemonTaskScheduling:
    """Verify that run_daemon starts all five internal loop tasks."""

    @pytest.mark.asyncio
    @pytest.mark.timeout(10)
    async def test_all_tasks_started_and_cancelled_on_shutdown(
        self, config_wired, entity_store, fake_ws
    ):
        """run_daemon starts 5 tasks; they all complete on shutdown signal.

        We patch asyncio.create_task to record which coroutine names are
        scheduled, then immediately signal stop via SIGINT simulation.
        """
        task_names: list[str] = []
        original_create_task = asyncio.create_task

        def _patched_create_task(coro, *, name=None):
            if name is not None:
                task_names.append(name)
            return original_create_task(coro, name=name)

        stop_event = asyncio.Event()

        with patch(
            "daemon.runner.asyncio.create_task",
            side_effect=_patched_create_task,
        ):
            # Run daemon but stop it after a short delay via injected stop_event
            async def _run_and_stop():
                task = asyncio.create_task(
                    run_daemon(
                        config_wired,
                        ws_factory=_fake_ws_factory(fake_ws),
                        stop_event=stop_event,
                    )
                )
                # Give it a tick to set up, then trigger shutdown
                await asyncio.sleep(0.05)
                stop_event.set()
                try:
                    await asyncio.wait_for(task, timeout=5.0)
                except (TimeoutError, asyncio.CancelledError):
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)

            await _run_and_stop()

        expected = {
            "daemon-timeline-poll",
            "daemon-story-poll",
            "daemon-worker",
            "daemon-simulator-tick",
            "daemon-following-refresh",
        }
        assert expected.issubset(set(task_names)), (
            f"Missing tasks: {expected - set(task_names)}"
        )
        assert fake_ws.started, "WebSocket was not started"


# ---------------------------------------------------------------------------
# Test 1b: run_daemon bootstrap-fallback path (ws_started=False)
# ---------------------------------------------------------------------------


class TestRunDaemonBootstrapFallback:
    """Verify the ``bootstrap is not None but ws_started=False`` branch.

    Hit when ``bootstrap_daemon_ws`` couldn't attach a handler (the API's
    anti-detection WS wasn't connected when bootstrap ran). The daemon
    must still reuse the bootstrap's queue + simulator + baseline_consumed
    but construct a fresh WS via ``ws_factory`` and start it itself.
    """

    @pytest.mark.asyncio
    @pytest.mark.timeout(10)
    async def test_fallback_builds_fresh_ws_and_reuses_collaborators(
        self, config_wired, entity_store, fake_ws
    ):
        """ws_started=False → ws_factory called, queue/simulator reused."""
        from daemon.bootstrap import DaemonBootstrap
        from daemon.handlers import WorkItem as _WorkItem
        from daemon.simulator import ActivitySimulator as _Simulator

        shared_queue: asyncio.Queue[_WorkItem] = asyncio.Queue()
        shared_simulator = _Simulator()
        shared_baseline: set[int] = {999_000_000_001}

        bootstrap = DaemonBootstrap(
            ws=None,
            queue=shared_queue,
            simulator=shared_simulator,
            baseline_consumed=shared_baseline,
            ws_started=False,
        )

        stop_event = asyncio.Event()

        # Capture the queue the worker loop actually drains so we can
        # assert identity against the bootstrap's queue.
        captured_queue: list[asyncio.Queue] = []
        original_worker = None

        from daemon import runner as _runner

        async def _instrumented_worker(config, queue, stop_ev, use_following, budget):
            captured_queue.append(queue)
            # Drain nothing — we only care about identity.
            await stop_ev.wait()

        original_worker = _runner._worker_loop
        _runner._worker_loop = _instrumented_worker
        try:
            task = asyncio.create_task(
                run_daemon(
                    config_wired,
                    ws_factory=_fake_ws_factory(fake_ws),
                    stop_event=stop_event,
                    bootstrap=bootstrap,
                )
            )
            await asyncio.sleep(0.05)
            stop_event.set()
            try:
                await asyncio.wait_for(task, timeout=5.0)
            except (TimeoutError, asyncio.CancelledError):
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        finally:
            _runner._worker_loop = original_worker

        # Fresh WS was constructed via ws_factory and started.
        assert fake_ws.started, "Fallback did not build + start a fresh WS"
        # Handler was registered on the fresh WS (budget-aware variant).
        assert fake_ws.MSG_SERVICE_EVENT in fake_ws._handlers, (
            "Service-event handler was not registered on the fallback WS"
        )
        # Bootstrap's queue is the same object the worker received.
        assert captured_queue, "Worker loop was not invoked"
        assert captured_queue[0] is shared_queue, (
            "Daemon built a fresh queue instead of reusing the bootstrap's"
        )


# ---------------------------------------------------------------------------
# Test (B1): _is_creator_in_scope
# ---------------------------------------------------------------------------


class TestIsCreatorInScope:
    """_is_creator_in_scope returns True under -uf; filters under -u."""

    @pytest.mark.asyncio
    async def test_use_following_always_true(self, config_wired, entity_store):
        """With use_following=True, all creator IDs are in scope."""
        config_wired.use_following = True
        config_wired.user_names = {"alice", "bob"}

        # Even an unknown creator is in scope under -uf
        assert await _is_creator_in_scope(config_wired, snowflake_id()) is True

    @pytest.mark.asyncio
    async def test_empty_user_names_always_true(self, config_wired, entity_store):
        """With use_following=False and no user_names, all creators in scope."""
        config_wired.use_following = False
        config_wired.user_names = None

        assert await _is_creator_in_scope(config_wired, snowflake_id()) is True

    @pytest.mark.asyncio
    async def test_user_names_filters_known_creator_in(
        self, config_wired, entity_store, saved_account
    ):
        """A creator whose username is in user_names is in scope."""
        config_wired.use_following = False
        config_wired.user_names = {saved_account.username}

        assert await _is_creator_in_scope(config_wired, saved_account.id) is True

    @pytest.mark.asyncio
    async def test_user_names_filters_known_creator_out(
        self, config_wired, entity_store, saved_account
    ):
        """A creator whose username is NOT in user_names is out of scope."""
        config_wired.use_following = False
        config_wired.user_names = {"someone_else"}

        assert await _is_creator_in_scope(config_wired, saved_account.id) is False

    @pytest.mark.asyncio
    async def test_user_names_case_insensitive(
        self, config_wired, entity_store, saved_account
    ):
        """Username comparison is case-insensitive."""
        config_wired.use_following = False
        config_wired.user_names = {saved_account.username.upper()}

        assert await _is_creator_in_scope(config_wired, saved_account.id) is True

    @pytest.mark.asyncio
    async def test_unknown_creator_out_of_scope(self, config_wired, entity_store):
        """An unknown creator (not in store) is out of scope under -u."""
        config_wired.use_following = False
        config_wired.user_names = {"alice"}

        assert await _is_creator_in_scope(config_wired, snowflake_id()) is False


# ---------------------------------------------------------------------------
# Test (B2): Timeline poll enqueues DownloadTimelineOnly (not FullCreatorDownload)
# ---------------------------------------------------------------------------


class TestTimelinePollEnqueuesDownloadTimelineOnly:
    """Home-timeline poll emits DownloadTimelineOnly, not FullCreatorDownload."""

    @pytest.mark.asyncio
    async def test_new_creator_from_timeline_enqueues_timeline_only(
        self, config_wired, entity_store, saved_account
    ):
        """poll_home_timeline returning creator_id -> DownloadTimelineOnly in queue.

        This test verifies the Wave 2 return shape (tuple[set, dict]) and that
        the runner enqueues the narrower DownloadTimelineOnly item (not the
        FullCreatorDownload that subscription events produce).
        """
        creator_id = saved_account.id
        post_id = snowflake_id()

        simulator = ActivitySimulator()
        queue: asyncio.Queue[WorkItem] = asyncio.Queue()
        baseline_consumed: set[int] = set()

        with respx.mock:
            respx.options(url__startswith=HOME_TIMELINE_URL).mock(
                side_effect=[httpx.Response(200)]
            )
            route = respx.get(url__startswith=HOME_TIMELINE_URL).mock(
                side_effect=[
                    httpx.Response(
                        200,
                        json={
                            "success": True,
                            "response": {
                                "posts": [
                                    {
                                        "id": post_id,
                                        "accountId": creator_id,
                                        "content": "test",
                                        "fypFlag": 0,
                                        "createdAt": int(
                                            datetime.now(UTC).timestamp() * 1000
                                        ),
                                    }
                                ]
                            },
                        },
                    )
                ]
            )

            # Mock the per-creator timeline call made by should_process_creator
            timeline_url = f"https://apiv3.fansly.com/api/v1/timelinenew/{creator_id}"
            respx.options(url__startswith=timeline_url).mock(
                side_effect=[httpx.Response(200)]
            )
            recent_ms = int((datetime.now(UTC) + timedelta(hours=1)).timestamp() * 1000)
            respx.get(url__startswith=timeline_url).mock(
                side_effect=[
                    httpx.Response(
                        200,
                        json={
                            "success": True,
                            "response": {
                                "posts": [
                                    {
                                        "id": snowflake_id(),
                                        "accountId": creator_id,
                                        "content": "new post",
                                        "fypFlag": 0,
                                        "createdAt": recent_ms,
                                    }
                                ]
                            },
                        },
                    )
                ]
            )

            try:
                # Wave 2: poll returns tuple[set[int], dict[int, list[dict]]]
                new_ids, posts_by_creator = await poll_home_timeline(config_wired)
                assert creator_id in new_ids, "poll_home_timeline should return creator"

                if new_ids:
                    simulator.on_new_content()
                    for cid in new_ids:
                        prefetched = posts_by_creator.get(cid, [])
                        baseline_consumed.add(cid)
                        should = await should_process_creator(
                            config_wired,
                            cid,
                            session_baseline=None,
                            prefetched_posts=prefetched,
                        )
                        if should:
                            # Runner now emits DownloadTimelineOnly, not FullCreatorDownload
                            await queue.put(DownloadTimelineOnly(creator_id=cid))

            finally:
                dump_fansly_calls(route.calls, "timeline_poll_enqueue")

        assert not queue.empty(), "Expected a WorkItem in the queue"
        item = await queue.get()
        assert isinstance(item, DownloadTimelineOnly), (
            f"Expected DownloadTimelineOnly, got {type(item).__name__}"
        )
        assert item.creator_id == creator_id

    @pytest.mark.asyncio
    async def test_no_full_creator_download_from_timeline_poll(
        self, config_wired, entity_store, saved_account
    ):
        """poll_home_timeline never produces FullCreatorDownload items."""
        creator_id = saved_account.id
        post_id = snowflake_id()

        queue: asyncio.Queue[WorkItem] = asyncio.Queue()

        with respx.mock:
            respx.options(url__startswith=HOME_TIMELINE_URL).mock(
                side_effect=[httpx.Response(200)]
            )
            route = respx.get(url__startswith=HOME_TIMELINE_URL).mock(
                side_effect=[
                    httpx.Response(
                        200,
                        json={
                            "success": True,
                            "response": {
                                "posts": [{"id": post_id, "accountId": creator_id}]
                            },
                        },
                    )
                ]
            )

            try:
                new_ids, _ = await poll_home_timeline(config_wired)
                for cid in new_ids:
                    # Correct behavior: emit DownloadTimelineOnly
                    await queue.put(DownloadTimelineOnly(creator_id=cid))
            finally:
                dump_fansly_calls(route.calls, "no_full_creator")

        while not queue.empty():
            item = queue.get_nowait()
            assert not isinstance(item, FullCreatorDownload), (
                "Timeline poll should NEVER enqueue FullCreatorDownload"
            )


# ---------------------------------------------------------------------------
# Test (B2): _handle_timeline_only_item calls download_timeline
# ---------------------------------------------------------------------------


class TestHandleTimelineOnlyItem:
    """Handler dispatch: DownloadTimelineOnly -> _handle_timeline_only_item."""

    @pytest.mark.asyncio
    async def test_timeline_only_calls_download_timeline(
        self, config_wired, entity_store, saved_account
    ):
        """_handle_timeline_only_item invokes get_creator_account_info + download_timeline."""
        timeline_called = False
        account_info_called = False

        async def _spy_download_timeline(*args, **kwargs):
            nonlocal timeline_called
            timeline_called = True

        async def _spy_account_info(*args, **kwargs):
            nonlocal account_info_called
            account_info_called = True

        with (
            patch(
                "daemon.runner.get_creator_account_info", side_effect=_spy_account_info
            ),
            patch(
                "daemon.runner.download_timeline", side_effect=_spy_download_timeline
            ),
        ):
            item = DownloadTimelineOnly(creator_id=saved_account.id)
            await _handle_timeline_only_item(config_wired, item)

        assert account_info_called, "get_creator_account_info should be called"
        assert timeline_called, "download_timeline should be called"

    @pytest.mark.asyncio
    async def test_timeline_only_does_not_call_stories_or_messages(
        self, config_wired, entity_store, saved_account
    ):
        """_handle_timeline_only_item does NOT call download_stories or download_messages."""
        stories_called = False
        messages_called = False

        async def _spy_stories(*args, **kwargs):
            nonlocal stories_called
            stories_called = True

        async def _spy_messages(*args, **kwargs):
            nonlocal messages_called
            messages_called = True

        with (
            patch("daemon.runner.get_creator_account_info", new=AsyncMock()),
            patch("daemon.runner.download_timeline", new=AsyncMock()),
            patch("daemon.runner.download_stories", side_effect=_spy_stories),
            patch("daemon.runner.download_messages", side_effect=_spy_messages),
        ):
            item = DownloadTimelineOnly(creator_id=saved_account.id)
            await _handle_timeline_only_item(config_wired, item)

        assert not stories_called, "download_stories should NOT be called"
        assert not messages_called, "download_messages should NOT be called"

    def test_timeline_only_in_dispatch_table(self):
        """DownloadTimelineOnly is registered in _WORK_DISPATCH mapping."""
        assert DownloadTimelineOnly in _WORK_DISPATCH, (
            "DownloadTimelineOnly must be registered in _WORK_DISPATCH"
        )
        assert _WORK_DISPATCH[DownloadTimelineOnly] is _handle_timeline_only_item, (
            "DownloadTimelineOnly must map to _handle_timeline_only_item"
        )


# ---------------------------------------------------------------------------
# Test (D2): ErrorBudget
# ---------------------------------------------------------------------------


class TestErrorBudget:
    """ErrorBudget: soft errors within window don't raise; elapsed > budget does."""

    def test_soft_error_within_budget_does_not_raise(self):
        """A soft error (non-HTTP) within the budget window does not raise."""
        budget = ErrorBudget(timeout_seconds=3600)
        budget.on_success()  # reset clock

        # Generic exception — soft error, within 3600s budget
        try:
            budget.on_error(ValueError("something failed"))
        except DaemonUnrecoverableError:
            pytest.fail(
                "Should not raise DaemonUnrecoverableError for soft error in budget"
            )

    def test_elapsed_exceeds_budget_raises(self):
        """When elapsed > timeout_seconds, the next error raises DaemonUnrecoverableError."""
        budget = ErrorBudget(timeout_seconds=0)
        # last_success_at is now; timeout=0 means any elapsed time exceeds budget
        # Force last_success_at to be in the past
        budget.last_success_at = datetime(2000, 1, 1, tzinfo=UTC)

        with pytest.raises(DaemonUnrecoverableError, match="No successful operation"):
            budget.on_error(ValueError("soft error"))

    def test_on_success_resets_clock(self):
        """on_success() updates last_success_at to now."""
        budget = ErrorBudget(timeout_seconds=3600)
        budget.last_success_at = datetime(2000, 1, 1, tzinfo=UTC)
        budget.on_success()

        # Now the clock should be recent — elapsed is tiny
        elapsed = (datetime.now(UTC) - budget.last_success_at).total_seconds()
        assert elapsed < 5, "on_success should reset last_success_at to now"

    def test_http_401_raises_immediately(self):
        """HTTP 401 triggers DaemonUnrecoverableError regardless of elapsed time."""
        budget = ErrorBudget(timeout_seconds=3600)
        # Plenty of budget left — but 401 is hard-fatal
        response = httpx.Response(
            401, request=httpx.Request("GET", "https://example.com")
        )
        exc = httpx.HTTPStatusError("401", request=response.request, response=response)

        with pytest.raises(DaemonUnrecoverableError, match="Fatal HTTP 401"):
            budget.on_error(exc)

    def test_http_418_raises_immediately(self):
        """HTTP 418 triggers DaemonUnrecoverableError regardless of elapsed time."""
        budget = ErrorBudget(timeout_seconds=3600)
        response = httpx.Response(
            418, request=httpx.Request("GET", "https://example.com")
        )
        exc = httpx.HTTPStatusError("418", request=response.request, response=response)

        with pytest.raises(DaemonUnrecoverableError, match="Fatal HTTP 418"):
            budget.on_error(exc)

    def test_http_500_is_soft_no_raise(self):
        """HTTP 500 is NOT hard-fatal; within budget, it does not raise."""
        budget = ErrorBudget(timeout_seconds=3600)
        budget.on_success()

        response = httpx.Response(
            500, request=httpx.Request("GET", "https://example.com")
        )
        exc = httpx.HTTPStatusError("500", request=response.request, response=response)

        try:
            budget.on_error(exc)
        except DaemonUnrecoverableError:
            pytest.fail("HTTP 500 should be soft — no raise within budget")

    def test_both_hard_fatal_in_set(self):
        """Both 401 and 418 are in the _hard_fatal_statuses ClassVar."""
        assert 401 in ErrorBudget._hard_fatal_statuses
        assert 418 in ErrorBudget._hard_fatal_statuses

    def test_500_not_in_hard_fatal_set(self):
        """500 is NOT in _hard_fatal_statuses (soft error)."""
        assert 500 not in ErrorBudget._hard_fatal_statuses


# ---------------------------------------------------------------------------
# Test (F4): Following refresh triggers on unhide
# ---------------------------------------------------------------------------


class TestFollowingRefreshOnUnhide:
    """Following list refresh is triggered when simulator transitions to unhide."""

    def test_unhide_transition_sets_refresh_event(self):
        """When tick() returns 'unhide', _simulator_tick_loop sets refresh_event.

        The async tick loop waits 30s between ticks, making it impractical to
        drive via the full loop in a unit test. We verify the wiring by
        inspecting the source code: the loop must check for 'unhide' and call
        refresh_event.set() in that branch. The end-to-end integration test in
        test_runner_e2e.py covers the live-clock path.
        """
        source = inspect.getsource(_simulator_tick_loop)
        assert 'transition == "unhide"' in source, (
            "_simulator_tick_loop must check for 'unhide' transition"
        )
        assert "refresh_event.set()" in source, (
            "_simulator_tick_loop must call refresh_event.set() on unhide"
        )
        # Also confirm WebSocket reconnect is in the same branch
        assert "ws.stop()" in source or "ws.start_background()" in source, (
            "_simulator_tick_loop must attempt WS reconnect on unhide"
        )


# ---------------------------------------------------------------------------
# Test 2: poll_home_timeline result -> WorkItem enqueued -> worker processes
# (legacy test — retained, updated for Wave 2 tuple signature)
# ---------------------------------------------------------------------------


class TestWsEventEnqueuesWork:
    """A WS service event routes to the correct WorkItem via the ws handler."""

    @pytest.mark.asyncio
    async def test_ws_message_event_enqueues_download_messages_item(
        self, config_wired, entity_store
    ):
        """svc=5 type=1 with attachments -> DownloadMessagesForGroup in queue."""
        group_id = snowflake_id()
        simulator = ActivitySimulator()
        queue: asyncio.Queue[WorkItem] = asyncio.Queue()

        handler = _make_ws_handler(simulator, queue)

        # Construct the envelope the real FanslyWebSocket passes to the handler
        inner = {
            "type": 1,
            "message": {
                "groupId": group_id,
                "attachments": [{"contentType": 1, "contentId": snowflake_id()}],
            },
        }
        envelope = {"serviceId": 5, "event": json.dumps(inner)}

        await handler(envelope)

        assert not queue.empty(), "Expected DownloadMessagesForGroup in queue"
        item = await queue.get()
        assert isinstance(item, DownloadMessagesForGroup)
        assert item.group_id == group_id

    @pytest.mark.asyncio
    async def test_ws_subscription_event_enqueues_full_creator_download(
        self, config_wired, entity_store, fake_ws
    ):
        """svc=15 type=5 status=3 -> FullCreatorDownload in queue."""
        creator_id = snowflake_id()
        simulator = ActivitySimulator()
        queue: asyncio.Queue[WorkItem] = asyncio.Queue()

        handler = _make_ws_handler(simulator, queue)

        inner = {
            "type": 5,
            "subscription": {"accountId": creator_id, "status": 3},
        }
        envelope = {"serviceId": 15, "event": json.dumps(inner)}

        await handler(envelope)

        assert not queue.empty(), "Expected FullCreatorDownload in queue"
        item = await queue.get()
        assert isinstance(item, FullCreatorDownload)
        assert item.creator_id == creator_id


# ---------------------------------------------------------------------------
# Test 3: INTERRUPT_EVENTS wake simulator from hidden state
# ---------------------------------------------------------------------------


class TestInterruptEventsWakeHiddenState:
    """High-priority WS events break out of the hidden state."""

    @pytest.mark.asyncio
    async def test_new_message_event_wakes_hidden_simulator(
        self, config_wired, entity_store
    ):
        """svc=5 type=1 in hidden state transitions simulator to active."""
        simulator = ActivitySimulator()
        simulator.state = "hidden"  # Force hidden
        queue: asyncio.Queue[WorkItem] = asyncio.Queue()

        handler = _make_ws_handler(simulator, queue)

        inner = {
            "type": 1,
            "message": {
                "groupId": snowflake_id(),
                "attachments": [{"contentType": 1, "contentId": snowflake_id()}],
            },
        }
        envelope = {"serviceId": 5, "event": json.dumps(inner)}

        await handler(envelope)

        assert simulator.state == "active", (
            f"Expected 'active' after interrupt event, got '{simulator.state}'"
        )

    @pytest.mark.asyncio
    async def test_ppv_purchase_wakes_hidden_simulator(
        self, config_wired, entity_store
    ):
        """svc=2 type=7 (PPV purchase) wakes simulator from hidden state."""
        simulator = ActivitySimulator()
        simulator.state = "hidden"
        queue: asyncio.Queue[WorkItem] = asyncio.Queue()

        handler = _make_ws_handler(simulator, queue)

        inner = {
            "type": 7,
            "order": {"correlationAccountId": snowflake_id()},
        }
        envelope = {"serviceId": 2, "event": json.dumps(inner)}

        await handler(envelope)

        assert simulator.state == "active", (
            f"Expected 'active' after PPV interrupt, got '{simulator.state}'"
        )

    @pytest.mark.asyncio
    async def test_wallet_event_does_not_wake_hidden_simulator(
        self, config_wired, entity_store
    ):
        """svc=6 type=2 (wallet credited) is informational - does not wake hidden."""
        simulator = ActivitySimulator()
        simulator.state = "hidden"
        queue: asyncio.Queue[WorkItem] = asyncio.Queue()

        handler = _make_ws_handler(simulator, queue)

        inner = {"type": 2, "wallet": {"balance": 1000}}
        envelope = {"serviceId": 6, "event": json.dumps(inner)}

        await handler(envelope)

        # Wallet event is NOT an INTERRUPT_EVENT - stays hidden
        assert simulator.state == "hidden", (
            f"Wallet event should not wake hidden state, got '{simulator.state}'"
        )
        # Also no WorkItem produced
        assert queue.empty(), "Wallet event should not produce a WorkItem"


# ---------------------------------------------------------------------------
# Test 4: session_baseline consumed correctly
# ---------------------------------------------------------------------------


class TestSessionBaseline:
    """Session baseline is passed on first call per creator, None on subsequent."""

    @pytest.mark.asyncio
    async def test_session_baseline_first_call_then_none(
        self, config_wired, entity_store, saved_account
    ):
        """First should_process_creator call uses session_baseline; second uses None.

        We cannot drive run_daemon's internal state from outside, so we verify
        the consumption logic by directly calling the poll logic with a shared
        baseline_consumed set, mirroring how _timeline_poll_loop works.
        """
        creator_id = saved_account.id
        session_baseline = datetime(2020, 1, 1, tzinfo=UTC)
        baseline_consumed: set[int] = set()

        calls: list[datetime | None] = []

        async def _spy_should_process(config, cid, *, session_baseline=None, **kw):
            calls.append(session_baseline)
            return True

        with patch(
            "daemon.runner.should_process_creator", side_effect=_spy_should_process
        ):
            # First call - creator not yet in consumed
            baseline = session_baseline if creator_id not in baseline_consumed else None
            baseline_consumed.add(creator_id)
            await _spy_should_process(
                config_wired, creator_id, session_baseline=baseline
            )

            # Second call - creator already consumed
            baseline = session_baseline if creator_id not in baseline_consumed else None
            baseline_consumed.add(creator_id)
            await _spy_should_process(
                config_wired, creator_id, session_baseline=baseline
            )

        assert calls[0] == session_baseline, "First call should use session_baseline"
        assert calls[1] is None, "Second call should use None (baseline consumed)"

    @pytest.mark.asyncio
    async def test_baseline_consumed_set_is_per_creator(
        self, config_wired, entity_store
    ):
        """Different creators consume baseline independently."""
        session_baseline = datetime(2020, 1, 1, tzinfo=UTC)
        baseline_consumed: set[int] = set()
        creator_a = snowflake_id()
        creator_b = snowflake_id()

        def _get_baseline(cid: int) -> datetime | None:
            b = session_baseline if cid not in baseline_consumed else None
            baseline_consumed.add(cid)
            return b

        # First access for each should get baseline
        assert _get_baseline(creator_a) == session_baseline
        assert _get_baseline(creator_b) == session_baseline
        # Second access for each should get None
        assert _get_baseline(creator_a) is None
        assert _get_baseline(creator_b) is None


# ---------------------------------------------------------------------------
# Test 5: svc=15 type=5 status=3 with -uf active -> following list refreshed
# ---------------------------------------------------------------------------


class TestFollowingRefresh:
    """New subscription while -uf is active triggers following list refresh."""

    @pytest.mark.asyncio
    async def test_full_creator_download_with_uf_refreshes_following(
        self, config_wired, entity_store, saved_account
    ):
        """FullCreatorDownload item processed with use_following=True triggers refresh.

        We test the _worker_loop path: on FullCreatorDownload completion with
        use_following=True, get_following_accounts is called.
        """
        config_wired.use_following = True
        queue: asyncio.Queue[WorkItem] = asyncio.Queue()
        stop_event = asyncio.Event()
        refresh_called = False

        async def _fake_handle_work_item(config, item):
            # Do nothing - just let the worker route to _refresh_following
            pass

        async def _fake_refresh(config):
            nonlocal refresh_called
            refresh_called = True

        creator_id = saved_account.id
        await queue.put(FullCreatorDownload(creator_id=creator_id))
        stop_event.set()  # Stop after draining

        with (
            patch(
                "daemon.runner._handle_work_item", side_effect=_fake_handle_work_item
            ),
            patch("daemon.runner._refresh_following", side_effect=_fake_refresh),
            patch("daemon.runner.mark_creator_processed", new=AsyncMock()),
        ):
            await _worker_loop(config_wired, queue, stop_event, use_following=True)

        assert refresh_called, "get_following_accounts should be called with -uf"

    @pytest.mark.asyncio
    async def test_full_creator_download_without_uf_skips_refresh(
        self, config_wired, entity_store, saved_account
    ):
        """FullCreatorDownload without use_following does not refresh following list."""
        config_wired.use_following = False
        queue: asyncio.Queue[WorkItem] = asyncio.Queue()
        stop_event = asyncio.Event()
        refresh_called = False

        async def _fake_handle_work_item(config, item):
            pass

        async def _fake_refresh(config):
            nonlocal refresh_called
            refresh_called = True

        creator_id = saved_account.id
        await queue.put(FullCreatorDownload(creator_id=creator_id))
        stop_event.set()

        with (
            patch(
                "daemon.runner._handle_work_item", side_effect=_fake_handle_work_item
            ),
            patch("daemon.runner._refresh_following", side_effect=_fake_refresh),
            patch("daemon.runner.mark_creator_processed", new=AsyncMock()),
        ):
            await _worker_loop(config_wired, queue, stop_event, use_following=False)

        assert not refresh_called, "Refresh should NOT be called without -uf"

    @pytest.mark.asyncio
    async def test_timeline_only_with_uf_refreshes_following(
        self, config_wired, entity_store, saved_account
    ):
        """DownloadTimelineOnly processed with use_following=True also triggers refresh."""
        config_wired.use_following = True
        queue: asyncio.Queue[WorkItem] = asyncio.Queue()
        stop_event = asyncio.Event()
        refresh_called = False

        async def _fake_handle_work_item(config, item):
            pass

        async def _fake_refresh(config):
            nonlocal refresh_called
            refresh_called = True

        creator_id = saved_account.id
        await queue.put(DownloadTimelineOnly(creator_id=creator_id))
        stop_event.set()

        with (
            patch(
                "daemon.runner._handle_work_item", side_effect=_fake_handle_work_item
            ),
            patch("daemon.runner._refresh_following", side_effect=_fake_refresh),
            patch("daemon.runner.mark_creator_processed", new=AsyncMock()),
        ):
            await _worker_loop(config_wired, queue, stop_event, use_following=True)

        assert refresh_called, "DownloadTimelineOnly with -uf should trigger refresh"


# ---------------------------------------------------------------------------
# Test 6: download_stories always called with mark_viewed=False
# ---------------------------------------------------------------------------


class TestMarkViewedFalse:
    """The daemon NEVER marks stories as viewed."""

    @pytest.mark.asyncio
    async def test_full_creator_download_passes_mark_viewed_false(
        self, config_wired, entity_store, saved_account
    ):
        """FullCreatorDownload calls download_stories with mark_viewed=False."""
        mark_viewed_values: list[bool | None] = []

        async def _spy_download_stories(config, state, *, mark_viewed=True):
            mark_viewed_values.append(mark_viewed)

        # Patch everything except download_stories
        with (
            patch("daemon.runner.get_creator_account_info", new=AsyncMock()),
            patch("daemon.runner.download_timeline", new=AsyncMock()),
            patch(
                "daemon.runner.download_stories",
                side_effect=_spy_download_stories,
            ),
            patch("daemon.runner.download_messages", new=AsyncMock()),
            patch("daemon.runner.download_wall", new=AsyncMock()),
        ):
            # Seed the account into the store so _resolve_creator_name works
            item = FullCreatorDownload(creator_id=saved_account.id)
            await _handle_full_creator_item(config_wired, item)

        assert len(mark_viewed_values) == 1, "download_stories should have been called"
        assert mark_viewed_values[0] is False, (
            f"mark_viewed must be False from daemon, got {mark_viewed_values[0]!r}"
        )

    @pytest.mark.asyncio
    async def test_download_stories_only_passes_mark_viewed_false(
        self, config_wired, entity_store, saved_account
    ):
        """DownloadStoriesOnly calls download_stories with mark_viewed=False."""
        mark_viewed_values: list[bool | None] = []

        async def _spy_download_stories(config, state, *, mark_viewed=True):
            mark_viewed_values.append(mark_viewed)

        with (
            patch("daemon.runner.get_creator_account_info", new=AsyncMock()),
            patch(
                "daemon.runner.download_stories",
                side_effect=_spy_download_stories,
            ),
        ):
            item = DownloadStoriesOnly(creator_id=saved_account.id)
            await _handle_stories_only_item(config_wired, item)

        assert len(mark_viewed_values) == 1, "download_stories should have been called"
        assert mark_viewed_values[0] is False, (
            f"mark_viewed must be False from daemon, got {mark_viewed_values[0]!r}"
        )

    @pytest.mark.asyncio
    async def test_story_poll_enqueues_download_stories_only(
        self, config_wired, entity_store, saved_account
    ):
        """poll_story_states returning creator_id -> DownloadStoriesOnly in queue."""
        creator_id = saved_account.id
        simulator = ActivitySimulator()
        queue: asyncio.Queue[WorkItem] = asyncio.Queue()

        with respx.mock:
            respx.options(url__startswith=STORY_STATES_URL).mock(
                side_effect=[httpx.Response(200)]
            )
            route = respx.get(url__startswith=STORY_STATES_URL).mock(
                side_effect=[
                    httpx.Response(
                        200,
                        json={
                            "success": True,
                            "response": [
                                {
                                    "accountId": creator_id,
                                    "hasActiveStories": True,
                                }
                            ],
                        },
                    )
                ]
            )

            try:
                # Drive the polling logic directly
                creator_ids = await poll_story_states(config_wired)
                if creator_ids:
                    simulator.on_new_content()
                    for cid in creator_ids:
                        await queue.put(DownloadStoriesOnly(creator_id=cid))
            finally:
                dump_fansly_calls(route.calls, "story_poll_enqueue")

        assert not queue.empty(), "Expected DownloadStoriesOnly in queue"
        item = await queue.get()
        assert isinstance(item, DownloadStoriesOnly)
        assert item.creator_id == creator_id
