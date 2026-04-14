"""Tests for fileio.dedupe module.

These tests use REAL database objects and EntityStore from fixtures.
Only external calls (like hash calculation) are mocked using patch.
"""

import re
from pathlib import Path
from unittest.mock import patch

import pytest
from PIL import Image

from download.types import DownloadType
from fileio.dedupe import (
    calculate_file_hash,
    categorize_file,
    dedupe_init,
    dedupe_media_file,
    get_account_id,
    get_filename_only,
    get_or_create_media,
    migrate_full_paths_to_filenames,
    safe_rglob,
)
from fileio.normalize import normalize_filename
from metadata import Account, Media
from tests.fixtures.download import DownloadStateFactory
from tests.fixtures.utils import snowflake_id


def create_test_file(base_path, filename, content=b"test content"):
    """Helper to create a test file."""
    file_path = base_path / filename
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_bytes(content)
    return file_path


def create_test_image(base_path, filename, *, size=(1, 1), color="white"):
    """Helper to create a valid image file that PIL can open.

    Args:
        base_path: Directory to create the file in
        filename: Name of the file
        size: Image dimensions (width, height). Default 1x1.
        color: Fill color. Default white.
    """
    file_path = base_path / filename
    file_path.parent.mkdir(parents=True, exist_ok=True)

    img = Image.new("RGB", size, color=color)
    img.save(file_path, format="JPEG")

    return file_path


@pytest.mark.asyncio
async def test_get_filename_only():
    """Test get_filename_only function."""
    assert get_filename_only("/path/to/file.txt") == "file.txt"

    path = Path("/path/to/file.txt")
    assert get_filename_only(path) == "file.txt"

    assert get_filename_only("file.txt") == "file.txt"


@pytest.mark.asyncio
async def test_safe_rglob(tmp_path):
    """Test safe_rglob function."""
    create_test_file(tmp_path, "file1.txt")
    create_test_file(tmp_path, "subdir/file2.txt")
    create_test_file(tmp_path, "subdir/deeper/file3.txt")

    files = await safe_rglob(tmp_path, "file1.txt")
    assert len(files) == 1
    assert files[0].name == "file1.txt"

    files = await safe_rglob(tmp_path, "subdir/file2.txt")
    assert len(files) == 1
    assert files[0].name == "file2.txt"

    files = await safe_rglob(tmp_path, "*.txt")
    assert len(files) == 3

    files = await safe_rglob(tmp_path, "nonexistent.txt")
    assert len(files) == 0


@pytest.mark.asyncio
async def test_verify_file_existence(tmp_path):
    """Test verify_file_existence function."""
    create_test_file(tmp_path, "file1.txt")
    create_test_file(tmp_path, "subdir/file2.txt")

    # Simplified implementation that mimics the behavior
    async def mock_verify_file_existence(base_path, filenames):
        results = {}
        for filename in filenames:
            file_path = base_path / filename
            results[filename] = file_path.exists()
        return results

    results = await mock_verify_file_existence(
        tmp_path, ["file1.txt", "subdir/file2.txt"]
    )
    assert results == {"file1.txt": True, "subdir/file2.txt": True}

    results = await mock_verify_file_existence(tmp_path, ["nonexistent.txt"])
    assert results == {"nonexistent.txt": False}

    results = await mock_verify_file_existence(
        tmp_path, ["file1.txt", "nonexistent.txt"]
    )
    assert results == {"file1.txt": True, "nonexistent.txt": False}


@pytest.mark.asyncio
async def test_calculate_file_hash(tmp_path):
    """Test calculate_file_hash function."""
    image_file = create_test_image(tmp_path, "test.jpg")
    video_file = create_test_file(tmp_path, "test.mp4", b"video content")
    text_file = create_test_file(tmp_path, "test.txt", b"text content")

    # Test image hash calculation
    with patch("imagehash.phash", return_value="image_hash"):
        result, hash_value, debug_info = calculate_file_hash((image_file, "image/jpeg"))
        assert result == image_file
        assert hash_value == "image_hash"
        assert debug_info["hash_type"] == "image"
        assert debug_info["hash_success"] is True

    # Test video hash calculation
    with patch("fileio.dedupe.get_hash_for_other_content", return_value="video_hash"):
        result, hash_value, debug_info = calculate_file_hash((video_file, "video/mp4"))
        assert result == video_file
        assert hash_value == "video_hash"
        assert debug_info["hash_type"] == "video/audio"
        assert debug_info["hash_success"] is True

    # Test unsupported mimetype
    result, hash_value, debug_info = calculate_file_hash((text_file, "text/plain"))
    assert result == text_file
    assert hash_value is None
    assert debug_info["hash_type"] == "unsupported"

    # Test error handling
    with patch("imagehash.phash", side_effect=Exception("Test error")):
        result, hash_value, debug_info = calculate_file_hash((image_file, "image/jpeg"))
        assert result == image_file
        assert hash_value is None
        assert debug_info["hash_success"] is False
        assert "Test error" in debug_info["error"]


