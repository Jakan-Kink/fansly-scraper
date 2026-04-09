"""Unit tests for helpers.logging module and metadata model behaviors."""

import gzip
import json
import logging
import os
import tempfile
import time
from datetime import UTC, datetime, timedelta
from enum import Enum
from pathlib import Path
from unittest.mock import patch

import pytest

from errors import StubNotImplementedError
from metadata.models import (
    Account,
    AccountMediaBundle,
    Attachment,
    ContentType,
    Conversation,
    FanslyObject,
    Group,
    Hashtag,
    Media,
    MediaLocation,
    MediaStoryState,
    Message,
    PinnedPost,
    Post,
    RelationshipMetadata,
    TimelineStats,
    _parse_timestamp,
    get_from_cache_by_type_name,
    get_store,
)
from tests.fixtures.utils.test_isolation import snowflake_id
from textio.logging import SizeAndTimeRotatingFileHandler


@pytest.fixture
def log_setup():
    """Set up test environment."""
    temp_dir = Path(tempfile.mkdtemp())
    log_filename = str(temp_dir / "test.log")
    logger = logging.getLogger("test_logger")
    logger.setLevel(logging.INFO)

    yield str(temp_dir), log_filename, logger

    # Cleanup
    handler_list = (
        logger.handlers.copy()
    )  # Make a copy to avoid modification during iteration
    for handler in handler_list:
        logger.removeHandler(handler)
        handler.close()

    # Remove test files
    try:
        for file_path in temp_dir.iterdir():
            file_path.unlink()
        temp_dir.rmdir()
    except OSError as e:
        print(f"Warning: Cleanup issue: {e}")


def test_size_based_rotation(log_setup):
    """Test log rotation based on file size."""
    temp_dir, log_filename, logger = log_setup

    # Create handler with small max size
    handler = SizeAndTimeRotatingFileHandler(
        log_filename,
        maxBytes=100,  # Small size to trigger rotation
        backupCount=3,
    )
    logger.addHandler(handler)

    # Write enough data to trigger multiple rotations
    for _i in range(5):
        logger.info("X" * 50)  # Each log should be > 50 bytes

    # Check that we have the expected number of files
    files = list(Path(temp_dir).iterdir())
    assert len(files) == 4  # Original + 3 backups
    assert Path(f"{log_filename}.1").exists()
    assert Path(f"{log_filename}.2").exists()
    assert Path(f"{log_filename}.3").exists()


def test_time_based_rotation(log_setup):
    """Test log rotation based on time."""
    temp_dir, log_filename, logger = log_setup

    # Create handler with short time interval
    handler = SizeAndTimeRotatingFileHandler(
        log_filename,
        when="s",  # Rotate every second
        interval=1,
        backupCount=2,
    )
    logger.addHandler(handler)

    with patch("textio.logging.datetime") as mock_datetime:
        # Mock the current time
        now = datetime.now(UTC)
        mock_datetime.now.return_value = now
        mock_datetime.side_effect = lambda *args, **kw: datetime(  # noqa: DTZ001 # Mock allows flexible datetime construction
            *args, **{"tzinfo": UTC, **kw}
        )

        # Set the initial rollover time
        handler.rolloverAt = (now + timedelta(seconds=1)).timestamp()

        # Write the first log
        logger.info("First log")

        # Simulate the passage of time to trigger the first rollover
        mock_datetime.now.return_value = now + timedelta(seconds=1.1)
        handler.doRollover()  # Manually trigger rollover
        logger.info("Second log")

        # Simulate the passage of time to trigger the second rollover
        mock_datetime.now.return_value = now + timedelta(seconds=2.2)
        handler.doRollover()  # Manually trigger rollover
        logger.info("Third log")

    # Check that we have the expected number of files
    files = list(Path(temp_dir).iterdir())
    assert len(files) == 3  # Original + 2 backups


