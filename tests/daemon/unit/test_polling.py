"""Unit tests for daemon.polling — poll_home_timeline and poll_story_states.

Each polling function is tested with a real EntityStore (via the entity_store
fixture) and a real FanslyApi wired into config._api.  RESPX intercepts at the
HTTP boundary using ``with respx.mock:`` context managers (not decorators) to
avoid asyncpg event-loop conflicts.

Test inventory
--------------
poll_home_timeline
  1. Fresh cache — new_creator_ids contains creator, posts_by_creator populated
  2. All posts already cached — new_creator_ids empty, posts_by_creator still populated
  3. Mixed: some new + some cached — only new creators in new_creator_ids, both in posts_by_creator
  4. Multiple new posts from same creator — ID deduplicated (set semantics), both posts in posts_by_creator
  5. Empty posts array from API — (set(), {}) returned
  6. API raises HTTPError — (set(), {}) returned, warning logged
  7. Return shape is tuple[set[int], dict[int, list[dict]]]
  8. posts_by_creator groups posts by accountId correctly
  9. Creator with only known posts appears in posts_by_creator but not new_creator_ids
 10. Empty response body returns (set(), {})
 11. API failure returns (set(), {})

poll_story_states
 12.  First run (no rows) — hasActiveStories=True → ID returned, row created True
 13.  Prior False → now True — ID returned, row updated True
 14.  Prior True → still True — NOT returned, row unchanged
 15.  Prior True → now False — NOT returned, row updated False
 16.  First run, hasActiveStories=False — NOT returned, row created False
 17.  API raises — empty list returned
 18.  hasActiveStories=False, storyCount=2 — treated as active (flip detected)
 19.  hasActiveStories=True, storyCount=0 — treated as active (existing behaviour)
 20.  hasActiveStories=False, storyCount=0 — NOT active
"""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest
import respx

from daemon.polling import poll_home_timeline, poll_story_states
from metadata.models import Account, MonitorState, Post
from tests.fixtures.api.api_fixtures import dump_fansly_calls
from tests.fixtures.utils.test_isolation import snowflake_id


# ---------------------------------------------------------------------------
# URL constants — use url__startswith because ngsw-bypass param is appended
# ---------------------------------------------------------------------------

HOME_TIMELINE_URL = "https://apiv3.fansly.com/api/v1/timeline/home"
STORY_STATES_URL = "https://apiv3.fansly.com/api/v1/mediastories/following"


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _make_post_dict(post_id: int, account_id: int) -> dict:
    """Build a minimal post dict as returned by get_json_response_contents."""
    return {
        "id": post_id,
        "accountId": account_id,
        "content": "test content",
        "fypFlag": 0,
        "createdAt": int(datetime.now(UTC).timestamp() * 1000),
    }


def _make_story_state_dict(
    account_id: int, *, has_active: bool, story_count: int = 0
) -> dict:
    """Build a minimal story state dict as returned by get_json_response_contents."""
    return {
        "accountId": account_id,
        "hasActiveStories": has_active,
        "storyCount": story_count,
    }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


# `saved_account` and `config_wired` come from the canonical fixtures
# (tests/fixtures/metadata/metadata_fixtures.py and tests/fixtures/core/
# config_fixtures.py respectively) via the wildcard import in tests/conftest.py.
# Per Cat L policy: don't redefine here.


# ---------------------------------------------------------------------------
# poll_home_timeline
# ---------------------------------------------------------------------------