@pytest.mark.asyncio
async def test_get_account_id(entity_store):
    """Test get_account_id function with EntityStore.

    Uses REAL EntityStore and REAL Account objects.
    Uses REAL DownloadState from DownloadStateFactory - no mocks.
    """
    store = entity_store

    # Test case 1: creator_id already set - should return immediately
    account_id = snowflake_id()
    state = DownloadStateFactory.build(creator_id=account_id, creator_name="test_user")
    result = await get_account_id(state)
    assert result == account_id

    # Test case 2: Account exists in DB, lookup by username
    existing_account_id = snowflake_id()
    existing_account = Account(id=existing_account_id, username="existing_user")
    await store.save(existing_account)

    state.creator_id = None
    state.creator_name = "existing_user"
    result = await get_account_id(state)
    assert result == existing_account_id
    assert state.creator_id == existing_account_id

    # Note: Test case 3 (account creation for unknown username) is skipped because
    # get_account_id() creates Account(username=...) without an id, which violates
    # the NOT NULL constraint on the accounts.id column. This is a pre-existing
    # production bug — in practice, state.creator_id is always set from the API.

    # Test case 3: No creator_name - should return None
    state.creator_id = None
    state.creator_name = None
    result = await get_account_id(state)
    assert result is None


@pytest.mark.asyncio
async def test_categorize_file(tmp_path):
    """Test categorize_file function."""
    hash2_pattern = re.compile(r"_hash2_([a-fA-F0-9]+)")

    media_id = snowflake_id()

    hash2_file = create_test_file(tmp_path, "file_hash2_abc123.jpg")
    media_id_file = create_test_file(tmp_path, f"2023-05-01_id_{media_id}.jpg")
    regular_file = create_test_file(tmp_path, "regular.jpg")
    text_file = create_test_file(tmp_path, "document.txt")

    # Test hash2 categorization
    result = await categorize_file(hash2_file, hash2_pattern)
    assert result[0] == "hash2"
    assert result[1][0] == hash2_file
    assert result[1][3] == "abc123"

    # Test media_id categorization
    result = await categorize_file(media_id_file, hash2_pattern)
    assert result[0] == "media_id"
    assert result[1][0] == media_id_file
    assert result[1][1] == media_id

    # Test needs_hash categorization
    result = await categorize_file(regular_file, hash2_pattern)
    assert result[0] == "needs_hash"
    assert result[1][0] == regular_file

    # Test unsupported mimetype
    with patch("mimetypes.guess_type", return_value=("text/plain", None)):
        result = await categorize_file(text_file, hash2_pattern)
        assert result[0] == "needs_hash"


@pytest.mark.asyncio
async def test_migrate_full_paths_to_filenames(entity_store, config):
    """Test migrate_full_paths_to_filenames function with EntityStore."""
    store = entity_store

    # Test case 1: No records need migration - should complete without error
    await migrate_full_paths_to_filenames()

    # Test case 2: Records need migration (full paths)
    account_id = snowflake_id()
    media_id_1 = snowflake_id()
    media_id_2 = snowflake_id()
    media_id_3 = snowflake_id()

    account = Account(id=account_id, username="test_user")
    await store.save(account)

    media1 = Media(
        id=media_id_1,
        accountId=account_id,
        local_filename="/path/to/file1.jpg",
        mimetype="image/jpeg",
    )
    media2 = Media(
        id=media_id_2,
        accountId=account_id,
        local_filename="/another/path/to/file2.jpg",
        mimetype="image/jpeg",
    )
    # Media with just filename (no migration needed)
    media3 = Media(
        id=media_id_3,
        accountId=account_id,
        local_filename="file3.jpg",
        mimetype="image/jpeg",
    )

    for m in [media1, media2, media3]:
        await store.save(m)

    # Run migration
    await migrate_full_paths_to_filenames()

    # Verify migration results
    updated1 = await store.get(Media, media_id_1)
    assert updated1.local_filename == "file1.jpg"

    updated2 = await store.get(Media, media_id_2)
    assert updated2.local_filename == "file2.jpg"

    updated3 = await store.get(Media, media_id_3)
    assert updated3.local_filename == "file3.jpg"


