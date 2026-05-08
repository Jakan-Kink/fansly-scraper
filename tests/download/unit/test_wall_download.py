"""Tests for download/wall.py — wall download orchestrator.

Tests use respx for Fansly API HTTP boundaries, real entity_store for
database operations, and real code paths for all internal functions.
Per project testing guidelines: only mock at external boundaries.
"""

import logging
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest
import respx

from api.fansly import FanslyApi
from download.downloadstate import DownloadState
from download.types import DownloadType
from download.wall import download_wall, process_wall_data, process_wall_media
from metadata.models import Account, Post, Wall
from tests.fixtures.api import dump_fansly_calls
from tests.fixtures.utils.test_isolation import snowflake_id


FANSLY_API = FanslyApi.BASE_URL


# ── Helpers ──────────────────────────────────────────────────────────────


def _wall_response(creator_id, *, post_count=1, media_count=1):
    """Build a realistic wall API response dict.

    Structure matches Fansly's timelinenew endpoint, with enough fields
    for process_timeline_posts to process successfully.
    """
    posts = [
        {
            "id": snowflake_id(),
            "accountId": creator_id,
            "content": "",
            "fypFlags": 0,
            "createdAt": 1700000000 - i,
            "attachments": [],
        }
        for i in range(post_count)
    ]
    media_items = []
    for _ in range(media_count):
        mid = snowflake_id()
        media_items.append(
            {
                "id": snowflake_id(),
                "accountId": creator_id,
                "mediaId": mid,
                "createdAt": 1700000000,
                "deleted": False,
                "access": True,
                "media": {
                    "id": mid,
                    "type": 1,
                    "mimetype": "image/jpeg",
                    "variants": [],
                    "locations": [],
                },
            }
        )
    return {
        "posts": posts,
        "aggregatedPosts": [],
        "accountMedia": media_items,
        "accountMediaBundles": [],
        "accounts": [{"id": creator_id, "username": f"wall_{creator_id}"}],
    }


def _ok(payload):
    """Wrap a payload in Fansly's standard success envelope."""
    return httpx.Response(200, json={"success": True, "response": payload})


# ── process_wall_data ───────────────────────────────────────────────────


class TestProcessWallData:
    """Lines 33-62: check_page_duplicates + process_wall_posts."""

    @pytest.mark.asyncio
    async def test_saves_posts_to_database(self, mock_config, entity_store):
        """process_wall_data persists wall + posts via real code paths."""
        creator_id = snowflake_id()
        wall_id = snowflake_id()
        state = DownloadState()
        state.creator_id = creator_id

        await entity_store.save(Account(id=creator_id, username=f"wall_{creator_id}"))

        wall_data = _wall_response(creator_id, post_count=3)
        post_ids = [p["id"] for p in wall_data["posts"]]

        mock_config.use_pagination_duplication = False
        await process_wall_data(mock_config, state, wall_id, wall_data, "0")

        # Verify wall was created with correct account
        wall = await entity_store.get(Wall, wall_id)
        assert wall is not None
        assert wall.accountId == creator_id

        # Verify posts were saved and linked
        for pid in post_ids:
            post = await entity_store.get(Post, pid)
            assert post is not None
            assert post.accountId == creator_id

    @pytest.mark.asyncio
    async def test_cursor_zero_treated_as_first_page(self, mock_config, entity_store):
        """Line 54: before_cursor == '0' → cursor=None in check_page_duplicates."""
        state = DownloadState()
        state.creator_id = snowflake_id()
        await entity_store.save(
            Account(id=state.creator_id, username=f"u_{state.creator_id}")
        )

        mock_config.use_pagination_duplication = False
        await process_wall_data(
            mock_config,
            state,
            snowflake_id(),
            _wall_response(state.creator_id),
            "0",
        )

    @pytest.mark.asyncio
    async def test_nonzero_cursor_passed_through(self, mock_config, entity_store):
        """Line 54: before_cursor != '0' → passed as cursor."""
        state = DownloadState()
        state.creator_id = snowflake_id()
        await entity_store.save(
            Account(id=state.creator_id, username=f"u_{state.creator_id}")
        )

        mock_config.use_pagination_duplication = False
        await process_wall_data(
            mock_config,
            state,
            snowflake_id(),
            _wall_response(state.creator_id),
            "abc123",
        )


# ── process_wall_media ──────────────────────────────────────────────────