def test_compression_gz(log_setup):
    """Test log compression with gzip."""
    _temp_dir, log_filename, logger = log_setup

    # Create handler with compression
    handler = SizeAndTimeRotatingFileHandler(
        log_filename, maxBytes=100, backupCount=2, compression="gz"
    )
    formatter = logging.Formatter("%(message)s")
    handler.setFormatter(formatter)
    logger.addHandler(handler)

    # Write enough data to trigger rotation
    logger.info("X" * 200)
    handler.flush()

    # Write more data to trigger another rotation
    logger.info("Y" * 100)
    handler.flush()

    # Check that the rotated file is compressed
    compressed_file = f"{log_filename}.1.gz"
    assert Path(compressed_file).exists()

    # Verify the content is readable
    with gzip.open(compressed_file, "rt") as f:
        content = f.read()
        assert ("X" * 200) in content


def test_utc_time_handling(log_setup):
    """Test UTC time handling in rotation."""
    _temp_dir, log_filename, logger = log_setup

    handler = SizeAndTimeRotatingFileHandler(
        log_filename, when="h", interval=1, utc=True, backupCount=1
    )
    logger.addHandler(handler)

    with patch("textio.logging.datetime") as mock_datetime:
        # Mock the current UTC time
        now = datetime.now(UTC)
        mock_datetime.now.return_value = now
        mock_datetime.side_effect = lambda *args, **kwargs: datetime(*args, **kwargs)  # noqa: DTZ001, PLW0108 # Mock passthrough

        # Set initial rollover time to 1 minute from now
        handler.rolloverAt = (now + timedelta(minutes=1)).timestamp()

        # Write the first log
        logger.info("Test log")

        # Simulate the passage of time to trigger rotation
        mock_datetime.now.return_value = now + timedelta(minutes=1, seconds=1)
        handler.doRollover()  # Manually trigger rollover
        logger.info("Another test")

    # Check that rotation occurred
    assert Path(f"{log_filename}.1").exists()


def test_invalid_compression(log_setup):
    """Test handling of invalid compression type."""
    _temp_dir, log_filename, _logger = log_setup

    with pytest.raises(ValueError, match="invalid"):
        SizeAndTimeRotatingFileHandler(log_filename, compression="invalid")


def test_rollover_on_init(log_setup):
    """Test file rollover check on initialization."""
    _temp_dir, log_filename, logger = log_setup

    # Create an initial log file
    with Path(log_filename).open("w") as f:
        f.write("X" * 1000)

    # Set old modification time
    old_time = time.time() - 7200  # 2 hours ago
    os.utime(log_filename, (old_time, old_time))

    # Create handler with size and time thresholds
    handler = SizeAndTimeRotatingFileHandler(
        log_filename, maxBytes=500, when="h", interval=1, backupCount=1
    )
    logger.addHandler(handler)

    # Check that the file was rotated on initialization
    assert Path(f"{log_filename}.1").exists()

    # Original file should be empty or very small
    assert Path(log_filename).stat().st_size < 100


def test_multiple_handlers(log_setup):
    """Test multiple handlers on the same file."""
    temp_dir, log_filename, logger = log_setup

    # Create two handlers with different settings
    handler1 = SizeAndTimeRotatingFileHandler(log_filename, maxBytes=100, backupCount=1)
    handler2 = SizeAndTimeRotatingFileHandler(
        log_filename, when="s", interval=1, backupCount=1
    )
    logger.addHandler(handler1)
    logger.addHandler(handler2)

    with patch("textio.logging.datetime") as mock_datetime:
        # Mock the current time
        now = datetime.now(UTC)
        mock_datetime.now.return_value = now
        mock_datetime.side_effect = lambda *args, **kw: datetime(  # noqa: DTZ001 # Mock allows flexible datetime construction
            *args, **{"tzinfo": UTC, **kw}
        )

        # Write logs with simulated time and size triggers
        logger.info("X" * 150)  # Should trigger size rotation
        mock_datetime.now.return_value = now + timedelta(seconds=1.1)
        logger.info("Test")  # Should trigger time rotation

    # Check files
    files = list(Path(temp_dir).iterdir())
    assert len(files) >= 2  # Should have at least original + backup


# ── Models: Snowflake, identity, stubs, enums, timestamps ────────────────


