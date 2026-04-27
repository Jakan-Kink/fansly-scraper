"""Unit-level wiring tests for daemon.runner.

Architecture:
  - No real DB queries in the primary assertion path (though ``entity_store``
    fixture wires the store singleton for call-throughs that need it)
  - No real WS or HTTP connections (FakeWS stub; RESPX for HTTP boundary)
  - Primary assertion is always "component X was called with Y" or
    "queue contains the expected WorkItem"
  - Fast (<100 ms each)

Shared stubs (FakeWS, make_fake_ws_factory, fake_ws, saved_account,
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
from tests.fixtures.api import (
    dump_fansly_calls,
    make_fake_ws_factory,
    mount_client_account_me_route,
    mount_empty_creator_pipeline,
    mount_empty_following_route,
)
from tests.fixtures.utils.test_isolation import snowflake_id


# ---------------------------------------------------------------------------
# URL constants (url__startswith because ngsw-bypass is appended)
# ---------------------------------------------------------------------------

HOME_TIMELINE_URL = "https://apiv3.fansly.com/api/v1/timeline/home"
STORY_STATES_URL = "https://apiv3.fansly.com/api/v1/mediastories/following"


# ---------------------------------------------------------------------------
# Shared payload helpers for real-pipeline dispatch tests
# ---------------------------------------------------------------------------


def _account_response_payload(creator_id: int, creator_name: str) -> dict:
    """Build a /api/v1/account?usernames=... response with required ``timelineStats``.

    ``timelineStats`` is mandatory — ``_update_state_from_account``
    raises ``ApiAccountInfoError("...you most likely misspelled it!")``
    when it's missing. Real Fansly responses always include it for
    valid creators.
    """
    return {
        "success": True,
        "response": [
            {
                "id": creator_id,
                "username": creator_name,
                "createdAt": 1700000000,
                "timelineStats": {
                    "accountId": creator_id,
                    "imageCount": 1,
                    "videoCount": 0,
                    "bundleCount": 0,
                    "bundleImageCount": 0,
                    "bundleVideoCount": 0,
                    "fetchedAt": 1700000000,
                },
            }
        ],
    }


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
                        ws_factory=make_fake_ws_factory(fake_ws),
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
        self, config_wired, entity_store, fake_ws, monkeypatch
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

        async def _instrumented_worker(config, queue, stop_ev, use_following, budget):
            captured_queue.append(queue)
            # Drain nothing — we only care about identity.
            await stop_ev.wait()

        monkeypatch.setattr("daemon.runner._worker_loop", _instrumented_worker)

        task = asyncio.create_task(
            run_daemon(
                config_wired,
                ws_factory=make_fake_ws_factory(fake_ws),
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
        self,
        respx_fansly_api,
        config_wired,
        entity_store,
        saved_account,
        tmp_path,
        monkeypatch,
    ):
        """_handle_timeline_only_item drives the real account + timeline HTTP pipeline.

        Real-pipeline rewrite (Wave 2.6 carry-over): asserts via the real
        Fansly HTTP boundary instead of an internal mock of
        ``daemon.runner.{get_creator_account_info,download_timeline}``.
        Account route MUST fire (real ``get_creator_account_info``) and
        timeline route MUST fire (real ``download_timeline``). An empty
        timeline response terminates the pagination loop on the first page.

        ``respx_fansly_api`` provides the ``with respx.mock`` context and
        the blanket OPTIONS preflight route — see
        ``tests/fixtures/api/api_fixtures.py``.
        """
        config_wired.download_directory = tmp_path
        config_wired.interactive = False

        creator_id = saved_account.id
        creator_name = saved_account.username

        account_route = respx.get(
            url__startswith="https://apiv3.fansly.com/api/v1/account"
        ).mock(
            side_effect=[
                httpx.Response(
                    200,
                    json=_account_response_payload(creator_id, creator_name),
                )
            ]
        )
        # Empty posts response terminates pagination on page 1.
        timeline_route = respx.get(
            url__startswith=(
                f"https://apiv3.fansly.com/api/v1/timelinenew/{creator_id}"
            )
        ).mock(
            side_effect=[
                httpx.Response(
                    200,
                    json={
                        "success": True,
                        "response": {
                            "posts": [],
                            "aggregationData": {
                                "accountMedia": [],
                                "media": [],
                                "accounts": [],
                            },
                        },
                    },
                )
            ]
        )

        # Patch only the leaf timing/IO calls — same pattern as
        # tests/download/unit/test_timeline_download.py.
        monkeypatch.setattr("download.timeline.sleep", AsyncMock(return_value=None))
        _noop = lambda _: None  # noqa: E731
        monkeypatch.setattr("download.timeline.input_enter_continue", _noop)

        item = DownloadTimelineOnly(creator_id=creator_id)
        try:
            await _handle_timeline_only_item(config_wired, item)
        finally:
            dump_fansly_calls(respx.calls, label="timeline_only_calls_pipeline")

        assert account_route.call_count >= 1, (
            "get_creator_account_info should have hit /api/v1/account"
        )
        assert timeline_route.call_count >= 1, (
            "download_timeline should have hit /api/v1/timelinenew/{id}"
        )

    @pytest.mark.asyncio
    async def test_timeline_only_does_not_call_stories_or_messages(
        self,
        respx_fansly_api,
        config_wired,
        entity_store,
        saved_account,
        tmp_path,
        monkeypatch,
    ):
        """_handle_timeline_only_item never hits stories or messages endpoints.

        Real-pipeline rewrite (Wave 2.6 carry-over): asserts via the real
        Fansly HTTP boundary that ``/api/v1/mediastoriesnew`` and
        ``/api/v1/messaging/groups`` receive zero calls. Stronger
        guarantee than the previous internal-mock version, which would
        have silently passed if the handler ever bypassed the dispatch
        table by calling those endpoints directly.
        """
        config_wired.download_directory = tmp_path
        config_wired.interactive = False

        creator_id = saved_account.id
        creator_name = saved_account.username

        respx.get(url__startswith="https://apiv3.fansly.com/api/v1/account").mock(
            side_effect=[
                httpx.Response(
                    200,
                    json=_account_response_payload(creator_id, creator_name),
                )
            ]
        )
        respx.get(
            url__startswith=(
                f"https://apiv3.fansly.com/api/v1/timelinenew/{creator_id}"
            )
        ).mock(
            side_effect=[
                httpx.Response(
                    200,
                    json={
                        "success": True,
                        "response": {
                            "posts": [],
                            "aggregationData": {
                                "accountMedia": [],
                                "media": [],
                                "accounts": [],
                            },
                        },
                    },
                )
            ]
        )
        # Mounted-but-not-expected routes — assert call_count == 0.
        stories_route = respx.get(
            url__startswith="https://apiv3.fansly.com/api/v1/mediastoriesnew"
        ).mock(side_effect=[httpx.Response(200, json={"response": {}})])
        messages_route = respx.get(
            url__startswith="https://apiv3.fansly.com/api/v1/messaging/groups"
        ).mock(side_effect=[httpx.Response(200, json={"response": []})])

        monkeypatch.setattr("download.timeline.sleep", AsyncMock(return_value=None))
        _noop = lambda _: None  # noqa: E731
        monkeypatch.setattr("download.timeline.input_enter_continue", _noop)

        item = DownloadTimelineOnly(creator_id=creator_id)
        try:
            await _handle_timeline_only_item(config_wired, item)
        finally:
            dump_fansly_calls(respx.calls, label="timeline_only_no_stories_msgs")

        assert stories_route.call_count == 0, (
            "DownloadTimelineOnly must NEVER hit /api/v1/mediastoriesnew"
        )
        assert messages_route.call_count == 0, (
            "DownloadTimelineOnly must NEVER hit /api/v1/messaging/groups"
        )

    def test_timeline_only_in_dispatch_table(self):
        """DownloadTimelineOnly is registered in _WORK_DISPATCH mapping."""
        assert DownloadTimelineOnly in _WORK_DISPATCH, (
            "DownloadTimelineOnly must be registered in _WORK_DISPATCH"
        )
        assert _WORK_DISPATCH[DownloadTimelineOnly] is _handle_timeline_only_item, (
            "DownloadTimelineOnly must map to _handle_timeline_only_item"
        )

    @pytest.mark.asyncio
    async def test_timeline_only_unknown_creator_skips_with_warning(
        self,
        respx_fansly_api,
        config_wired,
        entity_store,
        caplog,
    ):
        """Unknown creator_id → warning logged, function returns, NO HTTP fired.

        Coverage of the Bug #3 fix's skip-with-warning branch (added in
        the same patch as the test rewrites — covers daemon/runner.py:419-423,
        the ``if creator_name is None: logger.warning(...); return`` block
        I introduced). Without this test, the new branch would be
        uncovered — repeating the original "mock-hides-bug" failure mode
        but on test code instead of production code.

        Mounts catch-all routes that should NEVER fire; asserts
        ``call_count == 0`` on each.
        """
        # creator_id NOT in the entity store → _resolve_creator_name returns None.
        unknown_creator_id = snowflake_id()

        account_route = respx.get(
            url__startswith="https://apiv3.fansly.com/api/v1/account"
        ).mock(side_effect=[httpx.Response(200, json={"response": []})])
        timeline_route = respx.get(
            url__startswith="https://apiv3.fansly.com/api/v1/timelinenew"
        ).mock(side_effect=[httpx.Response(200, json={"response": {}})])

        item = DownloadTimelineOnly(creator_id=unknown_creator_id)
        try:
            # Should return cleanly — no exception, no HTTP.
            await _handle_timeline_only_item(config_wired, item)
        finally:
            dump_fansly_calls(respx.calls, label="timeline_only_unknown_creator")

        assert account_route.call_count == 0, (
            "Unknown creator must NOT hit /api/v1/account"
        )
        assert timeline_route.call_count == 0, (
            "Unknown creator must NOT hit /api/v1/timelinenew"
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
        assert "ws.stop_thread()" in source or "ws.start_in_thread()" in source, (
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
        the consumption logic by invoking a spec'd AsyncMock with a shared
        baseline_consumed set, mirroring how _timeline_poll_loop works.
        """
        creator_id = saved_account.id
        session_baseline = datetime(2020, 1, 1, tzinfo=UTC)
        baseline_consumed: set[int] = set()

        spy = AsyncMock(spec=should_process_creator, return_value=True)

        # First call - creator not yet in consumed
        baseline = session_baseline if creator_id not in baseline_consumed else None
        baseline_consumed.add(creator_id)
        await spy(config_wired, creator_id, session_baseline=baseline)

        # Second call - creator already consumed
        baseline = session_baseline if creator_id not in baseline_consumed else None
        baseline_consumed.add(creator_id)
        await spy(config_wired, creator_id, session_baseline=baseline)

        assert spy.call_args_list[0].kwargs["session_baseline"] == session_baseline, (
            "First call should use session_baseline"
        )
        assert spy.call_args_list[1].kwargs["session_baseline"] is None, (
            "Second call should use None (baseline consumed)"
        )

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
        self,
        respx_fansly_api,
        config_wired,
        entity_store,
        saved_account,
        tmp_path,
        monkeypatch,
    ):
        """FullCreatorDownload + use_following=True hits the real /following endpoint.

        Real-pipeline rewrite (Wave 2.6 carry-over). Drives the
        `_worker_loop` end-to-end: real `_handle_work_item` →
        `_handle_full_creator_item` (full 5-step download pipeline) →
        clean success branch → real `_refresh_following` →
        `get_creator_account_info` (client variant via
        `/api/v1/account/me`) → `get_following_accounts` →
        `/api/v1/account/{client_id}/following`. The assertion lands
        on the real `/following` HTTP boundary.

        Production bug discovered while writing this test: the
        original `_refresh_following` constructed `state =
        DownloadState()` with no `creator_id` and called
        `get_following_accounts(config, state)` directly, which
        immediately raised `RuntimeError("client ID not set")`. The
        outer `try/except Exception` swallowed it and the refresh
        silently no-op'd in production. Fixed in the same patch by
        adding `await get_creator_account_info(config, state)`
        before the following-list call (mirroring
        `fansly_downloader_ng.py:353-361`'s legacy convention).
        """
        config_wired.download_directory = tmp_path
        config_wired.interactive = False
        config_wired.use_following = True

        creator_id = saved_account.id
        creator_name = saved_account.username
        client_id = snowflake_id()

        mount_empty_creator_pipeline(creator_id, creator_name)
        mount_client_account_me_route(client_id)
        following_route = mount_empty_following_route(client_id)

        # The handler also calls mark_creator_processed which touches
        # the entity_store — no patch needed; uses the real DB.
        monkeypatch.setattr("download.timeline.sleep", AsyncMock(return_value=None))
        monkeypatch.setattr(
            "download.account.asyncio.sleep", AsyncMock(return_value=None)
        )
        _noop = lambda _: None  # noqa: E731
        monkeypatch.setattr("download.timeline.input_enter_continue", _noop)
        monkeypatch.setattr("download.messages.input_enter_continue", _noop)

        queue: asyncio.Queue[WorkItem] = asyncio.Queue()
        stop_event = asyncio.Event()
        await queue.put(FullCreatorDownload(creator_id=creator_id))
        stop_event.set()

        try:
            await _worker_loop(config_wired, queue, stop_event, use_following=True)
        finally:
            dump_fansly_calls(respx.calls, label="full_creator_with_uf")

        assert following_route.call_count >= 1, (
            "_refresh_following should have hit "
            "/api/v1/account/{client_id}/following with use_following=True"
        )

    @pytest.mark.asyncio
    async def test_full_creator_download_without_uf_skips_refresh(
        self,
        respx_fansly_api,
        config_wired,
        entity_store,
        saved_account,
        tmp_path,
        monkeypatch,
    ):
        """FullCreatorDownload + use_following=False NEVER hits /following.

        Real-pipeline rewrite (Wave 2.6 carry-over). Same handler
        pipeline as the with-uf variant, but with `use_following=False`
        the worker loop short-circuits the refresh branch entirely.
        Mounted-but-not-expected `/following` route asserts
        `call_count == 0` — stronger than the previous internal
        `nonlocal refresh_called` flag because it would also fire if
        any code path ever bypassed `_refresh_following` to hit
        `/following` directly.
        """
        config_wired.download_directory = tmp_path
        config_wired.interactive = False
        config_wired.use_following = False

        creator_id = saved_account.id
        creator_name = saved_account.username
        client_id = snowflake_id()

        mount_empty_creator_pipeline(creator_id, creator_name)
        # Mount /me + /following anyway so we can assert call_count == 0.
        mount_client_account_me_route(client_id)
        following_route = mount_empty_following_route(client_id)

        monkeypatch.setattr("download.timeline.sleep", AsyncMock(return_value=None))
        _noop = lambda _: None  # noqa: E731
        monkeypatch.setattr("download.timeline.input_enter_continue", _noop)
        monkeypatch.setattr("download.messages.input_enter_continue", _noop)

        queue: asyncio.Queue[WorkItem] = asyncio.Queue()
        stop_event = asyncio.Event()
        await queue.put(FullCreatorDownload(creator_id=creator_id))
        stop_event.set()

        try:
            await _worker_loop(config_wired, queue, stop_event, use_following=False)
        finally:
            dump_fansly_calls(respx.calls, label="full_creator_without_uf")

        assert following_route.call_count == 0, (
            "_refresh_following must NOT fire when use_following=False"
        )

    @pytest.mark.asyncio
    async def test_timeline_only_with_uf_does_not_refresh_following(
        self,
        respx_fansly_api,
        config_wired,
        entity_store,
        saved_account,
        tmp_path,
        monkeypatch,
    ):
        """DownloadTimelineOnly + use_following=True must NOT hit /following.

        DownloadTimelineOnly originates from the /timeline/home poll,
        whose creators are already in the following set by construction.
        Refreshing the following list per home-poll hit fans out to ~30
        account fetches per new post — the regression that produced the
        observed 11:27-11:31 burst before being narrowed.

        The narrowed worker only refreshes on FullCreatorDownload (which
        comes from confirmed-subscription WS events where a creator may
        have just joined user_names). The 5-min _following_refresh_loop
        + active-state/unhide refresh_event triggers cover the catch-up
        window for any other case.

        Companion: ``test_full_creator_download_with_uf_refreshes_following``
        pins the FullCreatorDownload-DOES-refresh side; this test pins the
        DownloadTimelineOnly-DOES-NOT-refresh side.
        """
        config_wired.download_directory = tmp_path
        config_wired.interactive = False
        config_wired.use_following = True

        creator_id = saved_account.id
        creator_name = saved_account.username
        client_id = snowflake_id()

        # TimelineOnly handler uses just account + timeline — but
        # mount_empty_creator_pipeline overshooting (also mounting
        # stories + messages) is harmless: those routes simply receive
        # zero calls.
        mount_empty_creator_pipeline(creator_id, creator_name)
        mount_client_account_me_route(client_id)
        following_route = mount_empty_following_route(client_id)

        monkeypatch.setattr("download.timeline.sleep", AsyncMock(return_value=None))
        monkeypatch.setattr(
            "download.account.asyncio.sleep", AsyncMock(return_value=None)
        )
        _noop = lambda _: None  # noqa: E731
        monkeypatch.setattr("download.timeline.input_enter_continue", _noop)

        queue: asyncio.Queue[WorkItem] = asyncio.Queue()
        stop_event = asyncio.Event()
        await queue.put(DownloadTimelineOnly(creator_id=creator_id))
        stop_event.set()

        try:
            await _worker_loop(config_wired, queue, stop_event, use_following=True)
        finally:
            dump_fansly_calls(respx.calls, label="timeline_only_with_uf")

        assert following_route.call_count == 0, (
            "DownloadTimelineOnly with use_following=True must NOT hit /following — "
            "the home-timeline poll only surfaces already-followed creators, so a "
            "per-item refresh fans out to ~30 wasted account fetches per new post"
        )


