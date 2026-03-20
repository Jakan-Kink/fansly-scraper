"""Unit tests for the normalize module."""

from datetime import UTC, datetime

import pytest

from fileio.normalize import get_id_from_filename, normalize_filename
from metadata import Account, Media
from tests.fixtures.utils.test_isolation import snowflake_id


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


class TestNormalizeFilename:
    """Tests for the normalize_filename function."""

    @pytest.mark.asyncio
    async def test_normalize_filename_with_database_match(self, entity_store, config):
        """Test normalize_filename with database match using real database."""
        store = entity_store

        acct_id = snowflake_id()
        media_id = snowflake_id()

        account = Account(id=acct_id, username="test_user")
        await store.save(account)

        media = Media(
            id=media_id,
            accountId=acct_id,
            createdAt=datetime(2023, 1, 1, 15, 30, tzinfo=UTC),
        )
        await store.save(media)

        filename = f"2023-01-01_at_10-30_id_{media_id}.jpg"
        result = await normalize_filename(filename, config=config)
        assert result == f"2023-01-01_at_15-30_UTC_id_{media_id}.jpg"

    @pytest.mark.asyncio
    async def test_normalize_filename_no_database_match(self, entity_store, config):
        """Test normalize_filename without database match.

        Even without database match, if config is provided, the function
        converts local time (assumed EST/EDT) to UTC.
        """
        media_id = snowflake_id()
        filename = f"2023-01-01_at_10-30_id_{media_id}.jpg"
        result = await normalize_filename(filename, config=config)
        assert result == f"2023-01-01_at_15-30_UTC_id_{media_id}.jpg"

    @pytest.mark.asyncio
    async def test_normalize_filename_different_extensions(self, entity_store, config):
        """Test normalize_filename with different extensions."""
        store = entity_store

        acct_id = snowflake_id()
        media_id = snowflake_id()

        account = Account(id=acct_id, username="test_user")
        await store.save(account)

        media = Media(
            id=media_id,
            accountId=acct_id,
            createdAt=datetime(2023, 1, 1, 15, 30, tzinfo=UTC),
        )
        await store.save(media)

        for ext in ["jpg", "mp4", "m3u8", "ts"]:
            filename = f"2023-01-01_at_10-30_id_{media_id}.{ext}"
            result = await normalize_filename(filename, config=config)
            assert result == f"2023-01-01_at_15-30_UTC_id_{media_id}.{ext}"

            filename = f"2023-01-01_at_15-30_UTC_id_{media_id}.{ext}"
            result = await normalize_filename(filename, config=config)
            assert result == filename

    @pytest.mark.asyncio
    async def test_normalize_filename_no_id(self, entity_store, config):
        """Test normalize_filename without ID pattern."""
        filename = "2023-01-01_at_12-30.jpg"
        assert await normalize_filename(filename, config=config) == filename

        filename = "random_file_without_id.mp4"
        assert await normalize_filename(filename, config=config) == filename

        filename = ""
        assert await normalize_filename(filename, config=config) == filename

    @pytest.mark.asyncio
    async def test_normalize_filename_malformed_timestamp(self, entity_store, config):
        """Test normalize_filename with malformed timestamp."""
        filename = "not_a_timestamp_id_12345.jpg"
        result = await normalize_filename(filename, config=config)
        assert result == filename

    @pytest.mark.asyncio
    async def test_normalize_filename_hash_pattern(self, entity_store, config):
        """Test normalize_filename with hash patterns."""
        filename = "2023-01-01_at_12-30_hash_abc123_id_123456.jpg"
        assert await normalize_filename(filename, config=config) == filename

        filename = "2023-01-01_at_12-30_hash1_abc123_id_123456.jpg"
        assert await normalize_filename(filename, config=config) == filename

        filename = "2023-01-01_at_12-30_hash2_abc123_id_123456.jpg"
        assert await normalize_filename(filename, config=config) == filename

    @pytest.mark.asyncio
    async def test_normalize_filename_invalid_date_format(self, entity_store, config):
        """Test normalize_filename with invalid date format."""
        filename = "2023-13-45_at_99-99_id_12345.jpg"
        result = await normalize_filename(filename, config=config)
        assert result == filename

    @pytest.mark.asyncio
    async def test_normalize_filename_with_timezone_no_db_match(
        self, entity_store, config
    ):
        """Test normalize_filename with timezone but no database match."""
        filename = "2023-01-01_at_10-30_EST_id_99999.jpg"
        result = await normalize_filename(filename, config=config)
        assert result == filename