class TestModelBehaviors:
    def test_snowflake_validation(self):
        with pytest.raises(Exception):
            Media(id="bad", accountId=snowflake_id())
        with pytest.raises(Exception):
            Media(id=999, accountId=snowflake_id())
        m = Media(id=str(snowflake_id()), accountId=snowflake_id())
        assert isinstance(m.id, int)

    def test_get_store_uninitialized(self):
        orig = FanslyObject._store
        try:
            FanslyObject._store = None
            with pytest.raises(RuntimeError, match="EntityStore"):
                get_store()
        finally:
            FanslyObject._store = orig

    def test_identity_comparison_and_hash(self):
        sid = snowflake_id()
        a1 = Account(id=sid, username="a")
        a2 = Account(id=sid, username="b")
        assert a1 == a2
        assert a1 != Account(id=snowflake_id(), username="a")
        assert a1 != "not an account"

        h = Hashtag(value="unsaved")
        assert hash(h) == hash(id(h))  # Unsaved → identity hash
        assert hash(a1) == hash((type(a1).__name__, a1.id))

    @pytest.mark.asyncio
    async def test_create_stub(self, entity_store):
        """create_stub with fresh entity_store ensures clean identity map."""
        stub = Post.create_stub(snowflake_id(), accountId=snowflake_id())
        assert stub._is_new is True
        assert stub.id is not None
        with pytest.raises(StubNotImplementedError):
            Post.create_stub(snowflake_id())
        with pytest.raises(StubNotImplementedError):
            Account.create_stub(snowflake_id())

    def test_update_fields(self):
        a = Account(id=snowflake_id(), username="before")
        assert FanslyObject.update_fields(a, {"username": "after"}) is True
        assert a.username == "after"
        assert FanslyObject.update_fields(a, {"username": "after"}) is False
        FanslyObject.update_fields(a, {"createdAt": 1700000000})
        assert isinstance(a.createdAt, datetime)

    def test_normalize_cdn_url(self):
        assert (
            Media.normalize_cdn_url("https://cdn.fansly.com/x.jpg?t=1")
            == "https://cdn.fansly.com/x.jpg"
        )
        assert (
            Media.normalize_cdn_url("https://cdn.fansly.com/x.jpg")
            == "https://cdn.fansly.com/x.jpg"
        )
        assert Media.normalize_cdn_url("") == ""
        assert Media.normalize_cdn_url(None) is None

    def test_content_type_enum(self):
        att = Attachment(
            id=snowflake_id(),
            postId=snowflake_id(),
            contentId=snowflake_id(),
            contentType="ACCOUNT_MEDIA",
            pos=0,
        )
        assert att.contentType == ContentType.ACCOUNT_MEDIA
        assert att.to_db_dict()["contentType"] == "ACCOUNT_MEDIA"
        assert att.is_account_media is True
        assert att.is_story is False

    def test_pinned_post_timestamp(self):
        pp = PinnedPost(
            postId=snowflake_id(),
            accountId=snowflake_id(),
            pos=0,
            createdAt=1700000000000,
        )
        assert isinstance(pp.createdAt, datetime)

    def test_derived_pks(self):
        aid = snowflake_id()
        assert TimelineStats(accountId=aid, imageCount=0).id == aid
        assert MediaStoryState(accountId=aid, status=0).id == aid

    def test_derived_pk_missing_account_id(self):
        """Lines 1204-1206, 1235-1237: _set_id_from_pk when dict has no accountId."""
        ts = TimelineStats._set_id_from_pk.__func__(TimelineStats, {"imageCount": 5})
        assert "id" not in ts

        mss = MediaStoryState._set_id_from_pk.__func__(MediaStoryState, {"status": 1})
        assert "id" not in mss

    def test_bundle_default_created_at(self):
        b = AccountMediaBundle.model_validate(
            {"id": snowflake_id(), "accountId": snowflake_id(), "deleted": False}
        )
        assert isinstance(b.createdAt, datetime)

    def test_group_last_message_dict(self):
        mid = snowflake_id()
        g = Group.model_validate(
            {
                "id": snowflake_id(),
                "createdBy": snowflake_id(),
                "lastMessage": {"id": mid},
                "createdAt": 1700000000,
            }
        )
        assert g.lastMessageId == mid

    def test_media_get_file_name(self):
        m = Media(
            id=snowflake_id(),
            accountId=snowflake_id(),
            createdAt=datetime(2024, 1, 15, 12, 0, tzinfo=UTC),
            download_url="https://cdn.fansly.com/x.mp4?t=1",
        )
        name = m.get_file_name()
        assert "2024-01-15" in name
        assert "_id_" in name
        assert name.endswith(".mp4")
        assert "preview_id" in m.get_file_name(for_preview=True)

    def test_video_metadata_extraction(self):
        m = Media.model_validate(
            {
                "id": snowflake_id(),
                "accountId": snowflake_id(),
                "mimetype": "video/mp4",
                "type": 2,
                "metadata": json.dumps(
                    {"original": {"width": 1920, "height": 1080}, "duration": 60.0}
                ),
            }
        )
        assert m.width == 1920
        assert m.duration == 60.0

    @pytest.mark.asyncio
    async def test_save_without_store(self):
        orig = FanslyObject._store
        try:
            FanslyObject._store = None
            with pytest.raises(RuntimeError):
                await Account(id=snowflake_id(), username="no_store").save()
        finally:
            FanslyObject._store = orig

    @pytest.mark.asyncio
    async def test_save_clean_object_noop(self, entity_store):
        a = Account(id=snowflake_id(), username="clean")
        await entity_store.save(a)
        a.mark_clean()
        a._is_new = False
        await a.save()  # No-op, should not error


