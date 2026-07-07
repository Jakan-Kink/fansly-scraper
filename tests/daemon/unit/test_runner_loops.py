"""Unit tests for daemon.runner orchestration loops (Wave 6 item #2).

Targets the previously-uncovered orchestration-layer branches in:
- _process_timeline_candidate out-of-scope + exception paths
- _timeline_poll_loop hidden state + happy path + error + DaemonUnrecoverable
- _story_poll_loop similar surface
- _following_refresh_loop early-return + idle/hidden + happy + error
- _simulator_tick_loop unhide + ws-reconnect-error + heartbeat
- _worker_loop exception paths + post-processing + _refresh_following

Mocking boundary (mission #1 — remove internal mocks):
- The two poll loops drive the REAL ``poll_home_timeline`` /
  ``poll_story_states`` end-to-end through respx (``respx_fansly_api``
  wires a real FanslyApi into ``mock_config``; the ``entity_store``
  fixture registers the real global store). A ConnectError/500 response
  forces the real error-log branch INSIDE the poll function.
- ``_is_creator_in_scope`` and ``should_process_creator`` are driven to
  their natural True/False outcomes by configuring real inputs
  (``config.user_names``, real ``Account`` / ``MonitorState`` rows via the
  store, prefetched posts) — never patched to a fixed return.
- ``mark_creator_processed`` runs real against the store (an ``Account``
  row is seeded first so the FK holds); the resulting ``MonitorState`` row
  is asserted on.
- ``get_following_accounts`` / ``get_creator_account_info`` are
  download-layer ENTRYPOINTS with their own respx+DB coverage; here they
  are replaced with ``async_noop_spy()`` (dispatch-only) or an
  ``AsyncMock`` returning a set. The returned set is a boundary INPUT the
  loop-under-test consumes, not internal behaviour of the loop itself.
- ``_handle_work_item`` is a dispatch router with its own coverage; worker
  tests replace it with ``async_noop_spy()`` (clean-success path) or a
  raiser (error path) — the WorkItem plumbing / post-processing under test
  runs real.
- A handful of DEFENSIVE re-raise / fallback branches (documented inline
  as DEFENSIVE-BRANCH) are only reachable by substituting a raising callee,
  because the real callee is total. Those substitutions exercise the loop's
  own contract, not the callee's behaviour.

Each loop test wires a real ActivitySimulator (or the StubSimulator fake
for branches the real state machine can't reach without external
transitions) + real ErrorBudget + real asyncio.Event + a fast dashboard
stub (no real waits).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx

from api.fansly import FanslyApi
from daemon.dashboard import NullDashboard
from daemon.handlers import (
    CheckCreatorAccess,
    DownloadMessagesForGroup,
    DownloadStoriesOnly,
    DownloadTimelineOnly,
    FullCreatorDownload,
    RedownloadCreatorMedia,
)
from daemon.runner import (
    ErrorBudget,
    _following_refresh_loop,
    _process_timeline_candidate,
    _refresh_following,
    _simulator_tick_loop,
    _story_poll_loop,
    _timeline_poll_loop,
    _worker_loop,
)
from daemon.simulator import ActivitySimulator
from errors import DaemonUnrecoverableError
from metadata.entity_store import PostgresEntityStore
from metadata.models import Account, MonitorState
from tests.fixtures.api import FakeWS
from tests.fixtures.api.api_fixtures import dump_fansly_calls
from tests.fixtures.daemon import StubSimulator, async_noop_spy
from tests.fixtures.utils.test_isolation import snowflake_id


# ---------------------------------------------------------------------------
# URL aliases — url__startswith because ngsw-bypass + query params are appended
# ---------------------------------------------------------------------------

HOME_TIMELINE_URL = FanslyApi.TIMELINE_HOME_ENDPOINT
STORY_STATES_URL = FanslyApi.MEDIA_STORIES_FOLLOWING_ENDPOINT
TIMELINE_NEW_URL = FanslyApi.TIMELINE_NEW_ENDPOINT.format("")


def _logged(caplog: pytest.LogCaptureFixture, level: str) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.levelname == level]


def _home_timeline_response(posts: list[dict]) -> httpx.Response:
    """Wrap posts in the /timeline/home JSON envelope."""
    return httpx.Response(
        200,
        json={"success": True, "response": {"posts": posts}},
    )


def _story_states_response(states: list[dict]) -> httpx.Response:
    """Wrap story-state entries in the /mediastories/following JSON envelope."""
    return httpx.Response(
        200,
        json={"success": True, "response": states},
    )


def _post_dict(post_id: int, account_id: int, created_at_ms: int | None = None) -> dict:
    """Minimal home-timeline post dict."""
    if created_at_ms is None:
        created_at_ms = int(datetime.now(UTC).timestamp() * 1000)
    return {
        "id": post_id,
        "accountId": account_id,
        "content": "test content",
        "fypFlag": 0,
        "createdAt": created_at_ms,
    }


def _story_state_dict(account_id: int, *, has_active: bool) -> dict:
    """Minimal story-state dict."""
    return {
        "accountId": account_id,
        "hasActiveStories": has_active,
        "storyCount": 0,
    }


async def _seed_account(
    entity_store: PostgresEntityStore, creator_id: int, username: str
) -> Account:
    """Persist a real Account row (FK precondition for scope/state paths)."""
    account = Account(
        id=creator_id,
        username=username,
        displayName=f"Display {username}",
        createdAt=datetime.now(UTC),
    )
    await entity_store.save(account)
    return account


class _FastDashboard(NullDashboard):
    """Test dashboard with no real waits.

    wait_with_countdown returns immediately so loop iterations don't pay
    the production countdown durations (300s for following-refresh, 30s
    for simulator tick, etc.). All other methods record for assertions.
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
        # stop_event (set from inside the real poll response side-effect).
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


async def _stop_soon(stop_event: asyncio.Event) -> None:
    """Set stop_event after a couple of event-loop ticks."""
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    stop_event.set()


# ---------------------------------------------------------------------------
# _process_timeline_candidate — real _is_creator_in_scope + should_process_creator
# ---------------------------------------------------------------------------