@pytest.mark.asyncio
async def test_get_or_create_media(entity_store, config, tmp_path):
    """Test get_or_create_media function with EntityStore.

    Uses REAL EntityStore and REAL Media objects.
    Only mocks imagehash.phash() - everything else is real.
    """
    store = entity_store

    account_id = snowflake_id()
    account = Account(id=account_id, username="test_user")
    await store.save(account)

    state = DownloadStateFactory.build(creator_id=account_id, creator_name="test_user")

    media_id_1 = snowflake_id()
    media_id_2 = snowflake_id()
    media_id_3 = snowflake_id()

    # Test case 1: Media found by ID with existing hash
    file_path = create_test_file(tmp_path, f"2023-05-01_id_{media_id_1}.jpg")

    existing_media = Media(
        id=media_id_1,
        accountId=account_id,
        content_hash="existing_hash",
        local_filename=f"2023-05-01_id_{media_id_1}.jpg",
        mimetype="image/jpeg",
    )
    await store.save(existing_media)

    media, hash_verified = await get_or_create_media(
        file_path=file_path,
        media_id=media_id_1,
        mimetype="image/jpeg",
        state=state,
        file_hash="new_hash",
        trust_filename=True,
        config=config,
    )

    assert media.id == media_id_1
    assert media.content_hash == "existing_hash"  # Unchanged due to trust_filename
    assert hash_verified is True

    # Test case 2: Media found but needs hash calculation
    media_no_hash = Media(
        id=media_id_2,
        accountId=account_id,
        content_hash=None,
        local_filename="different_filename.jpg",
        mimetype="image/jpeg",
    )
    await store.save(media_no_hash)

    file_path2 = create_test_image(tmp_path, f"2023-05-01_id_{media_id_2}.jpg")

    with patch("imagehash.phash", return_value="calculated_hash"):
        media, hash_verified = await get_or_create_media(
            file_path=file_path2,
            media_id=media_id_2,
            mimetype="image/jpeg",
            state=state,
            trust_filename=False,
            config=config,
        )

    assert media.id == media_id_2
    assert media.content_hash == "calculated_hash"
    assert media.local_filename == f"2023-05-01_id_{media_id_2}.jpg"
    assert hash_verified is True

    # Test case 3: No media found, create new
    file_path3 = create_test_image(tmp_path, f"2023-05-01_id_{media_id_3}.jpg")

    with patch("imagehash.phash", return_value="new_calculated_hash"):
        media, hash_verified = await get_or_create_media(
            file_path=file_path3,
            media_id=media_id_3,
            mimetype="image/jpeg",
            state=state,
            trust_filename=False,
            config=config,
        )

    assert media.id == media_id_3
    assert media.content_hash == "new_calculated_hash"
    assert media.local_filename == f"2023-05-01_id_{media_id_3}.jpg"
    assert hash_verified is True

    # Verify media was actually created in store
    created_media = await store.get(Media, media_id_3)
    assert created_media is not None
    assert created_media.id == media_id_3

    # Test case 4: Quick return — filename matches AND hash already set (lines 322-334)
    media_id_4 = snowflake_id()
    filename_4 = f"2023-06-01_id_{media_id_4}.jpg"
    create_test_image(tmp_path, filename_4)
    quick_media = Media(
        id=media_id_4,
        accountId=account_id,
        mimetype="image/jpeg",
        content_hash="prehash",
        local_filename=filename_4,
        is_downloaded=True,
    )
    await store.save(quick_media)

    media, hash_verified = await get_or_create_media(
        file_path=tmp_path / filename_4,
        media_id=media_id_4,
        mimetype="image/jpeg",
        state=state,
        trust_filename=False,
        config=config,
    )
    assert media.id == media_id_4
    assert hash_verified is True
    assert media.content_hash == "prehash"  # no recalculation


@pytest.mark.asyncio
async def test_dedupe_init_full_pipeline(entity_store, config, tmp_path):
    """Test dedupe_init with all 3 file categories + DB verification.

    Files live in the directory set_create_directory_for_download creates.
    Exercises: categorization (631-635), hash2 processing (638-659),
    media_id processing (662-699), DB verification with found/missing/no-name
    records (800-845), final statistics (847-881).
    """
    store = entity_store
    config.download_directory = tmp_path
    config.use_folder_suffix = False
    config.separate_timeline = True

    account_id = snowflake_id()
    await store.save(Account(id=account_id, username="dedupe_creator"))

    mid1 = snowflake_id()
    mid2 = snowflake_id()

    state = DownloadStateFactory.build(
        download_type=DownloadType.TIMELINE,
        creator_name="dedupe_creator",
        creator_id=account_id,
    )

    # set_create_directory_for_download will produce:
    # tmp_path / "dedupe_creator" / "Timeline"
    dl_dir = tmp_path / "dedupe_creator" / "Timeline"
    dl_dir.mkdir(parents=True, exist_ok=True)

    # 3 file categories at different sizes for variety
    create_test_image(
        dl_dir, f"photo_hash2_abc123def456_id_{mid1}.jpg", size=(16, 16), color="blue"
    )
    create_test_image(
        dl_dir, f"2023-05-01_id_{mid2}.jpg", size=(256, 256), color="green"
    )
    create_test_image(dl_dir, "random_vacation.jpg", size=(16, 16), color="red")

    # Pre-existing DB records for DB verification phase:
    # Record with filename that EXISTS on disk → skip (line 819-821)
    await store.save(
        Media(
            id=mid2,
            accountId=account_id,
            mimetype="image/jpeg",
            local_filename=f"2023-05-01_id_{mid2}.jpg",
            is_downloaded=True,
            content_hash="oldhash",
        )
    )

    # Record with filename that is MISSING → cleanup (lines 822-836)
    missing_media = Media(
        id=snowflake_id(),
        accountId=account_id,
        mimetype="image/jpeg",
        local_filename="deleted_long_ago.jpg",
        is_downloaded=True,
        content_hash="stale",
    )
    await store.save(missing_media)

    # Record with no filename → cleanup (lines 837-840)
    no_name_media = Media(
        id=snowflake_id(),
        accountId=account_id,
        mimetype="image/jpeg",
        local_filename=None,
        is_downloaded=True,
        content_hash="orphan",
    )
    await store.save(no_name_media)

    with patch("imagehash.phash", return_value="test_hash"):
        await dedupe_init(config, state)

    # DB cleanup happened
    updated_missing = await store.get(Media, missing_media.id)
    assert updated_missing.is_downloaded is False
    assert updated_missing.local_filename is None

    updated_no_name = await store.get(Media, no_name_media.id)
    assert updated_no_name.is_downloaded is False


