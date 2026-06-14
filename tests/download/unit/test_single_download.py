"""Tests for download/single.py — download_single_post orchestrator.

Real-pipeline rewrite (Wave 2 Category D #9): the previous version
patched ``process_timeline_posts``, ``fetch_and_process_media``,
``process_download_accessible_media``, and ``dedupe_init`` — all
internal-function mocks that hid the real persistence + media-resolution
behavior. This file now drives the orchestrator end-to-end through real
respx HTTP boundaries, real EntityStore persistence, and only patches at
true edges (CDN ``download_media`` leaf, ``input_enter_continue`` for
stdin avoidance).

Edges patched:
- Fansly HTTP via ``respx_fansly_api``
- ``download_media`` (CDN leaf — patched at both binding sites because
  ``download.common`` imports it at module scope)
- ``input_enter_continue`` (avoids blocking on stdin in test environment)

Real code throughout: ``process_timeline_posts`` persists Post + Media
via ``entity_store``; ``fetch_and_process_media`` issues a real
``/account/media`` HTTP call; ``process_download_accessible_media`` runs
full real orchestration; ``dedupe_init`` reads the real ``tmp_path``
download directory.
"""

import logging
from unittest.mock import AsyncMock

import httpx
import pytest
import respx

from api.fansly import FanslyApi
from download.downloadstate import DownloadState
from download.single import download_single_post
from download.types import DownloadType
from metadata.models import Post, get_store
from tests.fixtures.api import dump_fansly_calls
from tests.fixtures.metadata import AccountFactory, AccountMediaFactory, MediaFactory
from tests.fixtures.utils.test_isolation import snowflake_id