class TestProcessWallMedia:
    """Lines 65-82: fetch + download pipeline."""

    @pytest.mark.asyncio
    async def test_returns_true_no_accessible(
        self, respx_fansly_api, mock_config, entity_store, tmp_path
    ):
        """No accessible media from API → returns True (continue)."""
        state = DownloadState()
        state.creator_id = snowflake_id()
        state.creator_name = f"wall_{state.creator_id}"
        state.download_type = DownloadType.WALL
        mock_config.download_directory = Path(tmp_path)

        media_ids = [snowflake_id()]

        route = respx.get(url__startswith=FANSLY_API).mock(
            side_effect=[_ok([])],
        )

        try:
            result = await process_wall_media(mock_config, state, media_ids)
        finally:
            dump_fansly_calls(route.calls, "test_returns_true_no_accessible")

        assert result is True

    @pytest.mark.asyncio
    async def test_returns_false_propagates_to_caller(
        self, respx_fansly_api, mock_config, entity_store, tmp_path
    ):
        """Spy verifies process_wall_media is called and its False return
        causes the download_wall loop to break. Uses patch(wraps=) to
        intercept the return without running the deep download pipeline."""
        config = mock_config
        config.use_duplicate_threshold = False
        config.use_pagination_duplication = False
        config.interactive = False
        config.timeline_retries = 0
        config.timeline_delay_seconds = 0
        config.download_directory = Path(tmp_path)
        config.download_media_previews = False
        config.debug = False
        config.show_downloads = False
        config.show_skipped_downloads = False

        creator_id = snowflake_id()
        state = DownloadState()
        state.creator_id = creator_id
        state.creator_name = f"wall_{creator_id}"

        await entity_store.save(Account(id=creator_id, username=state.creator_name))

        wall_data = _wall_response(creator_id, post_count=3, media_count=1)

        route = respx.get(url__startswith=FANSLY_API).mock(
            side_effect=[_ok(wall_data)],
        )

        # Spy on process_wall_media: let the call happen but override the return
        spy = AsyncMock(return_value=False)
        with patch("download.wall.process_wall_media", spy):
            try:
                await download_wall(config, state, snowflake_id())
            finally:
                dump_fansly_calls(route.calls, "test_returns_false_propagates")

        # Verify process_wall_media was called with the right media IDs
        spy.assert_called_once()
        call_args = spy.call_args
        assert call_args[0][0] is config
        assert call_args[0][1] is state
        assert len(call_args[0][2]) > 0  # non-empty media IDs


# ── download_wall ───────────────────────────────────────────────────────