class TestProcessTimelineCandidate:
    """Real scope + filter decisions: out-of-scope skip, in-scope enqueue,
    and the defensive should_process_creator exception fallback."""

    @pytest.mark.asyncio
    async def test_out_of_scope_creator_is_skipped(
        self, respx_fansly_api, mock_config, entity_store, caplog
    ):
        """Real _is_creator_in_scope returns False → debug log + no enqueue.

        Scope is forced to a genuine miss: user_names names a DIFFERENT
        creator, and the candidate's Account (saved with its own username)
        fails config.is_username_in_scope. No monkeypatch — the real
        daemon.runner scope branch runs and reaches the store lookup.
        """
        caplog.set_level(logging.DEBUG)
        mock_config.use_following = False
        mock_config.user_names = {"someone_else"}

        creator_id = snowflake_id()
        await _seed_account(entity_store, creator_id, f"outofscope_{creator_id}")

        # A per-creator timeline route would be hit by should_process_creator
        # IF scope let us through — register it so a fall-through shows up.
        route = respx.get(url__startswith=TIMELINE_NEW_URL).mock(
            side_effect=[_home_timeline_response([])]
        )

        queue: asyncio.Queue = asyncio.Queue()
        budget = _make_budget()

        try:
            enqueued = await _process_timeline_candidate(
                mock_config,
                creator_id,
                prefetched=[],
                session_baseline=None,
                baseline_consumed=set(),
                queue=queue,
                budget=budget,
            )
        finally:
            dump_fansly_calls(route.calls, "out_of_scope")

        assert enqueued is False
        assert queue.empty()
        assert route.call_count == 0  # never reached the filter's timeline fetch

        debug = _logged(caplog, "DEBUG")
        assert any(f"creator {creator_id} out of scope" in m for m in debug)

    @pytest.mark.asyncio
    async def test_in_scope_new_content_enqueues(
        self, respx_fansly_api, mock_config, entity_store
    ):
        """Real scope True + real should_process_creator True → enqueue.

        Drives should_process_creator through its prefetched_posts fast path:
        a newer-than-baseline non-pinned post short-circuits before any GET.
        """
        mock_config.use_following = False
        mock_config.user_names = set()  # unrestricted → scope True naturally

        creator_id = snowflake_id()
        await _seed_account(entity_store, creator_id, f"inscope_{creator_id}")

        baseline = datetime(2026, 4, 10, 12, 0, 0, tzinfo=UTC)
        newer_ms = int((baseline + timedelta(hours=1)).timestamp() * 1000)
        await entity_store.save(
            MonitorState(creatorId=creator_id, lastCheckedAt=baseline)
        )

        # No API route consumed: prefetched non-pinned newer post short-circuits.
        route = respx.get(url__startswith=TIMELINE_NEW_URL).mock(side_effect=[])

        queue: asyncio.Queue = asyncio.Queue()
        budget = _make_budget()

        try:
            enqueued = await _process_timeline_candidate(
                mock_config,
                creator_id,
                prefetched=[_post_dict(snowflake_id(), creator_id, newer_ms)],
                session_baseline=None,
                baseline_consumed=set(),
                queue=queue,
                budget=budget,
            )
        finally:
            dump_fansly_calls(route.calls, "in_scope_new_content")

        assert enqueued is True
        assert route.call_count == 0
        assert queue.qsize() == 1
        item = queue.get_nowait()
        assert isinstance(item, DownloadTimelineOnly)
        assert item.creator_id == creator_id

    @pytest.mark.asyncio
    async def test_in_scope_stale_content_not_enqueued(
        self, respx_fansly_api, mock_config, entity_store
    ):
        """Real scope True + real should_process_creator False → not enqueued.

        In-scope creator whose only prefetched post is OLDER than the stored
        baseline: the real filter returns False, so _process_timeline_candidate
        returns False and enqueues nothing (covers the should=False path).
        """
        mock_config.use_following = False
        mock_config.user_names = set()

        creator_id = snowflake_id()
        await _seed_account(entity_store, creator_id, f"stale_{creator_id}")

        baseline = datetime(2026, 4, 10, 12, 0, 0, tzinfo=UTC)
        older_ms = int((baseline - timedelta(hours=1)).timestamp() * 1000)
        await entity_store.save(
            MonitorState(creatorId=creator_id, lastCheckedAt=baseline)
        )

        route = respx.get(url__startswith=TIMELINE_NEW_URL).mock(side_effect=[])

        queue: asyncio.Queue = asyncio.Queue()
        budget = _make_budget()

        try:
            enqueued = await _process_timeline_candidate(
                mock_config,
                creator_id,
                prefetched=[_post_dict(snowflake_id(), creator_id, older_ms)],
                session_baseline=None,
                baseline_consumed=set(),
                queue=queue,
                budget=budget,
            )
        finally:
            dump_fansly_calls(route.calls, "in_scope_stale")

        assert enqueued is False
        assert queue.empty()
        assert route.call_count == 0

    @pytest.mark.asyncio
    async def test_should_process_error_defaults_to_enqueue(
        self, config, entity_store, monkeypatch, caplog
    ):
        """The defensive except → warning + should=True.

        DEFENSIVE-BRANCH: the real ``should_process_creator``
        (daemon/filters.py) is total — every internal error path returns True
        conservatively, it never raises. The runner's ``try/except`` around it
        is belt-and-suspenders for a FUTURE contract change. The ONLY way to
        reach it is to substitute a raising callee. This is not a
        behaviour-replacing mock of the subject under test; it simulates a
        callee-contract violation the branch exists to survive. Real scope
        (``_is_creator_in_scope``, unrestricted) still runs unmocked.
        """
        caplog.set_level(logging.WARNING)
        config.use_following = False
        config.user_names = set()  # scope True naturally (no store lookup)

        creator_id = snowflake_id()

        async def _raises(*_a, **_k):
            raise RuntimeError("filter check boom")

        monkeypatch.setattr("daemon.runner.should_process_creator", _raises)

        queue: asyncio.Queue = asyncio.Queue()
        budget = _make_budget()

        enqueued = await _process_timeline_candidate(
            config,
            creator_id,
            prefetched=[],
            session_baseline=None,
            baseline_consumed=set(),
            queue=queue,
            budget=budget,
        )

        assert enqueued is True
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
# _timeline_poll_loop — real poll_home_timeline through respx
# ---------------------------------------------------------------------------


