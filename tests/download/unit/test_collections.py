"""Tests for download/collections.py — download_collections orchestrator.

Real-pipeline rewrite (Wave 2 Category D #8): the previous version
patched ``download.collections.fetch_and_process_media`` and
``download.collections.process_download_accessible_media`` — both
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

Real code throughout: ``process_account_data`` saves accounts;
``process_media_info`` persists Media + AccountMedia rows;
``fetch_and_process_media`` issues a real ``/account/media`` HTTP call;
``process_download_accessible_media`` runs full real orchestration
(invokes the patched ``download_media`` leaf for each accessible item).
"""

import logging
from unittest.mock import AsyncMock

import httpx
import pytest
import respx

import download.media as download_media_mod
from api.fansly import FanslyApi
from download.collections import download_collections
from download.downloadstate import DownloadState
from download.types import DownloadType
from metadata.models import AccountMedia, Media, get_store
from tests.fixtures.api import dump_fansly_calls
from tests.fixtures.fileio import tiny_jpeg_bytes
from tests.fixtures.utils.test_isolation import snowflake_id


def _account_media_payload(media_id: int, am_id: int, account_id: int) -> dict:
    """AccountMedia entry with the nested-media + previewId + signed-CDN URL shape.

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


class TestDownloadCollections:
    """Real-pipeline tests for download_collections."""

    @pytest.mark.asyncio
    async def test_success_persists_account_media_and_invokes_cdn(
        self, respx_fansly_api, mock_config, entity_store, tmp_path, monkeypatch
    ):
        """Successful 200 → real account+media persistence → real CDN download.

        Replaces the previous internal-mock version which only verified
        ``mock_fetch.assert_called_once()``. The real-pipeline version
        is strictly stronger:
          - asserts the real ``/api/v1/account/media`` HTTP boundary fired
          - asserts the AccountMedia + Media rows are persisted in the
            real EntityStore (FK chain Media → AccountMedia satisfied)
          - runs the REAL ``download_media`` pipeline (downgraded to a
            ``wraps``-spy, not a behavior-replacing mock): the signed-URL
            image is fetched from a respx-served CDN route and written to
            ``tmp_path`` via the real ``_download_regular_file`` +
            ``set_create_directory_for_download`` + dedupe path. Only the
            ``imagehash.phash`` external-lib leaf is patched.
        """
        mock_config.show_downloads = True
        mock_config.show_skipped_downloads = False
        mock_config.download_directory = tmp_path
        mock_config.interactive = False

        acct_id = snowflake_id()
        media_id = snowflake_id()
        am_id = snowflake_id()
        am_entry = _account_media_payload(media_id, am_id, acct_id)

        orders_route = respx.get(
            url__startswith=FanslyApi.ACCOUNT_MEDIA_ORDERS_ENDPOINT
        ).mock(
            side_effect=[
                httpx.Response(
                    200,
                    json={
                        "success": True,
                        "response": {
                            "accounts": [
                                {
                                    "id": acct_id,
                                    "username": f"coll_{acct_id}",
                                    "createdAt": 1700000000,
                                }
                            ],
                            "accountMedia": [am_entry],
                            "accountMediaOrders": [{"accountMediaId": am_id}],
                        },
                    },
                )
            ],
        )
        # fetch_and_process_media → /api/v1/account/media?ids=<am_id>
        media_route = respx.get(
            url__startswith=FanslyApi.ACCOUNT_MEDIA_ENDPOINT.format("")
        ).mock(
            side_effect=[
                httpx.Response(200, json={"success": True, "response": [am_entry]})
            ]
        )

        # Real CDN download: serve the signed-URL image bytes so the real
        # download_media pipeline (set_create_directory_for_download →
        # process_media_download persistence → _download_regular_file →
        # dedupe) runs end-to-end. download_media is downgraded to a
        # wraps-spy so the real code path executes while calls remain
        # observable. Patch at BOTH binding sites because download.common
        # imports download_media at module scope.
        jpeg = tiny_jpeg_bytes()
        cdn_route = respx.get(url__startswith="https://cdn.example.com/img.jpg").mock(
            side_effect=[
                httpx.Response(
                    200, content=jpeg, headers={"content-length": str(len(jpeg))}
                )
            ]
        )
        cdn_mock = AsyncMock(wraps=download_media_mod.download_media)
        monkeypatch.setattr("download.common.download_media", cdn_mock)
        monkeypatch.setattr("download.media.download_media", cdn_mock)
        # imagehash.phash is the external-lib leaf boundary (dedupe).
        monkeypatch.setattr(
            "fileio.fnmanip.imagehash.phash",
            lambda *_a, **_k: "hash_collections",
        )

        async def _noop(_):
            return None

        monkeypatch.setattr("download.common.input_enter_continue", _noop)
        monkeypatch.setattr("download.media.input_enter_continue", _noop)
        monkeypatch.setattr("download.collections.input_enter_continue", _noop)

        # Production callers populate creator_name AND creator_id before
        # reaching download_collections (both set by get_creator_account_info
        # → download/account.py:174 ``state.creator_id = account.id``, run
        # in the main flow). pathio.set_create_directory_for_download raises
        # RuntimeError without creator_name; process_media_download raises
        # ValueError without creator_id (the real persistence path needs the
        # creator FK). Seed both, mirroring the real precondition.
        state = DownloadState(creator_name=f"coll_{acct_id}")
        state.creator_id = acct_id
        state.duplicate_count = 3

        try:
            await download_collections(mock_config, state)
        finally:
            dump_fansly_calls(respx.calls, label="collections_success")

        assert state.download_type == DownloadType.COLLECTIONS
        assert orders_route.call_count == 1, "Collections orders endpoint not hit"
        assert media_route.call_count >= 1, (
            "fetch_and_process_media should have hit /api/v1/account/media"
        )
        # Real persistence: AccountMedia + Media rows landed in the store.
        store = get_store()
        persisted_media = await store.get(Media, media_id)
        assert persisted_media is not None, (
            "Real process_media_info should have persisted Media row"
        )
        assert persisted_media.accountId == acct_id
        persisted_am = await store.get(AccountMedia, am_id)
        assert persisted_am is not None, "AccountMedia row should be persisted"
        # download_media spy invoked — the orchestrator reached its actual
        # download path, running the real pipeline (not a mocked shortcut).
        assert cdn_mock.call_count >= 1, (
            "Real process_download_accessible_media should have invoked "
            "the download_media pipeline at least once"
        )
        # The real pipeline actually fetched the signed CDN URL and wrote
        # the image to tmp_path.
        assert cdn_route.called, "Real download_media should have fetched the CDN URL"
        assert state.pic_count == 1, "Real image download should increment pic_count"

    @pytest.mark.asyncio
    async def test_non_list_accounts_raises(
        self, respx_fansly_api, mock_config, entity_store, tmp_path
    ):
        """A collections payload whose ``accounts`` is not an array trips
        ``expect_list`` with a precise TypeError, through the real pipeline."""
        mock_config.download_directory = tmp_path
        mock_config.interactive = False

        route = respx.get(url__startswith=FanslyApi.ACCOUNT_MEDIA_ORDERS_ENDPOINT).mock(
            side_effect=[
                httpx.Response(
                    200,
                    json={"success": True, "response": {"accounts": "not-a-list"}},
                )
            ]
        )

        state = DownloadState(creator_name="bad_collection_user")
        try:
            with pytest.raises(TypeError, match="accounts"):
                await download_collections(mock_config, state)
        finally:
            dump_fansly_calls(route.calls, label="collections_non_list_accounts")

    @pytest.mark.asyncio
    async def test_empty_media_skips_batch_loop_and_no_duplicate_message(
        self,
        respx_fansly_api,
        mock_config,
        entity_store,
        tmp_path,
        monkeypatch,
        caplog,
    ):
        """Empty accountMedia → batch loop skipped, download_media called with [], no dup msg.

        Two orthogonal assertions over one run of the empty-collections
        pipeline (merged from ``test_success_empty_media_skips_batch_loop``
        and ``test_no_duplicate_message_when_zero``):

        (a) With no accountMedia in the response, ``process_media_info``
            is never called (batch loop guard), but
            ``process_download_accessible_media`` still runs and calls
            the REAL ``download_media(config, state, [])`` — the real leaf
            short-circuits on the empty list (``if not accessible_media:
            return``), so no CDN URL is fetched. ``download_media`` is a
            ``wraps``-spy over the real function, so its ``call_args`` still
            record the empty-list delegation while the production
            short-circuit path executes. The semantic check is "called once
            with empty list" rather than "not called", because
            ``download_media`` is the boundary between orchestration and
            per-item iteration.
        (b) With ``state.duplicate_count == 0`` — even with
            show_downloads=True and show_skipped_downloads=False — the
            conditional "Skipped N already downloaded" log line does NOT
            fire (asserted via caplog at INFO level).
        """
        caplog.set_level(logging.INFO)
        mock_config.show_downloads = True
        mock_config.show_skipped_downloads = False
        mock_config.download_directory = tmp_path
        mock_config.interactive = False

        respx.get(url__startswith=FanslyApi.ACCOUNT_MEDIA_ORDERS_ENDPOINT).mock(
            side_effect=[
                httpx.Response(
                    200,
                    json={
                        "success": True,
                        "response": {
                            "accounts": [],
                            "accountMedia": [],
                            "accountMediaOrders": [],
                        },
                    },
                )
            ],
        )
        # Even with empty media_ids, fetch_and_process_media may issue
        # a no-op HTTP call; mount the route to absorb it gracefully.
        respx.get(url__startswith=FanslyApi.ACCOUNT_MEDIA_ENDPOINT.format("")).mock(
            side_effect=[httpx.Response(200, json={"success": True, "response": []})]
        )

        # wraps-spy over the REAL download_media: with an empty accessible
        # list the real leaf short-circuits (no CDN fetch), but the spy
        # still records the delegation call for the assertions below.
        cdn_mock = AsyncMock(wraps=download_media_mod.download_media)
        monkeypatch.setattr("download.common.download_media", cdn_mock)
        monkeypatch.setattr("download.media.download_media", cdn_mock)

        async def _noop(_):
            return None

        monkeypatch.setattr("download.common.input_enter_continue", _noop)

        state = DownloadState(creator_name="empty_collection_user")
        state.duplicate_count = 0

        try:
            await download_collections(mock_config, state)
        finally:
            dump_fansly_calls(respx.calls, label="collections_empty")

        assert state.download_type == DownloadType.COLLECTIONS
        # (a) download_media delegated to once with an empty accessible list.
        assert cdn_mock.call_count == 1, (
            "process_download_accessible_media should delegate to download_media "
            "exactly once even when the accessible-media list is empty"
        )
        accessible_arg = cdn_mock.call_args.args[2]
        assert accessible_arg == [], (
            f"download_media should receive an empty accessible-media list "
            f"when no accountMedia entries exist, got: {accessible_arg!r}"
        )
        # (b) duplicate_count == 0 → no "Skipped N already downloaded" message.
        info_messages = [
            r.getMessage() for r in caplog.records if r.levelname == "INFO"
        ]
        skipped_messages = [m for m in info_messages if "Skipped" in m]
        assert skipped_messages == [], (
            f"With duplicate_count=0, the 'Skipped N already downloaded' "
            f"message must not fire. Got: {skipped_messages}"
        )

    @pytest.mark.asyncio
    async def test_failure_non_200_logs_error_and_prompts(
        self, respx_fansly_api, mock_config, monkeypatch
    ):
        """403 → error path logged, input_enter_continue invoked, no internal pipeline.

        The non-200 branch was already mostly clean (only patched
        ``input_enter_continue``); rewritten here to use ``monkeypatch``
        consistent with the other tests rather than ``with patch(...)``.
        """
        mock_config.interactive = False

        orders_route = respx.get(
            url__startswith=FanslyApi.ACCOUNT_MEDIA_ORDERS_ENDPOINT
        ).mock(side_effect=[httpx.Response(403, text="Forbidden")])

        # input_enter_continue is invoked on the failure path; no-op it.
        prompt_calls: list[bool] = []

        async def _record_prompt(interactive: bool) -> None:
            prompt_calls.append(interactive)

        monkeypatch.setattr("download.collections.input_enter_continue", _record_prompt)

        state = DownloadState()
        try:
            await download_collections(mock_config, state)
        finally:
            dump_fansly_calls(respx.calls, label="collections_403")

        assert state.download_type == DownloadType.COLLECTIONS
        assert orders_route.call_count == 1
        assert prompt_calls == [False], (
            "input_enter_continue should fire exactly once on the 403 path "
            "with config.interactive=False"
        )
