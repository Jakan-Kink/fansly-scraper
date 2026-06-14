"""Tests for process_download_accessible_media."""

from unittest.mock import AsyncMock, patch

import pytest

from download.common import process_download_accessible_media
from download.types import DownloadType


class TestProcessDownloadAccessibleMedia:
    """Test process_download_accessible_media with pre-filtered Media objects."""

    @pytest.mark.asyncio
    async def test_calls_download_media(
        self, mock_config, timeline_download_state, filtered_media_list
    ):
        """Test that download_media is called with the media list."""
        with (
            patch("download.common.download_media", new_callable=AsyncMock) as mock_dl,
            patch("download.common.set_create_directory_for_download"),
        ):
            result = await process_download_accessible_media(
                mock_config, timeline_download_state, filtered_media_list
            )

            assert result is True
            mock_dl.assert_awaited_once_with(
                mock_config, timeline_download_state, filtered_media_list
            )

    @pytest.mark.asyncio
    async def test_duplicate_threshold_restored(
        self, mock_config, timeline_download_state, filtered_media_list
    ):
        """Test that DUPLICATE_THRESHOLD is restored after processing."""
        original = mock_config.DUPLICATE_THRESHOLD
        timeline_download_state.download_type = DownloadType.MESSAGES
        timeline_download_state.total_message_items = 100

        with (
            patch("download.common.download_media", new_callable=AsyncMock),
            patch("download.common.set_create_directory_for_download"),
        ):
            await process_download_accessible_media(
                mock_config, timeline_download_state, filtered_media_list
            )

        assert original == mock_config.DUPLICATE_THRESHOLD
