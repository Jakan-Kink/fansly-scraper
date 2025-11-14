"""Integration tests for normalize module."""

from datetime import UTC, datetime

import pytest

from fileio.normalize import normalize_filename
from tests.fixtures.metadata import MediaFactory


class TestNormalizeFilenameIntegration:
    """Integration tests for normalize_filename."""

    def test_normalize_filename_with_database(self, uuid_test_db_factory):
        """Test normalize_filename with database match."""
        config = uuid_test_db_factory

        # Create real media object and insert into database
        media = MediaFactory(
            id=12345,
            accountId=555000000000000000,
            createdAt=datetime(2023, 1, 1, 15, 30, tzinfo=UTC),
        )

        # Insert into database
        with config._database.session_scope() as session:
            session.add(media)
            session.commit()

        # Test local time converts to UTC with database match
        filename = "2023-01-01_at_10-30_id_12345.jpg"
        result = normalize_filename(filename, config=config)
        assert result == "2023-01-01_at_15-30_UTC_id_12345.jpg"

        # Test UTC time stays unchanged
        filename = "2023-01-01_at_15-30_UTC_id_12345.jpg"
        result = normalize_filename(filename, config=config)
        assert result == filename

    def test_normalize_filename_with_database_no_match(self, uuid_test_db_factory):
        """Test normalize_filename without database match."""
        config = uuid_test_db_factory

        # No media in database, so query returns None
        filename = "2023-01-01_at_10-30_id_12345.jpg"
        result = normalize_filename(filename, config=config)
        # Without database match, time should stay unchanged
        assert result == filename

    def test_normalize_filename_with_mp4(self, uuid_test_db_factory):
        """Test normalize_filename with mp4 extension."""
        config = uuid_test_db_factory

        # Create real media object and insert into database
        media = MediaFactory(
            id=12345,
            accountId=555000000000000000,
            createdAt=datetime(2023, 1, 1, 15, 30, tzinfo=UTC),
        )

        # Insert into database
        with config._database.session_scope() as session:
            session.add(media)
            session.commit()

        # Test local time converts to UTC but preserves extension
        filename = "2023-01-01_at_10-30_id_12345.mp4"
        result = normalize_filename(filename, config=config)
        assert result == "2023-01-01_at_15-30_UTC_id_12345.mp4"

        # Test UTC time stays unchanged
        filename = "2023-01-01_at_15-30_UTC_id_12345.mp4"
        result = normalize_filename(filename, config=config)
        assert result == filename

    def test_normalize_filename_with_m3u8(self, uuid_test_db_factory):
        """Test normalize_filename with m3u8 extension."""
        config = uuid_test_db_factory

        # Create real media object and insert into database
        media = MediaFactory(
            id=12345,
            accountId=555000000000000000,
            createdAt=datetime(2023, 1, 1, 15, 30, tzinfo=UTC),
        )

        # Insert into database
        with config._database.session_scope() as session:
            session.add(media)
            session.commit()

        # Test local time converts to UTC but preserves extension
        filename = "2023-01-01_at_10-30_id_12345.m3u8"
        result = normalize_filename(filename, config=config)
        assert result == "2023-01-01_at_15-30_UTC_id_12345.m3u8"

        # Test UTC time stays unchanged
        filename = "2023-01-01_at_15-30_UTC_id_12345.m3u8"
        result = normalize_filename(filename, config=config)
        assert result == filename

    def test_normalize_filename_with_ts(self, uuid_test_db_factory):
        """Test normalize_filename with ts extension."""
        config = uuid_test_db_factory

        # Create real media object and insert into database
        media = MediaFactory(
            id=12345,
            accountId=555000000000000000,
            createdAt=datetime(2023, 1, 1, 15, 30, tzinfo=UTC),
        )

        # Insert into database
        with config._database.session_scope() as session:
            session.add(media)
            session.commit()

        # Test local time converts to UTC but preserves extension
        filename = "2023-01-01_at_10-30_id_12345.ts"
        result = normalize_filename(filename, config=config)
        assert result == "2023-01-01_at_15-30_UTC_id_12345.ts"

        # Test UTC time stays unchanged
        filename = "2023-01-01_at_15-30_UTC_id_12345.ts"
        result = normalize_filename(filename, config=config)
        assert result == filename

    def test_normalize_filename_hash_pattern(self, uuid_test_db_factory):
        """Test normalize_filename preserves hash patterns."""
        config = uuid_test_db_factory
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
