"""Integration tests for the preview-repair backfill on real files + DB."""

import logging
from datetime import UTC, datetime

import pytest

from fileio.preview_repair import repair_preview_folder_items
from metadata import Media
from tests.fixtures.download.download_factories import DownloadStateFactory
from tests.fixtures.metadata import AccountFactory, AccountMediaFactory, MediaFactory
from tests.fixtures.utils.test_isolation import snowflake_id


# The timestamp the test file names carry; the database has to agree with it.
_NAMED_AT = datetime(2026, 1, 2, 3, 4, 0, tzinfo=UTC)


@pytest.mark.asyncio
async def test_repair_renames_and_folders_preview(entity_store, config, tmp_path):
    store = entity_store
    acct_id = snowflake_id()
    mid = snowflake_id()
    am_id = snowflake_id()
    media_id = snowflake_id()
    await store.save(AccountFactory.build(id=acct_id, username="creator"))
    await store.save(MediaFactory.build(id=mid, accountId=acct_id, createdAt=_NAMED_AT))
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
    await store.save(MediaFactory.build(id=mid, accountId=acct_id, createdAt=_NAMED_AT))
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


@pytest.mark.parametrize("mode", [True, "dry-run"], ids=["repair", "dry-run"])
@pytest.mark.asyncio
async def test_repair_settles_duplicate_copies_of_one_media(
    entity_store, config, tmp_path, mode, caplog
):
    """One file per media survives, under the name the database gives it.

    Four media of one creator, each on disk twice:
      - a video named with two different minutes -> the database's minute wins;
      - a preview named once without ``preview`` and once without UTC ->
        one file with both;
      - a preview under Timeline and under Messages -> the Timeline copy;
      - a preview under both whose copies differ -> both stay, with a warning.
    A legacy name carrying a content hash is not touched, nor is a file with
    no media id, nor a local-time name whose media the database has never
    seen (its UTC time could only be guessed). Dry-run changes
    nothing on disk.
    """
    caplog.set_level(logging.WARNING)
    store = entity_store
    acct_id = snowflake_id()
    video, renamed, shared, differing = (snowflake_id() for _ in range(4))
    await store.save(AccountFactory.build(id=acct_id, username="creator"))
    await store.save(
        MediaFactory.build(
            id=video,
            accountId=acct_id,
            mimetype="video/mp4",
            createdAt=datetime(2025, 10, 23, 23, 10, 30, tzinfo=UTC),
        )
    )
    await store.save(
        MediaFactory.build(
            id=renamed,
            accountId=acct_id,
            mimetype="video/mp4",
            createdAt=datetime(2022, 12, 27, 19, 24, 5, tzinfo=UTC),
        )
    )
    for preview in (shared, differing):
        await store.save(
            MediaFactory.build(
                id=preview,
                accountId=acct_id,
                mimetype="video/mp4",
                createdAt=datetime(2025, 3, 12, 16, 2, 0, tzinfo=UTC),
            )
        )
    for preview in (renamed, shared, differing):
        parent = snowflake_id()
        await store.save(MediaFactory.build(id=parent, accountId=acct_id))
        await store.save(
            AccountMediaFactory.build(
                id=parent, accountId=acct_id, mediaId=parent, previewId=preview
            )
        )

    root = tmp_path / "creator"
    videos = root / "Timeline" / "Videos"
    timeline_previews = videos / "Previews"
    message_previews = root / "Messages" / "Videos" / "Previews"
    timeline_previews.mkdir(parents=True)
    message_previews.mkdir(parents=True)
    wrong_minute = videos / f"2025-10-23_at_23-09_UTC_id_{video}.mp4"
    right_minute = videos / f"2025-10-23_at_23-10_UTC_id_{video}.mp4"
    hashed = videos / f"2025-10-23_at_23-09_UTC_id_{video}_hash2_abc123.mp4"
    unknown = videos / f"2024-01-01_at_10-00_id_{snowflake_id()}.mp4"
    no_id = videos / "notes.txt"
    no_utc = timeline_previews / f"2022-12-27_at_14-24_preview_id_{renamed}.mp4"
    no_marker = timeline_previews / f"2022-12-27_at_19-24_UTC_id_{renamed}.mp4"
    both = timeline_previews / f"2022-12-27_at_19-24_UTC_preview_id_{renamed}.mp4"
    shared_name = f"2025-03-12_at_16-02_UTC_preview_id_{shared}.mp4"
    differing_name = f"2025-03-12_at_16-02_UTC_preview_id_{differing}.mp4"
    for path, content in (
        (wrong_minute, "video"),
        (right_minute, "video"),
        (hashed, "legacy"),
        (unknown, "never seen"),
        (no_id, "notes"),
        (no_utc, "preview"),
        (no_marker, "preview"),
        (timeline_previews / shared_name, "shared"),
        (message_previews / shared_name, "shared"),
        (timeline_previews / differing_name, "timeline cut"),
        (message_previews / differing_name, "message cut"),
    ):
        path.write_text(content)
    before = sorted(p.relative_to(root) for p in root.rglob("*") if p.is_file())

    config.separate_previews = True
    config.repair_previews = mode
    state = DownloadStateFactory.build(creator_id=acct_id, download_path=root)

    await repair_preview_folder_items(config, state)

    after = sorted(p.relative_to(root) for p in root.rglob("*") if p.is_file())
    warnings = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
    assert sum("differ in content" in message for message in warnings) == 1
    if mode == "dry-run":
        assert after == before
        return
    assert after == sorted(
        path.relative_to(root)
        for path in (
            right_minute,
            hashed,
            unknown,
            no_id,
            both,
            timeline_previews / shared_name,
            timeline_previews / differing_name,
            message_previews / differing_name,
        )
    )
    assert (await store.get(Media, video)).local_filename == right_minute.name
    assert (await store.get(Media, renamed)).local_filename == both.name


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


