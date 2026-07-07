"""Unit tests for daemon.filters -- should_process_creator.

Uses a real EntityStore (PostgreSQL) and a real FanslyApi wired into
mock_config._api via the ``respx_fansly_api`` fixture, which intercepts at
the HTTP boundary (and owns the blanket OPTIONS preflight route) without the
asyncpg event-loop conflicts the respx decorator form would introduce.

The main class shares ONE class-scoped database via ``class_entity_store``
(requested through ``reset_class_store``); every test creates its own Account
and MonitorState keyed by a unique snowflake creator id, so rows never collide
in the shared DB. The MonitorState-load-failure test keeps its own isolated
store because it wraps ``get_store`` with a failing proxy.

State persistence (mark_creator_processed) is tested in test_state.py.

Coverage targets:
  1. should_process_creator returns True when no MonitorState exists (first run)
  2. should_process_creator returns True when MonitorState.lastCheckedAt is None
  3. should_process_creator returns True when latest non-pinned post is newer
     than lastCheckedAt
  4. should_process_creator returns False when no non-pinned posts on any page
     within MAX_FILTER_PAGES (all-pinned pagination exhausted)
  5. should_process_creator returns False when latest post is older than baseline
  6. should_process_creator returns True on API failure (conservative fallback)
  7. should_process_creator uses session_baseline in preference to lastCheckedAt
  8. should_process_creator filters out pinned posts correctly and paginates
  9. Pagination stops early when page 1 has non-pinned posts (no page 2 fetch)
 10. Pagination finds non-pinned on page 2 when page 1 is all-pinned
 11. Pagination exhausts MAX_FILTER_PAGES when all pages are pinned -> False
 12. prefetched_posts with non-pinned: zero API calls made
 13. prefetched_posts all-pinned: falls through to API pagination
 14. MAX_FILTER_PAGES constant is importable and equals 3
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx

from api.fansly import FanslyApi
from config.fanslyconfig import FanslyConfig
from daemon.filters import (
    MAX_FILTER_PAGES,
    _is_newer_than_baseline,
    should_process_creator,
)
from metadata.entity_store import PostgresEntityStore
from metadata.models import get_store as real_get_store
from tests.fixtures.api.api_fixtures import dump_fansly_calls
from tests.fixtures.metadata.metadata_factories import (
    AccountFactory,
    MonitorStateFactory,
)
from tests.fixtures.utils.test_isolation import snowflake_id


# ---------------------------------------------------------------------------
# URL alias — use url__startswith because ngsw-bypass param is appended.
# Test routes use ``TIMELINE_URL`` as a creator-agnostic prefix; the actual
# /timelinenew/{creator_id}/... paths inherit from this prefix.
# ---------------------------------------------------------------------------

TIMELINE_URL = FanslyApi.TIMELINE_NEW_ENDPOINT.format("")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_post_dict(
    post_id: int,
    account_id: int,
    created_at_ms: int,
    *,
    pinned: bool = False,
) -> dict:
    """Build a minimal post dict matching the shape get_json_response_contents returns."""
    d: dict = {
        "id": post_id,
        "accountId": account_id,
        "content": "test content",
        "fypFlag": 0,
        "createdAt": created_at_ms,
    }
    if pinned:
        d["pinned"] = True
    return d


def _timeline_response(posts: list[dict]) -> httpx.Response:
    """Wrap posts in the standard Fansly timeline JSON envelope."""
    return httpx.Response(
        200,
        json={"success": True, "response": {"posts": posts}},
    )


async def _make_saved_account(store: PostgresEntityStore) -> int:
    """Persist a fresh Account with a unique snowflake id, returning the id."""
    account_id = snowflake_id()
    await store.save(AccountFactory.build(id=account_id))
    return account_id


# ---------------------------------------------------------------------------
# should_process_creator
# ---------------------------------------------------------------------------


@pytest.mark.asyncio(loop_scope="class")
@pytest.mark.xdist_group("daemon_filters")
class TestShouldProcessCreator:
    """Tests for should_process_creator() over ONE shared class DB."""

    async def test_returns_true_on_first_run_no_monitor_state(
        self, respx_fansly_api, mock_config, reset_class_store
    ):
        """No MonitorState row (first daemon run) -> process unconditionally.

        Without a stored baseline there is nothing to compare against, so we
        always process to avoid silently skipping a creator on the first run.
        """
        creator_id = await _make_saved_account(reset_class_store)

        route = respx.get(url__startswith=TIMELINE_URL).mock(
            side_effect=[_timeline_response([])]
        )
        try:
            result = await should_process_creator(mock_config, creator_id)
        finally:
            dump_fansly_calls(route.calls, "first_run_no_state")

        assert result is True

    async def test_returns_true_when_last_checked_at_is_none(
        self, respx_fansly_api, mock_config, reset_class_store
    ):
        """MonitorState exists but lastCheckedAt is None -> treat as first run."""
        creator_id = await _make_saved_account(reset_class_store)
        state = MonitorStateFactory.build(
            creatorId=creator_id,
            lastCheckedAt=None,
        )
        await reset_class_store.save(state)

        route = respx.get(url__startswith=TIMELINE_URL).mock(
            side_effect=[_timeline_response([])]
        )
        try:
            result = await should_process_creator(mock_config, creator_id)
        finally:
            dump_fansly_calls(route.calls, "last_checked_at_none")

        assert result is True

    @pytest.mark.parametrize(
        ("delta_hours", "expected"),
        [
            pytest.param(3, True, id="newer-post-processes"),
            pytest.param(-1, False, id="older-post-inactive-skipped"),
        ],
    )
    async def test_latest_post_age_vs_baseline(
        self,
        respx_fansly_api: FanslyApi,
        mock_config: FanslyConfig,
        reset_class_store: PostgresEntityStore,
        request: pytest.FixtureRequest,
        delta_hours: int,
        expected: bool,
    ) -> None:
        """Latest non-pinned post createdAt > lastCheckedAt -> process;
        createdAt <= lastCheckedAt -> creator inactive since last run -> skip."""
        creator_id = await _make_saved_account(reset_class_store)
        baseline = datetime(2026, 4, 10, 12, 0, 0, tzinfo=UTC)
        post_ms = int((baseline + timedelta(hours=delta_hours)).timestamp() * 1000)

        state = MonitorStateFactory.build(
            creatorId=creator_id,
            lastCheckedAt=baseline,
        )
        await reset_class_store.save(state)

        post = _make_post_dict(snowflake_id(), creator_id, post_ms)

        route = respx.get(url__startswith=TIMELINE_URL).mock(
            side_effect=[_timeline_response([post])]
        )
        try:
            result = await should_process_creator(mock_config, creator_id)
        finally:
            dump_fansly_calls(route.calls, request.node.name)

        assert result is expected

    async def test_returns_false_when_no_non_pinned_posts(
        self, respx_fansly_api, mock_config, reset_class_store
    ):
        """Empty posts list on every page -> no activity signal -> skip.

        With MAX_FILTER_PAGES=3 the function will make up to 3 GET requests.
        An empty page signals a genuinely empty timeline and returns False
        immediately without fetching further pages.
        """
        creator_id = await _make_saved_account(reset_class_store)
        baseline = datetime(2026, 4, 10, 12, 0, 0, tzinfo=UTC)

        state = MonitorStateFactory.build(
            creatorId=creator_id,
            lastCheckedAt=baseline,
        )
        await reset_class_store.save(state)

        route = respx.get(url__startswith=TIMELINE_URL).mock(
            side_effect=[_timeline_response([])]
        )
        try:
            result = await should_process_creator(mock_config, creator_id)
        finally:
            dump_fansly_calls(route.calls, "no_posts")

        assert result is False

    async def test_returns_true_on_api_failure(
        self, respx_fansly_api, mock_config, reset_class_store
    ):
        """API returns 500 (exhausts all retries) -> conservative True.

        RetryTransport is configured with total=3, so the first attempt plus
        3 retries = 4 total GET responses are consumed before the transport
        gives up and raises.
        """
        creator_id = await _make_saved_account(reset_class_store)
        baseline = datetime(2026, 4, 10, 12, 0, 0, tzinfo=UTC)

        state = MonitorStateFactory.build(
            creatorId=creator_id,
            lastCheckedAt=baseline,
        )
        await reset_class_store.save(state)

        # 4 responses: 1 initial attempt + 3 retries (total=3)
        route = respx.get(url__startswith=TIMELINE_URL).mock(
            side_effect=[
                httpx.Response(500),
                httpx.Response(500),
                httpx.Response(500),
                httpx.Response(500),
            ]
        )
        try:
            result = await should_process_creator(mock_config, creator_id)
        finally:
            dump_fansly_calls(route.calls, "api_failure")

        assert result is True

    async def test_session_baseline_overrides_monitor_state(
        self, respx_fansly_api, mock_config, reset_class_store
    ):
        """session_baseline kwarg takes priority over MonitorState.lastCheckedAt.

        lastCheckedAt is set NEWER than the post (would return False), but the
        session_baseline is set much OLDER (2000-01-01), forcing True.
        """
        creator_id = await _make_saved_account(reset_class_store)
        post_time = datetime(2026, 4, 10, 12, 0, 0, tzinfo=UTC)
        post_ms = int(post_time.timestamp() * 1000)

        # MonitorState lastCheckedAt is after the post -> would return False alone
        state = MonitorStateFactory.build(
            creatorId=creator_id,
            lastCheckedAt=post_time + timedelta(hours=1),
        )
        await reset_class_store.save(state)

        # session_baseline is before the post -> overrides, forces True
        old_baseline = datetime(2000, 1, 1, tzinfo=UTC)
        post = _make_post_dict(snowflake_id(), creator_id, post_ms)

        route = respx.get(url__startswith=TIMELINE_URL).mock(
            side_effect=[_timeline_response([post])]
        )
        try:
            result = await should_process_creator(
                mock_config,
                creator_id,
                session_baseline=old_baseline,
            )
        finally:
            dump_fansly_calls(route.calls, "session_baseline_override")

        assert result is True

    async def test_pinned_posts_filtered_out_all_pages_pinned(
        self, respx_fansly_api, mock_config, reset_class_store
    ):
        """All posts across every page are pinned -> False after MAX_FILTER_PAGES.

        Pinned posts reflect original publish time, not recent activity, so they
        must not trigger processing. With MAX_FILTER_PAGES=3 the function will
        exhaust all three pages (exactly MAX_FILTER_PAGES fetches) before
        returning False.
        """
        creator_id = await _make_saved_account(reset_class_store)
        baseline = datetime(2026, 4, 10, 12, 0, 0, tzinfo=UTC)
        # pinned post has a time NEWER than baseline, but it's pinned
        newer_ms = int((baseline + timedelta(hours=1)).timestamp() * 1000)

        state = MonitorStateFactory.build(
            creatorId=creator_id,
            lastCheckedAt=baseline,
        )
        await reset_class_store.save(state)

        pinned_post = _make_post_dict(snowflake_id(), creator_id, newer_ms, pinned=True)

        # Supply MAX_FILTER_PAGES all-pinned responses
        route = respx.get(url__startswith=TIMELINE_URL).mock(
            side_effect=[
                _timeline_response([pinned_post]),
                _timeline_response([pinned_post]),
                _timeline_response([pinned_post]),
            ]
        )
        try:
            result = await should_process_creator(mock_config, creator_id)
        finally:
            dump_fansly_calls(route.calls, "pinned_all_pages")

        assert result is False
        assert route.call_count == MAX_FILTER_PAGES

    # -----------------------------------------------------------------------
    # New pagination tests (D1)
    # -----------------------------------------------------------------------

    async def test_pagination_page2_has_non_pinned_returns_true(
        self, respx_fansly_api, mock_config, reset_class_store
    ):
        """Page 1 all-pinned, page 2 has a newer non-pinned post -> True.

        The function must paginate past an all-pinned first page rather than
        short-circuiting to False.
        """
        creator_id = await _make_saved_account(reset_class_store)
        baseline = datetime(2026, 4, 10, 12, 0, 0, tzinfo=UTC)
        pinned_ms = int((baseline + timedelta(hours=1)).timestamp() * 1000)
        newer_ms = int((baseline + timedelta(hours=2)).timestamp() * 1000)

        state = MonitorStateFactory.build(
            creatorId=creator_id,
            lastCheckedAt=baseline,
        )
        await reset_class_store.save(state)

        pinned_post = _make_post_dict(
            snowflake_id(), creator_id, pinned_ms, pinned=True
        )
        non_pinned_post = _make_post_dict(snowflake_id(), creator_id, newer_ms)

        route = respx.get(url__startswith=TIMELINE_URL).mock(
            side_effect=[
                _timeline_response([pinned_post]),  # page 1: all pinned
                _timeline_response([non_pinned_post]),  # page 2: non-pinned newer
            ]
        )
        try:
            result = await should_process_creator(mock_config, creator_id)
        finally:
            dump_fansly_calls(route.calls, "page2_non_pinned")

        assert result is True
        assert route.call_count == 2

    async def test_pagination_page1_non_pinned_no_page2_fetch(
        self, respx_fansly_api, mock_config, reset_class_store
    ):
        """Page 1 has a non-pinned post -> short-circuit, page 2 never fetched."""
        creator_id = await _make_saved_account(reset_class_store)
        baseline = datetime(2026, 4, 10, 12, 0, 0, tzinfo=UTC)
        newer_ms = int((baseline + timedelta(hours=1)).timestamp() * 1000)

        state = MonitorStateFactory.build(
            creatorId=creator_id,
            lastCheckedAt=baseline,
        )
        await reset_class_store.save(state)

        post = _make_post_dict(snowflake_id(), creator_id, newer_ms)

        route = respx.get(url__startswith=TIMELINE_URL).mock(
            side_effect=[
                _timeline_response([post]),
                # Second response should never be consumed
                _timeline_response([post]),
            ]
        )
        try:
            result = await should_process_creator(mock_config, creator_id)
        finally:
            dump_fansly_calls(route.calls, "page1_short_circuit")

        assert result is True
        assert route.call_count == 1

    # -----------------------------------------------------------------------
    # prefetched_posts tests (F1 prep)
    # -----------------------------------------------------------------------

    async def test_prefetched_posts_non_pinned_zero_api_calls(
        self, respx_fansly_api, mock_config, reset_class_store
    ):
        """prefetched_posts contains a non-pinned newer post -> True, zero GET calls."""
        creator_id = await _make_saved_account(reset_class_store)
        baseline = datetime(2026, 4, 10, 12, 0, 0, tzinfo=UTC)
        newer_ms = int((baseline + timedelta(hours=1)).timestamp() * 1000)

        state = MonitorStateFactory.build(
            creatorId=creator_id,
            lastCheckedAt=baseline,
        )
        await reset_class_store.save(state)

        post = _make_post_dict(snowflake_id(), creator_id, newer_ms)

        route = respx.get(url__startswith=TIMELINE_URL).mock(
            side_effect=[]  # should never be called
        )
        try:
            result = await should_process_creator(
                mock_config,
                creator_id,
                prefetched_posts=[post],
            )
        finally:
            dump_fansly_calls(route.calls, "prefetched_non_pinned")

        assert result is True
        assert route.call_count == 0

    async def test_prefetched_posts_all_pinned_falls_through_to_api(
        self, respx_fansly_api, mock_config, reset_class_store
    ):
        """prefetched_posts all-pinned -> falls through to paginate via API.

        The prefetched all-pinned page does not count against MAX_FILTER_PAGES,
        so the loop still makes up to MAX_FILTER_PAGES API calls. When those are
        also all-pinned the function returns False.
        """
        creator_id = await _make_saved_account(reset_class_store)
        baseline = datetime(2026, 4, 10, 12, 0, 0, tzinfo=UTC)
        pinned_ms = int((baseline + timedelta(hours=1)).timestamp() * 1000)
        newer_ms = int((baseline + timedelta(hours=2)).timestamp() * 1000)

        state = MonitorStateFactory.build(
            creatorId=creator_id,
            lastCheckedAt=baseline,
        )
        await reset_class_store.save(state)

        pinned_post = _make_post_dict(
            snowflake_id(), creator_id, pinned_ms, pinned=True
        )
        non_pinned_post = _make_post_dict(snowflake_id(), creator_id, newer_ms)

        route = respx.get(url__startswith=TIMELINE_URL).mock(
            side_effect=[
                _timeline_response([non_pinned_post]),  # API page 1 after prefetch
            ]
        )
        try:
            result = await should_process_creator(
                mock_config,
                creator_id,
                prefetched_posts=[pinned_post],
            )
        finally:
            dump_fansly_calls(route.calls, "prefetched_all_pinned_fallthrough")

        assert result is True
        assert route.call_count == 1

    # -----------------------------------------------------------------------
    # MAX_FILTER_PAGES constant
    # -----------------------------------------------------------------------

    def test_max_filter_pages_constant_accessible_and_equals_3(self):
        """MAX_FILTER_PAGES is importable from daemon.filters and equals 3."""
        assert MAX_FILTER_PAGES == 3


# ---------------------------------------------------------------------------
# Edge coverage — _is_newer_than_baseline, MonitorState load error
# ---------------------------------------------------------------------------


class TestIsNewerThanBaselineUnparseable:
    """Lines 76-80: _is_newer_than_baseline returns True conservatively on parse failure."""

    def test_unparseable_timestamp_returns_true_with_warning(self, caplog):
        """When createdAt cannot be parsed, log warning and return True (don't miss content)."""
        caplog.set_level(logging.WARNING)

        baseline = datetime(2026, 4, 15, tzinfo=UTC)
        # "garbage" is not valid ISO -> parse_timestamp raises -> conservative True
        result = _is_newer_than_baseline(
            {"createdAt": "garbage"}, baseline, creator_id=12345
        )

        assert result is True
        warnings = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
        assert any("unrecognised createdAt for creator 12345" in m for m in warnings)


class TestShouldProcessCreatorMonitorStateLoadError:
    """Lines 158-164: store.get(MonitorState) raises → log warning + return True conservatively.

    Kept on its own isolated ``entity_store`` (not the shared class DB) because
    it wraps the registered store with a failing proxy via monkeypatch.
    """

    @pytest.mark.asyncio
    async def test_monitor_state_load_failure_returns_true(
        self, config_wired, entity_store, monkeypatch, caplog
    ):
        """When MonitorState load raises, default to processing (don't miss content)."""
        caplog.set_level(logging.WARNING)
        creator_id = snowflake_id()

        real_store = real_get_store()

        class _LoadFails:
            def __init__(self, real):
                self._real = real

            def get_from_cache(self, model, key):
                return self._real.get_from_cache(model, key)

            async def get(self, model, key):
                if model.__name__ == "MonitorState":
                    raise RuntimeError("simulated MonitorState load failure")
                return await self._real.get(model, key)

            async def save(self, obj):
                return await self._real.save(obj)

            def __getattr__(self, name):
                return getattr(self._real, name)

        monkeypatch.setattr("daemon.filters.get_store", lambda: _LoadFails(real_store))

        # session_baseline=None forces the MonitorState load path.
        result = await should_process_creator(
            config_wired, creator_id, session_baseline=None
        )

        assert result is True
        warnings = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
        assert any(
            "could not load MonitorState" in m
            and str(creator_id) in m
            and "simulated MonitorState load failure" in m
            for m in warnings
        )