# ── _parse_timestamp + _coerce_api_types edge cases ────────────────────


class TestTimestampAndCoercion:
    """Covers 411 (None/datetime passthrough), 416-418 (string ISO timestamp),
    534 (non-dict early return), 543 (surrogate encoding failure)."""

    def test_parse_timestamp_none_and_datetime_passthrough(self):
        assert _parse_timestamp(None) is None
        now = datetime.now(UTC)
        assert _parse_timestamp(now) is now

    def test_parse_timestamp_iso_string(self):
        result = _parse_timestamp("2024-01-15T12:00:00Z")
        assert isinstance(result, datetime)
        assert result.year == 2024
        assert result.month == 1
        assert result.day == 15

        result2 = _parse_timestamp("2024-06-15T00:00:00+00:00")
        assert isinstance(result2, datetime)

    def test_parse_timestamp_unrecognized_type_passthrough(self):
        sentinel = [1, 2, 3]
        assert _parse_timestamp(sentinel) is sentinel
        assert _parse_timestamp({"key": "val"}) == {"key": "val"}
        assert _parse_timestamp(object) is object

    def test_coerce_api_types_non_dict(self):
        result = FanslyObject._coerce_api_types.__func__(FanslyObject, [1, 2, 3])
        assert result == [1, 2, 3]

    def test_coerce_surrogate_chars(self):
        bad_str = "hello \ud835 world"
        data = {"id": snowflake_id(), "accountId": snowflake_id(), "content": bad_str}
        result = FanslyObject._coerce_api_types.__func__(FanslyObject, data)
        assert "\ud835" not in result["content"]
        assert "\ufffd" in result["content"]


# ── _process_nested_cache_lookups all branches ─────────────────────────