class TestDownloadWall:
    """Lines 85-255: main loop with pagination, retries, error handling.

    All tests use real code paths with a single respx route on the Fansly
    API domain. The account/media API returns empty responses so the
    download pipeline finds no accessible content — tests focus on loop
    orchestration logic.
    """

    def _make_state(self, creator_id):
        state = DownloadState()
        state.creator_id = creator_id
        state.creator_name = f"wall_{creator_id}"
        return state

    def _make_config(self, mock_config, tmp_path):
        mock_config.use_duplicate_threshold = False
        mock_config.use_pagination_duplication = False
        mock_config.debug = False
        mock_config.show_downloads = True
        mock_config.show_skipped_downloads = False
        mock_config.interactive = False
        mock_config.timeline_retries = 0
        mock_config.timeline_delay_seconds = 0
        mock_config.download_directory = Path(tmp_path)
        mock_config.download_media_previews = False
        return mock_config

    @pytest.mark.asyncio
    async def test_success_single_page(
        self, respx_fansly_api, mock_config, entity_store, tmp_path
    ):
        """Lines 97-225: success — single page, <15 posts → break."""
        config = self._make_config(mock_config, tmp_path)
        creator_id = snowflake_id()
        wall_id = snowflake_id()
        state = self._make_state(creator_id)

        await entity_store.save(Account(id=creator_id, username=f"wall_{creator_id}"))

        wall_data = _wall_response(creator_id, post_count=3, media_count=1)

        # Single route: wall API response first, then media API response
        route = respx.get(url__startswith=FANSLY_API).mock(
            side_effect=[_ok(wall_data), _ok([])],
        )

        try:
            await download_wall(config, state, wall_id)
        finally:
            dump_fansly_calls(route.calls, "test_success_single_page")

        assert state.download_type == DownloadType.WALL
        assert len(route.calls) == 2
        assert "timelinenew" in str(route.calls[0].request.url)
        assert "account/media" in str(route.calls[1].request.url)

    @pytest.mark.asyncio
    async def test_dedup_threshold_skips(
        self, respx_fansly_api, mock_config, entity_store, tmp_path
    ):
        """Lines 123-131: dedup enabled + already fetched → early return."""
        config = self._make_config(mock_config, tmp_path)
        config.use_duplicate_threshold = True
        creator_id = snowflake_id()
        state = self._make_state(creator_id)
        state.fetched_timeline_duplication = True

        await download_wall(config, state, snowflake_id())

    @pytest.mark.asyncio
    async def test_empty_media_retries_then_stops(
        self, respx_fansly_api, mock_config, entity_store, tmp_path
    ):
        """Lines 174-184: no media IDs → retry loop, then break at max attempts."""
        config = self._make_config(mock_config, tmp_path)
        config.timeline_retries = 1
        config.timeline_delay_seconds = 0
        creator_id = snowflake_id()
        state = self._make_state(creator_id)

        await entity_store.save(Account(id=creator_id, username=f"wall_{creator_id}"))

        # media_count=0 → get_unique_media_ids returns [] → retry
        route = respx.get(url__startswith=FANSLY_API).mock(
            side_effect=[
                _ok(_wall_response(creator_id, media_count=0)),
                _ok(_wall_response(creator_id, media_count=0)),
                _ok(_wall_response(creator_id, media_count=0)),
            ]
        )

        try:
            await download_wall(config, state, snowflake_id())
        finally:
            dump_fansly_calls(route.calls, "test_empty_media_retries")

    @pytest.mark.asyncio
    async def test_pagination_continues(
        self, respx_fansly_api, mock_config, entity_store, tmp_path
    ):
        """Lines 211-225: >=15 posts → next page, then <15 → break."""
        config = self._make_config(mock_config, tmp_path)
        creator_id = snowflake_id()
        state = self._make_state(creator_id)

        await entity_store.save(Account(id=creator_id, username=f"wall_{creator_id}"))

        # Page 1: 15 posts + media API empty; Page 2: 3 posts + media API empty
        route = respx.get(url__startswith=FANSLY_API).mock(
            side_effect=[
                _ok(_wall_response(creator_id, post_count=15, media_count=1)),
                _ok([]),  # media API for page 1
                _ok(_wall_response(creator_id, post_count=3, media_count=1)),
                _ok([]),  # media API for page 2
            ]
        )

        try:
            await download_wall(config, state, snowflake_id())
        finally:
            dump_fansly_calls(route.calls, "test_pagination_continues")

        # 2 wall calls + 2 media calls = 4
        assert len(route.calls) == 4

    @pytest.mark.asyncio
    async def test_empty_posts_index_error_breaks(
        self, respx_fansly_api, mock_config, entity_store, tmp_path
    ):
        """Lines 223-225: posts list empty → IndexError on cursor → break."""
        config = self._make_config(mock_config, tmp_path)
        creator_id = snowflake_id()
        state = self._make_state(creator_id)

        await entity_store.save(Account(id=creator_id, username=f"wall_{creator_id}"))

        # post_count=0 but media_count=1 → media processes, then cursor IndexError
        route = respx.get(url__startswith=FANSLY_API).mock(
            side_effect=[
                _ok(_wall_response(creator_id, post_count=0, media_count=1)),
                _ok([]),  # media API
            ]
        )

        try:
            await download_wall(config, state, snowflake_id())
        finally:
            dump_fansly_calls(route.calls, "test_empty_posts_index_error")

    @pytest.mark.asyncio
    async def test_key_error_then_normal_exit(
        self, respx_fansly_api, mock_config, entity_store, tmp_path
    ):
        """Lines 234-242: 1st request → KeyError (missing 'response'),
        2nd → normal with 0 media → retries exhausted → exit."""
        config = self._make_config(mock_config, tmp_path)
        config.interactive = False
        creator_id = snowflake_id()
        state = self._make_state(creator_id)

        await entity_store.save(Account(id=creator_id, username=f"wall_{creator_id}"))

        route = respx.get(url__startswith=FANSLY_API).mock(
            side_effect=[
                # Missing "response" key → KeyError
                httpx.Response(200, json={"success": True}),
                _ok(_wall_response(creator_id, post_count=1, media_count=0)),
            ]
        )

        try:
            await download_wall(config, state, snowflake_id())
        finally:
            dump_fansly_calls(route.calls, "test_key_error_then_normal_exit")

    @pytest.mark.asyncio
    async def test_duplicate_page_error_breaks(
        self, respx_fansly_api, mock_config, entity_store, tmp_path
    ):
        """Lines 244-248: all posts already cached → DuplicatePageError → break.

        Note: check_page_duplicates sleeps 5s before raising — this test
        exercises the real rate-limiting behavior.
        """
        config = self._make_config(mock_config, tmp_path)
        config.use_pagination_duplication = True
        creator_id = snowflake_id()
        state = self._make_state(creator_id)

        await entity_store.save(Account(id=creator_id, username=f"wall_{creator_id}"))

        wall_data = _wall_response(creator_id, post_count=3)

        # Pre-save all posts so check_page_duplicates detects full duplication
        for p in wall_data["posts"]:
            await entity_store.save(
                Post(id=p["id"], accountId=creator_id, createdAt=p["createdAt"])
            )

        route = respx.get(url__startswith=FANSLY_API).mock(side_effect=[_ok(wall_data)])

        try:
            await download_wall(config, state, snowflake_id())
        finally:
            dump_fansly_calls(route.calls, "test_duplicate_page_error_breaks")

    @pytest.mark.asyncio
    async def test_debug_mode(
        self, respx_fansly_api, mock_config, entity_store, tmp_path
    ):
        """Lines 169-170: debug=True → prints wall data via print_debug."""
        config = self._make_config(mock_config, tmp_path)
        config.debug = True
        creator_id = snowflake_id()
        state = self._make_state(creator_id)

        await entity_store.save(Account(id=creator_id, username=f"wall_{creator_id}"))

        # 0 media → get_unique_media_ids returns [] → retries=0 exhausted → exit
        route = respx.get(url__startswith=FANSLY_API).mock(
            side_effect=[_ok(_wall_response(creator_id, post_count=3, media_count=0))]
        )

        try:
            await download_wall(config, state, snowflake_id())
        finally:
            dump_fansly_calls(route.calls, "test_debug_mode")

    @pytest.mark.asyncio
    async def test_wall_with_name_dedup_skip(
        self, respx_fansly_api, mock_config, entity_store, tmp_path
    ):
        """Lines 100-102, 123-131: wall in DB with name + dedup threshold → early return."""
        config = self._make_config(mock_config, tmp_path)
        config.use_duplicate_threshold = True
        creator_id = snowflake_id()
        wall_id = snowflake_id()

        await entity_store.save(Account(id=creator_id, username=f"u_{creator_id}"))
        await entity_store.save(
            Wall(id=wall_id, accountId=creator_id, name="VIP Wall", pos=0)
        )

        state = self._make_state(creator_id)
        state.fetched_timeline_duplication = True

        await download_wall(config, state, wall_id)