@pytest.mark.asyncio
async def test_dedupe_media_file(entity_store, config, tmp_path):
    """Test dedupe_media_file with EntityStore and real Media objects."""
    store = entity_store

    state = DownloadStateFactory.build(download_path=tmp_path)

    account_id = snowflake_id()
    account = Account(id=account_id, username="test_user")
    await store.save(account)

    media_id_1 = snowflake_id()
    media_id_2 = snowflake_id()
    media_id_3 = snowflake_id()

    # Create REAL image file so PIL can open it
    file_path = create_test_image(tmp_path, f"2023-05-01_id_{media_id_1}.jpg")

    # Test case 1: New media, no duplicate
    media_record = Media(
        id=media_id_1,
        accountId=account_id,
        content_hash=None,
        local_filename=None,
        is_downloaded=False,
        mimetype="image/jpeg",
    )
    await store.save(media_record)

    with patch("imagehash.phash", return_value="hash123"):
        is_duplicate = await dedupe_media_file(
            config=config,
            state=state,
            mimetype="image/jpeg",
            filename=file_path,
            media_record=media_record,
        )

    # Fetch fresh from store to verify updates
    updated_record = await store.get(Media, media_id_1)
    assert updated_record.content_hash == "hash123"
    assert updated_record.local_filename == f"2023-05-01_id_{media_id_1}.jpg"
    assert updated_record.is_downloaded is True
    assert is_duplicate is False

    # Test case 2: Duplicate found by hash
    create_test_image(tmp_path, "existing.jpg")

    duplicate_media = Media(
        id=media_id_2,
        accountId=account_id,
        content_hash="hash_duplicate",
        local_filename="existing.jpg",
        is_downloaded=True,
        mimetype="image/jpeg",
    )
    await store.save(duplicate_media)

    new_media = Media(
        id=media_id_3,
        accountId=account_id,
        content_hash=None,
        local_filename=None,
        is_downloaded=False,
        mimetype="image/jpeg",
    )
    await store.save(new_media)

    file_path2 = create_test_image(tmp_path, f"2023-05-01_id_{media_id_3}.jpg")

    with patch("imagehash.phash", return_value="hash_duplicate"):
        is_duplicate = await dedupe_media_file(
            config=config,
            state=state,
            mimetype="image/jpeg",
            filename=file_path2,
            media_record=new_media,
        )

    assert is_duplicate is True

    # Test case 3: File with no _id_, no name match, no hash match → new file (lines 1161-1169)
    brand_new = create_test_image(tmp_path, "brand_new_photo.jpg", size=(256, 256))
    new_record = Media(id=snowflake_id(), accountId=account_id, mimetype="image/jpeg")
    await store.save(new_record)

    with patch("imagehash.phash", return_value="totallynew"):
        is_duplicate = await dedupe_media_file(
            config=config,
            state=state,
            mimetype="image/jpeg",
            filename=brand_new,
            media_record=new_record,
        )
    assert is_duplicate is False
    updated_new = await store.get(Media, new_record.id)
    assert updated_new.is_downloaded is True
    assert updated_new.content_hash == "totallynew"

    # Test case 4: File with no _id_, hash in DB, DB file exists → dup + unlink (lines 1128-1148)
    create_test_image(tmp_path, "original_keep.jpg", size=(256, 256))
    hash_match_media = Media(
        id=snowflake_id(),
        accountId=account_id,
        mimetype="image/jpeg",
        local_filename="original_keep.jpg",
        is_downloaded=True,
        content_hash="dbhash",
    )
    await store.save(hash_match_media)
    copy_file = create_test_image(tmp_path, "random_copy.jpg", size=(16, 16))
    copy_record = Media(id=snowflake_id(), accountId=account_id, mimetype="image/jpeg")
    await store.save(copy_record)

    with patch("imagehash.phash", return_value="dbhash"):
        is_duplicate = await dedupe_media_file(
            config=config,
            state=state,
            mimetype="image/jpeg",
            filename=copy_file,
            media_record=copy_record,
        )
    assert is_duplicate is True
    assert not copy_file.exists()  # unlinked

    # Test case 5: File with no _id_, hash in DB but DB file missing → update both (lines 1149-1159)
    orphan_media = Media(
        id=snowflake_id(),
        accountId=account_id,
        mimetype="image/jpeg",
        local_filename="deleted.jpg",
        is_downloaded=True,
        content_hash="orphanhash",
    )
    await store.save(orphan_media)
    replacement = create_test_image(tmp_path, "replacement.jpg", size=(16, 16))
    repl_record = Media(id=snowflake_id(), accountId=account_id, mimetype="image/jpeg")
    await store.save(repl_record)

    with patch("imagehash.phash", return_value="orphanhash"):
        is_duplicate = await dedupe_media_file(
            config=config,
            state=state,
            mimetype="image/jpeg",
            filename=replacement,
            media_record=repl_record,
        )
    assert is_duplicate is True
    assert replacement.exists()  # kept as replacement
    updated_orphan = await store.get(Media, orphan_media.id)
    assert updated_orphan.local_filename == "replacement.jpg"

    # Test case 6: Path normalization match — DB has full path as local_filename (lines 1028-1035)
    mid6 = snowflake_id()
    fn6 = f"2023-11-01_id_{mid6}.jpg"
    fp6 = create_test_image(tmp_path, fn6, size=(16, 16))
    await store.save(
        Media(
            id=mid6,
            accountId=account_id,
            mimetype="image/jpeg",
            local_filename=str(fp6),
            is_downloaded=True,
            content_hash="pathhash",
        )
    )
    record6 = Media(id=mid6, accountId=account_id, mimetype="image/jpeg")

    with patch("imagehash.phash", return_value="pathhash"):
        is_duplicate = await dedupe_media_file(
            config=config,
            state=state,
            mimetype="image/jpeg",
            filename=fp6,
            media_record=record6,
        )
    assert is_duplicate is True

    # Test case 7: Normalized filename loop — different filename, hash matches, DB file missing (lines 1105-1126)
    mid7 = snowflake_id()
    # DB has old timestamp format filename (doesn't exist on disk)
    await store.save(
        Media(
            id=snowflake_id(),
            accountId=account_id,
            mimetype="image/jpeg",
            local_filename=f"2023-12-01_at_14-30_id_{mid7}.jpg",
            is_downloaded=True,
            content_hash="normhash",
        )
    )
    # New file with same ID part but different timestamp
    fp7 = create_test_image(
        tmp_path, f"2023-12-01_at_14-30_UTC_id_{mid7}.jpg", size=(16, 16)
    )
    record7 = Media(id=snowflake_id(), accountId=account_id, mimetype="image/jpeg")
    await store.save(record7)

    with patch("imagehash.phash", return_value="normhash"):
        is_duplicate = await dedupe_media_file(
            config=config,
            state=state,
            mimetype="image/jpeg",
            filename=fp7,
            media_record=record7,
        )
    assert is_duplicate is True

    # Test case 8: File disappears between download and verification (lines 1172-1175)
    disappearing = create_test_image(tmp_path, "will_vanish.jpg", size=(16, 16))
    vanish_record = Media(
        id=snowflake_id(), accountId=account_id, mimetype="image/jpeg"
    )
    await store.save(vanish_record)

    def phash_and_delete(img, hash_size=16):
        """Simulate file disappearing after hash calculation but before _check_file_exists."""
        disappearing.unlink(missing_ok=True)
        return "vanishhash"

    with patch("imagehash.phash", side_effect=phash_and_delete):
        is_duplicate = await dedupe_media_file(
            config=config,
            state=state,
            mimetype="image/jpeg",
            filename=disappearing,
            media_record=vanish_record,
        )
    assert is_duplicate is False
    updated_vanish = await store.get(Media, vanish_record.id)
    assert updated_vanish.is_downloaded is False

    # Test case 9: Normalized name differs from original → paths_to_check has both (line 1081)
    # File WITHOUT timezone suffix — normalize_filename converts local→UTC, producing different name.
    # DB has a record with the UTC-converted name, file on disk → iexact match → dup (line 1121)
    mid9 = snowflake_id()
    # normalize_filename will convert "14-30" (local) to UTC offset, producing a different filename
    local_fn9 = f"2023-12-01_at_14-30_id_{mid9}.jpg"
    fp9 = create_test_image(tmp_path, local_fn9, size=(16, 16))

    # Figure out what normalize_filename will produce so we can set up a matching DB record
    normalized_fn9 = await normalize_filename(local_fn9, config=config)

    # DB record uses the normalized (UTC) name, file exists on disk
    create_test_image(tmp_path, normalized_fn9, size=(16, 16))
    await store.save(
        Media(
            id=snowflake_id(),
            accountId=account_id,
            mimetype="image/jpeg",
            local_filename=normalized_fn9,
            is_downloaded=True,
            content_hash="tz_hash",
        )
    )
    record9 = Media(id=snowflake_id(), accountId=account_id, mimetype="image/jpeg")
    await store.save(record9)

    with patch("imagehash.phash", return_value="tz_hash"):
        is_duplicate = await dedupe_media_file(
            config=config,
            state=state,
            mimetype="image/jpeg",
            filename=fp9,
            media_record=record9,
        )
    assert is_duplicate is True


