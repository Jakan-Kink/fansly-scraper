"""Unit tests for the preview-repair backfill pass."""

import pytest

from fileio.preview_repair import (
    _canonical_target,
    _classify,
    _safe_move,
    build_preview_id_set,
)
from metadata import Media
from tests.fixtures.metadata import (
    AccountFactory,
    AccountMediaBundleFactory,
    AccountMediaFactory,
    MediaFactory,
)
from tests.fixtures.utils.test_isolation import snowflake_id


@pytest.mark.asyncio
async def test_build_preview_id_set_collects_account_media_and_bundle(entity_store):
    """preview_id rows from both AccountMedia and AccountMediaBundle are collected."""
    store = entity_store
    acct_id = snowflake_id()
    preview_id_1 = snowflake_id()
    preview_id_2 = snowflake_id()
    media_id_10 = snowflake_id()
    media_id_30 = snowflake_id()
    am_id_1 = snowflake_id()
    am_id_2 = snowflake_id()
    bundle_id = snowflake_id()

    await store.save(AccountFactory.build(id=acct_id))
    # Media rows that serve as previewId FK targets
    await store.save(MediaFactory.build(id=preview_id_1, accountId=acct_id))
    await store.save(MediaFactory.build(id=preview_id_2, accountId=acct_id))
    # Media rows that serve as mediaId FK targets
    await store.save(MediaFactory.build(id=media_id_10, accountId=acct_id))
    await store.save(MediaFactory.build(id=media_id_30, accountId=acct_id))

    await store.save(
        AccountMediaFactory.build(
            id=am_id_1, accountId=acct_id, mediaId=media_id_10, previewId=preview_id_1
        )
    )
    await store.save(
        AccountMediaBundleFactory.build(
            id=bundle_id, accountId=acct_id, previewId=preview_id_2
        )
    )
    # Non-preview row — should be excluded
    await store.save(
        AccountMediaFactory.build(
            id=am_id_2, accountId=acct_id, mediaId=media_id_30, previewId=None
        )
    )

    ids = await build_preview_id_set(acct_id)

    assert ids == {preview_id_1, preview_id_2}


@pytest.mark.asyncio
async def test_build_preview_id_set_scopes_by_account(entity_store):
    """Only previewId rows belonging to the requested account are returned."""
    store = entity_store
    acct_id_a = snowflake_id()
    acct_id_b = snowflake_id()
    preview_id_a = snowflake_id()
    preview_id_b = snowflake_id()
    media_id_a = snowflake_id()
    media_id_b = snowflake_id()

    await store.save(AccountFactory.build(id=acct_id_a))
    await store.save(AccountFactory.build(id=acct_id_b))
    # Media for account A
    await store.save(MediaFactory.build(id=preview_id_a, accountId=acct_id_a))
    await store.save(MediaFactory.build(id=media_id_a, accountId=acct_id_a))
    # Media for account B
    await store.save(MediaFactory.build(id=preview_id_b, accountId=acct_id_b))
    await store.save(MediaFactory.build(id=media_id_b, accountId=acct_id_b))

    await store.save(
        AccountMediaFactory.build(
            id=snowflake_id(),
            accountId=acct_id_a,
            mediaId=media_id_a,
            previewId=preview_id_a,
        )
    )
    await store.save(
        AccountMediaFactory.build(
            id=snowflake_id(),
            accountId=acct_id_b,
            mediaId=media_id_b,
            previewId=preview_id_b,
        )
    )

    assert await build_preview_id_set(acct_id_a) == {preview_id_a}


def test_classify_preview_by_id_set():
    assert _classify("x_id_11.jpg", preview_ids={11}) is True
    assert _classify("x_id_30.jpg", preview_ids={11}) is False
    assert _classify("no_id_here.jpg", preview_ids={11}) is False


def test_canonical_target_moves_into_previews_when_separate(tmp_path):
    src = tmp_path / "Pictures" / "2026-01-02_at_03-04_UTC_id_11.jpg"
    canonical_name = "2026-01-02_at_03-04_UTC_preview_id_11.jpg"
    target = _canonical_target(src, canonical_name, is_preview=True, separate=True)
    assert target == tmp_path / "Pictures" / "Previews" / canonical_name