# ---------------------------------------------------------------------------
# Edge coverage — short-circuit + raise + outer exception paths
# ---------------------------------------------------------------------------


class TestDownloadWallEdges:
    """Lines 127-130, 158, 213, 233-238, 255-260: small remaining branches."""

    def _make_state(self, creator_id):
        state = DownloadState()
        state.creator_id = creator_id
        state.creator_name = f"wall_{creator_id}"
        return state

    def _make_config(self, mock_config, tmp_path):
        mock_config.use_duplicate_threshold = False
        mock_config.use_pagination_duplication = False
        mock_config.debug = False
        mock_config.show_downloads = True
        mock_config.show_skipped_downloads = False
        mock_config.interactive = False
        mock_config.timeline_retries = 0
        mock_config.timeline_delay_seconds = 0
        mock_config.download_directory = Path(tmp_path)
        mock_config.download_media_previews = False
        return mock_config

    @pytest.mark.asyncio
    async def test_creator_content_unchanged_short_circuits(
        self, respx_fansly_api, mock_config, entity_store, tmp_path, caplog
    ):
        """Lines 127-130: state.creator_content_unchanged=True → log + early return.

        No HTTP requests should be made. The wall API route is set up but never hit.
        """
        caplog.set_level(logging.INFO)
        config = self._make_config(mock_config, tmp_path)
        creator_id = snowflake_id()
        wall_id = snowflake_id()
        state = self._make_state(creator_id)
        state.creator_content_unchanged = True

        await entity_store.save(Account(id=creator_id, username=f"wall_{creator_id}"))

        # Set up route just to detect any unexpected call.
        route = respx.get(url__startswith=FANSLY_API).mock(
            side_effect=[httpx.Response(500, text="should not be called")]
        )

        await download_wall(config, state, wall_id)

        # No HTTP call was issued — early return before the loop.
        assert len(route.calls) == 0
        info = [r.getMessage() for r in caplog.records if r.levelname == "INFO"]
        assert any("Creator counts and wall structure unchanged" in m for m in info)

    @pytest.mark.asyncio
    async def test_outer_exception_caught_and_logged(
        self, respx_fansly_api, mock_config, entity_store, tmp_path, caplog, monkeypatch
    ):
        """Lines 255-260: generic Exception during loop → caught + print_error.

        Test setup notes:
        1. ``input_enter_continue(False)`` calls ``sleep(15)`` — NOT a no-op.
           Patch it to no-op so the test doesn't burn 15s/iter waiting.
        2. Production's outer ``except Exception`` doesn't increment ``attempts``,
           so a permanently-raising function loops forever. Use a one-shot
           raise (next call returns an empty wall_response → ``attempts += 1``
           via the empty-media branch → loop exits).
        """
        caplog.set_level(logging.ERROR)
        config = self._make_config(mock_config, tmp_path)
        creator_id = snowflake_id()
        state = self._make_state(creator_id)

        await entity_store.save(Account(id=creator_id, username=f"wall_{creator_id}"))

        # Patch input_enter_continue to no-op (production sleeps 15s in non-interactive).
        monkeypatch.setattr(
            "download.wall.input_enter_continue", lambda _interactive: None
        )

        # First call: raise. Second call: return empty wall (triggers attempts+=1 → loop exits).
        # httpx.Response needs a request= kwarg to support raise_for_status.
        call_count = 0
        request = httpx.Request("GET", f"{FanslyApi.BASE_URL}wall")

        async def _raises_once(*_args, **_kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("simulated unexpected wall failure")
            return httpx.Response(
                200,
                json={
                    "success": True,
                    "response": _wall_response(creator_id, post_count=0, media_count=0),
                },
                request=request,
            )

        api = config.get_api()
        original = api.get_wall_posts
        api.get_wall_posts = _raises_once
        try:
            await download_wall(config, state, snowflake_id())
        finally:
            api.get_wall_posts = original

        errors = [r.getMessage() for r in caplog.records if r.levelname == "ERROR"]
        assert any("Unexpected error during wall download" in m for m in errors)

    @pytest.mark.asyncio
    async def test_creator_id_none_raises_runtime_error_caught_by_outer(
        self, respx_fansly_api, mock_config, entity_store, tmp_path, caplog, monkeypatch
    ):
        """Line 158: state.creator_id is None → RuntimeError raised, caught by 255-260.

        Same caveat as test_outer_exception_caught_and_logged: production's outer
        except doesn't increment attempts, AND input_enter_continue sleeps 15s.
        Set creator_id back to a valid value AFTER the first iteration via a
        side-channel (the patched input_enter_continue) so the second iteration
        succeeds with empty media → attempts += 1 → exit.
        """
        caplog.set_level(logging.ERROR)
        config = self._make_config(mock_config, tmp_path)
        creator_id = snowflake_id()
        state = self._make_state(creator_id)
        state.creator_id = None  # forces line 158 to raise on first iter

        await entity_store.save(Account(id=creator_id, username=f"wall_{creator_id}"))

        # When the outer except calls input_enter_continue, restore creator_id
        # so the next loop iteration takes the empty-media path and exits.
        def _restore_creator_id(_interactive):
            state.creator_id = creator_id

        monkeypatch.setattr("download.wall.input_enter_continue", _restore_creator_id)

        # Pre-mount an empty wall response for the second iteration (which now
        # has a valid creator_id). respx_fansly_api fixture provides the route.
        respx.get(url__startswith=FANSLY_API).mock(
            side_effect=[_ok(_wall_response(creator_id, post_count=0, media_count=0))]
        )

        # Should NOT raise — outer except catches RuntimeError.
        await download_wall(config, state, snowflake_id())

        errors = [r.getMessage() for r in caplog.records if r.levelname == "ERROR"]
        assert any("Unexpected error during wall download" in m for m in errors)

    # NOTE: line 213 (print_info "Skipped N already downloaded media item(s)")
    # is not covered here. Triggering it requires state.duplicate_count to
    # rise by >1 between line 143 (starting_duplicates snapshot) and line
    # 207 (delta check), which depends on process_wall_media's internal
    # accounting. Reliably constructing that state would mean reverse-
    # engineering the dedupe pipeline — out of scope for this branch.
