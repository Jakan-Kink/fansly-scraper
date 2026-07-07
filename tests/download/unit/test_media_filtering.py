"""Tests for process_download_accessible_media.

``download_media`` is the CDN-download leaf wrapper and is held at the
boundary here (a no-op CDN-leaf stand-in so we assert call args without
re-downloading real bytes — the full real download pipeline is exercised in
test_media_pipeline.py). ``set_create_directory_for_download`` runs REAL
against a ``tmp_path`` download directory, so the real threshold
save/restore wrapper executes against a real on-disk path.
"""

from unittest.mock import AsyncMock, patch

import pytest

from download.common import process_download_accessible_media
from download.types import DownloadType


class TestProcessDownloadAccessibleMedia:
    """Test process_download_accessible_media with pre-filtered Media objects."""

    @pytest.mark.asyncio
    async def test_calls_download_media(
        self, mock_config, tmp_path, timeline_download_state, filtered_media_list
    ):
        """download_media is invoked with the media list; the real directory
        wrapper creates the on-disk path under tmp_path."""
        mock_config.download_directory = tmp_path

        with patch("download.common.download_media", new_callable=AsyncMock) as mock_dl:
            result = await process_download_accessible_media(
                mock_config, timeline_download_state, filtered_media_list
            )

        assert result is True
        mock_dl.assert_awaited_once_with(
            mock_config, timeline_download_state, filtered_media_list
        )
        # set_create_directory_for_download ran for real against tmp_path.
        assert timeline_download_state.download_path is not None
        assert timeline_download_state.download_path.exists()

    @pytest.mark.asyncio
    async def test_duplicate_threshold_restored(
        self, mock_config, tmp_path, timeline_download_state, filtered_media_list
    ):
        """DUPLICATE_THRESHOLD is restored after processing (messages arm)."""
        mock_config.download_directory = tmp_path
        original = mock_config.DUPLICATE_THRESHOLD
        timeline_download_state.download_type = DownloadType.MESSAGES
        timeline_download_state.total_message_items = 100

        with patch("download.common.download_media", new_callable=AsyncMock):
            await process_download_accessible_media(
                mock_config, timeline_download_state, filtered_media_list
            )

        assert original == mock_config.DUPLICATE_THRESHOLD
        assert timeline_download_state.download_path is not None
        assert timeline_download_state.download_path.exists()