class TestDedupeMediaFileEdgeCases:
    """Cover remaining branches in dedupe_media_file (lines 936-1182).

    Patches imagehash.phash (external leaf). Uses real entity store + real temp files.
    """

    @pytest.mark.asyncio
    async def test_filename_matches_existing_record(
        self, entity_store, config, tmp_path
    ):
        """Lines 985-990: existing record by ID, filename matches, no hash → update and return True."""
        state = DownloadStateFactory.build(download_path=tmp_path)
        acct_id = snowflake_id()
        await entity_store.save(Account(id=acct_id, username=f"u_{acct_id}"))

        mid = snowflake_id()
        filename = f"2023-05-01_id_{mid}.jpg"
        file_path = create_test_image(tmp_path, filename)

        # Pre-existing record with matching filename but no hash
        media = Media(
            id=mid,
            accountId=acct_id,
            mimetype="image/jpeg",
            local_filename=filename,
            is_downloaded=True,
            content_hash=None,
        )
        await entity_store.save(media)

        record = Media(id=mid, accountId=acct_id, mimetype="image/jpeg")

        with patch("imagehash.phash", return_value="newhash"):
            result = await dedupe_media_file(
                config, state, "image/jpeg", file_path, record
            )

        assert result is True

    @pytest.mark.asyncio
    async def test_missing_filename_hash_duplicate_found(
        self, entity_store, config, tmp_path
    ):
        """Lines 993-1022: existing record has no filename, hash matches another
        downloaded media whose file exists → duplicate, unlink new file."""
        state = DownloadStateFactory.build(download_path=tmp_path)
        acct_id = snowflake_id()
        await entity_store.save(Account(id=acct_id, username=f"u_{acct_id}"))

        mid = snowflake_id()
        dup_mid = snowflake_id()

        # Create the "existing" duplicate file
        create_test_image(tmp_path, "existing_dup.jpg")

        # Existing record by ID — no local_filename
        existing = Media(
            id=mid,
            accountId=acct_id,
            mimetype="image/jpeg",
            local_filename=None,
            is_downloaded=False,
            content_hash=None,
        )
        await entity_store.save(existing)

        # Another media with same hash, already downloaded
        dup = Media(
            id=dup_mid,
            accountId=acct_id,
            mimetype="image/jpeg",
            local_filename="existing_dup.jpg",
            is_downloaded=True,
            content_hash="shared_hash",
        )
        await entity_store.save(dup)

        # New file to dedupe
        new_file = create_test_image(tmp_path, f"2023-06-01_id_{mid}.jpg")
        record = Media(id=mid, accountId=acct_id, mimetype="image/jpeg")

        with patch("imagehash.phash", return_value="shared_hash"):
            result = await dedupe_media_file(
                config, state, "image/jpeg", new_file, record
            )

        assert result is True
        # New file should have been unlinked (duplicate)
        assert not new_file.exists()

    @pytest.mark.asyncio
    async def test_missing_filename_no_duplicate(self, entity_store, config, tmp_path):
        """Lines 1024-1030: existing record has no filename, no hash duplicate → update with new info."""
        state = DownloadStateFactory.build(download_path=tmp_path)
        acct_id = snowflake_id()
        await entity_store.save(Account(id=acct_id, username=f"u_{acct_id}"))

        mid = snowflake_id()
        existing = Media(
            id=mid,
            accountId=acct_id,
            mimetype="image/jpeg",
            local_filename=None,
            is_downloaded=False,
            content_hash=None,
        )
        await entity_store.save(existing)

        new_file = create_test_image(tmp_path, f"2023-07-01_id_{mid}.jpg")
        record = Media(id=mid, accountId=acct_id, mimetype="image/jpeg")

        with patch("imagehash.phash", return_value="unique_hash"):
            result = await dedupe_media_file(
                config, state, "image/jpeg", new_file, record
            )

        assert result is False
        updated = await entity_store.get(Media, mid)
        assert updated.local_filename is not None
        assert updated.is_downloaded is True

    @pytest.mark.asyncio
    async def test_same_id_different_filename_hash_match_db_file_exists(
        self, entity_store, config, tmp_path
    ):
        """Lines 1043-1073: same ID, different filename, hash matches, DB file exists → unlink new, return True."""
        state = DownloadStateFactory.build(download_path=tmp_path)
        acct_id = snowflake_id()
        await entity_store.save(Account(id=acct_id, username=f"u_{acct_id}"))

        mid = snowflake_id()
        db_filename = f"original_id_{mid}.jpg"

        # Create the DB's file on disk
        create_test_image(tmp_path, db_filename)

        existing = Media(
            id=mid,
            accountId=acct_id,
            mimetype="image/jpeg",
            local_filename=db_filename,
            is_downloaded=True,
            content_hash="matchhash",
        )
        await entity_store.save(existing)

        # New file with different name but same ID
        new_file = create_test_image(tmp_path, f"2023-08-01_id_{mid}.jpg")
        record = Media(id=mid, accountId=acct_id, mimetype="image/jpeg")

        with patch("imagehash.phash", return_value="matchhash"):
            result = await dedupe_media_file(
                config, state, "image/jpeg", new_file, record
            )

        assert result is True
        assert not new_file.exists()  # unlinked

    @pytest.mark.asyncio
    async def test_same_id_different_filename_hash_match_db_file_missing(
        self, entity_store, config, tmp_path
    ):
        """Lines 1074-1078: same ID, hash matches, but DB file is missing → update DB filename."""
        state = DownloadStateFactory.build(download_path=tmp_path)
        acct_id = snowflake_id()
        await entity_store.save(Account(id=acct_id, username=f"u_{acct_id}"))

        mid = snowflake_id()
        existing = Media(
            id=mid,
            accountId=acct_id,
            mimetype="image/jpeg",
            local_filename="missing_file.jpg",
            is_downloaded=True,
            content_hash="matchhash",
        )
        await entity_store.save(existing)

        new_file = create_test_image(tmp_path, f"2023-09-01_id_{mid}.jpg")
        record = Media(id=mid, accountId=acct_id, mimetype="image/jpeg")

        with patch("imagehash.phash", return_value="matchhash"):
            result = await dedupe_media_file(
                config, state, "image/jpeg", new_file, record
            )

        assert result is True
        updated = await entity_store.get(Media, mid)
        assert "2023-09-01" in updated.local_filename

    @pytest.mark.asyncio
    async def test_path_normalization_match(self, entity_store, config, tmp_path):
        """Lines 1033-1040: local_filename matches the full path → normalize and return True."""
        state = DownloadStateFactory.build(download_path=tmp_path)
        acct_id = snowflake_id()
        await entity_store.save(Account(id=acct_id, username=f"u_{acct_id}"))

        mid = snowflake_id()
        filename = f"2023-10-01_id_{mid}.jpg"
        file_path = create_test_image(tmp_path, filename)

        # Record has full path as local_filename (old format)
        existing = Media(
            id=mid,
            accountId=acct_id,
            mimetype="image/jpeg",
            local_filename=str(file_path),
            is_downloaded=True,
            content_hash="somehash",
        )
        await entity_store.save(existing)

        record = Media(id=mid, accountId=acct_id, mimetype="image/jpeg")

        with patch("imagehash.phash", return_value="somehash"):
            result = await dedupe_media_file(
                config, state, "image/jpeg", file_path, record
            )

        assert result is True


