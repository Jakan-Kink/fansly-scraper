"""Test media filtering functionality.

Tests the filtering logic that determines which Media items are eligible
for download based on URL availability and preview settings. This filtering
now lives in fetch_and_process_media's return expression:

    [m for m in all_media if m.download_url
     and (not m.is_preview or config.download_media_previews)]
"""

from unittest.mock import AsyncMock, patch

import pytest

from download.common import process_download_accessible_media
from download.downloadstate import DownloadState
from download.types import DownloadType
from metadata.models import Media
from tests.fixtures.utils.test_isolation import snowflake_id


def _make_media(
    *,
    download_url: str | None = None,
    is_preview: bool = False,
    mimetype: str = "video/mp4",
) -> Media:
    """Create a Media object for filtering tests."""
    return Media(
        id=snowflake_id(),
        accountId=snowflake_id(),
        mimetype=mimetype,
        download_url=download_url,
        is_preview=is_preview,
    )


def _filter_media(media_list: list[Media], download_previews: bool) -> list[Media]:
    """Apply the same filtering expression used by fetch_and_process_media."""
    return [
        m
        for m in media_list
        if m.download_url and (not m.is_preview or download_previews)
    ]


class TestMediaFilteringLogic:
    """Test the filtering expression from fetch_and_process_media.

    These are pure unit tests of the filtering logic — no DB or HTTP needed.
    """

    def test_previews_enabled_includes_all_with_urls(self):
        """With previews enabled, all media with URLs should be included."""
        media = [
            _make_media(download_url="http://example.com/primary1.mp4"),
            _make_media(
                download_url="http://example.com/preview1.mp4", is_preview=True
            ),
            _make_media(download_url=None),  # No URL — excluded
        ]

        result = _filter_media(media, download_previews=True)
        assert len(result) == 2
        assert all(m.download_url for m in result)

    def test_previews_disabled_excludes_previews(self):
        """With previews disabled, preview media should be excluded."""
        media = [
            _make_media(download_url="http://example.com/primary1.mp4"),
            _make_media(
                download_url="http://example.com/preview1.mp4", is_preview=True
            ),
        ]

        result = _filter_media(media, download_previews=False)
        assert len(result) == 1
        assert not result[0].is_preview

    def test_only_previews_available_enabled(self):
        """When only previews have URLs and previews are enabled, include them."""
        media = [
            _make_media(download_url=None),  # Primary without URL
            _make_media(
                download_url="http://example.com/preview1.mp4", is_preview=True
            ),
        ]

        result = _filter_media(media, download_previews=True)
        assert len(result) == 1
        assert result[0].is_preview

    def test_only_previews_available_disabled(self):
        """When only previews have URLs and previews are disabled, nothing passes."""
        media = [
            _make_media(download_url=None),  # Primary without URL
            _make_media(
                download_url="http://example.com/preview1.mp4", is_preview=True
            ),
        ]

        result = _filter_media(media, download_previews=False)
        assert len(result) == 0

    def test_mixed_media_types(self):
        """Filtering works across different MIME types."""
        media = [
            _make_media(
                download_url="http://example.com/video1.mp4", mimetype="video/mp4"
            ),
            _make_media(
                download_url="http://example.com/preview.mp4",
                mimetype="video/mp4",
                is_preview=True,
            ),
            _make_media(
                download_url="http://example.com/image1.jpg", mimetype="image/jpeg"
            ),
            _make_media(
                download_url="http://example.com/preview.jpg",
                mimetype="image/jpeg",
                is_preview=True,
            ),
        ]

        result = _filter_media(media, download_previews=True)
        assert len(result) == 4

        result = _filter_media(media, download_previews=False)
        assert len(result) == 2
        assert all(not m.is_preview for m in result)

    def test_both_inaccessible(self):
        """When neither primary nor preview has a URL, nothing passes."""
        media = [
            _make_media(download_url=None),
            _make_media(download_url=None, is_preview=True),
        ]

        result = _filter_media(media, download_previews=True)
        assert len(result) == 0

    def test_primary_only_accessible(self):
        """When primary has URL but preview doesn't, primary always passes."""
        media = [
            _make_media(download_url="http://example.com/primary1.mp4"),
        ]

        # With previews enabled
        result = _filter_media(media, download_previews=True)
        assert len(result) == 1

        # With previews disabled
        result = _filter_media(media, download_previews=False)
        assert len(result) == 1

    def test_empty_list(self):
        """Empty input produces empty output."""
        assert _filter_media([], download_previews=True) == []
        assert _filter_media([], download_previews=False) == []


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