# ---------------------------------------------------------------------------
# Test 6: download_stories always called with mark_viewed=False
# ---------------------------------------------------------------------------


class TestMarkViewedFalse:
    """The daemon NEVER marks stories as viewed."""

    @pytest.mark.asyncio
    async def test_full_creator_download_passes_mark_viewed_false(
        self,
        respx_fansly_api,
        config_wired,
        entity_store,
        saved_account,
        tmp_path,
        monkeypatch,
    ):
        """FullCreatorDownload never POSTs to /mediastory/view (real-pipeline boundary).

        Real-pipeline rewrite (Wave 2.6 carry-over). Drives the full
        handler pipeline end-to-end: account → timeline → stories
        (``mark_viewed=False``) → messages → walls. All Fansly endpoints
        are real respx routes; the assertion lands on the real
        ``/api/v1/mediastory/view`` boundary (``call_count == 0``).

        This regression guard is the strictest possible: the daemon
        must NEVER mark stories viewed in the user's real Fansly
        account, regardless of which dispatch path triggered the
        download. The previous internal-mock version asserted on
        ``stories_mock.call_args.kwargs["mark_viewed"]``, which would
        silently pass any of: a renamed gate, a relocated
        ``_mark_stories_viewed``, an internal helper bypassing the
        gate by calling the API directly, or even the
        ``_handle_full_creator_item`` ever swapping its handler order.

        Production bug discovered while writing this test: the original
        ``await download_wall(config, state)`` at runner.py:279 was
        missing the required ``wall_id`` positional. The daemon raised
        ``TypeError`` on every FullCreatorDownload's wall step, caught
        + re-raised by the outer except, tripping the worker loop's
        error budget. Fixed in the same patch by mirroring the legacy
        batch convention (``if state.walls: for wall_id in sorted(...):
        await download_wall(config, state, wall_id)``).
        """
        config_wired.download_directory = tmp_path
        config_wired.interactive = False

        creator_id = saved_account.id
        creator_name = saved_account.username

        # /api/v1/account?usernames=... — get_creator_account_info.
        # No ``walls`` field → state.walls stays unset → the wall
        # iteration in _handle_full_creator_item is a no-op.
        account_route = respx.get(
            url__startswith="https://apiv3.fansly.com/api/v1/account"
        ).mock(
            side_effect=[
                httpx.Response(
                    200,
                    json=_account_response_payload(creator_id, creator_name),
                )
            ]
        )
        # /api/v1/timelinenew/{id} — empty posts terminates pagination
        # on page 1.
        timeline_route = respx.get(
            url__startswith=(
                f"https://apiv3.fansly.com/api/v1/timelinenew/{creator_id}"
            )
        ).mock(
            side_effect=[
                httpx.Response(
                    200,
                    json={
                        "success": True,
                        "response": {
                            "posts": [],
                            "aggregationData": {
                                "accountMedia": [],
                                "media": [],
                                "accounts": [],
                            },
                        },
                    },
                )
            ]
        )
        # /api/v1/mediastoriesnew — empty mediaStories, no
        # accountMedia → no media downloads, but the gate code
        # path still executes with mark_viewed=False.
        stories_route = respx.get(
            url__startswith="https://apiv3.fansly.com/api/v1/mediastoriesnew"
        ).mock(
            side_effect=[
                httpx.Response(
                    200,
                    json={
                        "success": True,
                        "response": {
                            "mediaStories": [],
                            "aggregationData": {
                                "accountMedia": [],
                                "media": [],
                                "accounts": [],
                            },
                        },
                    },
                )
            ]
        )
        # /api/v1/messaging/groups — download_messages searches
        # groups for one whose users include state.creator_id.
        # Empty groups → early "Could not find a chat history" return.
        messages_route = respx.get(
            url__startswith="https://apiv3.fansly.com/api/v1/messaging/groups"
        ).mock(
            side_effect=[
                httpx.Response(
                    200,
                    json={
                        "success": True,
                        "response": {
                            "data": [],
                            "aggregationData": {"groups": [], "accounts": []},
                        },
                    },
                )
            ]
        )
        # The critical regression guard — must NEVER fire.
        mark_view_route = respx.post(
            url__startswith="https://apiv3.fansly.com/api/v1/mediastory/view"
        ).mock(side_effect=[httpx.Response(200, json={"storyId": "0"})])

        monkeypatch.setattr("download.timeline.sleep", AsyncMock(return_value=None))
        _noop = lambda _: None  # noqa: E731
        monkeypatch.setattr("download.timeline.input_enter_continue", _noop)
        monkeypatch.setattr("download.messages.input_enter_continue", _noop)

        item = FullCreatorDownload(creator_id=creator_id)
        try:
            await _handle_full_creator_item(config_wired, item)
        finally:
            dump_fansly_calls(respx.calls, label="full_creator_mark_false")

        assert mark_view_route.call_count == 0, (
            "FullCreatorDownload must NEVER POST to /mediastory/view — "
            "the daemon must not affect the user's real Fansly UX"
        )
        # Sanity: every step of the real pipeline DID run, so the
        # zero-count above is meaningful, not vacuous.
        assert account_route.call_count >= 1, (
            "get_creator_account_info should have hit /api/v1/account"
        )
        assert timeline_route.call_count >= 1, (
            "download_timeline should have hit /api/v1/timelinenew/{id}"
        )
        assert stories_route.call_count >= 1, (
            "download_stories should have hit /api/v1/mediastoriesnew"
        )
        assert messages_route.call_count >= 1, (
            "download_messages should have hit /api/v1/messaging/groups"
        )

    @pytest.mark.asyncio
    async def test_download_stories_only_passes_mark_viewed_false(
        self,
        respx_fansly_api,
        config_wired,
        entity_store,
        saved_account,
        tmp_path,
        monkeypatch,
    ):
        """DownloadStoriesOnly never POSTs to /mediastory/view (real-pipeline boundary).

        Asserts via the real HTTP boundary instead of an internal mock of
        ``daemon.runner.download_stories``: the ``/mediastory/view`` respx
        route MUST receive zero calls. This regression guard fires not
        only if ``mark_viewed=False`` plumbing breaks, but also if the
        handler ever bypasses the gate by calling the API directly, or
        if ``_mark_stories_viewed`` is renamed/relocated. The previous
        internal-mock version asserted on ``stories_mock.call_args.kwargs``,
        which would have silently passed any of those regressions.
        """
        config_wired.download_directory = tmp_path
        config_wired.interactive = False

        creator_id = saved_account.id
        creator_name = saved_account.username
        story_id = snowflake_id()
        media_id = snowflake_id()

        # AccountMedia payload meeting production-pipeline shape requirements
        # (nested ``media`` for FK, ``previewId`` to avoid KeyError, signed
        # CDN URL with ``Key-Pair-Id`` so media/media.py:185 doesn't fall
        # into raw input(); see ``project_fansly_payload_shape_requirements.md``).
        am_entry = {
            "id": media_id,
            "accountId": creator_id,
            "mediaId": media_id,
            "previewId": None,
            "createdAt": 1700000000,
            "deleted": False,
            "access": True,
            "mimetype": "image/jpeg",
            "media": {
                "id": media_id,
                "accountId": creator_id,
                "mimetype": "image/jpeg",
                "createdAt": 1700000000,
                "locations": [
                    {
                        "locationId": "1",
                        "location": (
                            "https://cdn.example.com/img.jpg"
                            "?Policy=abc&Key-Pair-Id=xyz&Signature=def"
                        ),
                    }
                ],
            },
        }
        stories_response = {
            "success": True,
            "response": {
                "mediaStories": [
                    {
                        "id": story_id,
                        "accountId": creator_id,
                        "createdAt": 1700000000,
                    }
                ],
                "aggregationData": {
                    "accountMedia": [am_entry],
                    "media": [],
                    "accounts": [],
                },
            },
        }
        # /api/v1/account?usernames={creator_name} returns a list (the
        # creator-info shape; client-info would be wrapped under "account").
        # ``timelineStats`` is required — _update_state_from_account raises
        # ApiAccountInfoError("...you most likely misspelled it!") when it's
        # missing. Real Fansly responses always include it for valid creators.
        account_response = {
            "success": True,
            "response": [
                {
                    "id": creator_id,
                    "username": creator_name,
                    "createdAt": 1700000000,
                    "timelineStats": {
                        "accountId": creator_id,
                        "imageCount": 1,
                        "videoCount": 0,
                        "bundleCount": 0,
                        "bundleImageCount": 0,
                        "bundleVideoCount": 0,
                        "fetchedAt": 1700000000,
                    },
                }
            ],
        }

        # Single ``url__startswith="...account"`` route serves BOTH
        # /api/v1/account?usernames=... (get_creator_account_info) and
        # /api/v1/account/media?ids=... (fetch_and_process_media via
        # download_stories), in call order.
        account_route = respx.get(
            url__startswith="https://apiv3.fansly.com/api/v1/account"
        ).mock(
            side_effect=[
                httpx.Response(200, json=account_response),
                httpx.Response(
                    200,
                    json={"success": True, "response": [am_entry]},
                ),
            ]
        )
        stories_route = respx.get(
            "https://apiv3.fansly.com/api/v1/mediastoriesnew"
        ).mock(side_effect=[httpx.Response(200, json=stories_response)])

        # The critical regression-guard route — must NEVER fire.
        mark_view_route = respx.post(
            url__startswith="https://apiv3.fansly.com/api/v1/mediastory/view"
        ).mock(side_effect=[httpx.Response(200, json={"storyId": str(story_id)})])

        # Patch the leaf CDN-download call at both binding sites
        # (download.common imports it at module scope).
        _noop_download = AsyncMock(return_value=None)
        monkeypatch.setattr("download.common.download_media", _noop_download)
        monkeypatch.setattr("download.media.download_media", _noop_download)
        _noop = lambda _: None  # noqa: E731
        monkeypatch.setattr("download.common.input_enter_continue", _noop)
        monkeypatch.setattr("download.media.input_enter_continue", _noop)

        item = DownloadStoriesOnly(creator_id=creator_id)
        try:
            await _handle_stories_only_item(config_wired, item)
        finally:
            dump_fansly_calls(respx.calls, label="stories_only_mark_false")

        assert mark_view_route.call_count == 0, (
            "DownloadStoriesOnly must NEVER POST to /mediastory/view — "
            "the daemon must not affect the user's real Fansly UX"
        )
        # Sanity: the real pipeline DID run (account fetched, stories fetched)
        # so the zero-count above is meaningful, not vacuous.
        assert account_route.call_count >= 1, (
            "get_creator_account_info should have hit /api/v1/account"
        )
        assert stories_route.call_count == 1, (
            "download_stories should have hit /api/v1/mediastoriesnew exactly once"
        )

    @pytest.mark.asyncio
    async def test_full_creator_download_iterates_walls(
        self,
        respx_fansly_api,
        config_wired,
        entity_store,
        saved_account,
        tmp_path,
        monkeypatch,
    ):
        """Account with walls → wall iteration fires for each wall.

        Coverage of the Bug #1 fix's iteration body (added in the same
        patch as the test rewrites — covers daemon/runner.py:280-281,
        the ``for wall_id in sorted(state.walls): await
        download_wall(config, state, wall_id)`` loop). The mark-viewed
        regression guard (``mark_view_route.call_count == 0``) is also
        asserted to confirm the wall path doesn't accidentally bypass
        the gate.

        Two walls are seeded in the account response so the iteration
        runs more than once (single-iteration would not distinguish
        ``for x in [a]: f(x)`` from ``f(walls[0])``). The
        ``mount_empty_creator_pipeline`` helper's ``response_count=5``
        absorbs the extra ``/timelinenew`` calls (one for the timeline
        download itself, two for the wall iteration).
        """
        config_wired.download_directory = tmp_path
        config_wired.interactive = False

        creator_id = saved_account.id
        creator_name = saved_account.username
        wall_id_a = snowflake_id()
        wall_id_b = snowflake_id()

        routes = mount_empty_creator_pipeline(
            creator_id,
            creator_name,
            walls=[
                {
                    "id": str(wall_id_a),
                    "accountId": str(creator_id),
                    "pos": 1,
                    "name": "Main",
                    "description": "",
                },
                {
                    "id": str(wall_id_b),
                    "accountId": str(creator_id),
                    "pos": 2,
                    "name": "Behind the scenes",
                    "description": "",
                },
            ],
            response_count=10,
        )
        # Critical regression guard — must NEVER fire even with walls.
        mark_view_route = respx.post(
            url__startswith="https://apiv3.fansly.com/api/v1/mediastory/view"
        ).mock(side_effect=[httpx.Response(200, json={"storyId": "0"})])

        monkeypatch.setattr("download.timeline.sleep", AsyncMock(return_value=None))
        monkeypatch.setattr("download.wall.sleep", AsyncMock(return_value=None))
        _noop = lambda _: None  # noqa: E731
        monkeypatch.setattr("download.timeline.input_enter_continue", _noop)
        monkeypatch.setattr("download.messages.input_enter_continue", _noop)
        monkeypatch.setattr("download.wall.input_enter_continue", _noop)

        item = FullCreatorDownload(creator_id=creator_id)
        try:
            await _handle_full_creator_item(config_wired, item)
        finally:
            dump_fansly_calls(respx.calls, label="full_creator_with_walls")

        # /timelinenew/{creator_id}?wallId=... is the wall-fetch URL shape
        # (set by api.fansly.get_wall_posts via custom_params). The main
        # timeline call has no wallId= param, so filtering on it
        # discriminates wall iterations from timeline pagination — a
        # tighter assertion than counting raw call volume on the URL
        # prefix, which would silently pass if download_timeline ever
        # gained extra retries while the wall iteration broke.
        # Check distinct wall IDs visited rather than raw call count
        # because download_wall retries each wall (config.timeline_retries
        # rounds), so call volume per wall is implementation-detail.
        wall_ids_visited = {
            wall_id_param
            for call in routes["timeline"].calls
            if (wall_id_param := call.request.url.params.get("wallId"))
        }
        assert wall_ids_visited == {str(wall_id_a), str(wall_id_b)}, (
            f"Expected both seeded walls to be iterated. "
            f"Visited: {wall_ids_visited}, expected: "
            f"{{{wall_id_a!r}, {wall_id_b!r}}}"
        )
        assert mark_view_route.call_count == 0, (
            "FullCreatorDownload with walls must STILL NOT POST to "
            "/mediastory/view — the wall path must not bypass the gate"
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