class TestProcessNestedCacheLookups:
    """Covers lines 666, 700, 722, 731-672, 734, 746, 753."""

    def test_store_is_none_returns_data_unchanged(self):
        orig_store = FanslyObject._store
        try:
            FanslyObject._store = None
            data = {"id": snowflake_id(), "accountId": snowflake_id()}
            result = Account._process_nested_cache_lookups(data)
            assert result is data
        finally:
            FanslyObject._store = orig_store

    @pytest.mark.asyncio
    async def test_relationship_value_none_skipped(self, entity_store):
        aid = snowflake_id()
        data = {
            "id": aid,
            "username": "test_none_rel",
            "avatar": None,
            "banner": None,
        }
        acct = Account.model_validate(data)
        assert acct.avatar is None
        assert acct.banner is None

    @pytest.mark.asyncio
    async def test_list_items_int_cached_and_not_int_or_dict(self, entity_store):
        aid = snowflake_id()
        acct = Account(id=aid, username="list_test")
        await entity_store.save(acct)

        cached_variant = Media(id=snowflake_id(), accountId=aid)
        await entity_store.save(cached_variant)

        preresolved = Media(id=snowflake_id(), accountId=aid)
        await entity_store.save(preresolved)

        data = {
            "id": snowflake_id(),
            "accountId": aid,
            "variants": [
                cached_variant.id,
                preresolved,
            ],
        }
        result = Media._process_nested_cache_lookups(data)
        assert cached_variant in result["variants"]
        assert preresolved in result["variants"]

    @pytest.mark.asyncio
    async def test_scalar_int_cached_with_alias(self, entity_store):
        aid = snowflake_id()
        acct = Account(id=aid, username="alias_test")
        await entity_store.save(acct)

        uncached_id = snowflake_id()
        data = {"id": snowflake_id(), "accountId": aid, "account": uncached_id}
        result = Media._process_nested_cache_lookups(data)
        assert result.get("account") == uncached_id

        data2 = {"id": snowflake_id(), "accountId": aid, "account": aid}
        result2 = Media._process_nested_cache_lookups(data2)
        assert result2["account"] is acct

    @pytest.mark.asyncio
    async def test_scalar_dict_with_id_cached_and_not_cached(self, entity_store):
        aid = snowflake_id()
        cached_acct = Account(id=aid, username="cached_dict_test")
        await entity_store.save(cached_acct)

        data = {
            "id": snowflake_id(),
            "accountId": aid,
            "account": {"id": aid, "username": "ignored_uses_cached"},
        }
        result = Media._process_nested_cache_lookups(data)
        assert result["account"] is cached_acct

        uncached_id = snowflake_id()
        data2 = {
            "id": snowflake_id(),
            "accountId": aid,
            "account": {"id": uncached_id, "username": "enriched"},
        }
        result2 = Media._process_nested_cache_lookups(data2)
        assert isinstance(result2["account"], dict)
        assert result2["account"]["id"] == uncached_id

    @pytest.mark.asyncio
    async def test_scalar_dict_without_id_enriched(self, entity_store):
        aid = snowflake_id()
        data = {
            "id": aid,
            "username": "stats_test",
            "timelineStats": {"accountId": aid, "imageCount": 42},
        }
        result = Account._process_nested_cache_lookups(data)
        assert isinstance(result["timelineStats"], dict)
        assert result["timelineStats"]["imageCount"] == 42

    @pytest.mark.asyncio
    async def test_belongs_to_fk_column_cache_resolution(self, entity_store):
        aid = snowflake_id()
        acct = Account(id=aid, username="fk_resolve_test")
        await entity_store.save(acct)

        data_cached = {"id": snowflake_id(), "accountId": aid}
        result = Media._process_nested_cache_lookups(data_cached)
        assert result.get("account") is acct

        uncached_aid = snowflake_id()
        data_uncached = {"id": snowflake_id(), "accountId": uncached_aid}
        result2 = Media._process_nested_cache_lookups(data_uncached)
        assert "account" not in result2

    @pytest.mark.asyncio
    async def test_alias_key_removal_for_list(self, entity_store):
        aid = snowflake_id()
        acct = Account(id=aid, username="alias_list")
        await entity_store.save(acct)

        mention_id = snowflake_id()
        data = {
            "id": snowflake_id(),
            "accountId": aid,
            "accountMentions": [
                {
                    "id": mention_id,
                    "postId": snowflake_id(),
                    "handle": "test",
                    "accountId": aid,
                },
            ],
        }
        result = Post._process_nested_cache_lookups(data)
        assert "mentions" in result
        assert "accountMentions" not in result

    @pytest.mark.asyncio
    async def test_scalar_alias_removal_all_paths(self, entity_store):
        from pydantic import Field as PydanticField

        class _AliasedParent(FanslyObject):
            __table_name__: str = ""
            __tracked_fields__ = set()
            __relationships__ = {
                "related": RelationshipMetadata(
                    target_field="relatedId",
                    is_list=False,
                    query_field="related",
                    inverse_type="Account",
                    query_strategy="direct_field",
                    fk_column="relatedId",
                ),
            }
            id: int | None = None
            relatedId: int | None = None  # noqa: N815
            related: Account | None = PydanticField(default=None, alias="altRelated")

        _AliasedParent.model_rebuild()

        aid = snowflake_id()
        acct = Account(id=aid, username="alias_scalar")
        await entity_store.save(acct)

        data_int_cached = {"id": snowflake_id(), "altRelated": aid}
        result1 = _AliasedParent._process_nested_cache_lookups(data_int_cached)
        assert result1["related"] is acct
        assert "altRelated" not in result1

        data_dict_cached = {
            "id": snowflake_id(),
            "altRelated": {"id": aid, "username": "x"},
        }
        result2 = _AliasedParent._process_nested_cache_lookups(data_dict_cached)
        assert result2["related"] is acct
        assert "altRelated" not in result2

        data_dict_no_id = {
            "id": snowflake_id(),
            "altRelated": {"username": "no_id_dict"},
        }
        result3 = _AliasedParent._process_nested_cache_lookups(data_dict_no_id)
        assert isinstance(result3["related"], dict)
        assert "altRelated" not in result3