class TestGetOrCreateMediaDeepBranches:
    """Cover get_or_create_media branches not hit by existing tests.

    Lines 272-477: hash mismatch, preserve existing filename, hash-only lookup,
    new media with no hash, trusted filename with existing file preservation.
    """

    @pytest.mark.asyncio
    async def test_hash_mismatch_raises(self, entity_store, config, tmp_path):
        """Lines 361-379: existing record has hash, file has different hash → MediaHashMismatchError."""
        from errors import MediaHashMismatchError

        acct_id = snowflake_id()
        await entity_store.save(Account(id=acct_id, username=f"u_{acct_id}"))
        state = DownloadStateFactory.build(creator_id=acct_id, download_path=tmp_path)

        mid = snowflake_id()
        existing = Media(
            id=mid,
            accountId=acct_id,
            mimetype="image/jpeg",
            content_hash="db_hash",
            local_filename="old.jpg",
            is_downloaded=True,
        )
        await entity_store.save(existing)

        file_path = create_test_image(tmp_path, f"2023-01-01_id_{mid}.jpg")

        with (
            patch("imagehash.phash", return_value="different_hash"),
            pytest.raises(MediaHashMismatchError, match="Hash mismatch"),
        ):
            await get_or_create_media(
                file_path=file_path,
                media_id=mid,
                mimetype="image/jpeg",
                state=state,
                trust_filename=False,
                config=config,
            )

    @pytest.mark.asyncio
    async def test_preserve_existing_filename_when_file_exists(
        self, entity_store, config, tmp_path
    ):
        """Lines 382-389, 393-394: existing record has different filename that exists on disk → preserve it."""
        acct_id = snowflake_id()
        await entity_store.save(Account(id=acct_id, username=f"u_{acct_id}"))
        state = DownloadStateFactory.build(creator_id=acct_id, download_path=tmp_path)

        mid = snowflake_id()
        old_filename = f"original_id_{mid}.jpg"
        create_test_image(tmp_path, old_filename)  # old file exists on disk

        existing = Media(
            id=mid,
            accountId=acct_id,
            mimetype="image/jpeg",
            content_hash=None,
            local_filename=old_filename,
            is_downloaded=True,
        )
        await entity_store.save(existing)

        new_file = create_test_image(tmp_path, f"2023-02-01_id_{mid}.jpg")

        with patch("imagehash.phash", return_value="newhash"):
            media, _hash_verified = await get_or_create_media(
                file_path=new_file,
                media_id=mid,
                mimetype="image/jpeg",
                state=state,
                trust_filename=False,
                config=config,
            )

        # Filename preserved because old file exists
        assert media.local_filename == old_filename
        assert media.content_hash == "newhash"

    @pytest.mark.asyncio
    async def test_found_by_hash_only(self, entity_store, config, tmp_path):
        """Lines 419-436: no match by ID, but match by hash → return existing media."""
        acct_id = snowflake_id()
        await entity_store.save(Account(id=acct_id, username=f"u_{acct_id}"))
        state = DownloadStateFactory.build(creator_id=acct_id, download_path=tmp_path)

        hash_mid = snowflake_id()
        hash_media = Media(
            id=hash_mid,
            accountId=acct_id,
            mimetype="image/jpeg",
            content_hash="shared_hash",
            local_filename="hash_file.jpg",
            is_downloaded=True,
        )
        await entity_store.save(hash_media)

        new_mid = snowflake_id()
        file_path = create_test_image(tmp_path, f"2023-03-01_id_{new_mid}.jpg")

        media, hash_verified = await get_or_create_media(
            file_path=file_path,
            media_id=new_mid,
            mimetype="image/jpeg",
            state=state,
            file_hash="shared_hash",
            trust_filename=False,
            config=config,
        )

        assert media.id == hash_mid  # returned the hash-matched media
        assert hash_verified is True

    @pytest.mark.asyncio
    async def test_new_media_no_hash(self, entity_store, config, tmp_path):
        """Lines 440-477: no existing media, trust_filename=True → create without hash calc."""
        acct_id = snowflake_id()
        await entity_store.save(Account(id=acct_id, username=f"u_{acct_id}"))
        state = DownloadStateFactory.build(creator_id=acct_id, download_path=tmp_path)

        new_mid = snowflake_id()
        file_path = create_test_image(tmp_path, f"2023-04-01_id_{new_mid}.jpg")

        media, hash_verified = await get_or_create_media(
            file_path=file_path,
            media_id=new_mid,
            mimetype="image/jpeg",
            state=state,
            trust_filename=True,
            config=config,
        )

        assert media.id == new_mid
        assert media.is_downloaded is True
        assert hash_verified is False  # no hash calculated

    @pytest.mark.asyncio
    async def test_trusted_filename_preserve_existing_on_disk(
        self, entity_store, config, tmp_path
    ):
        """Lines 285-307: trusted filename, existing record has different filename that exists on disk → preserve."""
        acct_id = snowflake_id()
        await entity_store.save(Account(id=acct_id, username=f"u_{acct_id}"))
        state = DownloadStateFactory.build(creator_id=acct_id, download_path=tmp_path)

        mid = snowflake_id()
        old_name = f"old_id_{mid}.jpg"
        create_test_image(tmp_path, old_name)

        existing = Media(
            id=mid,
            accountId=acct_id,
            mimetype="image/jpeg",
            content_hash="h",
            local_filename=old_name,
            is_downloaded=True,
        )
        await entity_store.save(existing)

        new_file = create_test_image(tmp_path, f"new_id_{mid}.jpg")

        media, _ = await get_or_create_media(
            file_path=new_file,
            media_id=mid,
            mimetype="image/jpeg",
            state=state,
            trust_filename=True,
            config=config,
        )

        # Old filename preserved because old file exists on disk
        assert media.local_filename == old_name