class TestTimelinePollLoop:
    """Hidden/should_poll-false skips, real-poll happy path + active
    transition, the real poll error branch, and DaemonUnrecoverable re-raise."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "sim",
        [
            _make_simulator("hidden"),
            StubSimulator(timeline_interval=1.0, should_poll=False),
        ],
        ids=["hidden_interval_zero", "should_poll_false"],
    )
    async def test_poll_skipped(self, sim, respx_fansly_api, mock_config, entity_store):
        """No poll when hidden (interval<=0) OR should_poll False mid-loop.

        Merged skip pair: the real simulator ties should_poll to state==hidden
        (interval 0); the StubSimulator decouples them to reach the mid-loop
        should_poll guard with interval>0. Both must leave the real
        poll_home_timeline route uncalled.
        """
        route = respx.get(url__startswith=HOME_TIMELINE_URL).mock(
            side_effect=[_home_timeline_response([])]
        )

        stop_event = asyncio.Event()
        refresh_event = asyncio.Event()
        queue: asyncio.Queue = asyncio.Queue()

        try:
            await asyncio.gather(
                _timeline_poll_loop(
                    mock_config,
                    sim,
                    queue,
                    None,
                    set(),
                    stop_event,
                    _make_budget(),
                    refresh_event,
                    _FastDashboard(),
                ),
                _stop_soon(stop_event),
            )
        finally:
            dump_fansly_calls(route.calls, "timeline_poll_skipped")

        assert route.call_count == 0

    @pytest.mark.asyncio
    async def test_happy_path_real_poll_enqueues_new_creators(
        self, respx_fansly_api, mock_config, entity_store
    ):
        """Real poll returns two uncached creators → both enqueued.

        Exercises the whole chain: real poll_home_timeline (cache-miss
        detection) → real _process_timeline_candidate → real
        _is_creator_in_scope (unrestricted True) → real should_process_creator
        (first-run True, no MonitorState) → enqueue. One poll then stop.
        """
        mock_config.use_following = False
        mock_config.user_names = set()

        creator_ids = [snowflake_id(), snowflake_id()]
        for cid in creator_ids:
            await _seed_account(entity_store, cid, f"c_{cid}")

        stop_event = asyncio.Event()
        posts = [_post_dict(snowflake_id(), cid) for cid in creator_ids]

        def _home_side_effect(_request):
            stop_event.set()  # one poll produces the work; stop right after
            return _home_timeline_response(posts)

        home_route = respx.get(url__startswith=HOME_TIMELINE_URL).mock(
            side_effect=_home_side_effect
        )
        # First-run should_process_creator returns True with no MonitorState,
        # before any per-creator fetch; register a fallback route to expose an
        # accidental fetch.
        tl_route = respx.get(url__startswith=TIMELINE_NEW_URL).mock(
            side_effect=[_home_timeline_response([]) for _ in range(6)]
        )

        refresh_event = asyncio.Event()
        queue: asyncio.Queue = asyncio.Queue()
        sim = _make_simulator("active")

        try:
            await _timeline_poll_loop(
                mock_config,
                sim,
                queue,
                None,
                set(),
                stop_event,
                _make_budget(),
                refresh_event,
                _FastDashboard(),
            )
        finally:
            dump_fansly_calls(home_route.calls, "timeline_happy_home")
            dump_fansly_calls(tl_route.calls, "timeline_happy_percreator")

        assert home_route.called
        assert queue.qsize() == 2
        items = [queue.get_nowait() for _ in range(2)]
        assert {item.creator_id for item in items} == set(creator_ids)
        assert all(isinstance(i, DownloadTimelineOnly) for i in items)
        # Real simulator already active → on_new_content False → no refresh.
        assert not refresh_event.is_set()
        assert sim.state == "active"

    @pytest.mark.asyncio
    async def test_cache_miss_but_stale_enqueues_nothing(
        self, respx_fansly_api, mock_config, entity_store
    ):
        """A cache-miss creator whose content is stale → queued_any stays False.

        The poll flags the creator as new (uncached post id), but the real
        should_process_creator returns False (post older than baseline). So
        _process_timeline_candidate returns False for the only creator:
        queued_any never flips, on_new_content is not called, and the queue
        stays empty. Covers the queued_any-False loop-continue branch with a
        real filter decision (no mocks).
        """
        mock_config.use_following = False
        mock_config.user_names = set()

        creator_id = snowflake_id()
        await _seed_account(entity_store, creator_id, f"stale_{creator_id}")

        baseline = datetime(2026, 4, 10, 12, 0, 0, tzinfo=UTC)
        older_ms = int((baseline - timedelta(hours=1)).timestamp() * 1000)
        await entity_store.save(
            MonitorState(creatorId=creator_id, lastCheckedAt=baseline)
        )

        stop_event = asyncio.Event()
        stale_post = _post_dict(snowflake_id(), creator_id, older_ms)

        def _home_side_effect(_request):
            stop_event.set()
            return _home_timeline_response([stale_post])

        home_route = respx.get(url__startswith=HOME_TIMELINE_URL).mock(
            side_effect=_home_side_effect
        )
        # The prefetched stale post short-circuits the filter → no per-creator
        # fetch; register a route to expose an unexpected one.
        tl_route = respx.get(url__startswith=TIMELINE_NEW_URL).mock(side_effect=[])

        sim = _make_simulator("active")
        refresh_event = asyncio.Event()
        queue: asyncio.Queue = asyncio.Queue()

        try:
            await _timeline_poll_loop(
                mock_config,
                sim,
                queue,
                None,
                set(),
                stop_event,
                _make_budget(),
                refresh_event,
                _FastDashboard(),
            )
        finally:
            dump_fansly_calls(home_route.calls, "timeline_cache_miss_stale_home")
            dump_fansly_calls(tl_route.calls, "timeline_cache_miss_stale_pc")

        assert home_route.called
        assert tl_route.call_count == 0
        assert queue.empty()
        assert not refresh_event.is_set()

    @pytest.mark.asyncio
    async def test_transition_to_active_sets_refresh_event(
        self, respx_fansly_api, mock_config, entity_store
    ):
        """StubSimulator transition True on enqueue → refresh_event set.

        Real poll + real candidate processing; the StubSimulator's
        on_new_content returns True (a transition the real active simulator
        can't produce), driving the refresh_event branch.
        """
        mock_config.use_following = False
        mock_config.user_names = set()

        creator_id = snowflake_id()
        await _seed_account(entity_store, creator_id, f"c_{creator_id}")

        stop_event = asyncio.Event()

        def _home_side_effect(_request):
            stop_event.set()
            return _home_timeline_response([_post_dict(snowflake_id(), creator_id)])

        home_route = respx.get(url__startswith=HOME_TIMELINE_URL).mock(
            side_effect=_home_side_effect
        )
        tl_route = respx.get(url__startswith=TIMELINE_NEW_URL).mock(
            side_effect=[_home_timeline_response([]) for _ in range(3)]
        )

        sim = StubSimulator(timeline_interval=1.0, should_poll=True, transitions=True)
        refresh_event = asyncio.Event()
        queue: asyncio.Queue = asyncio.Queue()

        try:
            await _timeline_poll_loop(
                mock_config,
                sim,
                queue,
                None,
                set(),
                stop_event,
                _make_budget(),
                refresh_event,
                _FastDashboard(),
            )
        finally:
            dump_fansly_calls(home_route.calls, "timeline_transition_home")
            dump_fansly_calls(tl_route.calls, "timeline_transition_percreator")

        assert home_route.called
        assert refresh_event.is_set()
        assert queue.qsize() == 1

    @pytest.mark.asyncio
    async def test_poll_error_logs_and_continues(
        self, respx_fansly_api, mock_config, entity_store, caplog
    ):
        """Real poll swallows a ConnectError storm → (set(), {}) → no enqueue.

        Covers poll_home_timeline's HTTPError log path (daemon/polling.py).
        RetryTransport retries network errors (total=3): 4 ConnectErrors.
        """
        caplog.set_level(logging.WARNING)
        sim = _make_simulator("active")

        stop_event = asyncio.Event()
        # RetryTransport retries network errors (total=3): 4 attempts per poll.
        # Raise a ConnectError on each attempt; stop the loop once this poll's
        # 4 attempts are exhausted so a second poll never StopIterations.
        remaining = [4]

        def _home_side_effect(_request):
            remaining[0] -= 1
            if remaining[0] <= 0:
                stop_event.set()
            raise httpx.ConnectError("refused")

        route = respx.get(url__startswith=HOME_TIMELINE_URL).mock(
            side_effect=_home_side_effect
        )

        refresh_event = asyncio.Event()
        queue: asyncio.Queue = asyncio.Queue()

        try:
            await _timeline_poll_loop(
                mock_config,
                sim,
                queue,
                None,
                set(),
                stop_event,
                _make_budget(),
                refresh_event,
                _FastDashboard(),
            )
        finally:
            dump_fansly_calls(route.calls, "timeline_poll_error")

        warnings = _logged(caplog, "WARNING")
        assert any("home timeline API call failed" in m for m in warnings)
        assert queue.qsize() == 0

    @pytest.mark.asyncio
    async def test_loop_generic_error_logs_and_continues(
        self, mock_config, monkeypatch, caplog
    ):
        """The loop's own except Exception → 'timeline poll error' + on_error.

        DEFENSIVE-BRANCH: the real poll_home_timeline is total (swallows every
        error, returns (set(), {})), so the loop's ``except Exception`` guard
        (runner.py 809-812) is only reachable when the poll itself raises a
        plain exception. Substituting a raising poll exercises the loop's
        continue-on-error contract, not poll behaviour.
        """
        caplog.set_level(logging.WARNING)
        sim = _make_simulator("active")
        stop_event = asyncio.Event()

        async def _poll(_config):
            stop_event.set()
            raise RuntimeError("transient poll boom")

        monkeypatch.setattr("daemon.runner.poll_home_timeline", _poll)

        refresh_event = asyncio.Event()
        queue: asyncio.Queue = asyncio.Queue()

        await _timeline_poll_loop(
            mock_config,
            sim,
            queue,
            None,
            set(),
            stop_event,
            _make_budget(),
            refresh_event,
            _FastDashboard(),
        )

        warnings = _logged(caplog, "WARNING")
        assert any(
            "timeline poll error" in m and "transient poll boom" in m for m in warnings
        )
        assert queue.qsize() == 0

    @pytest.mark.asyncio
    async def test_daemon_unrecoverable_re_raised(self, mock_config, monkeypatch):
        """DaemonUnrecoverableError from the poll bypasses the generic except.

        DEFENSIVE-BRANCH: the real poll_home_timeline swallows all exceptions
        and never raises DaemonUnrecoverableError; the loop's
        ``except DaemonUnrecoverableError: raise`` is a propagation guard for a
        future non-total poll. Substituting a raising poll is the only way to
        reach it — exercises the loop's re-raise contract, not poll behaviour.
        """
        sim = _make_simulator("active")

        async def _poll(_config):
            raise DaemonUnrecoverableError("hard fatal")

        monkeypatch.setattr("daemon.runner.poll_home_timeline", _poll)

        stop_event = asyncio.Event()
        refresh_event = asyncio.Event()
        queue: asyncio.Queue = asyncio.Queue()

        with pytest.raises(DaemonUnrecoverableError, match="hard fatal"):
            await _timeline_poll_loop(
                mock_config,
                sim,
                queue,
                None,
                set(),
                stop_event,
                _make_budget(),
                refresh_event,
                _FastDashboard(),
            )


# ---------------------------------------------------------------------------
# _story_poll_loop — real poll_story_states through respx
# ---------------------------------------------------------------------------


class TestStoryPollLoop:
    """Hidden/should_poll-false skips, real-poll in-scope enqueue + out-of-scope
    skip, active transition, real error branch, and DaemonUnrecoverable."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "sim",
        [
            _make_simulator("hidden"),
            StubSimulator(story_interval=1.0, should_poll=False),
        ],
        ids=["hidden_interval_zero", "should_poll_false"],
    )
    async def test_poll_skipped(self, sim, respx_fansly_api, mock_config, entity_store):
        """No poll when hidden (interval<=0) OR should_poll False mid-loop."""
        route = respx.get(url__startswith=STORY_STATES_URL).mock(
            side_effect=[_story_states_response([])]
        )

        stop_event = asyncio.Event()
        refresh_event = asyncio.Event()
        queue: asyncio.Queue = asyncio.Queue()

        try:
            await asyncio.gather(
                _story_poll_loop(
                    mock_config,
                    sim,
                    queue,
                    stop_event,
                    _make_budget(),
                    refresh_event,
                    _FastDashboard(),
                ),
                _stop_soon(stop_event),
            )
        finally:
            dump_fansly_calls(route.calls, "story_poll_skipped")

        assert route.call_count == 0

    @pytest.mark.asyncio
    async def test_happy_path_enqueues_in_scope_only(
        self, respx_fansly_api, mock_config, entity_store
    ):
        """Real poll flips two creators active; real scope keeps only one.

        Exercises real poll_story_states (MonitorState flip detection +
        persistence) → real _is_creator_in_scope. user_names restricts scope
        to one creator's username; the other's Account resolves out of scope.
        """
        in_scope_id = snowflake_id()
        out_scope_id = snowflake_id()
        in_name = f"instory_{in_scope_id}"
        out_name = f"outstory_{out_scope_id}"
        await _seed_account(entity_store, in_scope_id, in_name)
        await _seed_account(entity_store, out_scope_id, out_name)

        mock_config.use_following = False
        mock_config.user_names = {in_name}

        stop_event = asyncio.Event()

        def _story_side_effect(_request):
            stop_event.set()
            return _story_states_response(
                [
                    _story_state_dict(in_scope_id, has_active=True),
                    _story_state_dict(out_scope_id, has_active=True),
                ]
            )

        route = respx.get(url__startswith=STORY_STATES_URL).mock(
            side_effect=_story_side_effect
        )

        refresh_event = asyncio.Event()
        queue: asyncio.Queue = asyncio.Queue()

        try:
            await _story_poll_loop(
                mock_config,
                _make_simulator("active"),
                queue,
                stop_event,
                _make_budget(),
                refresh_event,
                _FastDashboard(),
            )
        finally:
            dump_fansly_calls(route.calls, "story_happy")

        assert route.called
        # Only the in-scope creator's item lands on the queue.
        assert queue.qsize() == 1
        item = queue.get_nowait()
        assert isinstance(item, DownloadStoriesOnly)
        assert item.creator_id == in_scope_id
        # Real poll persisted MonitorState flips for BOTH creators.
        for cid in (in_scope_id, out_scope_id):
            row = await entity_store.get(MonitorState, cid)
            assert row is not None
            assert row.lastHasActiveStories is True

    @pytest.mark.asyncio
    async def test_transition_to_active_sets_refresh_event(
        self, respx_fansly_api, mock_config, entity_store
    ):
        """StubSimulator transition True when the real poll yields a creator.

        Scope is configured out (user_names names someone else) so the inner
        per-creator loop short-circuits; we only assert the transition fired
        refresh_event before iterating creators.
        """
        creator_id = snowflake_id()
        await _seed_account(entity_store, creator_id, f"story_{creator_id}")
        mock_config.use_following = False
        mock_config.user_names = {"someone_else"}

        stop_event = asyncio.Event()

        def _story_side_effect(_request):
            stop_event.set()
            return _story_states_response(
                [_story_state_dict(creator_id, has_active=True)]
            )

        route = respx.get(url__startswith=STORY_STATES_URL).mock(
            side_effect=_story_side_effect
        )

        sim = StubSimulator(story_interval=1.0, should_poll=True, transitions=True)
        refresh_event = asyncio.Event()
        queue: asyncio.Queue = asyncio.Queue()

        try:
            await _story_poll_loop(
                mock_config,
                sim,
                queue,
                stop_event,
                _make_budget(),
                refresh_event,
                _FastDashboard(),
            )
        finally:
            dump_fansly_calls(route.calls, "story_transition")

        assert route.called
        assert refresh_event.is_set()
        assert queue.empty()  # creator out of scope → not enqueued

    @pytest.mark.asyncio
    async def test_poll_error_logs_and_continues(
        self, respx_fansly_api, mock_config, entity_store, caplog
    ):
        """Real poll swallows a ConnectError storm → [] → no enqueue.

        Covers poll_story_states's HTTPError log path (daemon/polling.py).
        """
        caplog.set_level(logging.WARNING)
        sim = _make_simulator("active")

        stop_event = asyncio.Event()
        remaining = [4]

        def _story_side_effect(_request):
            remaining[0] -= 1
            if remaining[0] <= 0:
                stop_event.set()
            raise httpx.ConnectError("refused")

        route = respx.get(url__startswith=STORY_STATES_URL).mock(
            side_effect=_story_side_effect
        )

        refresh_event = asyncio.Event()
        queue: asyncio.Queue = asyncio.Queue()

        try:
            await _story_poll_loop(
                mock_config,
                sim,
                queue,
                stop_event,
                _make_budget(),
                refresh_event,
                _FastDashboard(),
            )
        finally:
            dump_fansly_calls(route.calls, "story_poll_error")

        warnings = _logged(caplog, "WARNING")
        assert any("story states API call failed" in m for m in warnings)
        assert queue.qsize() == 0

    @pytest.mark.asyncio
    async def test_loop_generic_error_logs_and_continues(
        self, mock_config, monkeypatch, caplog
    ):
        """The loop's own except Exception → 'story poll error' + on_error.

        DEFENSIVE-BRANCH: mirror of the timeline case — the real
        poll_story_states is total, so the loop's ``except Exception`` guard
        (runner.py 893-896) is only reachable via a raising poll substitute.
        """
        caplog.set_level(logging.WARNING)
        sim = _make_simulator("active")
        stop_event = asyncio.Event()

        async def _poll(_config):
            stop_event.set()
            raise RuntimeError("story boom")

        monkeypatch.setattr("daemon.runner.poll_story_states", _poll)

        refresh_event = asyncio.Event()
        queue: asyncio.Queue = asyncio.Queue()

        await _story_poll_loop(
            mock_config,
            sim,
            queue,
            stop_event,
            _make_budget(),
            refresh_event,
            _FastDashboard(),
        )

        warnings = _logged(caplog, "WARNING")
        assert any("story poll error" in m and "story boom" in m for m in warnings)
        assert queue.qsize() == 0

    @pytest.mark.asyncio
    async def test_daemon_unrecoverable_re_raised(self, mock_config, monkeypatch):
        """DaemonUnrecoverableError from the poll bypasses the generic except.

        DEFENSIVE-BRANCH: mirror of the timeline case — the real
        poll_story_states never raises DaemonUnrecoverableError; substituting a
        raising poll is the only way to exercise the loop's re-raise contract.
        """
        sim = _make_simulator("active")

        async def _poll(_config):
            raise DaemonUnrecoverableError("story fatal")

        monkeypatch.setattr("daemon.runner.poll_story_states", _poll)

        stop_event = asyncio.Event()
        refresh_event = asyncio.Event()
        queue: asyncio.Queue = asyncio.Queue()

        with pytest.raises(DaemonUnrecoverableError, match="story fatal"):
            await _story_poll_loop(
                mock_config,
                sim,
                queue,
                stop_event,
                _make_budget(),
                refresh_event,
                _FastDashboard(),
            )


