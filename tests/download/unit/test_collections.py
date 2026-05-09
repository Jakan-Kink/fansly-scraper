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

from api.fansly import FanslyApi
from download.collections import download_collections
from download.downloadstate import DownloadState
from download.types import DownloadType
from metadata.models import AccountMedia, Media, get_store
from tests.fixtures.api import dump_fansly_calls
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
        """Successful 200 → real account+media persistence → CDN leaf invoked once.

        Replaces the previous internal-mock version which only verified
        ``mock_fetch.assert_called_once()``. The real-pipeline version
        is strictly stronger:
          - asserts the real ``/api/v1/account/media`` HTTP boundary fired
          - asserts the AccountMedia + Media rows are persisted in the
            real EntityStore (FK chain Media → AccountMedia satisfied)
          - asserts the CDN download leaf was invoked (proving the
            orchestrator reached process_download_accessible_media's
            actual download path, not a mocked one)
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
            url__startswith=f"{FanslyApi.BASE_URL}account/media/orders/"
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
            url__startswith=f"{FanslyApi.BASE_URL}account/media"
        ).mock(
            side_effect=[
                httpx.Response(200, json={"success": True, "response": [am_entry]})
            ]
        )

        # CDN leaf bypass — real download would write bytes to disk.
        # Patch at BOTH binding sites because download.common imports
        # download_media at module scope.
        cdn_mock = AsyncMock(return_value=None)
        monkeypatch.setattr("download.common.download_media", cdn_mock)
        monkeypatch.setattr("download.media.download_media", cdn_mock)

        async def _noop(_):
            return None

        monkeypatch.setattr("download.common.input_enter_continue", _noop)
        monkeypatch.setattr("download.media.input_enter_continue", _noop)
        monkeypatch.setattr("download.collections.input_enter_continue", _noop)

        # Production callers populate creator_name before reaching
        # download_collections (it's set by get_creator_account_info in
        # fansly_downloader_ng.main). pathio.set_create_directory_for_download
        # raises RuntimeError without it.
        state = DownloadState(creator_name=f"coll_{acct_id}")
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
        # CDN leaf invoked — the orchestrator reached its actual download
        # path, not a mocked shortcut.
        assert cdn_mock.call_count >= 1, (
            "Real process_download_accessible_media should have invoked "
            "the CDN download leaf at least once"
        )

    @pytest.mark.asyncio
    async def test_success_empty_media_skips_batch_loop(
        self, respx_fansly_api, mock_config, entity_store, tmp_path, monkeypatch
    ):
        """Empty accountMedia → batch loop skipped, download_media called with empty list.

        Real-pipeline coverage: with no accountMedia in the response,
        ``process_media_info`` is never called (batch loop guard at
        line 33), but ``process_download_accessible_media`` still runs
        and calls ``download_media(config, state, [])`` — the leaf
        iterates and short-circuits on the empty list, so no actual
        CDN URL is fetched. The semantic check is "called with empty
        list" rather than "not called", because ``download_media`` is
        the boundary between orchestration and per-item iteration.
        """
        mock_config.download_directory = tmp_path
        mock_config.interactive = False

        respx.get(url__startswith=f"{FanslyApi.BASE_URL}account/media/orders/").mock(
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
        respx.get(url__startswith=f"{FanslyApi.BASE_URL}account/media").mock(
            side_effect=[httpx.Response(200, json={"success": True, "response": []})]
        )

        cdn_mock = AsyncMock(return_value=None)
        monkeypatch.setattr("download.common.download_media", cdn_mock)
        monkeypatch.setattr("download.media.download_media", cdn_mock)

        async def _noop(_):
            return None

        monkeypatch.setattr("download.common.input_enter_continue", _noop)

        state = DownloadState(creator_name="empty_collection_user")
        try:
            await download_collections(mock_config, state)
        finally:
            dump_fansly_calls(respx.calls, label="collections_empty")

        assert state.download_type == DownloadType.COLLECTIONS
        assert cdn_mock.call_count == 1, (
            "process_download_accessible_media should delegate to download_media "
            "exactly once even when the accessible-media list is empty"
        )
        accessible_arg = cdn_mock.call_args.args[2]
        assert accessible_arg == [], (
            f"download_media should receive an empty accessible-media list "
            f"when no accountMedia entries exist, got: {accessible_arg!r}"
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
            url__startswith=f"{FanslyApi.BASE_URL}account/media/orders/"
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

    @pytest.mark.asyncio
    async def test_no_duplicate_message_when_zero(
        self,
        respx_fansly_api,
        mock_config,
        entity_store,
        tmp_path,
        monkeypatch,
        caplog,
    ):
        """duplicate_count == 0 → "Skipped N already downloaded" message NOT printed.

        Asserts via caplog that the conditional log line at lines 47-55 of
        download/collections.py does not fire when ``state.duplicate_count``
        is zero — even with show_downloads=True and show_skipped_downloads=False.
        """
        caplog.set_level(logging.INFO)
        mock_config.show_downloads = True
        mock_config.show_skipped_downloads = False
        mock_config.download_directory = tmp_path
        mock_config.interactive = False

        respx.get(url__startswith=f"{FanslyApi.BASE_URL}account/media/orders/").mock(
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
        respx.get(url__startswith=f"{FanslyApi.BASE_URL}account/media").mock(
            side_effect=[httpx.Response(200, json={"success": True, "response": []})]
        )

        cdn_mock = AsyncMock(return_value=None)
        monkeypatch.setattr("download.common.download_media", cdn_mock)
        monkeypatch.setattr("download.media.download_media", cdn_mock)

        async def _noop(_):
            return None

        monkeypatch.setattr("download.common.input_enter_continue", _noop)

        state = DownloadState(creator_name="no_dup_user")
        state.duplicate_count = 0

        try:
            await download_collections(mock_config, state)
        finally:
            dump_fansly_calls(respx.calls, label="collections_no_dups")

        info_messages = [
            r.getMessage() for r in caplog.records if r.levelname == "INFO"
        ]
        skipped_messages = [m for m in info_messages if "Skipped" in m]
        assert skipped_messages == [], (
            f"With duplicate_count=0, the 'Skipped N already downloaded' "
            f"message must not fire. Got: {skipped_messages}"
        )
