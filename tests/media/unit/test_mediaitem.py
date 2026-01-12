"""Unit tests for media/mediaitem.py"""

from media.mediaitem import MediaItem


class TestMediaItem:
    """Tests for MediaItem dataclass."""

    def test_created_at_str(self):
        """Test created_at_str formats timestamp correctly."""
        # 2024-01-15 14:30:00 UTC
        timestamp = 1705329000
        item = MediaItem(created_at=timestamp)
        result = item.created_at_str()
        assert result == "2024-01-15_at_14-30_UTC"

    def test_get_download_url_file_extension_with_url(self):
        """Test get_download_url_file_extension extracts extension."""
        item = MediaItem(download_url="https://example.com/path/file.jpg?param=value")
        assert item.get_download_url_file_extension() == "jpg"

    def test_get_download_url_file_extension_without_url(self):
        """Test get_download_url_file_extension returns None when no URL."""
        item = MediaItem(download_url=None)
        assert item.get_download_url_file_extension() is None

    def test_get_file_name_regular_with_extension(self):
        """Test get_file_name for regular media with extension."""
        item = MediaItem(
            media_id=12345,
            created_at=1705329000,
            file_extension="jpg",
        )
        result = item.get_file_name(for_preview=False)
        assert result == "2024-01-15_at_14-30_UTC_id_12345.jpg"

    def test_get_file_name_preview_with_extension(self):
        """Test get_file_name for preview with extension."""
        item = MediaItem(
            media_id=12345,
            created_at=1705329000,
            preview_extension="webp",
        )
        result = item.get_file_name(for_preview=True)
        assert result == "2024-01-15_at_14-30_UTC_preview_id_12345.webp"

    def test_get_file_name_regular_without_extension_with_url(self):
        """Test get_file_name extracts extension from download_url."""
        item = MediaItem(
            media_id=12345,
            created_at=1705329000,
            file_extension=None,
            download_url="https://example.com/media.mp4?token=abc",
        )
        result = item.get_file_name(for_preview=False)
        assert result == "2024-01-15_at_14-30_UTC_id_12345.mp4"

    def test_get_file_name_preview_without_extension_with_url(self):
        """Test get_file_name extracts extension from preview_url."""
        item = MediaItem(
            media_id=12345,
            created_at=1705329000,
            preview_extension=None,
            preview_url="https://example.com/preview.webp?token=xyz",
        )
        result = item.get_file_name(for_preview=True)
        assert result == "2024-01-15_at_14-30_UTC_preview_id_12345.webp"

    def test_get_file_name_without_extension_or_url(self):
        """Test get_file_name when extension and URL are None."""
        item = MediaItem(
            media_id=12345,
            created_at=1705329000,
            file_extension=None,
            download_url=None,
        )
        result = item.get_file_name(for_preview=False)
        assert result == "2024-01-15_at_14-30_UTC_id_12345.None"

    def test_dataclass_defaults(self):
        """Test MediaItem default values."""
        item = MediaItem()
        assert item.media_id == 0
        assert item.metadata is None
        assert item.mimetype is None
        assert item.created_at == 0
        assert item.download_url is None
        assert item.file_extension is None
        assert item.is_preview is False