# ---------------------------------------------------------------------------
# _following_refresh_loop
#
# get_following_accounts / get_creator_account_info are download-layer
# ENTRYPOINTS (download/account.py) with their own respx+DB coverage. The
# loop consumes get_following_accounts' RETURN VALUE (a set of usernames) to
# update config.user_names — that set is a boundary INPUT to the loop, so an
# AsyncMock returning a set is a legitimate edge double, not an internal
# behaviour-replacing mock of the loop under test.
# ---------------------------------------------------------------------------


class TestFollowingRefreshLoop:
    """Early-return, hidden skip, happy update, refresh_event clear, error,
    ordering, and DaemonUnrecoverable."""

    @pytest.mark.asyncio
    async def test_use_following_false_returns_immediately(self, config):
        """not config.use_following → early return, no loop iteration."""
        config.use_following = False
        stop_event = asyncio.Event()
        refresh_event = asyncio.Event()

        # Returns immediately even though stop_event is never set.
        await _following_refresh_loop(
            config,
            _make_simulator("active"),
            stop_event,
            refresh_event,
            _make_budget(),
            _FastDashboard(),
        )

    @pytest.mark.asyncio
    async def test_hidden_state_skips_refresh(self, config, monkeypatch):
        """simulator.state == 'hidden' → continue without fetching."""
        config.use_following = True
        sim = _make_simulator("hidden")

        following_spy = async_noop_spy()
        info_spy = async_noop_spy()
        monkeypatch.setattr("daemon.runner.get_following_accounts", following_spy)
        monkeypatch.setattr("daemon.runner.get_creator_account_info", info_spy)

        stop_event = asyncio.Event()
        refresh_event = asyncio.Event()

        await asyncio.gather(
            _following_refresh_loop(
                config,
                sim,
                stop_event,
                refresh_event,
                _make_budget(),
                _FastDashboard(),
            ),
            _stop_soon(stop_event),
        )

        assert following_spy.await_count == 0

    @pytest.mark.asyncio
    async def test_happy_path_updates_config_and_clears_refresh_event(
        self, config, monkeypatch, caplog
    ):
        """Merged happy path: refresh returns names → config updated + info log,
        get_creator_account_info runs BEFORE get_following_accounts, and a
        pre-set refresh_event is cleared after the wait.
        """
        caplog.set_level(logging.INFO)
        config.use_following = True
        config.user_names = {"old_user"}

        call_order: list[str] = []
        stop_event = asyncio.Event()

        async def _account_info(_config, _state):
            call_order.append("account_info")

        async def _following(_config, _state):
            call_order.append("following")
            stop_event.set()
            return {"new_user1", "new_user2"}

        monkeypatch.setattr("daemon.runner.get_creator_account_info", _account_info)
        monkeypatch.setattr("daemon.runner.get_following_accounts", _following)

        refresh_event = asyncio.Event()
        refresh_event.set()  # caller asked for an immediate refresh

        await _following_refresh_loop(
            config,
            _make_simulator("active"),
            stop_event,
            refresh_event,
            _make_budget(),
            _FastDashboard(),
        )

        assert config.user_names == {"new_user1", "new_user2"}
        # account_info must precede following (regression: fresh DownloadState
        # otherwise raises "client ID not set" every tick).
        assert call_order == ["account_info", "following"]
        # Pre-set refresh_event cleared after processing.
        assert not refresh_event.is_set()

        info = _logged(caplog, "INFO")
        assert any(
            "following list refreshed (periodic) - 2 creators" in m for m in info
        )

    @pytest.mark.asyncio
    async def test_empty_following_set_does_not_clobber_user_names(
        self, config, monkeypatch
    ):
        """get_following_accounts returns an empty set → config.user_names is
        left unchanged and budget.on_success still runs (covers the
        ``if new_names`` false branch in the loop body).
        """
        config.use_following = True
        config.user_names = {"kept"}
        stop_event = asyncio.Event()

        async def _account_info(_config, _state):
            return None

        async def _following(_config, _state):
            stop_event.set()
            return set()  # empty → skip the config update

        monkeypatch.setattr("daemon.runner.get_creator_account_info", _account_info)
        monkeypatch.setattr("daemon.runner.get_following_accounts", _following)

        refresh_event = asyncio.Event()

        await _following_refresh_loop(
            config,
            _make_simulator("active"),
            stop_event,
            refresh_event,
            _make_budget(),
            _FastDashboard(),
        )

        assert config.user_names == {"kept"}

    @pytest.mark.asyncio
    async def test_refresh_error_logged_continues(self, config, monkeypatch, caplog):
        """get_following_accounts raises → warning log + budget.on_error + continue.

        DEFENSIVE/edge: get_following_accounts is an entrypoint; a transient
        failure raising out of it is a real runtime possibility the loop must
        survive. A raising AsyncMock stands in for the entrypoint's failure.
        """
        caplog.set_level(logging.WARNING)
        config.use_following = True
        stop_event = asyncio.Event()

        async def _account_info(_config, _state):
            return None

        async def _raises(_config, _state):
            stop_event.set()
            raise RuntimeError("refresh boom")

        monkeypatch.setattr("daemon.runner.get_creator_account_info", _account_info)
        monkeypatch.setattr("daemon.runner.get_following_accounts", _raises)

        refresh_event = asyncio.Event()

        await _following_refresh_loop(
            config,
            _make_simulator("active"),
            stop_event,
            refresh_event,
            _make_budget(),
            _FastDashboard(),
        )

        warnings = _logged(caplog, "WARNING")
        assert any(
            "following list refresh failed (periodic)" in m and "refresh boom" in m
            for m in warnings
        )

    @pytest.mark.asyncio
    async def test_daemon_unrecoverable_re_raised(self, config, monkeypatch):
        """DaemonUnrecoverableError bypasses the generic except.

        DEFENSIVE-BRANCH: exercises the loop's re-raise contract via a raising
        entrypoint double.
        """
        config.use_following = True

        async def _account_info(_config, _state):
            return None

        async def _raises(_config, _state):
            raise DaemonUnrecoverableError("refresh fatal")

        monkeypatch.setattr("daemon.runner.get_creator_account_info", _account_info)
        monkeypatch.setattr("daemon.runner.get_following_accounts", _raises)

        stop_event = asyncio.Event()
        refresh_event = asyncio.Event()

        with pytest.raises(DaemonUnrecoverableError, match="refresh fatal"):
            await _following_refresh_loop(
                config,
                _make_simulator("active"),
                stop_event,
                refresh_event,
                _make_budget(),
                _FastDashboard(),
            )


