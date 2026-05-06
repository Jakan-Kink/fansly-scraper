"""Tests for process_download_accessible_media."""

from unittest.mock import AsyncMock, patch

import pytest

from download.common import process_download_accessible_media
from download.downloadstate import DownloadState
from download.types import DownloadType
from metadata.models import Media
from tests.fixtures.utils.test_isolation import snowflake_id


class TestProcessDownloadAccessibleMedia:
    """Test process_download_accessible_media with pre-filtered Media objects."""

    @pytest.fixture
    def state(self):
        """Create a test download state."""
        state = DownloadState()
        state.creator_name = "test_creator"
        state.download_type = DownloadType.TIMELINE
        return state

    @pytest.fixture
    def media_list(self):
        """Create test Media objects (already filtered — have download URLs)."""
        account_id = snowflake_id()
        return [
            Media(
                id=snowflake_id(),
                accountId=account_id,
                mimetype="video/mp4",
                download_url="http://example.com/video1.mp4",
            ),
            Media(
                id=snowflake_id(),
                accountId=account_id,
                mimetype="image/jpeg",
                download_url="http://example.com/image1.jpg",
            ),
        ]

    @pytest.mark.asyncio
    async def test_calls_download_media(self, mock_config, state, media_list):
        """Test that download_media is called with the media list."""
        with (
            patch("download.common.download_media", new_callable=AsyncMock) as mock_dl,
            patch("download.common.set_create_directory_for_download"),
        ):
            result = await process_download_accessible_media(
                mock_config, state, media_list
            )

            assert result is True
            mock_dl.assert_awaited_once_with(mock_config, state, media_list)

    @pytest.mark.asyncio
    async def test_duplicate_threshold_restored(self, mock_config, state, media_list):
        """Test that DUPLICATE_THRESHOLD is restored after processing."""
        original = mock_config.DUPLICATE_THRESHOLD
        state.download_type = DownloadType.MESSAGES
        state.total_message_items = 100

        with (
            patch("download.common.download_media", new_callable=AsyncMock),
            patch("download.common.set_create_directory_for_download"),
        ):
            await process_download_accessible_media(mock_config, state, media_list)

        assert original == mock_config.DUPLICATE_THRESHOLD
