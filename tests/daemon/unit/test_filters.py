"""Unit tests for daemon.filters -- should_process_creator.

Uses a real EntityStore (PostgreSQL) and a real FanslyApi wired into
config._api.  RESPX intercepts at the HTTP boundary using
``with respx.mock:`` context managers (NOT decorators) to avoid asyncpg
event-loop conflicts.

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

from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx

from daemon.filters import MAX_FILTER_PAGES, should_process_creator
from tests.fixtures.api.api_fixtures import dump_fansly_calls
from tests.fixtures.metadata.metadata_factories import (
    MonitorStateFactory,
)
from tests.fixtures.utils.test_isolation import snowflake_id


# ---------------------------------------------------------------------------
# URL constant -- use url__startswith because ngsw-bypass param is appended
# ---------------------------------------------------------------------------

TIMELINE_URL = "https://apiv3.fansly.com/api/v1/timelinenew/"


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


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


# `saved_account` and `config_wired` come from the canonical fixtures
# (tests/fixtures/metadata/metadata_fixtures.py and tests/fixtures/core/
# config_fixtures.py respectively) via the wildcard import in tests/conftest.py.
# Per Cat L policy: don't redefine here.


# ---------------------------------------------------------------------------
# should_process_creator
# ---------------------------------------------------------------------------


class TestShouldProcessCreator:
    """Tests for should_process_creator()."""

    @pytest.mark.asyncio
    async def test_returns_true_on_first_run_no_monitor_state(
        self, config_wired, entity_store, saved_account
    ):
        """No MonitorState row (first daemon run) -> process unconditionally.

        Without a stored baseline there is nothing to compare against, so we
        always process to avoid silently skipping a creator on the first run.
        """
        creator_id = saved_account.id

        with respx.mock:
            respx.options(url__startswith=TIMELINE_URL).mock(
                side_effect=[httpx.Response(200)]
            )
            route = respx.get(url__startswith=TIMELINE_URL).mock(
                side_effect=[_timeline_response([])]
            )
            try:
                result = await should_process_creator(config_wired, creator_id)
            finally:
                dump_fansly_calls(route.calls, "first_run_no_state")

        assert result is True

    @pytest.mark.asyncio
    async def test_returns_true_when_last_checked_at_is_none(
        self, config_wired, entity_store, saved_account
    ):
        """MonitorState exists but lastCheckedAt is None -> treat as first run."""
        state = MonitorStateFactory.build(
            creatorId=saved_account.id,
            lastCheckedAt=None,
        )
        await entity_store.save(state)

        with respx.mock:
            respx.options(url__startswith=TIMELINE_URL).mock(
                side_effect=[httpx.Response(200)]
            )
            route = respx.get(url__startswith=TIMELINE_URL).mock(
                side_effect=[_timeline_response([])]
            )
            try:
                result = await should_process_creator(config_wired, saved_account.id)
            finally:
                dump_fansly_calls(route.calls, "last_checked_at_none")

        assert result is True

    @pytest.mark.asyncio
    async def test_returns_true_when_latest_post_is_newer_than_baseline(
        self, config_wired, entity_store, saved_account
    ):
        """Latest non-pinned post createdAt > lastCheckedAt -> process."""
        baseline = datetime(2026, 4, 10, 12, 0, 0, tzinfo=UTC)
        newer_ms = int((baseline + timedelta(hours=3)).timestamp() * 1000)

        state = MonitorStateFactory.build(
            creatorId=saved_account.id,
            lastCheckedAt=baseline,
        )
        await entity_store.save(state)

        post = _make_post_dict(snowflake_id(), saved_account.id, newer_ms)

        with respx.mock:
            respx.options(url__startswith=TIMELINE_URL).mock(
                side_effect=[httpx.Response(200)]
            )
            route = respx.get(url__startswith=TIMELINE_URL).mock(
                side_effect=[_timeline_response([post])]
            )
            try:
                result = await should_process_creator(config_wired, saved_account.id)
            finally:
                dump_fansly_calls(route.calls, "newer_post")

        assert result is True

    @pytest.mark.asyncio
    async def test_returns_false_when_no_non_pinned_posts(
        self, config_wired, entity_store, saved_account
    ):
        """Empty posts list on every page -> no activity signal -> skip.

        With MAX_FILTER_PAGES=3 the function will make up to 3 GET requests.
        An empty page signals a genuinely empty timeline and returns False
        immediately without fetching further pages.
        """
        baseline = datetime(2026, 4, 10, 12, 0, 0, tzinfo=UTC)

        state = MonitorStateFactory.build(
            creatorId=saved_account.id,
            lastCheckedAt=baseline,
        )
        await entity_store.save(state)

        with respx.mock:
            respx.options(url__startswith=TIMELINE_URL).mock(
                side_effect=[httpx.Response(200)]
            )
            route = respx.get(url__startswith=TIMELINE_URL).mock(
                side_effect=[_timeline_response([])]
            )
            try:
                result = await should_process_creator(config_wired, saved_account.id)
            finally:
                dump_fansly_calls(route.calls, "no_posts")

        assert result is False

    @pytest.mark.asyncio
    async def test_returns_false_when_latest_post_is_older_than_baseline(
        self, config_wired, entity_store, saved_account
    ):
        """Latest post createdAt <= lastCheckedAt -> creator inactive since last run."""
        baseline = datetime(2026, 4, 10, 12, 0, 0, tzinfo=UTC)
        older_ms = int((baseline - timedelta(hours=1)).timestamp() * 1000)

        state = MonitorStateFactory.build(
            creatorId=saved_account.id,
            lastCheckedAt=baseline,
        )
        await entity_store.save(state)

        post = _make_post_dict(snowflake_id(), saved_account.id, older_ms)

        with respx.mock:
            respx.options(url__startswith=TIMELINE_URL).mock(
                side_effect=[httpx.Response(200)]
            )
            route = respx.get(url__startswith=TIMELINE_URL).mock(
                side_effect=[_timeline_response([post])]
            )
            try:
                result = await should_process_creator(config_wired, saved_account.id)
            finally:
                dump_fansly_calls(route.calls, "older_post")

        assert result is False

    @pytest.mark.asyncio
    async def test_returns_true_on_api_failure(
        self, config_wired, entity_store, saved_account
    ):
        """API returns 500 (exhausts all retries) -> conservative True.

        RetryTransport is configured with total=3, so the first attempt plus
        3 retries = 4 total GET responses are consumed before the transport
        gives up and raises.
        """
        baseline = datetime(2026, 4, 10, 12, 0, 0, tzinfo=UTC)

        state = MonitorStateFactory.build(
            creatorId=saved_account.id,
            lastCheckedAt=baseline,
        )
        await entity_store.save(state)

        with respx.mock:
            respx.options(url__startswith=TIMELINE_URL).mock(
                side_effect=[httpx.Response(200)]
            )
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
                result = await should_process_creator(config_wired, saved_account.id)
            finally:
                dump_fansly_calls(route.calls, "api_failure")

        assert result is True

    @pytest.mark.asyncio
    async def test_session_baseline_overrides_monitor_state(
        self, config_wired, entity_store, saved_account
    ):
        """session_baseline kwarg takes priority over MonitorState.lastCheckedAt.

        lastCheckedAt is set NEWER than the post (would return False), but the
        session_baseline is set much OLDER (2000-01-01), forcing True.
        """
        post_time = datetime(2026, 4, 10, 12, 0, 0, tzinfo=UTC)
        post_ms = int(post_time.timestamp() * 1000)

        # MonitorState lastCheckedAt is after the post -> would return False alone
        state = MonitorStateFactory.build(
            creatorId=saved_account.id,
            lastCheckedAt=post_time + timedelta(hours=1),
        )
        await entity_store.save(state)

        # session_baseline is before the post -> overrides, forces True
        old_baseline = datetime(2000, 1, 1, tzinfo=UTC)
        post = _make_post_dict(snowflake_id(), saved_account.id, post_ms)

        with respx.mock:
            respx.options(url__startswith=TIMELINE_URL).mock(
                side_effect=[httpx.Response(200)]
            )
            route = respx.get(url__startswith=TIMELINE_URL).mock(
                side_effect=[_timeline_response([post])]
            )
            try:
                result = await should_process_creator(
                    config_wired,
                    saved_account.id,
                    session_baseline=old_baseline,
                )
            finally:
                dump_fansly_calls(route.calls, "session_baseline_override")

        assert result is True

    @pytest.mark.asyncio
    async def test_pinned_posts_filtered_out_all_pages_pinned(
        self, config_wired, entity_store, saved_account
    ):
        """All posts across every page are pinned -> False after MAX_FILTER_PAGES.

        Pinned posts reflect original publish time, not recent activity, so they
        must not trigger processing. With MAX_FILTER_PAGES=3 the function will
        exhaust all three pages before returning False.
        """
        baseline = datetime(2026, 4, 10, 12, 0, 0, tzinfo=UTC)
        # pinned post has a time NEWER than baseline, but it's pinned
        newer_ms = int((baseline + timedelta(hours=1)).timestamp() * 1000)

        state = MonitorStateFactory.build(
            creatorId=saved_account.id,
            lastCheckedAt=baseline,
        )
        await entity_store.save(state)

        pinned_post = _make_post_dict(
            snowflake_id(), saved_account.id, newer_ms, pinned=True
        )

        with respx.mock:
            # 3 page fetches = 3 OPTIONS + 3 GETs
            respx.options(url__startswith=TIMELINE_URL).mock(
                side_effect=[
                    httpx.Response(200),
                    httpx.Response(200),
                    httpx.Response(200),
                ]
            )
            # Supply MAX_FILTER_PAGES all-pinned responses
            route = respx.get(url__startswith=TIMELINE_URL).mock(
                side_effect=[
                    _timeline_response([pinned_post]),
                    _timeline_response([pinned_post]),
                    _timeline_response([pinned_post]),
                ]
            )
            try:
                result = await should_process_creator(config_wired, saved_account.id)
            finally:
                dump_fansly_calls(route.calls, "pinned_all_pages")

        assert result is False

    # -----------------------------------------------------------------------
    # New pagination tests (D1)
    # -----------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_pagination_page2_has_non_pinned_returns_true(
        self, config_wired, entity_store, saved_account
    ):
        """Page 1 all-pinned, page 2 has a newer non-pinned post -> True.

        The function must paginate past an all-pinned first page rather than
        short-circuiting to False.
        """
        baseline = datetime(2026, 4, 10, 12, 0, 0, tzinfo=UTC)
        pinned_ms = int((baseline + timedelta(hours=1)).timestamp() * 1000)
        newer_ms = int((baseline + timedelta(hours=2)).timestamp() * 1000)

        state = MonitorStateFactory.build(
            creatorId=saved_account.id,
            lastCheckedAt=baseline,
        )
        await entity_store.save(state)

        pinned_post = _make_post_dict(
            snowflake_id(), saved_account.id, pinned_ms, pinned=True
        )
        non_pinned_post = _make_post_dict(snowflake_id(), saved_account.id, newer_ms)

        with respx.mock:
            # 2 page fetches = 2 OPTIONS + 2 GETs
            respx.options(url__startswith=TIMELINE_URL).mock(
                side_effect=[
                    httpx.Response(200),
                    httpx.Response(200),
                ]
            )
            route = respx.get(url__startswith=TIMELINE_URL).mock(
                side_effect=[
                    _timeline_response([pinned_post]),  # page 1: all pinned
                    _timeline_response([non_pinned_post]),  # page 2: non-pinned newer
                ]
            )
            try:
                result = await should_process_creator(config_wired, saved_account.id)
            finally:
                dump_fansly_calls(route.calls, "page2_non_pinned")

        assert result is True
        assert route.call_count == 2

    @pytest.mark.asyncio
    async def test_pagination_all_three_pages_pinned_returns_false(
        self, config_wired, entity_store, saved_account
    ):
        """Pages 1-3 all pinned -> False after exactly MAX_FILTER_PAGES fetches."""
        baseline = datetime(2026, 4, 10, 12, 0, 0, tzinfo=UTC)
        pinned_ms = int((baseline + timedelta(hours=1)).timestamp() * 1000)

        state = MonitorStateFactory.build(
            creatorId=saved_account.id,
            lastCheckedAt=baseline,
        )
        await entity_store.save(state)

        pinned_post = _make_post_dict(
            snowflake_id(), saved_account.id, pinned_ms, pinned=True
        )

        with respx.mock:
            # 3 page fetches = 3 OPTIONS + 3 GETs
            respx.options(url__startswith=TIMELINE_URL).mock(
                side_effect=[
                    httpx.Response(200),
                    httpx.Response(200),
                    httpx.Response(200),
                ]
            )
            route = respx.get(url__startswith=TIMELINE_URL).mock(
                side_effect=[
                    _timeline_response([pinned_post]),
                    _timeline_response([pinned_post]),
                    _timeline_response([pinned_post]),
                ]
            )
            try:
                result = await should_process_creator(config_wired, saved_account.id)
            finally:
                dump_fansly_calls(route.calls, "all_three_pinned")

        assert result is False
        assert route.call_count == MAX_FILTER_PAGES

    @pytest.mark.asyncio
    async def test_pagination_page1_non_pinned_no_page2_fetch(
        self, config_wired, entity_store, saved_account
    ):
        """Page 1 has a non-pinned post -> short-circuit, page 2 never fetched."""
        baseline = datetime(2026, 4, 10, 12, 0, 0, tzinfo=UTC)
        newer_ms = int((baseline + timedelta(hours=1)).timestamp() * 1000)

        state = MonitorStateFactory.build(
            creatorId=saved_account.id,
            lastCheckedAt=baseline,
        )
        await entity_store.save(state)

        post = _make_post_dict(snowflake_id(), saved_account.id, newer_ms)

        with respx.mock:
            respx.options(url__startswith=TIMELINE_URL).mock(
                side_effect=[httpx.Response(200)]
            )
            route = respx.get(url__startswith=TIMELINE_URL).mock(
                side_effect=[
                    _timeline_response([post]),
                    # Second response should never be consumed
                    _timeline_response([post]),
                ]
            )
            try:
                result = await should_process_creator(config_wired, saved_account.id)
            finally:
                dump_fansly_calls(route.calls, "page1_short_circuit")

        assert result is True
        assert route.call_count == 1

    # -----------------------------------------------------------------------
    # prefetched_posts tests (F1 prep)
    # -----------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_prefetched_posts_non_pinned_zero_api_calls(
        self, config_wired, entity_store, saved_account
    ):
        """prefetched_posts contains a non-pinned newer post -> True, zero GET calls."""
        baseline = datetime(2026, 4, 10, 12, 0, 0, tzinfo=UTC)
        newer_ms = int((baseline + timedelta(hours=1)).timestamp() * 1000)

        state = MonitorStateFactory.build(
            creatorId=saved_account.id,
            lastCheckedAt=baseline,
        )
        await entity_store.save(state)

        post = _make_post_dict(snowflake_id(), saved_account.id, newer_ms)

        with respx.mock:
            respx.options(url__startswith=TIMELINE_URL).mock(
                side_effect=[httpx.Response(200)]
            )
            route = respx.get(url__startswith=TIMELINE_URL).mock(
                side_effect=[]  # should never be called
            )
            try:
                result = await should_process_creator(
                    config_wired,
                    saved_account.id,
                    prefetched_posts=[post],
                )
            finally:
                dump_fansly_calls(route.calls, "prefetched_non_pinned")

        assert result is True
        assert route.call_count == 0

    @pytest.mark.asyncio
    async def test_prefetched_posts_all_pinned_falls_through_to_api(
        self, config_wired, entity_store, saved_account
    ):
        """prefetched_posts all-pinned -> falls through to paginate via API.

        The prefetched all-pinned page does not count against MAX_FILTER_PAGES,
        so the loop still makes up to MAX_FILTER_PAGES API calls. When those are
        also all-pinned the function returns False.
        """
        baseline = datetime(2026, 4, 10, 12, 0, 0, tzinfo=UTC)
        pinned_ms = int((baseline + timedelta(hours=1)).timestamp() * 1000)
        newer_ms = int((baseline + timedelta(hours=2)).timestamp() * 1000)

        state = MonitorStateFactory.build(
            creatorId=saved_account.id,
            lastCheckedAt=baseline,
        )
        await entity_store.save(state)

        pinned_post = _make_post_dict(
            snowflake_id(), saved_account.id, pinned_ms, pinned=True
        )
        non_pinned_post = _make_post_dict(snowflake_id(), saved_account.id, newer_ms)

        with respx.mock:
            respx.options(url__startswith=TIMELINE_URL).mock(
                side_effect=[httpx.Response(200)]
            )
            route = respx.get(url__startswith=TIMELINE_URL).mock(
                side_effect=[
                    _timeline_response([non_pinned_post]),  # API page 1 after prefetch
                ]
            )
            try:
                result = await should_process_creator(
                    config_wired,
                    saved_account.id,
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
# Edge coverage — _parse_created_at, _is_newer_than_baseline, MonitorState load error
# ---------------------------------------------------------------------------


class TestParseCreatedAtEdges:
    """Lines 55-60: _parse_created_at branches — datetime passthrough + None fallback."""

    def test_datetime_input_returned_as_is(self):
        """Line 55-56: when raw is already a datetime, return it unchanged."""
        from daemon.filters import _parse_created_at

        ts = datetime(2026, 4, 15, 12, 0, 0, tzinfo=UTC)
        result = _parse_created_at(ts)
        assert result is ts

    def test_string_input_returns_none(self):
        """Line 60: string input → falls through both isinstance checks → None."""
        from daemon.filters import _parse_created_at

        assert _parse_created_at("2026-04-15") is None

    def test_none_input_returns_none(self):
        """Line 60: None input → falls through → None."""
        from daemon.filters import _parse_created_at

        assert _parse_created_at(None) is None


class TestIsNewerThanBaselineUnparseable:
    """Lines 76-80: _is_newer_than_baseline returns True conservatively on parse failure."""

    def test_unparseable_timestamp_returns_true_with_warning(self, caplog):
        """When createdAt cannot be parsed, log warning and return True (don't miss content)."""
        import logging as _logging

        from daemon.filters import _is_newer_than_baseline

        caplog.set_level(_logging.WARNING)

        baseline = datetime(2026, 4, 15, tzinfo=UTC)
        # createdAt is a string — _parse_created_at returns None → conservative True
        result = _is_newer_than_baseline(
            {"createdAt": "garbage"}, baseline, creator_id=12345
        )

        assert result is True
        warnings = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
        assert any("unrecognised createdAt for creator 12345" in m for m in warnings)


class TestShouldProcessCreatorMonitorStateLoadError:
    """Lines 158-164: store.get(MonitorState) raises → log warning + return True conservatively."""

    @pytest.mark.asyncio
    async def test_monitor_state_load_failure_returns_true(
        self, config_wired, entity_store, monkeypatch, caplog
    ):
        """When MonitorState load raises, default to processing (don't miss content)."""
        import logging as _logging

        caplog.set_level(_logging.WARNING)
        creator_id = snowflake_id()

        from daemon.filters import get_store as real_get_store

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
