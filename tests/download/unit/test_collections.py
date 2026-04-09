"""Tests for download/collections.py — download_collections orchestrator."""

from unittest.mock import AsyncMock, patch

import httpx
import pytest
import respx

from download.collections import download_collections
from download.downloadstate import DownloadState
from download.types import DownloadType
from metadata.models import Account
from tests.fixtures.utils.test_isolation import snowflake_id


class TestDownloadCollections:
    """Tests for download_collections — success and failure paths.

    Uses respx_fansly_api fixture (activates respx.mock + CORS preflight handling).
    Patches fetch_and_process_media and process_download_accessible_media
    since they involve further API calls and file I/O.
    """

    @pytest.mark.asyncio
    async def test_success_with_accounts_and_media(
        self, respx_fansly_api, mock_config, entity_store
    ):
        """Lines 17-55: successful 200 with accounts, media, orders, batch processing, dup message."""
        mock_config.show_downloads = True
        mock_config.show_skipped_downloads = False

        acct_id = snowflake_id()
        media_id = snowflake_id()
        am_id = snowflake_id()

        await entity_store.save(Account(id=acct_id, username=f"coll_{acct_id}"))

        respx.get("https://apiv3.fansly.com/api/v1/account/media/orders/").mock(
            side_effect=[
                httpx.Response(
                    200,
                    json={
                        "success": True,
                        "response": {
                            "accounts": [
                                {"id": acct_id, "username": f"coll_{acct_id}"}
                            ],
                            "accountMedia": [
                                {
                                    "id": am_id,
                                    "accountId": acct_id,
                                    "mediaId": media_id,
                                    "createdAt": 1700000000,
                                    "deleted": False,
                                    "access": True,
                                    "media": {
                                        "id": media_id,
                                        "accountId": acct_id,
                                        "mimetype": "image/jpeg",
                                        "type": 1,
                                        "status": 1,
                                    },
                                },
                            ],
                            "accountMediaOrders": [{"accountMediaId": am_id}],
                        },
                    },
                )
            ],
        )

        state = DownloadState()
        state.duplicate_count = 3

        with (
            patch(
                "download.collections.fetch_and_process_media",
                new_callable=AsyncMock,
                return_value=[],
            ) as mock_fetch,
            patch(
                "download.collections.process_download_accessible_media",
                new_callable=AsyncMock,
                return_value=True,
            ),
        ):
            await download_collections(mock_config, state)

        assert state.download_type == DownloadType.COLLECTIONS
        mock_fetch.assert_called_once()

    @pytest.mark.asyncio
    async def test_success_empty_media(
        self, respx_fansly_api, mock_config, entity_store
    ):
        """Lines 32-33: empty accountMedia → batch loop skipped."""
        respx.get("https://apiv3.fansly.com/api/v1/account/media/orders/").mock(
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

        state = DownloadState()

        with (
            patch(
                "download.collections.fetch_and_process_media",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch(
                "download.collections.process_download_accessible_media",
                new_callable=AsyncMock,
                return_value=True,
            ),
        ):
            await download_collections(mock_config, state)

    @pytest.mark.asyncio
    async def test_failure_non_200(self, respx_fansly_api, mock_config):
        """Lines 57-63: non-200 → error + input_enter_continue."""
        mock_config.interactive = False

        respx.get("https://apiv3.fansly.com/api/v1/account/media/orders/").mock(
            side_effect=[httpx.Response(403, text="Forbidden")],
        )

        state = DownloadState()

        with patch("download.collections.input_enter_continue"):
            await download_collections(mock_config, state)

        assert state.download_type == DownloadType.COLLECTIONS

    @pytest.mark.asyncio
    async def test_no_duplicate_message_when_zero(
        self, respx_fansly_api, mock_config, entity_store
    ):
        """Lines 47-48: duplicate_count == 0 → skip message."""
        mock_config.show_downloads = True
        mock_config.show_skipped_downloads = False

        respx.get("https://apiv3.fansly.com/api/v1/account/media/orders/").mock(
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

        state = DownloadState()
        state.duplicate_count = 0

        with (
            patch(
                "download.collections.fetch_and_process_media",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch(
                "download.collections.process_download_accessible_media",
                new_callable=AsyncMock,
                return_value=True,
            ),
        ):
            await download_collections(mock_config, state)