def _account_media_payload(media_id: int, am_id: int, account_id: int) -> dict:
    """AccountMedia entry with nested-media + previewId + signed-CDN URL.

    See ``project_fansly_payload_shape_requirements.md`` for why each
    field is required by the real pipeline.
    """
    return {
        "id": am_id,
        "accountId": account_id,
        "mediaId": media_id,
        "previewId": None,
        "createdAt": 1700000000,
        "deleted": False,
        "access": True,
        "mimetype": "image/jpeg",
        "media": {
            "id": media_id,
            "accountId": account_id,
            "mimetype": "image/jpeg",
            "type": 1,
            "status": 1,
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


def _post_payload(post_id: int, creator_id: int) -> dict:
    """Standard Post payload used as the ``posts[0]`` element."""
    return {
        "id": post_id,
        "accountId": creator_id,
        "fypFlags": 0,
        "createdAt": 1700000000,
    }


class TestDownloadSinglePost:
    """Real-pipeline tests for download_single_post."""

    @pytest.mark.asyncio
    async def test_success_with_bundles_persists_post_and_invokes_cdn(
        self, respx_fansly_api, mock_config, entity_store, tmp_path, monkeypatch
    ):
        """Bundle path: real Post persisted, real /account/media hit, CDN leaf invoked.

        Replaces 4 internal-function patches with real-pipeline assertions:
          - Post is persisted in the real EntityStore
          - state.creator_name is set from the accounts response
          - /api/v1/account/media respx route fired (fetch_and_process_media)
          - download_media leaf was invoked at least once with non-empty list
        """
        # Real Pydantic Post model enforces Snowflake range (≥10^15).
        post_id = snowflake_id()
        mock_config.post_id = str(post_id)
        mock_config.show_downloads = True
        mock_config.show_skipped_downloads = False
        mock_config.download_directory = tmp_path
        mock_config.interactive = False

        creator_id = snowflake_id()
        media_id = snowflake_id()
        am_id = snowflake_id()
        bundle_id = snowflake_id()
        am_entry = _account_media_payload(media_id, am_id, creator_id)

        post_route = respx.get(url__startswith=f"{FanslyApi.BASE_URL}post").mock(
            side_effect=[
                httpx.Response(
                    200,
                    json={
                        "success": True,
                        "response": {
                            "posts": [_post_payload(post_id, creator_id)],
                            "aggregatedPosts": [],
                            "accountMediaBundles": [
                                {
                                    "id": bundle_id,
                                    "accountId": creator_id,
                                    "createdAt": 1700000000,
                                    "deleted": False,
                                    "accountMediaIds": [am_id],
                                }
                            ],
                            "accountMedia": [am_entry],
                            "accounts": [
                                {
                                    "id": creator_id,
                                    "username": "single_creator",
                                    "displayName": "Single Creator",
                                    "createdAt": 1700000000,
                                }
                            ],
                        },
                    },
                )
            ],
        )
        # fetch_and_process_media → /api/v1/account/media?ids=<am_id>
        media_route = respx.get(
            url__startswith=f"{FanslyApi.BASE_URL}account/media"
        ).mock(
            side_effect=[
                httpx.Response(200, json={"success": True, "response": [am_entry]})
            ]
        )

        cdn_mock = AsyncMock(return_value=None)
        monkeypatch.setattr("download.common.download_media", cdn_mock)
        monkeypatch.setattr("download.media.download_media", cdn_mock)

        async def _noop(_):
            return None

        monkeypatch.setattr("download.common.input_enter_continue", _noop)
        monkeypatch.setattr("download.media.input_enter_continue", _noop)
        monkeypatch.setattr("download.single.input_enter_continue", _noop)

        state = DownloadState()
        state.duplicate_count = 2

        try:
            await download_single_post(mock_config, state)
        finally:
            dump_fansly_calls(respx.calls, label="single_bundles_success")

        assert state.download_type == DownloadType.SINGLE
        assert state.creator_name == "single_creator"
        assert state.creator_id == creator_id
        assert post_route.call_count == 1
        assert media_route.call_count >= 1, (
            "fetch_and_process_media should have hit /api/v1/account/media"
        )
        # Real persistence: Post landed in the store.
        store = get_store()
        persisted_post = await store.get(Post, post_id)
        assert persisted_post is not None, (
            "Real process_timeline_posts should have persisted the Post"
        )
        assert persisted_post.accountId == creator_id
        # CDN leaf invoked with non-empty accessible-media list.
        assert cdn_mock.call_count >= 1
        accessible_arg = cdn_mock.call_args.args[2]
        assert len(accessible_arg) >= 1, (
            "download_media should receive at least one accessible-media entry "
            "when the post has accountMedia + bundles"
        )

    @pytest.mark.asyncio
    async def test_success_with_account_media_only_capitalizes_username(
        self,
        respx_fansly_api,
        mock_config,
        entity_store,
        tmp_path,
        monkeypatch,
        caplog,
    ):
        """No bundles + no displayName → creator_id from accountMedia[0], capitalize username.

        Covers download/single.py:75-77 (creator_id from accountMedia
        when bundles is empty) and 94-97 (capitalize username when
        displayName is missing).
        """
        caplog.set_level(logging.INFO)
        post_id = snowflake_id()
        mock_config.post_id = str(post_id)
        mock_config.download_directory = tmp_path
        mock_config.interactive = False

        creator_id = snowflake_id()
        media_id = snowflake_id()
        am_id = snowflake_id()
        am_entry = _account_media_payload(media_id, am_id, creator_id)

        respx.get(url__startswith=f"{FanslyApi.BASE_URL}post").mock(
            side_effect=[
                httpx.Response(
                    200,
                    json={
                        "success": True,
                        "response": {
                            "posts": [_post_payload(post_id, creator_id)],
                            "aggregatedPosts": [],
                            "accountMediaBundles": [],
                            "accountMedia": [am_entry],
                            "accounts": [
                                {
                                    "id": creator_id,
                                    "username": "nocaps",
                                    "displayName": None,
                                    "createdAt": 1700000000,
                                }
                            ],
                        },
                    },
                )
            ],
        )
        respx.get(url__startswith=f"{FanslyApi.BASE_URL}account/media").mock(
            side_effect=[
                httpx.Response(200, json={"success": True, "response": [am_entry]})
            ]
        )

        cdn_mock = AsyncMock(return_value=None)
        monkeypatch.setattr("download.common.download_media", cdn_mock)
        monkeypatch.setattr("download.media.download_media", cdn_mock)

        async def _noop(_):
            return None

        monkeypatch.setattr("download.common.input_enter_continue", _noop)
        monkeypatch.setattr("download.media.input_enter_continue", _noop)

        state = DownloadState()
        try:
            await download_single_post(mock_config, state)
        finally:
            dump_fansly_calls(respx.calls, label="single_account_media_only")

        assert state.creator_name == "nocaps"
        assert state.creator_id == creator_id
        # The "Inspecting post X by Nocaps" line proves capitalize() ran.
        info_messages = [
            r.getMessage() for r in caplog.records if r.levelname == "INFO"
        ]
        capitalize_messages = [m for m in info_messages if "Nocaps" in m]
        assert capitalize_messages, (
            f"Expected the displayName-fallback capitalize() branch to fire. "
            f"INFO messages: {info_messages}"
        )

    @pytest.mark.asyncio
    async def test_no_accessible_content_logs_warning(
        self, respx_fansly_api, mock_config, entity_store, monkeypatch, caplog
    ):
        """No accountMedia + no bundles → warning logged, no download attempted.

        Covers download/single.py:118-119 (the print_warning branch).
        Real ``process_timeline_posts`` runs (Post persisted) but the
        guard at line 67 short-circuits the download path.
        """
        caplog.set_level(logging.WARNING)
        post_id = snowflake_id()
        mock_config.post_id = str(post_id)
        mock_config.interactive = False

        creator_id = snowflake_id()

        # Real ``process_timeline_posts`` requires the Account to exist
        # before the Post can be persisted (FK posts_accountId_fkey).
        # Real API responses always include the post's author in
        # accounts[], so this matches production shape.
        respx.get(url__startswith=f"{FanslyApi.BASE_URL}post").mock(
            side_effect=[
                httpx.Response(
                    200,
                    json={
                        "success": True,
                        "response": {
                            "posts": [_post_payload(post_id, creator_id)],
                            "aggregatedPosts": [],
                            "accountMediaBundles": [],
                            "accountMedia": [],
                            "accounts": [
                                {
                                    "id": creator_id,
                                    "username": f"u_{creator_id}",
                                    "createdAt": 1700000000,
                                }
                            ],
                        },
                    },
                )
            ],
        )

        state = DownloadState()
        try:
            await download_single_post(mock_config, state)
        finally:
            dump_fansly_calls(respx.calls, label="single_no_content")

        warning_messages = [
            r.getMessage() for r in caplog.records if r.levelname == "WARNING"
        ]
        assert any(f"post {post_id}" in w for w in warning_messages), (
            f"Expected 'Could not find any accessible content in post {post_id}' "
            f"warning, got: {warning_messages}"
        )

    @pytest.mark.asyncio
    async def test_api_failure_logs_error_and_prompts(
        self, respx_fansly_api, mock_config, monkeypatch
    ):
        """Non-200 → error logged, input_enter_continue invoked, no download attempted."""
        mock_config.post_id = "999"
        mock_config.interactive = False

        post_route = respx.get(url__startswith=f"{FanslyApi.BASE_URL}post").mock(
            side_effect=[httpx.Response(404, text="Not Found")]
        )

        prompt_calls: list[bool] = []

        async def _record_prompt(interactive: bool) -> None:
            prompt_calls.append(interactive)

        monkeypatch.setattr("download.single.input_enter_continue", _record_prompt)

        state = DownloadState()
        try:
            await download_single_post(mock_config, state)
        finally:
            dump_fansly_calls(respx.calls, label="single_404")

        assert post_route.call_count == 1
        assert prompt_calls == [False], (
            "input_enter_continue should fire exactly once on the 404 path"
        )

    @pytest.mark.asyncio
    async def test_non_interactive_no_post_id_raises(self, mock_config):
        """Non-interactive mode without post_id → RuntimeError (logic-only test)."""
        mock_config.post_id = None
        mock_config.interactive = False

        state = DownloadState()
        with pytest.raises(RuntimeError, match="non-interactive"):
            await download_single_post(mock_config, state)

    @pytest.mark.asyncio
    async def test_repair_preview_folder_items_called_after_dedupe_init(
        self,
        respx_fansly_api,
        mock_config,
        entity_store,
        tmp_path,
        monkeypatch,
    ):
        """repair_preview_folder_items runs after dedupe_init in the single-post path.

        Behavioral assertion: with repair_previews=True and separate_previews=True,
        a bug-era file ``img_id_<preview_media_id>.jpg`` in the creator folder
        gets renamed to ``Previews/img_preview_id_<preview_media_id>.jpg`` by the
        time download_single_post returns.

        The preview Media is injected via the API response's ``preview`` key inside
        the accountMedia entry (process_media_info persists both ``media`` and
        ``preview`` nested dicts). AccountMedia.previewId is set to preview_media_id
        so build_preview_id_set returns {preview_media_id}. The bug-era file in the
        resolved creator folder (single_creator/) triggers the rename.
        """
        post_id = snowflake_id()
        mock_config.post_id = str(post_id)
        mock_config.show_downloads = True
        mock_config.show_skipped_downloads = False
        mock_config.download_directory = tmp_path
        mock_config.interactive = False
        mock_config.repair_previews = True
        mock_config.separate_previews = True
        # No "_fansly" suffix so the resolved download_path equals the
        # pre-created creator_folder below (dedupe_init would otherwise walk
        # "single_creator_fansly/" and miss the bug-era file).
        mock_config.use_folder_suffix = False

        creator_id = snowflake_id()
        media_id = snowflake_id()
        preview_media_id = snowflake_id()
        am_id = snowflake_id()

        # Bug-era file: named with _id_<preview_media_id> (no preview_ marker).
        # Must exist BEFORE download_single_post runs so the repair walk finds it.
        # For DownloadType.SINGLE with separate_timeline (default), dedupe_init
        # resolves download_path to "<creator>/Timeline", which is what the
        # repair walk roots at — so the bug-era file must live under Timeline/.
        creator_folder = tmp_path / "single_creator"
        timeline_dir = creator_folder / "Timeline"
        timeline_dir.mkdir(parents=True, exist_ok=True)
        bug_era_file = timeline_dir / f"img_id_{preview_media_id}.jpg"
        bug_era_file.write_bytes(b"fake-preview-jpeg")

        am_entry = {
            "id": am_id,
            "accountId": creator_id,
            "mediaId": media_id,
            "previewId": preview_media_id,
            "createdAt": 1700000000,
            "deleted": False,
            "access": True,
            "mimetype": "image/jpeg",
            "media": {
                "id": media_id,
                "accountId": creator_id,
                "mimetype": "image/jpeg",
                "type": 1,
                "status": 1,
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
            # ``process_media_info`` persists the ``preview`` nested dict as a
            # separate Media row before saving AccountMedia (FK constraint order).
            "preview": {
                "id": preview_media_id,
                "accountId": creator_id,
                "mimetype": "image/jpeg",
                "type": 1,
                "status": 1,
                "createdAt": 1700000000,
                "locations": [],
            },
        }

        bundle_id = snowflake_id()
        respx.get(url__startswith=f"{FanslyApi.BASE_URL}post").mock(
            side_effect=[
                httpx.Response(
                    200,
                    json={
                        "success": True,
                        "response": {
                            "posts": [_post_payload(post_id, creator_id)],
                            "aggregatedPosts": [],
                            "accountMediaBundles": [
                                {
                                    "id": bundle_id,
                                    "accountId": creator_id,
                                    "createdAt": 1700000000,
                                    "deleted": False,
                                    "accountMediaIds": [am_id],
                                }
                            ],
                            "accountMedia": [am_entry],
                            "accounts": [
                                {
                                    "id": creator_id,
                                    "username": "single_creator",
                                    "displayName": "Single Creator",
                                    "createdAt": 1700000000,
                                }
                            ],
                        },
                    },
                )
            ],
        )
        respx.get(url__startswith=f"{FanslyApi.BASE_URL}account/media").mock(
            side_effect=[
                httpx.Response(200, json={"success": True, "response": [am_entry]})
            ]
        )

        cdn_mock = AsyncMock(return_value=None)
        monkeypatch.setattr("download.common.download_media", cdn_mock)
        monkeypatch.setattr("download.media.download_media", cdn_mock)

        async def _noop(_):
            return None

        monkeypatch.setattr("download.common.input_enter_continue", _noop)
        monkeypatch.setattr("download.media.input_enter_continue", _noop)
        monkeypatch.setattr("download.single.input_enter_continue", _noop)

        # Pre-persist the preview metadata as a PRIOR run would have: the
        # backfill repairs old files whose AccountMedia.previewId is already in
        # the DB. (In the single-post flow the live fetch persists AccountMedia
        # only AFTER the repair call, so build_preview_id_set would otherwise be
        # empty at repair time.)
        await entity_store.save(
            AccountFactory.build(id=creator_id, username="single_creator")
        )
        await entity_store.save(MediaFactory.build(id=media_id, accountId=creator_id))
        await entity_store.save(
            MediaFactory.build(id=preview_media_id, accountId=creator_id)
        )
        await entity_store.save(
            AccountMediaFactory.build(
                id=am_id,
                accountId=creator_id,
                mediaId=media_id,
                previewId=preview_media_id,
            )
        )

        state = DownloadState()
        try:
            await download_single_post(mock_config, state)
        finally:
            dump_fansly_calls(respx.calls, label="single_repair_preview")

        # Canonical target: Previews/ subfolder under Timeline/, preview_id_
        # marker injected.
        expected_canonical = (
            timeline_dir / "Previews" / f"img_preview_id_{preview_media_id}.jpg"
        )
        assert expected_canonical.exists(), (
            f"repair_preview_folder_items should have renamed the bug-era file "
            f"{bug_era_file} to {expected_canonical}; "
            f"creator_folder contents: {list(creator_folder.rglob('*'))}"
        )
        assert not bug_era_file.exists(), (
            "bug-era file should have been moved (renamed), not left in place"
        )

    @pytest.mark.asyncio
    async def test_interactive_input_loop_rejects_invalid_then_accepts(
        self, respx_fansly_api, mock_config, entity_store, monkeypatch
    ):
        """Interactive mode: loop rejects invalid IDs, accepts a valid Snowflake.

        Covers download/single.py:36-48 (the ``while True: input(...)``
        prompt loop). Feeds three responses via a stubbed ``input``:
          1. 'not a post id' — rejected by ``is_valid_post_id``
          2. 'https://fansly.com/post/abc' — rejected
          3. A valid Snowflake → loop breaks, real flow continues to
             the API call which we serve a 404 to short-circuit at the
             earliest production seam.

        The 404 path is the cleanest way to verify "the loop accepted the
        valid input and proceeded" without setting up the full success
        pipeline (which the other tests already cover).
        """
        mock_config.post_id = None
        mock_config.interactive = True

        valid_post_id = snowflake_id()
        # Sequence of inputs — first two invalid, third valid.
        inputs = iter(
            [
                "not a post id",
                "https://fansly.com/post/abc",
                str(valid_post_id),
            ]
        )

        async def _fake_aprompt_text(_prompt: str, **_k) -> str:
            return next(inputs)

        monkeypatch.setattr("download.single.aprompt_text", _fake_aprompt_text)

        # Server returns 404 for the (real, valid) post ID — the test
        # verifies the loop EXITED with the valid ID, not the success
        # path itself.
        post_route = respx.get(url__startswith=f"{FanslyApi.BASE_URL}post").mock(
            side_effect=[httpx.Response(404, text="Not Found")]
        )

        prompt_calls: list[bool] = []

        async def _record_prompt(interactive: bool) -> None:
            prompt_calls.append(interactive)

        monkeypatch.setattr("download.single.input_enter_continue", _record_prompt)

        state = DownloadState()
        try:
            await download_single_post(mock_config, state)
        finally:
            dump_fansly_calls(respx.calls, label="single_interactive_loop")

        # The post route was hit with the valid ID — proves the loop
        # exited via ``is_valid_post_id``'s True branch on input #3.
        assert post_route.call_count == 1
        assert str(valid_post_id) in str(post_route.calls[0].request.url), (
            f"Expected the valid Snowflake ID {valid_post_id} in the "
            f"post URL; got {post_route.calls[0].request.url}"
        )
        # 404 path then hit input_enter_continue.
        assert prompt_calls == [True], (
            "input_enter_continue should fire once with config.interactive=True"
        )
