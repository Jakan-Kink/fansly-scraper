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

    @pytest.mark.parametrize(
        "db_match",
        [True, False],
        ids=["database_match", "no_database_match"],
    )
    @pytest.mark.asyncio
    async def test_normalize_filename_database_lookup(
        self, entity_store, config, db_match
    ):
        """Test normalize_filename with and without a database match.

        With a match, the Media row's createdAt (15:30 UTC) drives the rename.
        Without a match, if config is provided, the function converts local
        time (assumed EST/EDT) to UTC — which yields the same 15-30_UTC name
        for a 10-30 EST timestamp.
        """
        store = entity_store

        media_id = snowflake_id()

        if db_match:
            acct_id = snowflake_id()
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

    @pytest.mark.parametrize(
        "filename",
        [
            "2023-01-01_at_12-30.jpg",
            "random_file_without_id.mp4",
            "",
            "not_a_timestamp_id_12345.jpg",
            "2023-01-01_at_12-30_hash_abc123_id_123456.jpg",
            "2023-01-01_at_12-30_hash1_abc123_id_123456.jpg",
            "2023-01-01_at_12-30_hash2_abc123_id_123456.jpg",
            "2023-13-45_at_99-99_id_12345.jpg",
        ],
        ids=[
            "no_id_with_timestamp",
            "no_id_random",
            "empty",
            "malformed_timestamp",
            "hash_pattern",
            "hash1_pattern",
            "hash2_pattern",
            "invalid_date_format",
        ],
    )
    @pytest.mark.asyncio
    async def test_normalize_filename_unmodifiable_no_db(self, filename):
        """Inputs that short-circuit before any DB lookup are returned as-is.

        These shapes hit early returns (hash pattern, no id_ marker, no
        timestamp match, or a ValueError from an invalid date) before any use
        of ``config``, so no config object and no UUID database is needed.
        """
        assert await normalize_filename(filename, config=None) == filename

    @pytest.mark.parametrize(
        "filename",
        ["2023-01-01_at_10-30_EST_id_99999.jpg"],
        ids=["timezone_no_db_match"],
    )
    @pytest.mark.asyncio
    async def test_normalize_filename_unmodifiable_db_miss(
        self, entity_store, config, filename
    ):
        """A non-UTC timezone with an id reaches the DB lookup but finds no row.

        This shape passes id/timestamp parsing and queries store.get(Media,
        99999); with no matching row the filename is returned unchanged. It
        genuinely needs the database fixture.
        """
        assert await normalize_filename(filename, config=config) == filename

    @pytest.mark.parametrize(
        ("name_template", "membership", "expected_template"),
        [
            (
                "2026-01-02_at_03-04_UTC_id_{mid}.jpg",
                "member",
                "2026-01-02_at_03-04_UTC_preview_id_{mid}.jpg",
            ),
            (
                "2026-01-02_at_03-04_UTC_id_{mid}.jpg",
                "other",
                "2026-01-02_at_03-04_UTC_id_{mid}.jpg",
            ),
            (
                "2026-01-02_at_03-04_UTC_preview_id_{mid}.jpg",
                "member",
                "2026-01-02_at_03-04_UTC_preview_id_{mid}.jpg",
            ),
            (
                "anything_hash2_deadbeef.jpg",
                "member",
                "anything_hash2_deadbeef.jpg",
            ),
        ],
        ids=[
            "rewrites_id_to_preview_for_known_preview",
            "leaves_non_preview_untouched",
            "leaves_already_preview_untouched",
            "skips_hash_pattern",
        ],
    )
    @pytest.mark.asyncio
    async def test_normalize_preview_marker_membership(
        self, name_template, membership, expected_template
    ):
        """preview_ids membership branches of normalize_filename.

        Bug-era preview files are saved with an already-UTC name and the plain
        `id_` marker; correction is membership-driven. A member id gets the
        preview_ marker; a non-member id and an already-preview name are left
        unchanged (no double prefix); hash-pattern filenames short-circuit
        before the membership check. All four shapes hit early returns (hash
        pattern or the already-UTC return) before any config/DB use, so no
        config object and no UUID database is needed.
        """
        mid = snowflake_id()
        preview_ids = {mid} if membership == "member" else {snowflake_id()}
        name = name_template.format(mid=mid)
        result = await normalize_filename(name, config=None, preview_ids=preview_ids)
        assert result == expected_template.format(mid=mid)
