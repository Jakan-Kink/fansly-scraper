"""Integration tests for normalize module."""

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from fileio.normalize import normalize_filename


@pytest.fixture
def mock_config():
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


class TestNormalizeFilenameIntegration:
    """Integration tests for normalize_filename."""

    def test_normalize_filename_with_database(self, mock_config):
        """Test normalize_filename with database match."""
        config, mock_session = mock_config

        # Mock media with UTC timestamp for database match
        mock_media = MagicMock()
        mock_media.created_at = datetime(2023, 1, 1, 15, 30, tzinfo=UTC)
        mock_session.execute.return_value.scalar_one_or_none.return_value = mock_media

        # Test local time converts to UTC with database match
        filename = "2023-01-01_at_10-30_id_12345.jpg"
        result = normalize_filename(filename, config=config)
        assert result == "2023-01-01_at_15-30_UTC_id_12345.jpg"

        # Test UTC time stays unchanged
        filename = "2023-01-01_at_15-30_UTC_id_12345.jpg"
        result = normalize_filename(filename, config=config)
        assert result == filename

    def test_normalize_filename_with_database_no_match(self, mock_config):
        """Test normalize_filename without database match."""
        config, mock_session = mock_config

        # Mock no media found in database
        mock_session.execute.return_value.scalar_one_or_none.return_value = None

        filename = "2023-01-01_at_10-30_id_12345.jpg"
        result = normalize_filename(filename, config=config)
        # Without database match, time should stay unchanged
        assert result == filename

    def test_normalize_filename_with_mp4(self, mock_config):
        """Test normalize_filename with mp4 extension."""
        config, mock_session = mock_config

        # Mock media with UTC timestamp
        mock_media = MagicMock()
        mock_media.created_at = datetime(2023, 1, 1, 15, 30, tzinfo=UTC)
        mock_session.execute.return_value.scalar_one_or_none.return_value = mock_media

        # Test local time converts to UTC but preserves extension
        filename = "2023-01-01_at_10-30_id_12345.mp4"
        result = normalize_filename(filename, config=config)
        assert result == "2023-01-01_at_15-30_UTC_id_12345.mp4"

        # Test UTC time stays unchanged
        filename = "2023-01-01_at_15-30_UTC_id_12345.mp4"
        result = normalize_filename(filename, config=config)
        assert result == filename

    def test_normalize_filename_with_m3u8(self, mock_config):
        """Test normalize_filename with m3u8 extension."""
        config, mock_session = mock_config

        # Mock media with UTC timestamp
        mock_media = MagicMock()
        mock_media.created_at = datetime(2023, 1, 1, 15, 30, tzinfo=UTC)
        mock_session.execute.return_value.scalar_one_or_none.return_value = mock_media

        # Test local time converts to UTC but preserves extension
        filename = "2023-01-01_at_10-30_id_12345.m3u8"
        result = normalize_filename(filename, config=config)
        assert result == "2023-01-01_at_15-30_UTC_id_12345.m3u8"

        # Test UTC time stays unchanged
        filename = "2023-01-01_at_15-30_UTC_id_12345.m3u8"
        result = normalize_filename(filename, config=config)
        assert result == filename

    def test_normalize_filename_with_ts(self, mock_config):
        """Test normalize_filename with ts extension."""
        config, mock_session = mock_config

        # Mock media with UTC timestamp
        mock_media = MagicMock()
        mock_media.created_at = datetime(2023, 1, 1, 15, 30, tzinfo=UTC)
        mock_session.execute.return_value.scalar_one_or_none.return_value = mock_media

        # Test local time converts to UTC but preserves extension
        filename = "2023-01-01_at_10-30_id_12345.ts"
        result = normalize_filename(filename, config=config)
        assert result == "2023-01-01_at_15-30_UTC_id_12345.ts"

        # Test UTC time stays unchanged
        filename = "2023-01-01_at_15-30_UTC_id_12345.ts"
        result = normalize_filename(filename, config=config)
        assert result == filename

    def test_normalize_filename_hash_pattern(self, mock_config):
        """Test normalize_filename preserves hash patterns."""
        config, _ = mock_config
        # Hash patterns should be preserved exactly as is
        filename = "2023-01-01_at_10-30_hash_abcdef_id_12345.jpg"
        result = normalize_filename(filename, config=config)
        assert result == filename

        filename = "2023-01-01_at_10-30_hash1_abcdef_id_12345.jpg"
        result = normalize_filename(filename, config=config)
        assert result == filename

        filename = "2023-01-01_at_10-30_hash2_abcdef_id_12345.jpg"
        result = normalize_filename(filename, config=config)
        assert result == filename
