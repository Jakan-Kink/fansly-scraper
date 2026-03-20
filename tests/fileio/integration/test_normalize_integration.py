"""Integration tests for normalize module."""

from datetime import UTC, datetime

import pytest

from fileio.normalize import normalize_filename
from metadata import Account, Media
from tests.fixtures.utils.test_isolation import snowflake_id


class TestNormalizeFilenameIntegration:
    """Integration tests for normalize_filename with EntityStore."""

    @pytest.mark.asyncio
    async def test_normalize_filename_with_database(self, entity_store, config):
        """Test normalize_filename with database match."""
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

        filename = f"2023-01-01_at_15-30_UTC_id_{media_id}.jpg"
        result = await normalize_filename(filename, config=config)
        assert result == filename

    @pytest.mark.asyncio
    async def test_normalize_filename_with_database_no_match(
        self, entity_store, config
    ):
        """Test normalize_filename without database match."""
        media_id = snowflake_id()
        filename = f"2023-01-01_at_10-30_id_{media_id}.jpg"
        result = await normalize_filename(filename, config=config)
        assert result == f"2023-01-01_at_15-30_UTC_id_{media_id}.jpg"

    @pytest.mark.parametrize("ext", ["mp4", "m3u8", "ts"])
    @pytest.mark.asyncio
    async def test_normalize_filename_with_extensions(self, entity_store, config, ext):
        """Test normalize_filename preserves different extensions."""
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

        filename = f"2023-01-01_at_10-30_id_{media_id}.{ext}"
        result = await normalize_filename(filename, config=config)
        assert result == f"2023-01-01_at_15-30_UTC_id_{media_id}.{ext}"

        filename = f"2023-01-01_at_15-30_UTC_id_{media_id}.{ext}"
        result = await normalize_filename(filename, config=config)
        assert result == filename

    @pytest.mark.asyncio
    async def test_normalize_filename_hash_pattern(self, entity_store, config):
        """Test normalize_filename preserves hash patterns."""
        for hash_type in ["hash", "hash1", "hash2"]:
            filename = f"2023-01-01_at_10-30_{hash_type}_abcdef_id_12345.jpg"
            result = await normalize_filename(filename, config=config)
            assert result == filename