# ---------------------------------------------------------------------------
# _refresh_following — helper called from _worker_loop on FullCreatorDownload
# ---------------------------------------------------------------------------


class TestRefreshFollowing:
    """Happy update, empty-set no-clobber, and swallowed error."""

    @pytest.mark.asyncio
    async def test_happy_path_updates_user_names_and_logs(
        self, config, monkeypatch, caplog
    ):
        """get_following_accounts returns names → config updated + info log."""
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
        """Empty new_names → existing user_names preserved."""
        config.user_names = {"unchanged"}

        async def _info_ok(_c, _s):
            return None

        async def _get_following(_c, _s):
            return set()

        monkeypatch.setattr("daemon.runner.get_creator_account_info", _info_ok)
        monkeypatch.setattr("daemon.runner.get_following_accounts", _get_following)

        await _refresh_following(config)

        assert config.user_names == {"unchanged"}

    @pytest.mark.asyncio
    async def test_exception_logged_does_not_raise(self, config, monkeypatch, caplog):
        """Any exception → warning log + return without raising or mutating.

        DEFENSIVE/edge: entrypoint failure the helper must swallow.
        """
        caplog.set_level(logging.WARNING)
        config.user_names = {"unchanged"}

        async def _raises(_c, _s):
            raise RuntimeError("refresh boom")

        monkeypatch.setattr("daemon.runner.get_creator_account_info", _raises)

        await _refresh_following(config)  # must not raise

        warnings = _logged(caplog, "WARNING")
        assert any(
            "following list refresh failed" in m and "refresh boom" in m
            for m in warnings
        )
        assert config.user_names == {"unchanged"}


