"""Unit tests for the normalize module."""

import re
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import select

from fileio.normalize import get_id_from_filename, normalize_filename
from metadata.media import Media


class TestGetIdFromFilename:
    """Tests for the get_id_from_filename function."""

    def test_get_id_from_filename_with_id(self):
        """Test get_id_from_filename with valid ID."""
        media_id, is_preview = get_id_from_filename("2023-01-01_at_12-30_id_123456.jpg")
        assert media_id == 123456
        assert not is_preview

    def test_get_id_from_filename_with_preview_id(self):
        """Test get_id_from_filename with preview ID."""
        media_id, is_preview = get_id_from_filename(
            "2023-01-01_at_12-30_preview_id_123456.jpg"
        )
        assert media_id == 123456
        assert is_preview

    def test_get_id_from_filename_no_id(self):
        """Test get_id_from_filename without ID."""
        media_id, is_preview = get_id_from_filename("file_without_id.jpg")
        assert media_id is None
        assert not is_preview


@pytest.fixture
def mock_db_config():
    """Create a mock database config."""
    with patch("metadata.database.require_database_config") as mock_decorator:
        mock_decorator.side_effect = lambda f: f
        mock_config = MagicMock()
        mock_session = MagicMock()

        # Set up context manager for session scope
        mock_ctx = MagicMock()
        mock_ctx.__enter__ = MagicMock(return_value=mock_session)
        mock_ctx.__exit__ = MagicMock(return_value=None)
        mock_config._database.session_scope.return_value = mock_ctx

        # Mock the SQLAlchemy query chain
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None  # Default to no match
        mock_session.execute.return_value = mock_result

        yield mock_config, mock_session


class TestNormalizeFilename:
    """Tests for the normalize_filename function."""

    def test_normalize_filename_with_database_match(self, mock_db_config):
        """Test normalize_filename with database match."""
        mock_config, mock_session = mock_db_config

        # Mock a Media object with created_at in UTC
        mock_media = MagicMock()
        mock_media.created_at = datetime(2023, 1, 1, 15, 30, tzinfo=timezone.utc)
        mock_session.execute.return_value.scalar_one_or_none.return_value = mock_media

        # Mock the database query and result
        filename = "2023-01-01_at_10-30_id_12345.jpg"
        result = normalize_filename(filename, config=mock_config)
        assert result == "2023-01-01_at_15-30_UTC_id_12345.jpg"

    def test_normalize_filename_no_database_match(self, mock_db_config):
        """Test normalize_filename without database match."""
        mock_config, mock_session = mock_db_config

        # Mock no media found in database
        mock_session.execute.return_value.scalar_one_or_none.return_value = None

        # Without database match, time should stay unchanged
        filename = "2023-01-01_at_10-30_id_12345.jpg"
        result = normalize_filename(filename, config=mock_config)
        assert result == filename

    def test_normalize_filename_different_extensions(self, mock_db_config):
        """Test normalize_filename with different extensions."""
        mock_config, mock_session = mock_db_config

        # Mock a Media object with created_at in UTC
        mock_media = MagicMock()
        mock_media.created_at = datetime(2023, 1, 1, 15, 30, tzinfo=timezone.utc)
        mock_session.execute.return_value.scalar_one_or_none.return_value = mock_media

        # Test local time gets converted with database match
        filename = "2023-01-01_at_10-30_id_12345.jpg"
        result = normalize_filename(filename, config=mock_config)
        assert result == "2023-01-01_at_15-30_UTC_id_12345.jpg"

        # Test various extensions get preserved
        for ext in ["jpg", "mp4", "m3u8", "ts"]:
            # Local time should convert with database match
            filename = f"2023-01-01_at_10-30_id_12345.{ext}"
            result = normalize_filename(filename, config=mock_config)
            assert result == f"2023-01-01_at_15-30_UTC_id_12345.{ext}"

            # UTC time should stay unchanged
            filename = f"2023-01-01_at_15-30_UTC_id_12345.{ext}"
            result = normalize_filename(filename, config=mock_config)
            assert result == filename

    def test_normalize_filename_no_id(self, mock_db_config):
        """Test normalize_filename without ID pattern."""
        mock_config, _ = mock_db_config
        # Files without ID should be returned unchanged
        filename = "2023-01-01_at_12-30.jpg"
        assert normalize_filename(filename, config=mock_config) == filename

        filename = "random_file_without_id.mp4"
        assert normalize_filename(filename, config=mock_config) == filename

        filename = ""
        assert normalize_filename(filename, config=mock_config) == filename

    def test_normalize_filename_hash_pattern(self, mock_db_config):
        """Test normalize_filename with hash patterns."""
        mock_config, _ = mock_db_config
        # Hash patterns should be preserved exactly as is
        filename = "2023-01-01_at_12-30_hash_abc123_id_123456.jpg"
        assert normalize_filename(filename, config=mock_config) == filename

        filename = "2023-01-01_at_12-30_hash1_abc123_id_123456.jpg"
        assert normalize_filename(filename, config=mock_config) == filename

        filename = "2023-01-01_at_12-30_hash2_abc123_id_123456.jpg"
        assert normalize_filename(filename, config=mock_config) == filename
