"""Integration tests for the preview-repair backfill on real files + DB."""

import pytest

from fileio.preview_repair import repair_preview_folder_items
from metadata import Media
from tests.fixtures.download.download_factories import DownloadStateFactory
from tests.fixtures.metadata import AccountFactory, AccountMediaFactory, MediaFactory
from tests.fixtures.utils.test_isolation import snowflake_id


@pytest.mark.asyncio
async def test_repair_renames_and_folders_preview(entity_store, config, tmp_path):
    store = entity_store
    acct_id = snowflake_id()
    mid = snowflake_id()
    am_id = snowflake_id()
    media_id = snowflake_id()
    await store.save(AccountFactory.build(id=acct_id, username="creator"))
    await store.save(MediaFactory.build(id=mid, accountId=acct_id))
    await store.save(MediaFactory.build(id=media_id, accountId=acct_id))
    await store.save(
        AccountMediaFactory.build(
            id=am_id, accountId=acct_id, mediaId=media_id, previewId=mid
        )
    )

    creator_dir = tmp_path / "creator"
    pics = creator_dir / "Pictures"
    pics.mkdir(parents=True)
    bug_file = pics / f"2026-01-02_at_03-04_UTC_id_{mid}.jpg"
    bug_file.write_text("preview-bytes")

    config.separate_previews = True
    config.repair_previews = True

    state = DownloadStateFactory.build(
        creator_id=acct_id,
        download_path=creator_dir,
    )

    await repair_preview_folder_items(config, state)

    moved = pics / "Previews" / f"2026-01-02_at_03-04_UTC_preview_id_{mid}.jpg"
    assert moved.exists()
    assert not bug_file.exists()
    refreshed = await store.get(Media, mid)
    assert refreshed.local_filename == moved.name


@pytest.mark.asyncio
async def test_repair_dry_run_mutates_nothing(entity_store, config, tmp_path):
    store = entity_store
    acct_id = snowflake_id()
    mid = snowflake_id()
    am_id = snowflake_id()
    media_id = snowflake_id()
    await store.save(AccountFactory.build(id=acct_id))
    await store.save(MediaFactory.build(id=mid, accountId=acct_id))
    await store.save(MediaFactory.build(id=media_id, accountId=acct_id))
    await store.save(
        AccountMediaFactory.build(
            id=am_id, accountId=acct_id, mediaId=media_id, previewId=mid
        )
    )

    creator_dir = tmp_path / "creator"
    pics = creator_dir / "Pictures"
    pics.mkdir(parents=True)
    bug_file = pics / f"2026-01-02_at_03-04_UTC_id_{mid}.jpg"
    bug_file.write_text("x")

    config.separate_previews = True
    config.repair_previews = "dry-run"

    state = DownloadStateFactory.build(
        creator_id=acct_id,
        download_path=creator_dir,
    )

    await repair_preview_folder_items(config, state)

    assert bug_file.exists()  # untouched
    assert not (pics / "Previews").exists()
    refreshed = await store.get(Media, mid)
    assert refreshed.local_filename is None


@pytest.mark.asyncio
async def test_repair_idempotent_second_run_is_noop(entity_store, config, tmp_path):
    store = entity_store
    acct_id = snowflake_id()
    mid = snowflake_id()
    am_id = snowflake_id()
    media_id = snowflake_id()
    await store.save(AccountFactory.build(id=acct_id))
    await store.save(MediaFactory.build(id=mid, accountId=acct_id))
    await store.save(MediaFactory.build(id=media_id, accountId=acct_id))
    await store.save(
        AccountMediaFactory.build(
            id=am_id, accountId=acct_id, mediaId=media_id, previewId=mid
        )
    )

    creator_dir = tmp_path / "creator"
    pics = creator_dir / "Pictures"
    pics.mkdir(parents=True)
    (pics / f"2026-01-02_at_03-04_UTC_id_{mid}.jpg").write_text("x")

    config.separate_previews = True
    config.repair_previews = True

    state = DownloadStateFactory.build(
        creator_id=acct_id,
        download_path=creator_dir,
    )

    await repair_preview_folder_items(config, state)
    moved = pics / "Previews" / f"2026-01-02_at_03-04_UTC_preview_id_{mid}.jpg"
    assert moved.exists()
    await repair_preview_folder_items(config, state)  # second run: no error
    assert moved.exists()


@pytest.mark.asyncio
async def test_repair_noop_when_flag_disabled(fresh_config, tmp_path):
    """repair_previews falsy -> immediate return, file untouched."""
    fresh_config.repair_previews = False
    bug = tmp_path / "creator" / f"2026-01-02_at_03-04_UTC_id_{snowflake_id()}.jpg"
    bug.parent.mkdir(parents=True)
    bug.write_text("x")
    state = DownloadStateFactory.build(
        creator_id=snowflake_id(), download_path=bug.parent
    )

    await repair_preview_folder_items(fresh_config, state)

    assert bug.exists()  # untouched


@pytest.mark.asyncio
async def test_repair_noop_when_no_creator_id(fresh_config, tmp_path):
    """creator_id None -> immediate return (no walk)."""
    fresh_config.repair_previews = True
    bug = tmp_path / f"2026-01-02_at_03-04_UTC_id_{snowflake_id()}.jpg"
    bug.write_text("x")
    state = DownloadStateFactory.build(creator_id=None, download_path=tmp_path)

    await repair_preview_folder_items(fresh_config, state)

    assert bug.exists()  # untouched


@pytest.mark.asyncio
async def test_repair_noop_when_no_persisted_previews(entity_store, config, tmp_path):
    """Empty preview set (creator has no previews) -> return before any move."""
    config.repair_previews = True
    config.separate_previews = True
    acct_id = snowflake_id()
    await entity_store.save(AccountFactory.build(id=acct_id))
    bug = tmp_path / f"2026-01-02_at_03-04_UTC_id_{snowflake_id()}.jpg"
    bug.write_text("x")
    state = DownloadStateFactory.build(creator_id=acct_id, download_path=tmp_path)

    await repair_preview_folder_items(config, state)

    assert bug.exists()  # untouched — no previews to repair
    assert not (tmp_path / "Previews").exists()