@pytest.mark.parametrize("mode", [True, "dry-run"], ids=["repair", "dry-run"])
@pytest.mark.asyncio
async def test_repair_gives_extensionless_previews_their_extension(
    mode, entity_store, config, tmp_path
):
    """A preview saved as ``.None`` gets the extension its media type calls for.

    The post's own media is a hash duplicate of the preview, so its record
    names the same file and follows the rename. A preview whose type has no
    known extension keeps its name.
    """
    store = entity_store
    acct_id = snowflake_id()
    preview, duplicate, odd_preview, odd_parent = (snowflake_id() for _ in range(4))
    stamp = "2026-01-02_at_03-04_UTC"
    old_name = f"{stamp}_preview_id_{preview}.None"
    odd_name = f"{stamp}_preview_id_{odd_preview}.None"
    await store.save(AccountFactory.build(id=acct_id, username="creator"))
    for media_id, mimetype, name in (
        (preview, "video/mp4", old_name),
        (duplicate, "video/mp4", old_name),
        (odd_preview, "application/octet-stream", odd_name),
        (odd_parent, "video/mp4", None),
    ):
        await store.save(
            MediaFactory.build(
                id=media_id,
                accountId=acct_id,
                mimetype=mimetype,
                createdAt=_NAMED_AT,
                local_filename=name,
                is_downloaded=True,
            )
        )
    for parent, shown in ((duplicate, preview), (odd_parent, odd_preview)):
        await store.save(
            AccountMediaFactory.build(
                id=parent, accountId=acct_id, mediaId=parent, previewId=shown
            )
        )
    previews = tmp_path / "creator" / "Timeline" / "Videos" / "Previews"
    previews.mkdir(parents=True)
    (previews / old_name).write_text("video")
    (previews / odd_name).write_text("unknown")
    config.separate_previews = True
    config.repair_previews = mode
    state = DownloadStateFactory.build(
        creator_id=acct_id, download_path=tmp_path / "creator"
    )

    await repair_preview_folder_items(config, state)

    new_name = f"{stamp}_preview_id_{preview}.mp4"
    expected = old_name if mode == "dry-run" else new_name
    assert sorted(p.name for p in previews.iterdir()) == sorted([expected, odd_name])
    assert {
        media_id: (await store.get(Media, media_id)).local_filename
        for media_id in (preview, duplicate, odd_preview)
    } == {preview: expected, duplicate: expected, odd_preview: odd_name}


@pytest.mark.parametrize("mode", [True, "dry-run"], ids=["repair", "dry-run"])
@pytest.mark.asyncio
async def test_repair_removes_a_second_save_of_the_same_video(
    entity_store, config, tmp_path, mode, caplog
):
    """A video saved twice keeps only the copy named as the database says.

    Two saves of one video differ in bytes but barely in size. That holds for
    a video under two times in its name, and for a preview saved under both
    Timeline and Messages. Dry-run only says what it would delete.
    """
    caplog.set_level(logging.INFO)
    store = entity_store
    acct_id = snowflake_id()
    video, preview, parent = (snowflake_id() for _ in range(3))
    await store.save(AccountFactory.build(id=acct_id, username="creator"))
    root = tmp_path / "creator"
    videos = root / "Timeline" / "Videos"
    wrong = videos / f"2025-02-23_at_17-08_UTC_id_{video}.mp4"
    right = videos / f"2025-02-23_at_16-59_UTC_id_{video}.mp4"
    preview_name = f"2026-01-02_at_03-04_UTC_preview_id_{preview}.mp4"
    kept_preview = videos / "Previews" / preview_name
    message_copy = root / "Messages" / "Videos" / "Previews" / preview_name
    await store.save(
        MediaFactory.build(
            id=video,
            accountId=acct_id,
            mimetype="video/mp4",
            createdAt=datetime(2025, 2, 23, 16, 59, tzinfo=UTC),
            local_filename=wrong.name,
            is_downloaded=True,
        )
    )
    await store.save(
        MediaFactory.build(
            id=preview, accountId=acct_id, mimetype="video/mp4", createdAt=_NAMED_AT
        )
    )
    await store.save(MediaFactory.build(id=parent, accountId=acct_id))
    await store.save(
        AccountMediaFactory.build(
            id=parent, accountId=acct_id, mediaId=parent, previewId=preview
        )
    )
    for path, content in (
        (wrong, b"A" * 1000),
        (right, b"B" * 1002),
        (kept_preview, b"C" * 1000),
        (message_copy, b"D" * 1003),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    config.separate_previews = True
    config.repair_previews = mode
    state = DownloadStateFactory.build(creator_id=acct_id, download_path=root)

    await repair_preview_folder_items(config, state)

    left = sorted(p for p in root.rglob("*") if p.is_file())
    messages = [r.getMessage() for r in caplog.records]
    recorded = (await store.get(Media, video)).local_filename
    if mode == "dry-run":
        assert left == sorted([wrong, right, kept_preview, message_copy])
        assert recorded == wrong.name
        assert any(f"[dry-run] delete {wrong}" in m for m in messages)
        assert any(f"[dry-run] delete {message_copy}" in m for m in messages)
    else:
        assert left == sorted([right, kept_preview])
        assert recorded == right.name
        assert right.read_bytes() == b"B" * 1002
