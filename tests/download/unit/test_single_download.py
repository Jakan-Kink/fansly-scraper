"""Tests for download/single.py — download_single_post orchestrator."""

from unittest.mock import AsyncMock, patch

import httpx
import pytest
import respx

from download.downloadstate import DownloadState
from download.single import download_single_post
from download.types import DownloadType
from tests.fixtures.utils.test_isolation import snowflake_id


class TestDownloadSinglePost:
    """Tests for download_single_post — post_id sources, success, failure, edge cases.

    Uses respx_fansly_api fixture. Patches metadata processing functions
    (process_timeline_posts) and download pipeline (fetch_and_process_media,
    process_download_accessible_media, dedupe_init).
    """

    @pytest.mark.asyncio
    async def test_success_with_bundles(self, respx_fansly_api, mock_config):
        """Lines 19-116: success with accountMediaBundles + accounts.
        Covers: post_id from config (23-27), creator from bundles (72-75), display name (90-93)."""
        mock_config.post_id = "123456789"
        mock_config.show_downloads = True
        mock_config.show_skipped_downloads = False

        creator_id = snowflake_id()
        am_id = snowflake_id()
        bundle_id = snowflake_id()

        respx.get("https://apiv3.fansly.com/api/v1/post").mock(
            side_effect=[
                httpx.Response(
                    200,
                    json={
                        "success": True,
                        "response": {
                            "posts": [
                                {
                                    "id": 123456789,
                                    "accountId": creator_id,
                                    "fypFlags": 0,
                                    "createdAt": 1700000000,
                                }
                            ],
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
                            "accountMedia": [
                                {
                                    "id": am_id,
                                    "accountId": creator_id,
                                    "mediaId": snowflake_id(),
                                    "createdAt": 1700000000,
                                    "deleted": False,
                                    "access": True,
                                }
                            ],
                            "accounts": [
                                {
                                    "id": creator_id,
                                    "username": "single_creator",
                                    "displayName": "Single Creator",
                                }
                            ],
                        },
                    },
                )
            ],
        )

        state = DownloadState()
        state.duplicate_count = 2

        with (
            patch("download.single.process_timeline_posts", new_callable=AsyncMock),
            patch(
                "download.single.fetch_and_process_media",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch(
                "download.single.process_download_accessible_media",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch("download.single.dedupe_init", new_callable=AsyncMock),
        ):
            await download_single_post(mock_config, state)

        assert state.download_type == DownloadType.SINGLE
        assert state.creator_name == "single_creator"

    @pytest.mark.asyncio
    async def test_success_with_account_media_only(self, respx_fansly_api, mock_config):
        """Lines 76-77: no bundles, creator_id from accountMedia[0].
        Lines 94-97: no displayName → capitalize username."""
        mock_config.post_id = "987654321"

        creator_id = snowflake_id()
        am_id = snowflake_id()

        respx.get("https://apiv3.fansly.com/api/v1/post").mock(
            side_effect=[
                httpx.Response(
                    200,
                    json={
                        "success": True,
                        "response": {
                            "posts": [
                                {
                                    "id": 987654321,
                                    "accountId": creator_id,
                                    "fypFlags": 0,
                                    "createdAt": 1700000000,
                                }
                            ],
                            "aggregatedPosts": [],
                            "accountMediaBundles": [],
                            "accountMedia": [
                                {
                                    "id": am_id,
                                    "accountId": creator_id,
                                    "mediaId": snowflake_id(),
                                    "createdAt": 1700000000,
                                    "deleted": False,
                                    "access": True,
                                }
                            ],
                            "accounts": [
                                {
                                    "id": creator_id,
                                    "username": "nocaps",
                                    "displayName": None,
                                }
                            ],
                        },
                    },
                )
            ],
        )

        state = DownloadState()

        with (
            patch("download.single.process_timeline_posts", new_callable=AsyncMock),
            patch(
                "download.single.fetch_and_process_media",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch(
                "download.single.process_download_accessible_media",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch("download.single.dedupe_init", new_callable=AsyncMock),
        ):
            await download_single_post(mock_config, state)

    @pytest.mark.asyncio
    async def test_no_accessible_content(self, respx_fansly_api, mock_config):
        """Lines 118-119: no accountMedia or bundles → warning."""
        mock_config.post_id = "111111111"

        creator_id = snowflake_id()

        respx.get("https://apiv3.fansly.com/api/v1/post").mock(
            side_effect=[
                httpx.Response(
                    200,
                    json={
                        "success": True,
                        "response": {
                            "posts": [
                                {
                                    "id": 111111111,
                                    "accountId": creator_id,
                                    "fypFlags": 0,
                                    "createdAt": 1700000000,
                                }
                            ],
                            "aggregatedPosts": [],
                            "accountMediaBundles": [],
                            "accountMedia": [],
                            "accounts": [],
                        },
                    },
                )
            ],
        )

        state = DownloadState()

        with patch("download.single.process_timeline_posts", new_callable=AsyncMock):
            await download_single_post(mock_config, state)

    @pytest.mark.asyncio
    async def test_api_failure(self, respx_fansly_api, mock_config):
        """Lines 121-126: non-200 → error + input_enter_continue."""
        mock_config.post_id = "999"
        mock_config.interactive = False

        respx.get("https://apiv3.fansly.com/api/v1/post").mock(
            side_effect=[httpx.Response(404, text="Not Found")],
        )

        state = DownloadState()

        with patch("download.single.input_enter_continue"):
            await download_single_post(mock_config, state)

    @pytest.mark.asyncio
    async def test_non_interactive_no_post_id_raises(self, mock_config):
        """Lines 29-33: non-interactive mode without post_id → RuntimeError."""
        mock_config.post_id = None
        mock_config.interactive = False

        state = DownloadState()
        with pytest.raises(RuntimeError, match="non-interactive"):
            await download_single_post(mock_config, state)