# ---------------------------------------------------------------------------
# _simulator_tick_loop — unhide + ws-reconnect + budget reset + heartbeat
# ---------------------------------------------------------------------------


class TestSimulatorTickLoopUnhide:
    """Unhide transition: ws reconnect success/failure, budget reset."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("stop_raises", "expect_ws_state", "expect_warning"),
        [
            (None, True, False),
            (RuntimeError("ws stop boom"), False, True),
        ],
        ids=["reconnect_success", "reconnect_failure"],
    )
    async def test_unhide_ws_reconnect(
        self, stop_raises, expect_ws_state, expect_warning, caplog
    ):
        """On unhide the loop resets budget, sets refresh_event, and reconnects
        the real FakeWS. Merged success/failure pair — a FakeWS whose
        stop_thread raises drives the reconnect-failure branch.
        """
        caplog.set_level(logging.WARNING)
        sim = _make_simulator("hidden")

        stop_event = asyncio.Event()

        def _tick():
            sim.state = "active"
            stop_event.set()
            return "unhide"

        sim.tick = _tick  # type: ignore[method-assign]

        # FakeWS is the client-level no-network double for FanslyWebSocket; no
        # real FanslyWebSocket is constructible without a live connection.
        ws = FakeWS(stop_raises=stop_raises)
        refresh_event = asyncio.Event()
        dashboard = _FastDashboard()

        await _simulator_tick_loop(
            sim,
            stop_event,
            ws,
            refresh_event,
            _make_budget(),
            dashboard,
        )

        assert refresh_event.is_set()
        assert ws.stop_calls == 1
        assert expect_ws_state in dashboard.ws_states
        if expect_ws_state:
            assert ws.start_calls == 1
        warnings = _logged(caplog, "WARNING")
        assert (
            any("WebSocket reconnect failed after unhide" in m for m in warnings)
            is expect_warning
        )

    @pytest.mark.asyncio
    async def test_unhide_resets_error_budget(self):
        """Unhide calls budget.on_success(), preventing a false unrecoverable
        exit after a long hidden window; a subsequent soft error must NOT raise.
        """
        sim = _make_simulator("hidden")
        budget = _make_budget()  # 1-hour timeout
        # Wind the clock back 5h+ (as if hidden with no API activity).
        budget.last_success_at = datetime(2020, 1, 1, tzinfo=UTC)

        stop_event = asyncio.Event()

        def _tick():
            sim.state = "active"
            stop_event.set()
            return "unhide"

        sim.tick = _tick  # type: ignore[method-assign]

        refresh_event = asyncio.Event()

        await _simulator_tick_loop(
            sim,
            stop_event,
            FakeWS(),
            refresh_event,
            budget,
            _FastDashboard(),
        )

        delta = (datetime.now(UTC) - budget.last_success_at).total_seconds()
        assert delta < 5.0, "on_success() should have reset last_success_at"
        # Clock reset → the first post-unhide soft error must not escalate.
        budget.on_error(RuntimeError("soft error after unhide"))


class _FakeTime:
    """Stub for the ``time`` module imported inside ``daemon.runner``.

    Boundary double: replaces the stdlib monotonic clock so the 15-minute
    heartbeat interval is deterministic without a real 900s wait.
    """

    def __init__(self, value: float) -> None:
        self.value = value

    def monotonic(self) -> float:
        return self.value


class TestSimulatorTickLoopHeartbeat:
    """15-minute heartbeat DEBUG log fires only once the interval elapses,
    in every simulator state."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("monotonic", "expect_heartbeat"),
        [(1000.0, True), (0.1, False)],
        ids=["interval_elapsed", "interval_not_elapsed"],
    )
    async def test_heartbeat_emission_gated_on_interval(
        self, monotonic, expect_heartbeat, monkeypatch, caplog
    ):
        """Merged emit/no-emit pair keyed on the fake monotonic clock."""
        caplog.set_level(logging.DEBUG)
        monkeypatch.setattr("daemon.runner.time", _FakeTime(monotonic))

        sim = _make_simulator("active")
        stop_event = asyncio.Event()

        def _tick():
            stop_event.set()

        sim.tick = _tick  # type: ignore[method-assign]

        refresh_event = asyncio.Event()

        await _simulator_tick_loop(
            sim,
            stop_event,
            FakeWS(),
            refresh_event,
            _make_budget(),
            _FastDashboard(),
        )

        emitted = any(
            "WS alive" in r.getMessage()
            for r in caplog.records
            if r.levelname == "DEBUG"
        )
        assert emitted is expect_heartbeat

    @pytest.mark.parametrize("state", ["active", "idle", "hidden"])
    @pytest.mark.asyncio
    async def test_heartbeat_shows_state_for_all_states(
        self, state, monkeypatch, caplog
    ):
        """Heartbeat includes the current simulator state in all three states."""
        caplog.set_level(logging.DEBUG)
        monkeypatch.setattr("daemon.runner.time", _FakeTime(1000.0))

        sim = _make_simulator(state)
        stop_event = asyncio.Event()

        def _tick():
            stop_event.set()

        sim.tick = _tick  # type: ignore[method-assign]

        refresh_event = asyncio.Event()

        await _simulator_tick_loop(
            sim,
            stop_event,
            FakeWS(),
            refresh_event,
            _make_budget(),
            _FastDashboard(),
        )

        assert any(
            f"state={state}" in r.getMessage()
            for r in caplog.records
            if r.levelname == "DEBUG" and "WS alive" in r.getMessage()
        ), f"Heartbeat did not mention state={state}"


