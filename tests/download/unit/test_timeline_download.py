"""Tests for download/timeline.py — timeline download orchestrator."""

from unittest.mock import AsyncMock, patch

import httpx
import pytest
import respx

from download.downloadstate import DownloadState
from download.timeline import (
    download_timeline,
    process_timeline_data,
    process_timeline_media,
)
from download.types import DownloadType
from errors import DuplicatePageError
from tests.fixtures.utils.test_isolation import snowflake_id


class TestProcessTimelineData:
    """Lines 46-58: check_page_duplicates + process_timeline_posts."""

    @pytest.mark.asyncio
    async def test_processes_timeline(self, mock_config):
        creator_id = snowflake_id()

        state = DownloadState()
        state.creator_id = creator_id

        timeline = {
            "posts": [
                {
                    "id": snowflake_id(),
                    "accountId": creator_id,
                    "fypFlags": 0,
                    "createdAt": 1700000000,
                }
            ],
            "aggregatedPosts": [],
            "accountMedia": [],
            "accountMediaBundles": [],
            "accounts": [{"id": creator_id, "username": f"tl_{creator_id}"}],
        }

        with (
            patch("download.timeline.check_page_duplicates", new_callable=AsyncMock),
            patch("download.timeline.process_timeline_posts", new_callable=AsyncMock),
        ):
            await process_timeline_data(mock_config, state, timeline, 0)