class TestPollHomeTimeline:
    """Tests for daemon.polling.poll_home_timeline.

    Return shape: tuple[set[int], dict[int, list[dict]]]
      - new_creator_ids: creators with at least one post absent from the cache
      - posts_by_creator: ALL creators on the page → their post dicts
        (includes creators whose posts are all already cached)
    """

    @pytest.mark.asyncio
    async def test_fresh_cache_returns_all_creator_ids(
        self, config_wired, entity_store, saved_account
    ):
        """Fresh cache — new_creator_ids contains creator, posts_by_creator populated."""
        creator_id = saved_account.id
        post_id = snowflake_id()
        post_dict = _make_post_dict(post_id, creator_id)

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
                            "response": {"posts": [post_dict]},
                        },
                    )
                ]
            )

            try:
                new_ids, posts_by_creator = await poll_home_timeline(config_wired)
            finally:
                dump_fansly_calls(route.calls, "fresh_cache")

        assert creator_id in new_ids
        assert len(new_ids) == 1
        assert creator_id in posts_by_creator
        assert len(posts_by_creator[creator_id]) == 1
        assert posts_by_creator[creator_id][0]["id"] == post_id

    @pytest.mark.asyncio
    async def test_all_posts_cached_new_ids_empty_posts_by_creator_populated(
        self, config_wired, entity_store, saved_account
    ):
        """All posts already cached — new_creator_ids empty but posts_by_creator
        still contains the creator and their posts.

        Design decision: posts_by_creator includes ALL creators seen on the
        timeline page, even those with no new posts. The runner uses it to
        pass prefetched posts to should_process_creator without re-fetching.
        """
        creator_id = saved_account.id
        post_id = snowflake_id()

        # Seed cache: save the post so get_from_cache finds it
        post = Post(
            id=post_id,
            accountId=creator_id,
            content="already known",
            fypFlag=0,
            createdAt=datetime.now(UTC),
        )
        await entity_store.save(post)

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
                                "posts": [_make_post_dict(post_id, creator_id)]
                            },
                        },
                    )
                ]
            )

            try:
                new_ids, posts_by_creator = await poll_home_timeline(config_wired)
            finally:
                dump_fansly_calls(route.calls, "all_cached")

        # No new posts — creator absent from new_ids
        assert new_ids == set()
        # But posts_by_creator still has the creator's post for the runner
        assert creator_id in posts_by_creator
        assert len(posts_by_creator[creator_id]) == 1

    @pytest.mark.asyncio
    async def test_mixed_posts_returns_only_new_creators_in_new_ids(
        self, config_wired, entity_store, saved_account
    ):
        """Mixed known + unknown posts — only new creator in new_ids, both in posts_by_creator."""
        creator_id = saved_account.id

        # A second creator
        creator_id2 = snowflake_id()
        acc2 = Account(
            id=creator_id2,
            username=f"creator2_{creator_id2}",
            displayName="Creator 2",
            createdAt=datetime.now(UTC),
        )
        await entity_store.save(acc2)

        known_post_id = snowflake_id()
        new_post_id = snowflake_id()

        # Seed one post as known (from creator_id)
        known_post = Post(
            id=known_post_id,
            accountId=creator_id,
            content="already known",
            fypFlag=0,
            createdAt=datetime.now(UTC),
        )
        await entity_store.save(known_post)

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
                                    _make_post_dict(known_post_id, creator_id),
                                    _make_post_dict(new_post_id, creator_id2),
                                ]
                            },
                        },
                    )
                ]
            )

            try:
                new_ids, posts_by_creator = await poll_home_timeline(config_wired)
            finally:
                dump_fansly_calls(route.calls, "mixed_posts")

        # Only creator_id2 has a new post
        assert creator_id not in new_ids
        assert creator_id2 in new_ids
        assert len(new_ids) == 1
        # Both creators appear in posts_by_creator
        assert creator_id in posts_by_creator
        assert creator_id2 in posts_by_creator

    @pytest.mark.asyncio
    async def test_multiple_new_posts_same_creator_deduplicates(
        self, config_wired, entity_store, saved_account
    ):
        """Multiple new posts from same creator — ID appears once in new_ids,
        both posts appear in posts_by_creator."""
        creator_id = saved_account.id
        post_id1 = snowflake_id()
        post_id2 = snowflake_id()

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
                                    _make_post_dict(post_id1, creator_id),
                                    _make_post_dict(post_id2, creator_id),
                                ]
                            },
                        },
                    )
                ]
            )

            try:
                new_ids, posts_by_creator = await poll_home_timeline(config_wired)
            finally:
                dump_fansly_calls(route.calls, "same_creator_dedup")

        # Set semantics: creator only appears once in new_ids
        assert new_ids == {creator_id}
        # posts_by_creator accumulates both posts for this creator
        assert creator_id in posts_by_creator
        assert len(posts_by_creator[creator_id]) == 2
        returned_ids = {p["id"] for p in posts_by_creator[creator_id]}
        assert returned_ids == {post_id1, post_id2}

    @pytest.mark.asyncio
    async def test_empty_posts_array_returns_empty_tuple(
        self, config_wired, entity_store
    ):
        """API returns empty posts array — (set(), {}) returned."""
        with respx.mock:
            respx.options(url__startswith=HOME_TIMELINE_URL).mock(
                side_effect=[httpx.Response(200)]
            )
            route = respx.get(url__startswith=HOME_TIMELINE_URL).mock(
                side_effect=[
                    httpx.Response(
                        200,
                        json={"success": True, "response": {"posts": []}},
                    )
                ]
            )

            try:
                new_ids, posts_by_creator = await poll_home_timeline(config_wired)
            finally:
                dump_fansly_calls(route.calls, "empty_posts")

        assert new_ids == set()
        assert posts_by_creator == {}

    @pytest.mark.asyncio
    async def test_api_http_error_returns_empty_tuple(self, config_wired, entity_store):
        """API raises ConnectError (HTTPError subclass) — returns (set(), {}).

        RetryTransport retries network errors (total=3), so we provide 4
        ConnectError responses (1 initial + 3 retries) to ensure the final
        exception propagates as httpx.HTTPError rather than StopIteration.
        """
        with respx.mock:
            respx.options(url__startswith=HOME_TIMELINE_URL).mock(
                side_effect=[httpx.Response(200)]
            )
            route = respx.get(url__startswith=HOME_TIMELINE_URL).mock(
                side_effect=[
                    httpx.ConnectError("Connection refused"),
                    httpx.ConnectError("Connection refused"),
                    httpx.ConnectError("Connection refused"),
                    httpx.ConnectError("Connection refused"),
                ]
            )

            new_ids, posts_by_creator = await poll_home_timeline(config_wired)
            # All 4 calls raised exceptions so call.response is not available;
            # dump_fansly_calls would ValueError — verify call count directly.
            assert len(route.calls) == 4

        assert new_ids == set()
        assert posts_by_creator == {}