# ---------------------------------------------------------------------------
# _worker_loop — queue draining, exception handling, real post-processing
#
# _handle_work_item is a dispatch router (own coverage); worker tests swap it
# for async_noop_spy() (clean success) or a raiser (error). mark_creator_processed
# runs REAL against the store — an Account row is seeded so the FK holds, and the
# resulting MonitorState row is asserted on.
# ---------------------------------------------------------------------------


class TestWorkerLoopExceptionPaths:
    """queue.get cancellation + per-item exception handling + budget wiring."""

    @pytest.mark.asyncio
    async def test_cancelled_error_on_queue_get_breaks_loop(self, config, monkeypatch):
        """asyncio.CancelledError from queue.get → break (clean exit)."""
        queue: asyncio.Queue = asyncio.Queue()
        stop_event = asyncio.Event()

        async def _wait_for(coro, **_kwargs):
            # Production calls asyncio.wait_for(queue.get(), timeout=1.0).
            # Close the intercepted queue.get() coroutine so it isn't left
            # un-awaited (RuntimeWarning) on GC, then raise the cancellation.
            coro.close()
            raise asyncio.CancelledError

        monkeypatch.setattr("daemon.runner.asyncio.wait_for", _wait_for)

        # Should exit cleanly without hanging.
        await _worker_loop(config, queue, stop_event, use_following=False)

    @pytest.mark.asyncio
    async def test_handler_generic_exception_logged_and_continues(
        self, config, entity_store, monkeypatch, caplog
    ):
        """Handler raises → ERROR log + budget.on_error + drain via task_done.

        The handler failure is an entrypoint-contract edge the worker must
        survive; a raising stand-in for _handle_work_item drives it.
        """
        caplog.set_level(logging.ERROR)
        creator_id = snowflake_id()

        async def _raises(_config, _item):
            raise RuntimeError("worker boom")

        monkeypatch.setattr("daemon.runner._handle_work_item", _raises)

        queue: asyncio.Queue = asyncio.Queue()
        await queue.put(FullCreatorDownload(creator_id=creator_id))
        stop_event = asyncio.Event()

        async def _stop_when_drained():
            await queue.join()
            stop_event.set()

        await asyncio.gather(
            _worker_loop(
                config, queue, stop_event, use_following=False, budget=_make_budget()
            ),
            _stop_when_drained(),
        )

        errors = _logged(caplog, "ERROR")
        assert any(
            "worker error on FullCreatorDownload" in m and "worker boom" in m
            for m in errors
        )

    @pytest.mark.asyncio
    async def test_handler_error_without_budget_continues(
        self, config, monkeypatch, caplog
    ):
        """Handler error with budget=None skips ErrorBudget.on_error and still
        drains via task_done."""
        caplog.set_level(logging.ERROR)

        async def _raises(_config, _item):
            raise RuntimeError("no-budget boom")

        monkeypatch.setattr("daemon.runner._handle_work_item", _raises)

        queue: asyncio.Queue = asyncio.Queue()
        await queue.put(FullCreatorDownload(creator_id=snowflake_id()))
        stop_event = asyncio.Event()

        async def _stop_when_drained():
            await queue.join()
            stop_event.set()

        await asyncio.gather(
            _worker_loop(config, queue, stop_event, use_following=False),
            _stop_when_drained(),
        )

        errors = _logged(caplog, "ERROR")
        assert any("no-budget boom" in m for m in errors)

    @pytest.mark.asyncio
    async def test_handler_daemon_unrecoverable_raised(self, config, monkeypatch):
        """DaemonUnrecoverableError bypasses the generic except and propagates.

        DEFENSIVE-BRANCH: exercises the worker's re-raise contract via a
        raising handler double.
        """

        async def _raises(_config, _item):
            raise DaemonUnrecoverableError("worker fatal")

        monkeypatch.setattr("daemon.runner._handle_work_item", _raises)

        queue: asyncio.Queue = asyncio.Queue()
        await queue.put(FullCreatorDownload(creator_id=snowflake_id()))
        stop_event = asyncio.Event()

        with pytest.raises(DaemonUnrecoverableError, match="worker fatal"):
            await _worker_loop(config, queue, stop_event, use_following=False)