class TestProcessTimelineMedia:
    """Lines 77-80: start_batch, fetch, download."""

    @pytest.mark.asyncio
    async def test_returns_should_continue(self, mock_config):
        state = DownloadState()

        with (
            patch(
                "download.timeline.fetch_and_process_media",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch(
                "download.timeline.process_download_accessible_media",
                new_callable=AsyncMock,
                return_value=True,
            ),
        ):
            result = await process_timeline_media(
                mock_config, state, [str(snowflake_id())]
            )

        assert result is True

    @pytest.mark.asyncio
    async def test_returns_false_to_stop(self, mock_config):
        state = DownloadState()

        with (
            patch(
                "download.timeline.fetch_and_process_media",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch(
                "download.timeline.process_download_accessible_media",
                new_callable=AsyncMock,
                return_value=False,
            ),
        ):
            result = await process_timeline_media(
                mock_config, state, [str(snowflake_id())]
            )

        assert result is False


class TestDownloadTimeline:
    """Lines 97-229: main download_timeline loop with pagination, retries, error handling.

    Uses respx_fansly_api fixture. Patches all metadata and download pipeline functions.
    """

    @pytest.mark.asyncio
    async def test_success_single_page(self, respx_fansly_api, mock_config):
        """Lines 97-199: success — single page with media, pagination ends via IndexError."""
        mock_config.use_duplicate_threshold = False
        mock_config.use_pagination_duplication = False
        mock_config.debug = False
        mock_config.show_downloads = True
        mock_config.show_skipped_downloads = False
        mock_config.interactive = False
        mock_config.timeline_retries = 0
        mock_config.timeline_delay_seconds = 0

        creator_id = snowflake_id()
        post_id = snowflake_id()

        state = DownloadState()
        state.creator_id = creator_id
        state.creator_name = f"tldl_{creator_id}"

        respx.get(
            url__startswith=f"https://apiv3.fansly.com/api/v1/timelinenew/{creator_id}"
        ).mock(
            side_effect=[
                httpx.Response(
                    200,
                    json={
                        "success": True,
                        "response": {
                            "posts": [
                                {
                                    "id": post_id,
                                    "accountId": creator_id,
                                    "fypFlags": 0,
                                    "createdAt": 1700000000,
                                }
                            ],
                            "aggregatedPosts": [],
                            "accountMedia": [
                                {
                                    "id": snowflake_id(),
                                    "accountId": creator_id,
                                    "mediaId": snowflake_id(),
                                    "createdAt": 1700000000,
                                    "deleted": False,
                                    "access": True,
                                },
                            ],
                            "accountMediaBundles": [],
                            "accounts": [
                                {"id": creator_id, "username": f"tldl_{creator_id}"}
                            ],
                        },
                    },
                ),
                httpx.Response(
                    200,
                    json={
                        "success": True,
                        "response": {
                            "posts": [],
                            "aggregatedPosts": [],
                            "accountMedia": [],
                            "accountMediaBundles": [],
                            "accounts": [],
                        },
                    },
                ),
            ],
        )

        with (
            patch(
                "download.timeline.process_timeline_media",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch("download.timeline.check_page_duplicates", new_callable=AsyncMock),
            patch("download.timeline.process_timeline_posts", new_callable=AsyncMock),
            patch("download.timeline.sleep", new_callable=AsyncMock),
            patch("download.timeline.input_enter_continue"),
        ):
            await download_timeline(mock_config, state)

        assert state.download_type == DownloadType.TIMELINE

    @pytest.mark.asyncio
    async def test_empty_media_retries(self, respx_fansly_api, mock_config):
        """Lines 154-164: no media IDs → retry with delay, exhaust retries."""
        mock_config.use_duplicate_threshold = False
        mock_config.use_pagination_duplication = False
        mock_config.debug = False
        mock_config.timeline_retries = 1
        mock_config.timeline_delay_seconds = 0

        creator_id = snowflake_id()

        state = DownloadState()
        state.creator_id = creator_id
        state.creator_name = f"retry_{creator_id}"

        respx.get(
            url__startswith=f"https://apiv3.fansly.com/api/v1/timelinenew/{creator_id}"
        ).mock(
            side_effect=[
                httpx.Response(
                    200,
                    json={
                        "success": True,
                        "response": {
                            "posts": [
                                {
                                    "id": snowflake_id(),
                                    "accountId": creator_id,
                                    "fypFlags": 0,
                                    "createdAt": 1700000000,
                                }
                            ],
                            "aggregatedPosts": [],
                            "accountMedia": [],
                            "accountMediaBundles": [],
                            "accounts": [],
                        },
                    },
                ),
                httpx.Response(
                    200,
                    json={
                        "success": True,
                        "response": {
                            "posts": [
                                {
                                    "id": snowflake_id(),
                                    "accountId": creator_id,
                                    "fypFlags": 0,
                                    "createdAt": 1700000000,
                                }
                            ],
                            "aggregatedPosts": [],
                            "accountMedia": [],
                            "accountMediaBundles": [],
                            "accounts": [],
                        },
                    },
                ),
            ],
        )

        with (
            patch("download.timeline.check_page_duplicates", new_callable=AsyncMock),
            patch("download.timeline.process_timeline_posts", new_callable=AsyncMock),
            patch("download.timeline.sleep", new_callable=AsyncMock),
            patch("download.timeline.input_enter_continue"),
        ):
            await download_timeline(mock_config, state)

    @pytest.mark.asyncio
    async def test_skips_when_duplication_fetched(self, mock_config):
        """Lines 106-112: fetched_timeline_duplication=True → skip entirely."""
        mock_config.use_duplicate_threshold = True
        mock_config.use_pagination_duplication = False

        state = DownloadState()
        state.creator_id = snowflake_id()
        state.fetched_timeline_duplication = True

        await download_timeline(mock_config, state)
        assert state.download_type == DownloadType.TIMELINE

    @pytest.mark.asyncio
    async def test_should_continue_false_breaks(self, respx_fansly_api, mock_config):
        """Lines 174-176: process_timeline_media returns False → break."""
        mock_config.use_duplicate_threshold = False
        mock_config.use_pagination_duplication = False
        mock_config.debug = False
        mock_config.timeline_retries = 0
        mock_config.show_downloads = False

        creator_id = snowflake_id()

        state = DownloadState()
        state.creator_id = creator_id
        state.creator_name = f"brk_{creator_id}"

        respx.get(
            url__startswith=f"https://apiv3.fansly.com/api/v1/timelinenew/{creator_id}"
        ).mock(
            side_effect=[
                httpx.Response(
                    200,
                    json={
                        "success": True,
                        "response": {
                            "posts": [
                                {
                                    "id": snowflake_id(),
                                    "accountId": creator_id,
                                    "fypFlags": 0,
                                    "createdAt": 1700000000,
                                }
                            ],
                            "aggregatedPosts": [],
                            "accountMedia": [
                                {
                                    "id": snowflake_id(),
                                    "accountId": creator_id,
                                    "mediaId": snowflake_id(),
                                    "createdAt": 1700000000,
                                    "deleted": False,
                                    "access": True,
                                }
                            ],
                            "accountMediaBundles": [],
                            "accounts": [],
                        },
                    },
                )
            ],
        )

        with (
            patch(
                "download.timeline.process_timeline_media",
                new_callable=AsyncMock,
                return_value=False,
            ),
            patch("download.timeline.check_page_duplicates", new_callable=AsyncMock),
            patch("download.timeline.process_timeline_posts", new_callable=AsyncMock),
            patch("download.timeline.input_enter_continue"),
        ):
            await download_timeline(mock_config, state)

    @pytest.mark.asyncio
    async def test_key_error_handling(self, respx_fansly_api, mock_config):
        """Lines 209-216: KeyError during timeline processing → error + input_enter_continue."""
        mock_config.use_duplicate_threshold = False
        mock_config.use_pagination_duplication = False
        mock_config.timeline_retries = 0
        mock_config.interactive = False

        creator_id = snowflake_id()

        state = DownloadState()
        state.creator_id = creator_id
        state.creator_name = f"ke_{creator_id}"

        # Response missing expected keys → KeyError
        respx.get(
            url__startswith=f"https://apiv3.fansly.com/api/v1/timelinenew/{creator_id}"
        ).mock(
            side_effect=[httpx.Response(200, json={"success": True, "response": {}})],
        )

        with patch("download.timeline.input_enter_continue"):
            await download_timeline(mock_config, state)

    @pytest.mark.asyncio
    async def test_duplicate_page_error_breaks(self, respx_fansly_api, mock_config):
        """Lines 218-222: DuplicatePageError → print info, mark handled, break."""
        mock_config.use_duplicate_threshold = False
        mock_config.use_pagination_duplication = False
        mock_config.timeline_retries = 0

        creator_id = snowflake_id()

        state = DownloadState()
        state.creator_id = creator_id
        state.creator_name = f"dup_{creator_id}"

        respx.get(
            url__startswith=f"https://apiv3.fansly.com/api/v1/timelinenew/{creator_id}"
        ).mock(
            side_effect=[
                httpx.Response(
                    200,
                    json={
                        "success": True,
                        "response": {
                            "posts": [
                                {
                                    "id": snowflake_id(),
                                    "accountId": creator_id,
                                    "fypFlags": 0,
                                    "createdAt": 1700000000,
                                }
                            ],
                            "aggregatedPosts": [],
                            "accountMedia": [],
                            "accountMediaBundles": [],
                            "accounts": [],
                        },
                    },
                )
            ],
        )

        with (
            patch(
                "download.timeline.check_page_duplicates",
                new_callable=AsyncMock,
                side_effect=DuplicatePageError("all dupes"),
            ),
            patch("download.timeline.process_timeline_posts", new_callable=AsyncMock),
            patch("download.timeline.input_enter_continue"),
        ):
            await download_timeline(mock_config, state)