# ── to_db_dict, save, _get_id, mark_dirty, update_fields, normalize ────


class TestSerializationAndHelpers:
    def test_mark_dirty_clears_snapshot(self):
        a = Account(id=snowflake_id(), username="mark_dirty")
        a.mark_clean()
        assert not a.is_dirty()
        a.mark_dirty()
        assert a.is_dirty()

    def test_get_id_from_dict(self):
        assert FanslyObject._get_id({"id": 42}) == 42
        assert FanslyObject._get_id({"no_id": True}) is None
        assert (
            FanslyObject._get_id(Account(id=snowflake_id(), username="x")) is not None
        )

    def test_to_db_dict_non_content_type_enum(self):
        class TestEnum(Enum):
            FOO = 42

        att = Attachment(
            id=snowflake_id(),
            postId=snowflake_id(),
            contentId=snowflake_id(),
            contentType=ContentType.ACCOUNT_MEDIA,
            pos=0,
        )
        db = att.to_db_dict()
        assert db["contentType"] == "ACCOUNT_MEDIA"

        object.__setattr__(att, "pos", TestEnum.FOO)
        db2 = att.to_db_dict()
        assert db2["pos"] == 42

    @pytest.mark.asyncio
    async def test_save_calls_store_and_marks_clean(self, entity_store):
        a = Account(id=snowflake_id(), username="save_test")
        a._is_new = True
        await a.save()
        assert not a.is_dirty()
        assert not a._is_new

    def test_update_fields_with_exclude(self):
        a = Account(id=snowflake_id(), username="excl_test", displayName="original")
        updated = FanslyObject.update_fields(
            a,
            {"username": "new", "displayName": "new_dn"},
            exclude={"displayName"},
        )
        assert updated is True
        assert a.username == "new"
        assert a.displayName == "original"

    def test_update_fields_timestamp_not_datetime_field(self):
        a = Account(id=snowflake_id(), username="ts_skip", flags=0)
        FanslyObject.update_fields(a, {"flags": 42})
        assert a.flags == 42

    def test_normalize_cdn_url_exception(self):
        result = FanslyObject.normalize_cdn_url(12345)  # type: ignore[arg-type]
        assert result == 12345

    def test_get_from_cache_by_type_name_unknown(self, entity_store):
        result = get_from_cache_by_type_name(entity_store, "NonExistentType", 123)
        assert result is None

    def test_get_from_cache_by_type_name_known(self, entity_store):
        aid = snowflake_id()
        acct = Account(id=aid, username="registry_test")
        entity_store.cache_instance(acct)
        result = get_from_cache_by_type_name(entity_store, "Account", aid)
        assert result is acct