def test_canonical_target_marker_only_when_not_separate(tmp_path):
    src = tmp_path / "Pictures" / "2026-01-02_at_03-04_UTC_id_11.jpg"
    canonical_name = "2026-01-02_at_03-04_UTC_preview_id_11.jpg"
    target = _canonical_target(src, canonical_name, is_preview=True, separate=False)
    assert target == tmp_path / "Pictures" / canonical_name


def test_canonical_target_idempotent_when_already_in_previews(tmp_path):
    src = (
        tmp_path / "Pictures" / "Previews" / "2026-01-02_at_03-04_UTC_preview_id_11.jpg"
    )
    canonical_name = src.name
    target = _canonical_target(src, canonical_name, is_preview=True, separate=True)
    assert target == src


@pytest.mark.asyncio
async def test_safe_move_renames_and_updates_local_filename(entity_store, tmp_path):
    store = entity_store
    acct_id = snowflake_id()
    mid = snowflake_id()
    await store.save(AccountFactory.build(id=acct_id))
    await store.save(MediaFactory.build(id=mid, accountId=acct_id, content_hash="h1"))
    src = tmp_path / "Pictures" / f"old_id_{mid}.jpg"
    src.parent.mkdir(parents=True)
    src.write_text("data")
    target = tmp_path / "Pictures" / "Previews" / f"new_preview_id_{mid}.jpg"

    moved = await _safe_move(src, target, media_id=mid)

    assert moved == target
    assert target.exists()
    assert not src.exists()
    refreshed = await store.get(Media, mid)
    assert refreshed.local_filename == f"new_preview_id_{mid}.jpg"


@pytest.mark.asyncio
async def test_safe_move_identical_unlinks_source(entity_store, tmp_path):
    store = entity_store
    acct_id = snowflake_id()
    mid = snowflake_id()
    await store.save(AccountFactory.build(id=acct_id))
    await store.save(MediaFactory.build(id=mid, accountId=acct_id))
    src = tmp_path / "Pictures" / f"old_id_{mid}.jpg"
    target = tmp_path / "Pictures" / "Previews" / f"new_preview_id_{mid}.jpg"
    src.parent.mkdir(parents=True)
    target.parent.mkdir(parents=True)
    src.write_bytes(b"identical")
    target.write_bytes(b"identical")

    moved = await _safe_move(src, target, media_id=mid)

    assert moved is None  # collision resolved, no new path to scan
    assert not src.exists()  # duplicate source removed
    assert target.exists()


@pytest.mark.asyncio
async def test_safe_move_identical_reconciles_local_filename(entity_store, tmp_path):
    """Identical-duplicate branch must repoint the record at the survivor.

    The source file is unlinked as a byte-identical duplicate, so its content
    now lives only at ``target``. The source Media's ``local_filename`` must be
    updated to the surviving file and persisted; otherwise the DB record is
    stranded pointing at a deleted file.
    """
    store = entity_store
    acct_id = snowflake_id()
    mid = snowflake_id()
    await store.save(AccountFactory.build(id=acct_id))
    src = tmp_path / "Pictures" / f"old_id_{mid}.jpg"
    target = tmp_path / "Pictures" / "Previews" / f"new_preview_id_{mid}.jpg"
    src.parent.mkdir(parents=True)
    target.parent.mkdir(parents=True)
    src.write_bytes(b"identical")
    target.write_bytes(b"identical")
    await store.save(
        MediaFactory.build(id=mid, accountId=acct_id, local_filename=src.name)
    )

    moved = await _safe_move(src, target, media_id=mid)

    assert moved is None  # collision resolved on disk
    assert not src.exists()  # source removed as duplicate
    refreshed = await store.get(Media, mid)
    assert refreshed.local_filename == target.name  # repointed at survivor


@pytest.mark.asyncio
async def test_safe_move_different_skips_and_keeps_both(entity_store, tmp_path):
    store = entity_store
    acct_id = snowflake_id()
    mid = snowflake_id()
    await store.save(AccountFactory.build(id=acct_id))
    await store.save(MediaFactory.build(id=mid, accountId=acct_id))
    src = tmp_path / "Pictures" / f"old_id_{mid}.jpg"
    target = tmp_path / "Pictures" / "Previews" / f"new_preview_id_{mid}.jpg"
    src.parent.mkdir(parents=True)
    target.parent.mkdir(parents=True)
    src.write_bytes(b"AAAA")
    target.write_bytes(b"BBBB")

    moved = await _safe_move(src, target, media_id=mid)

    assert moved is None
    assert src.exists()  # different data - never clobbered
    assert target.exists()