# ---------------------------------------------------------------------------
# poll_story_states
# ---------------------------------------------------------------------------


class TestPollStoryStates:
    """Tests for daemon.polling.poll_story_states."""

    @pytest.mark.asyncio
    async def test_first_run_active_story_returns_creator_and_creates_row(
        self, config_wired, entity_store, saved_account
    ):
        """First run, no MonitorState row — hasActiveStories=True → ID returned,
        row created with lastHasActiveStories=True."""
        creator_id = saved_account.id

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
                                _make_story_state_dict(creator_id, has_active=True)
                            ],
                        },
                    )
                ]
            )

            try:
                result = await poll_story_states(config_wired)
            finally:
                dump_fansly_calls(route.calls, "first_run_active")

        assert creator_id in result

        # Verify MonitorState row was created with True
        saved_state = await entity_store.get(MonitorState, creator_id)
        assert saved_state is not None
        assert saved_state.lastHasActiveStories is True

    @pytest.mark.asyncio
    async def test_prior_false_now_true_returns_creator_updates_row(
        self, config_wired, entity_store, saved_account
    ):
        """Prior False → now True — ID returned, row updated to True."""
        creator_id = saved_account.id

        # Seed: prior state is False
        prior_state = MonitorState(
            creatorId=creator_id,
            lastHasActiveStories=False,
        )
        await entity_store.save(prior_state)

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
                                _make_story_state_dict(creator_id, has_active=True)
                            ],
                        },
                    )
                ]
            )

            try:
                result = await poll_story_states(config_wired)
            finally:
                dump_fansly_calls(route.calls, "prior_false_now_true")

        assert creator_id in result

        updated = await entity_store.get(MonitorState, creator_id)
        assert updated is not None
        assert updated.lastHasActiveStories is True

    @pytest.mark.asyncio
    async def test_prior_true_still_true_not_returned(
        self, config_wired, entity_store, saved_account
    ):
        """Prior True → still True — NOT returned (no flip), row stays True."""
        creator_id = saved_account.id

        prior_state = MonitorState(
            creatorId=creator_id,
            lastHasActiveStories=True,
        )
        await entity_store.save(prior_state)

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
                                _make_story_state_dict(creator_id, has_active=True)
                            ],
                        },
                    )
                ]
            )

            try:
                result = await poll_story_states(config_wired)
            finally:
                dump_fansly_calls(route.calls, "prior_true_still_true")

        assert creator_id not in result
        assert result == []

    @pytest.mark.asyncio
    async def test_prior_true_now_false_not_returned_row_updated(
        self, config_wired, entity_store, saved_account
    ):
        """Prior True → now False — NOT returned, row updated to False."""
        creator_id = saved_account.id

        prior_state = MonitorState(
            creatorId=creator_id,
            lastHasActiveStories=True,
        )
        await entity_store.save(prior_state)

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
                                _make_story_state_dict(creator_id, has_active=False)
                            ],
                        },
                    )
                ]
            )

            try:
                result = await poll_story_states(config_wired)
            finally:
                dump_fansly_calls(route.calls, "prior_true_now_false")

        assert creator_id not in result

        updated = await entity_store.get(MonitorState, creator_id)
        assert updated is not None
        assert updated.lastHasActiveStories is False

    @pytest.mark.asyncio
    async def test_first_run_inactive_not_returned_row_created_false(
        self, config_wired, entity_store, saved_account
    ):
        """First run, hasActiveStories=False — NOT returned, row created with False."""
        creator_id = saved_account.id

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
                                _make_story_state_dict(creator_id, has_active=False)
                            ],
                        },
                    )
                ]
            )

            try:
                result = await poll_story_states(config_wired)
            finally:
                dump_fansly_calls(route.calls, "first_run_inactive")

        assert creator_id not in result
        assert result == []

        saved_state = await entity_store.get(MonitorState, creator_id)
        assert saved_state is not None
        assert saved_state.lastHasActiveStories is False

    @pytest.mark.asyncio
    async def test_api_http_error_returns_empty_list(self, config_wired, entity_store):
        """API raises ConnectError (HTTPError subclass) — returns empty list.

        RetryTransport retries network errors (total=3), so we provide 4
        ConnectError responses (1 initial + 3 retries) to ensure the final
        exception propagates as httpx.HTTPError rather than StopIteration.
        """
        with respx.mock:
            respx.options(url__startswith=STORY_STATES_URL).mock(
                side_effect=[httpx.Response(200)]
            )
            route = respx.get(url__startswith=STORY_STATES_URL).mock(
                side_effect=[
                    httpx.ConnectError("Connection refused"),
                    httpx.ConnectError("Connection refused"),
                    httpx.ConnectError("Connection refused"),
                    httpx.ConnectError("Connection refused"),
                ]
            )

            result = await poll_story_states(config_wired)
            # All 4 calls raised exceptions so call.response is not available;
            # dump_fansly_calls would ValueError — verify call count directly.
            assert len(route.calls) == 4

        assert result == []

    @pytest.mark.asyncio
    async def test_story_count_nonzero_treated_as_active_flip_detected(
        self, config_wired, entity_store, saved_account
    ):
        """hasActiveStories=False, storyCount=2 — storyCount fallback triggers
        active detection. Prior state is False (inactive), so flip is detected
        and creator ID returned."""
        creator_id = saved_account.id

        # Seed prior state as inactive
        prior_state = MonitorState(
            creatorId=creator_id,
            lastHasActiveStories=False,
        )
        await entity_store.save(prior_state)

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
                                _make_story_state_dict(
                                    creator_id, has_active=False, story_count=2
                                )
                            ],
                        },
                    )
                ]
            )

            try:
                result = await poll_story_states(config_wired)
            finally:
                dump_fansly_calls(route.calls, "story_count_nonzero_flip")

        assert creator_id in result

        updated = await entity_store.get(MonitorState, creator_id)
        assert updated is not None
        assert updated.lastHasActiveStories is True

    @pytest.mark.asyncio
    async def test_has_active_stories_true_story_count_zero_still_active(
        self, config_wired, entity_store, saved_account
    ):
        """hasActiveStories=True, storyCount=0 — hasActiveStories alone is
        sufficient; existing behaviour preserved. Prior state is False, so flip
        is detected and creator ID returned."""
        creator_id = saved_account.id

        prior_state = MonitorState(
            creatorId=creator_id,
            lastHasActiveStories=False,
        )
        await entity_store.save(prior_state)

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
                                _make_story_state_dict(
                                    creator_id, has_active=True, story_count=0
                                )
                            ],
                        },
                    )
                ]
            )

            try:
                result = await poll_story_states(config_wired)
            finally:
                dump_fansly_calls(route.calls, "has_active_true_count_zero")

        assert creator_id in result

    @pytest.mark.asyncio
    async def test_has_active_false_story_count_zero_not_active(
        self, config_wired, entity_store, saved_account
    ):
        """hasActiveStories=False, storyCount=0 — neither signal active; creator
        is NOT returned regardless of prior state."""
        creator_id = saved_account.id

        prior_state = MonitorState(
            creatorId=creator_id,
            lastHasActiveStories=False,
        )
        await entity_store.save(prior_state)

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
                                _make_story_state_dict(
                                    creator_id, has_active=False, story_count=0
                                )
                            ],
                        },
                    )
                ]
            )

            try:
                result = await poll_story_states(config_wired)
            finally:
                dump_fansly_calls(route.calls, "both_signals_inactive")

        assert creator_id not in result
        assert result == []