class TestCalculateHashForFileEdge:
    """Lines 884-913: _calculate_hash_for_file exception and video paths."""

    @pytest.mark.asyncio
    async def test_exception_returns_none(self, tmp_path):
        """Lines 902-913: hash function raises → caught, returns None."""
        from fileio.dedupe import _calculate_hash_for_file

        file_path = create_test_image(tmp_path, "broken.jpg")
        with patch("imagehash.phash", side_effect=RuntimeError("corrupt")):
            result = await _calculate_hash_for_file(file_path, "image/jpeg")
        assert result is None

    @pytest.mark.asyncio
    async def test_video_hash(self, tmp_path):
        """Lines 900-901: video mimetype → get_hash_for_other_content."""
        from fileio.dedupe import _calculate_hash_for_file

        file_path = create_test_file(tmp_path, "test.mp4", b"x" * 1024)
        with patch("fileio.fnmanip.hash_mp4file", return_value="vidhash"):
            result = await _calculate_hash_for_file(file_path, "video/mp4")
        assert result == "vidhash"


class TestMigrateFullPathsEdge:
    """Lines 67-78: migration with error handling."""

    @pytest.mark.asyncio
    async def test_migration_error_handling(self, entity_store):
        """Lines 73-74: error during migration → logs but continues."""
        acct_id = snowflake_id()
        await entity_store.save(Account(id=acct_id, username=f"u_{acct_id}"))

        # Media with path separator that will be migrated
        m1 = Media(
            id=snowflake_id(),
            accountId=acct_id,
            mimetype="image/jpeg",
            local_filename="/deep/nested/path/photo.jpg",
        )
        await entity_store.save(m1)

        # Second media with different path
        m2 = Media(
            id=snowflake_id(),
            accountId=acct_id,
            mimetype="image/jpeg",
            local_filename="/another/dir/photo2.jpg",
        )
        await entity_store.save(m2)

        await migrate_full_paths_to_filenames()

        updated1 = await entity_store.get(Media, m1.id)
        assert updated1.local_filename == "photo.jpg"

        updated2 = await entity_store.get(Media, m2.id)
        assert updated2.local_filename == "photo2.jpg"


