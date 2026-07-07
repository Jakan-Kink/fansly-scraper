"""Unit tests for daemon.polling — poll_home_timeline and poll_story_states.

Each polling function is tested with a real EntityStore and a real FanslyApi
wired into mock_config._api via the ``respx_fansly_api`` fixture.  RESPX
intercepts at the HTTP boundary through that fixture (which owns the respx
mock context and the blanket OPTIONS preflight route), so tests register only
their per-call GET routes.

Both classes share ONE class-scoped database each via ``class_entity_store``
(requested through ``reset_class_store``, which clears the in-memory
cache/identity map between methods — the known-post detection is cache-only,
so the reset keeps it per-test). Every test creates its own Account /
MonitorState keyed by a unique snowflake creator id.

Test inventory
--------------
poll_home_timeline
  1. Fresh cache — new_creator_ids contains creator, posts_by_creator populated
  2. All posts already cached — new_creator_ids empty, posts_by_creator still populated
  3. Mixed: some new + some cached — only new creators in new_creator_ids, both in posts_by_creator
  4. Multiple new posts from same creator — ID deduplicated (set semantics), both posts in posts_by_creator
  5. Empty posts array from API — (set(), {}) returned
  6. API raises HTTPError — (set(), {}) returned, warning logged
  7. Generic (non-HTTPError) exception — (set(), {}) returned, warning logged

poll_story_states
  8.  Truth table (one parametrized test, 8 rows) over
      (prior_state: None|True|False, hasActiveStories, storyCount) →
      (creator returned?, MonitorState row value): first-run active/inactive,
      prior False→True, prior True→True, prior True→False,
      storyCount>0 fallback, hasActiveStories-alone, both-signals-inactive
  9.  API raises — empty list returned
 10.  Generic exception — empty list, warning logged
 11.  Non-list response shape — empty list, warning logged
 12.  store.save raises mid-loop — creator still returned, warning logged
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import httpx
import pytest
import respx

from api.fansly import FanslyApi
from config.fanslyconfig import FanslyConfig
from daemon.polling import poll_home_timeline, poll_story_states
from metadata.entity_store import PostgresEntityStore
from metadata.models import Account, MonitorState, Post
from metadata.models import get_store as real_get_store
from tests.fixtures.api.api_fixtures import dump_fansly_calls
from tests.fixtures.metadata.metadata_factories import AccountFactory
from tests.fixtures.utils.test_isolation import snowflake_id


# ---------------------------------------------------------------------------
# URL aliases — use url__startswith because ngsw-bypass param is appended
# ---------------------------------------------------------------------------

HOME_TIMELINE_URL = FanslyApi.TIMELINE_HOME_ENDPOINT
STORY_STATES_URL = FanslyApi.MEDIA_STORIES_FOLLOWING_ENDPOINT


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


async def _make_saved_account(store: PostgresEntityStore) -> int:
    """Persist a fresh Account with a unique snowflake id, returning the id."""
    account_id = snowflake_id()
    await store.save(AccountFactory.build(id=account_id))
    return account_id


# ---------------------------------------------------------------------------
# poll_home_timeline
# ---------------------------------------------------------------------------


@pytest.mark.asyncio(loop_scope="class")
@pytest.mark.xdist_group("daemon_polling_home")
class TestPollHomeTimeline:
    """Tests for daemon.polling.poll_home_timeline over ONE shared class DB.

    Return shape: tuple[set[int], dict[int, list[dict]]]
      - new_creator_ids: creators with at least one post absent from the cache
      - posts_by_creator: ALL creators on the page → their post dicts
        (includes creators whose posts are all already cached)
    """

    async def test_fresh_cache_returns_all_creator_ids(
        self, respx_fansly_api, mock_config, reset_class_store
    ):
        """Fresh cache — new_creator_ids contains creator, posts_by_creator populated."""
        creator_id = await _make_saved_account(reset_class_store)
        post_id = snowflake_id()
        post_dict = _make_post_dict(post_id, creator_id)

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
            new_ids, posts_by_creator = await poll_home_timeline(mock_config)
        finally:
            dump_fansly_calls(route.calls, "fresh_cache")

        assert creator_id in new_ids
        assert len(new_ids) == 1
        assert creator_id in posts_by_creator
        assert len(posts_by_creator[creator_id]) == 1
        assert posts_by_creator[creator_id][0]["id"] == post_id

    async def test_all_posts_cached_new_ids_empty_posts_by_creator_populated(
        self, respx_fansly_api, mock_config, reset_class_store
    ):
        """All posts already cached — new_creator_ids empty but posts_by_creator
        still contains the creator and their posts.

        Design decision: posts_by_creator includes ALL creators seen on the
        timeline page, even those with no new posts. The runner uses it to
        pass prefetched posts to should_process_creator without re-fetching.
        """
        creator_id = await _make_saved_account(reset_class_store)
        post_id = snowflake_id()

        # Seed cache: save the post so get_from_cache finds it
        post = Post(
            id=post_id,
            accountId=creator_id,
            content="already known",
            fypFlags=0,
            createdAt=datetime.now(UTC),
        )
        await reset_class_store.save(post)

        route = respx.get(url__startswith=HOME_TIMELINE_URL).mock(
            side_effect=[
                httpx.Response(
                    200,
                    json={
                        "success": True,
                        "response": {"posts": [_make_post_dict(post_id, creator_id)]},
                    },
                )
            ]
        )

        try:
            new_ids, posts_by_creator = await poll_home_timeline(mock_config)
        finally:
            dump_fansly_calls(route.calls, "all_cached")

        # No new posts — creator absent from new_ids
        assert new_ids == set()
        # But posts_by_creator still has the creator's post for the runner
        assert creator_id in posts_by_creator
        assert len(posts_by_creator[creator_id]) == 1

    async def test_mixed_posts_returns_only_new_creators_in_new_ids(
        self, respx_fansly_api, mock_config, reset_class_store
    ):
        """Mixed known + unknown posts — only new creator in new_ids, both in posts_by_creator."""
        creator_id = await _make_saved_account(reset_class_store)

        # A second creator
        creator_id2 = snowflake_id()
        acc2 = Account(
            id=creator_id2,
            username=f"creator2_{creator_id2}",
            displayName="Creator 2",
            createdAt=datetime.now(UTC),
        )
        await reset_class_store.save(acc2)

        known_post_id = snowflake_id()
        new_post_id = snowflake_id()

        # Seed one post as known (from creator_id)
        known_post = Post(
            id=known_post_id,
            accountId=creator_id,
            content="already known",
            fypFlags=0,
            createdAt=datetime.now(UTC),
        )
        await reset_class_store.save(known_post)

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
            new_ids, posts_by_creator = await poll_home_timeline(mock_config)
        finally:
            dump_fansly_calls(route.calls, "mixed_posts")

        # Only creator_id2 has a new post
        assert creator_id not in new_ids
        assert creator_id2 in new_ids
        assert len(new_ids) == 1
        # Both creators appear in posts_by_creator
        assert creator_id in posts_by_creator
        assert creator_id2 in posts_by_creator

    async def test_multiple_new_posts_same_creator_deduplicates(
        self, respx_fansly_api, mock_config, reset_class_store
    ):
        """Multiple new posts from same creator — ID appears once in new_ids,
        both posts appear in posts_by_creator."""
        creator_id = await _make_saved_account(reset_class_store)
        post_id1 = snowflake_id()
        post_id2 = snowflake_id()

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
            new_ids, posts_by_creator = await poll_home_timeline(mock_config)
        finally:
            dump_fansly_calls(route.calls, "same_creator_dedup")

        # Set semantics: creator only appears once in new_ids
        assert new_ids == {creator_id}
        # posts_by_creator accumulates both posts for this creator
        assert creator_id in posts_by_creator
        assert len(posts_by_creator[creator_id]) == 2
        returned_ids = {p["id"] for p in posts_by_creator[creator_id]}
        assert returned_ids == {post_id1, post_id2}

    async def test_empty_posts_array_returns_empty_tuple(
        self, respx_fansly_api, mock_config, reset_class_store
    ):
        """API returns empty posts array — (set(), {}) returned."""
        route = respx.get(url__startswith=HOME_TIMELINE_URL).mock(
            side_effect=[
                httpx.Response(
                    200,
                    json={"success": True, "response": {"posts": []}},
                )
            ]
        )

        try:
            new_ids, posts_by_creator = await poll_home_timeline(mock_config)
        finally:
            dump_fansly_calls(route.calls, "empty_posts")

        assert new_ids == set()
        assert posts_by_creator == {}

    async def test_api_http_error_returns_empty_tuple(
        self, respx_fansly_api, mock_config, reset_class_store
    ):
        """API raises ConnectError (HTTPError subclass) — returns (set(), {}).

        RetryTransport retries network errors (total=3), so we provide 4
        ConnectError responses (1 initial + 3 retries) to ensure the final
        exception propagates as httpx.HTTPError rather than StopIteration.
        """
        route = respx.get(url__startswith=HOME_TIMELINE_URL).mock(
            side_effect=[
                httpx.ConnectError("Connection refused"),
                httpx.ConnectError("Connection refused"),
                httpx.ConnectError("Connection refused"),
                httpx.ConnectError("Connection refused"),
            ]
        )

        try:
            new_ids, posts_by_creator = await poll_home_timeline(mock_config)
        finally:
            dump_fansly_calls(route.calls, "api_http_error_tuple")
        # All 4 calls raised exceptions (1 initial + 3 retries).
        assert len(route.calls) == 4

        assert new_ids == set()
        assert posts_by_creator == {}

    async def test_generic_exception_returns_empty_tuple(
        self, respx_fansly_api, mock_config, reset_class_store, monkeypatch, caplog
    ):
        """Lines 64-68: non-HTTPError exception → log warning + return empty."""
        caplog.set_level(logging.WARNING)

        api = mock_config.get_api()

        def _raises():
            raise RuntimeError("simulated non-http failure")

        monkeypatch.setattr(api, "get_home_timeline", _raises)

        new_ids, posts_by_creator = await poll_home_timeline(mock_config)

        assert new_ids == set()
        assert posts_by_creator == {}

        warnings = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
        assert any(
            "unexpected error fetching home timeline" in m
            and "simulated non-http failure" in m
            for m in warnings
        )


# ---------------------------------------------------------------------------
# poll_story_states
# ---------------------------------------------------------------------------


@pytest.mark.asyncio(loop_scope="class")
@pytest.mark.xdist_group("daemon_polling_stories")
class TestPollStoryStates:
    """Tests for daemon.polling.poll_story_states over ONE shared class DB."""

    @pytest.mark.parametrize(
        (
            "prior_state",
            "has_active",
            "story_count",
            "expect_returned",
            "expect_row_state",
        ),
        [
            pytest.param(
                None, True, 0, True, True, id="first-run-active-creates-row-true"
            ),
            pytest.param(
                False, True, 0, True, True, id="prior-false-now-true-updates-row"
            ),
            pytest.param(
                True, True, 0, False, True, id="prior-true-still-true-no-flip"
            ),
            pytest.param(
                True, False, 0, False, False, id="prior-true-now-false-updates-row"
            ),
            pytest.param(
                None, False, 0, False, False, id="first-run-inactive-creates-row-false"
            ),
            pytest.param(
                False,
                False,
                2,
                True,
                True,
                id="story-count-nonzero-treated-active-flip",
            ),
            pytest.param(
                False,
                True,
                0,
                True,
                True,
                id="has-active-true-count-zero-still-active",
            ),
            pytest.param(
                False,
                False,
                0,
                False,
                False,
                id="both-signals-inactive-not-returned",
            ),
        ],
    )
    async def test_story_state_truth_table(
        self,
        respx_fansly_api: FanslyApi,
        mock_config: FanslyConfig,
        reset_class_store: PostgresEntityStore,
        request: pytest.FixtureRequest,
        prior_state: bool | None,
        has_active: bool,
        story_count: int,
        expect_returned: bool,
        expect_row_state: bool,
    ) -> None:
        """Truth table over (prior MonitorState, hasActiveStories, storyCount).

        A creator is returned only on an inactive→active flip (no prior row
        counts as inactive). ``storyCount > 0`` is treated as active even when
        ``hasActiveStories`` is False; ``hasActiveStories=True`` alone is
        sufficient when ``storyCount == 0``. The MonitorState row is
        created/updated to the observed active state either way.
        """
        creator_id = await _make_saved_account(reset_class_store)

        if prior_state is not None:
            await reset_class_store.save(
                MonitorState(
                    creatorId=creator_id,
                    lastHasActiveStories=prior_state,
                )
            )

        route = respx.get(url__startswith=STORY_STATES_URL).mock(
            side_effect=[
                httpx.Response(
                    200,
                    json={
                        "success": True,
                        "response": [
                            _make_story_state_dict(
                                creator_id,
                                has_active=has_active,
                                story_count=story_count,
                            )
                        ],
                    },
                )
            ]
        )

        try:
            result = await poll_story_states(mock_config)
        finally:
            dump_fansly_calls(route.calls, request.node.name)

        if expect_returned:
            assert creator_id in result
        else:
            assert creator_id not in result
            assert result == []

        row = await reset_class_store.get(MonitorState, creator_id)
        assert row is not None
        assert row.lastHasActiveStories is expect_row_state

    async def test_api_http_error_returns_empty_list(
        self, respx_fansly_api, mock_config, reset_class_store
    ):
        """API raises ConnectError (HTTPError subclass) — returns empty list.

        RetryTransport retries network errors (total=3), so we provide 4
        ConnectError responses (1 initial + 3 retries) to ensure the final
        exception propagates as httpx.HTTPError rather than StopIteration.
        """
        route = respx.get(url__startswith=STORY_STATES_URL).mock(
            side_effect=[
                httpx.ConnectError("Connection refused"),
                httpx.ConnectError("Connection refused"),
                httpx.ConnectError("Connection refused"),
                httpx.ConnectError("Connection refused"),
            ]
        )

        try:
            result = await poll_story_states(mock_config)
        finally:
            dump_fansly_calls(route.calls, "api_http_error_list")
        # All 4 calls raised exceptions (1 initial + 3 retries).
        assert len(route.calls) == 4

        assert result == []

    # -----------------------------------------------------------------------
    # Edge coverage — generic exception, malformed response shape, save errors
    # -----------------------------------------------------------------------

    async def test_generic_exception_returns_empty_list(
        self, respx_fansly_api, mock_config, reset_class_store, monkeypatch, caplog
    ):
        """Lines 111-115: non-HTTPError exception → log + return []."""
        caplog.set_level(logging.WARNING)

        api = mock_config.get_api()

        def _raises():
            raise RuntimeError("simulated story API failure")

        monkeypatch.setattr(api, "get_story_states_following", _raises)

        result = await poll_story_states(mock_config)

        assert result == []
        warnings = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
        assert any(
            "unexpected error fetching story states" in m
            and "simulated story API failure" in m
            for m in warnings
        )

    async def test_non_list_response_returns_empty_with_warning(
        self, respx_fansly_api, mock_config, reset_class_store, caplog
    ):
        """Lines 117-122: response is not a list → log warning + return []."""
        caplog.set_level(logging.WARNING)

        route = respx.get(url__startswith=STORY_STATES_URL).mock(
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
        try:
            result = await poll_story_states(mock_config)
        finally:
            dump_fansly_calls(route.calls, "non_list_response")

        assert result == []
        warnings = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
        assert any("unexpected story states response shape" in m for m in warnings)

    async def test_save_exception_inside_loop_logged_and_skipped(
        self, respx_fansly_api, mock_config, reset_class_store, monkeypatch, caplog
    ):
        """Lines 156-162: store.save raises mid-loop → log warning + creator NOT returned."""
        caplog.set_level(logging.WARNING)
        creator_id = snowflake_id()

        # Patch get_store to return a wrapper whose save raises for MonitorState.
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
                                "storyCount": 1,
                            }
                        ],
                    },
                )
            ]
        )
        try:
            result = await poll_story_states(mock_config)
        finally:
            dump_fansly_calls(route.calls, "save_exception")

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