# ---------------------------------------------------------------------------
# Edge coverage — generic exception, malformed response shape, save errors
# ---------------------------------------------------------------------------


class TestPollHomeTimelineGenericException:
    """Lines 64-68: catch-all Exception path (non-HTTPError) returns (set(), {})."""

    @pytest.mark.asyncio
    async def test_generic_exception_returns_empty_tuple(
        self, config_wired, entity_store, monkeypatch, caplog
    ):
        """When the API client raises a non-HTTPError exception, log warning + return empty."""
        import logging as _logging

        caplog.set_level(_logging.WARNING)

        api = config_wired.get_api()

        def _raises():
            raise RuntimeError("simulated non-http failure")

        monkeypatch.setattr(api, "get_home_timeline", _raises)

        new_ids, posts_by_creator = await poll_home_timeline(config_wired)

        assert new_ids == set()
        assert posts_by_creator == {}

        warnings = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
        assert any(
            "unexpected error fetching home timeline" in m
            and "simulated non-http failure" in m
            for m in warnings
        )


class TestPollStoryStatesEdges:
    """Lines 111-122 + 156-162: generic exception, non-list response, save error."""

    @pytest.mark.asyncio
    async def test_generic_exception_returns_empty_list(
        self, config_wired, entity_store, monkeypatch, caplog
    ):
        """Lines 111-115: non-HTTPError exception → log + return []."""
        import logging as _logging

        caplog.set_level(_logging.WARNING)

        api = config_wired.get_api()

        def _raises():
            raise RuntimeError("simulated story API failure")

        monkeypatch.setattr(api, "get_story_states_following", _raises)

        result = await poll_story_states(config_wired)

        assert result == []
        warnings = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
        assert any(
            "unexpected error fetching story states" in m
            and "simulated story API failure" in m
            for m in warnings
        )

    @pytest.mark.asyncio
    async def test_non_list_response_returns_empty_with_warning(
        self, config_wired, entity_store, caplog
    ):
        """Lines 117-122: response is not a list → log warning + return []."""
        import logging as _logging

        caplog.set_level(_logging.WARNING)

        with respx.mock:
            respx.options(url__startswith=STORY_STATES_URL).mock(
                side_effect=[httpx.Response(200)]
            )
            respx.get(url__startswith=STORY_STATES_URL).mock(
                side_effect=[
                    httpx.Response(
                        200,
                        json={
                            "success": True,
                            # response should be a list; instead is a dict.
                            "response": {"unexpected": "shape"},
                        },
                    )
                ]
            )
            result = await poll_story_states(config_wired)

        assert result == []
        warnings = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
        assert any("unexpected story states response shape" in m for m in warnings)

    @pytest.mark.asyncio
    async def test_save_exception_inside_loop_logged_and_skipped(
        self, config_wired, entity_store, monkeypatch, caplog
    ):
        """Lines 156-162: store.save raises mid-loop → log warning + creator NOT returned."""
        import logging as _logging

        caplog.set_level(_logging.WARNING)
        creator_id = snowflake_id()

        # Patch get_store to return a wrapper whose save raises for MonitorState.
        from daemon.polling import get_store as real_get_store

        real_store = real_get_store()

        class _SaveFails:
            def __init__(self, real):
                self._real = real

            def get_from_cache(self, model, key):
                return self._real.get_from_cache(model, key)

            async def get(self, model, key):
                return await self._real.get(model, key)

            async def save(self, obj):
                if obj.__class__.__name__ == "MonitorState":
                    raise RuntimeError("simulated monitor save failure")
                return await self._real.save(obj)

            def __getattr__(self, name):
                return getattr(self._real, name)

        monkeypatch.setattr("daemon.polling.get_store", lambda: _SaveFails(real_store))

        with respx.mock:
            respx.options(url__startswith=STORY_STATES_URL).mock(
                side_effect=[httpx.Response(200)]
            )
            respx.get(url__startswith=STORY_STATES_URL).mock(
                side_effect=[
                    httpx.Response(
                        200,
                        json={
                            "success": True,
                            "response": [
                                {
                                    "accountId": creator_id,
                                    "hasActiveStories": True,
                                    "storyCount": 1,
                                }
                            ],
                        },
                    )
                ]
            )
            result = await poll_story_states(config_wired)

        # CORRECTED: production appends to creators_with_new_stories BEFORE the
        # save try-block fires. The creator IS returned even when save raises;
        # the warning log is the only observable difference vs the happy path.
        # Comment in production at line 142-143 explains why: persistence is
        # best-effort, the new-stories signal is preserved separately.
        assert creator_id in result

        warnings = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
        assert any(
            "could not save MonitorState" in m
            and str(creator_id) in m
            and "simulated monitor save failure" in m
            for m in warnings
        )