# ── Model-specific validator non-dict early returns ────────────────────


class TestValidatorNonDictPaths:
    def test_media_location_non_dict(self):
        result = MediaLocation._normalize_location.__func__(MediaLocation, "not_a_dict")
        assert result == "not_a_dict"

    def test_media_extract_video_non_dict(self):
        result = Media._extract_video_dimensions.__func__(Media, 42)
        assert result == 42

    def test_bundle_prepare_non_dict(self):
        result = AccountMediaBundle._prepare_bundle_data.__func__(
            AccountMediaBundle, "string"
        )
        assert result == "string"

    def test_attachment_coerce_content_type_non_dict(self):
        result = Attachment._coerce_content_type.__func__(Attachment, 99)
        assert result == 99

    def test_post_prepare_non_dict(self):
        result = Post._prepare_post_data.__func__(Post, [])
        assert result == []

    def test_message_prepare_non_dict(self):
        result = Message._prepare_message_data.__func__(Message, True)
        assert result is True

    def test_group_resolve_last_message_non_dict(self):
        result = Group._resolve_last_message.__func__(Group, "raw")
        assert result == "raw"


# ── Media properties + Conversation ────────────────────────────────────


class TestMediaPropertiesAndConversation:
    def test_created_at_timestamp_both_paths(self):
        m_none = Media(id=snowflake_id(), accountId=snowflake_id(), createdAt=None)
        assert m_none.created_at_timestamp == 0.0

        m_set = Media(
            id=snowflake_id(),
            accountId=snowflake_id(),
            createdAt=datetime(2024, 6, 15, 12, 0, tzinfo=UTC),
        )
        assert m_set.created_at_timestamp > 0
        assert m_set.created_at_timestamp == m_set.createdAt.timestamp()

    def test_get_file_name_no_extension_no_url(self):
        m = Media(id=snowflake_id(), accountId=snowflake_id())
        name = m.get_file_name()
        assert "unknown" in name
        assert name.endswith(".None")

    def test_video_metadata_no_original_key(self):
        m = Media.model_validate(
            {
                "id": snowflake_id(),
                "accountId": snowflake_id(),
                "mimetype": "video/mp4",
                "type": 2,
                "metadata": json.dumps({"bitrate": 5000}),
            }
        )
        assert m.width is None
        assert m.duration is None

    def test_video_metadata_bad_json(self):
        m = Media.model_validate(
            {
                "id": snowflake_id(),
                "accountId": snowflake_id(),
                "mimetype": "video/mp4",
                "type": 2,
                "metadata": "not valid json {{{",
            }
        )
        assert m.width is None

    def test_video_metadata_not_video_mimetype(self):
        m = Media.model_validate(
            {
                "id": snowflake_id(),
                "accountId": snowflake_id(),
                "mimetype": "image/jpeg",
                "type": 1,
                "metadata": json.dumps({"original": {"width": 999}, "duration": 10}),
            }
        )
        assert m.width is None

    def test_aggregated_post_no_store(self):
        orig = FanslyObject._store
        try:
            FanslyObject._store = None
            att = Attachment(
                id=snowflake_id(),
                postId=snowflake_id(),
                contentId=snowflake_id(),
                contentType=ContentType.AGGREGATED_POSTS,
                pos=0,
            )
            assert att.aggregated_post is None
        finally:
            FanslyObject._store = orig

    def test_conversation_to_group_dict(self):
        gid = snowflake_id()
        aid = snowflake_id()
        mid = snowflake_id()

        conv = Conversation(groupId=gid, account_id=aid, lastMessageId=mid)
        d = conv.to_group_dict()
        assert d == {"id": gid, "createdBy": aid, "lastMessageId": mid}

        conv2 = Conversation(groupId=gid, account_id=aid)
        d2 = conv2.to_group_dict()
        assert d2 == {"id": gid, "createdBy": aid}
        assert "lastMessageId" not in d2