class TestWorkerLoopPostProcessing:
    """Clean-success post-processing: real mark_creator_processed writes a
    MonitorState row for post-processed item types; skipped for others."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "item_factory",
        [
            lambda cid: RedownloadCreatorMedia(creator_id=cid),
            lambda cid: CheckCreatorAccess(creator_id=cid),
            lambda cid: DownloadStoriesOnly(creator_id=cid),
            # DownloadTimelineOnly with use_following=False takes the
            # 'if use_following and FullCreatorDownload' FALSE branch straight
            # to mark_creator_processed (no _refresh_following).
            lambda cid: DownloadTimelineOnly(creator_id=cid),
        ],
        ids=["redownload", "check_access", "stories_only", "timeline_only"],
    )
    async def test_post_processed_item_marks_creator_via_real_state(
        self, item_factory, config, entity_store, monkeypatch
    ):
        """A clean handler success on a post-processed item type runs the REAL
        mark_creator_processed, which persists a MonitorState row.
        """
        creator_id = snowflake_id()
        await _seed_account(entity_store, creator_id, f"worker_{creator_id}")

        # Only the dispatch router is a spy; mark_creator_processed runs real.
        monkeypatch.setattr("daemon.runner._handle_work_item", async_noop_spy())

        queue: asyncio.Queue = asyncio.Queue()
        await queue.put(item_factory(creator_id))
        stop_event = asyncio.Event()

        async def _stop_when_drained():
            await queue.join()
            stop_event.set()

        await asyncio.gather(
            _worker_loop(config, queue, stop_event, use_following=False),
            _stop_when_drained(),
        )

        # Real mark_creator_processed created/updated the MonitorState row.
        row = await entity_store.get(MonitorState, creator_id)
        assert row is not None
        assert row.lastCheckedAt is not None

    @pytest.mark.asyncio
    async def test_success_non_postprocessed_item_skips_mark(
        self, config, entity_store, monkeypatch
    ):
        """DownloadMessagesForGroup is in neither post-processing group → goes
        straight to task_done, no MonitorState written.
        """
        sender_id = snowflake_id()
        await _seed_account(entity_store, sender_id, f"sender_{sender_id}")

        monkeypatch.setattr("daemon.runner._handle_work_item", async_noop_spy())

        queue: asyncio.Queue = asyncio.Queue()
        await queue.put(DownloadMessagesForGroup(group_id=500, sender_id=sender_id))
        stop_event = asyncio.Event()

        async def _stop_when_drained():
            await queue.join()
            stop_event.set()

        await asyncio.gather(
            _worker_loop(config, queue, stop_event, use_following=False),
            _stop_when_drained(),
        )

        # No post-processing → no MonitorState row for this creator.
        assert await entity_store.get(MonitorState, sender_id) is None

    @pytest.mark.asyncio
    async def test_full_creator_download_refreshes_following_when_use_following(
        self, config, entity_store, monkeypatch
    ):
        """FullCreatorDownload + use_following=True → real mark_creator_processed
        AND _refresh_following runs. get_following/account_info are entrypoints
        (boundary doubles); mark_creator_processed writes a real MonitorState row.
        """
        creator_id = snowflake_id()
        await _seed_account(entity_store, creator_id, f"full_{creator_id}")

        monkeypatch.setattr("daemon.runner._handle_work_item", async_noop_spy())

        info_spy = async_noop_spy()

        async def _following(_c, _s):
            return {"followed_a", "followed_b"}

        monkeypatch.setattr("daemon.runner.get_creator_account_info", info_spy)
        monkeypatch.setattr("daemon.runner.get_following_accounts", _following)

        queue: asyncio.Queue = asyncio.Queue()
        await queue.put(FullCreatorDownload(creator_id=creator_id))
        stop_event = asyncio.Event()

        async def _stop_when_drained():
            await queue.join()
            stop_event.set()

        await asyncio.gather(
            _worker_loop(config, queue, stop_event, use_following=True),
            _stop_when_drained(),
        )

        # _refresh_following ran and updated user_names from the entrypoint set.
        assert config.user_names == {"followed_a", "followed_b"}
        # Real mark_creator_processed wrote the MonitorState row.
        row = await entity_store.get(MonitorState, creator_id)
        assert row is not None
        assert row.lastCheckedAt is not None