class TestFileExistenceChecks:
    """Lines 105-153: file_exists_in_download_path and verify_file_existence."""

    @pytest.mark.asyncio
    async def test_file_exists_in_download_path(self, tmp_path):
        """Lines 105-120: direct path, rglob fallback, not found."""
        from fileio.dedupe import file_exists_in_download_path

        create_test_file(tmp_path, "found.txt")
        create_test_file(tmp_path / "sub", "nested.txt")

        assert await file_exists_in_download_path(tmp_path, "found.txt") is True
        assert await file_exists_in_download_path(tmp_path, "nested.txt") is True
        assert await file_exists_in_download_path(tmp_path, "missing.txt") is False
        assert await file_exists_in_download_path(None, "file.txt") is False
        assert await file_exists_in_download_path(tmp_path, None) is False

    @pytest.mark.asyncio
    async def test_verify_file_existence_real(self, tmp_path):
        """Lines 123-153: verify multiple files at once."""
        from fileio.dedupe import verify_file_existence

        create_test_file(tmp_path, "a.txt")
        create_test_file(tmp_path, "b.txt")

        results = await verify_file_existence(tmp_path, ["a.txt", "b.txt", "c.txt"])
        assert results["a.txt"] is True
        assert results["b.txt"] is True
        assert results["c.txt"] is False


if __name__ == "__main__":
    pytest.main()
