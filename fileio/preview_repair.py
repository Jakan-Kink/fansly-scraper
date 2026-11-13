"""Filesystem backfill that repairs already-downloaded files' names.

Reconstructs the persisted preview set from ``AccountMedia.previewId`` /
``AccountMediaBundle.previewId``, then renames/moves bug-era preview files to
the canonical ``preview_id_`` marker (and ``Previews/`` folder when
``separate_previews``), gives every media file the database's UTC timestamp,
and removes identical duplicate copies, updating ``Media.local_filename``.
Gated by the ``repair_previews`` flag; a sibling pass to ``dedupe_init``.
"""

import asyncio
import filecmp
import re
from pathlib import Path

from loguru import logger

from config import FanslyConfig
from download.downloadstate import DownloadState
from fileio.dedupe import safe_rglob
from fileio.normalize import get_id_from_filename, normalize_filename
from metadata import AccountMedia, AccountMediaBundle, Media
from metadata.models import get_store
from textio import print_info


_TIMESTAMP_PREFIX = re.compile(r"^\d{4}-\d{2}-\d{2}_at_\d{2}-\d{2}(?:_[A-Z]+)?_")


async def build_preview_id_set(account_id: int) -> set[int]:
    """Return the set of Media ids that are persisted previews for a creator.

    Args:
        account_id: Creator account id to scope the lookup.

    Returns:
        Set of ``previewId`` values across AccountMedia and AccountMediaBundle.
    """
    store = get_store()
    ids: set[int] = set()
    # The is-not-None checks narrow SnowflakeId | None -> int for the set; the
    # previewId__null=False filter already guarantees non-None at runtime.
    for am in await store.find(
        AccountMedia, accountId=account_id, previewId__null=False
    ):
        if am.previewId is not None:
            ids.add(am.previewId)
    for bundle in await store.find(
        AccountMediaBundle, accountId=account_id, previewId__null=False
    ):
        if bundle.previewId is not None:
            ids.add(bundle.previewId)
    return ids


def _classify(filename: str, preview_ids: set[int]) -> bool:
    """True iff the file's media id is a persisted preview."""
    media_id, _ = get_id_from_filename(filename)
    return media_id is not None and media_id in preview_ids


def _canonical_target(
    source: Path,
    canonical_name: str,
    *,
    is_preview: bool,
    separate: bool,
) -> Path:
    """Compute the destination path for a file's canonical name + folder.

    When ``is_preview`` and ``separate`` and the file is not already under a
    ``Previews/`` directory, the target is ``<media-type>/Previews/<name>``;
    otherwise the target is ``<source.parent>/<name>``.
    """
    parent = source.parent
    if is_preview and separate and parent.name != "Previews":
        parent = parent / "Previews"
    return parent / canonical_name


async def _files_identical(a: Path, b: Path) -> bool:
    """True iff the two files are byte-for-byte identical."""
    return await asyncio.to_thread(filecmp.cmp, a, b, shallow=False)


async def _safe_move(source: Path, target: Path, *, media_id: int) -> Path | None:
    """Rename/move ``source`` to ``target`` and update Media.local_filename.

    Collision rule: if ``target`` already exists, compare byte content -
    identical -> unlink the duplicate ``source`` (return None); different ->
    skip and warn (return None, never clobber). On a clean move, update
    ``Media.local_filename`` to the new basename and return the new path.
    """
    if target == source:
        return None  # idempotent: already canonical

    if await asyncio.to_thread(target.exists):
        if await _files_identical(source, target):
            # Byte-identical duplicate: the source content now lives only at
            # target, so repoint the record at the survivor — not the file we
            # just deleted — or the DB strands a Media on a nonexistent path.
            await asyncio.to_thread(source.unlink)
            await _repoint_media(media_id, target.name)
        else:
            logger.warning(
                f"preview_repair: target exists with different content, "
                f"skipping {source.name} -> {target} (media_id={media_id})"
            )
        return None

    await asyncio.to_thread(target.parent.mkdir, parents=True, exist_ok=True)
    await asyncio.to_thread(source.rename, target)

    await _repoint_media(media_id, target.name)
    return target


async def _repoint_media(media_id: int, new_basename: str) -> None:
    """Point a Media's ``local_filename`` at ``new_basename`` and persist it.

    No-op (with a debug note) when the id has no Media record, so a file
    operation without a matching DB row doesn't pass silently.
    """
    store = get_store()
    media = await store.get(Media, media_id)
    if media is None:
        logger.debug(
            f"preview_repair: no Media for id={media_id}; "
            f"local_filename not updated to {new_basename!r}"
        )
        return
    media.local_filename = new_basename
    await store.save(media)


def _name_from_database(name: str, media: Media | None) -> str:
    """Give ``name`` the timestamp the database holds for its media.

    Two downloads of one media can differ in the minute; the database's
    ``createdAt`` decides which name is right. Unchanged when there is no
    Media record, the name carries no timestamp, or it is a legacy name with
    the content hash in it (``dedupe_init`` owns those).
    """
    if "_hash" in name or media is None or media.createdAt is None:
        return name
    stamp = media.createdAt.strftime("%Y-%m-%d_at_%H-%M_UTC")
    return _TIMESTAMP_PREFIX.sub(f"{stamp}_", name, count=1)


async def _drop_message_copies(
    previews: dict[int, list[Path]], root: Path, *, dry_run: bool
) -> None:
    """Delete a preview's Messages copy when Timeline holds the same file.

    A preview used in a post and in a message is downloaded under both; the
    Timeline copy is the one kept. Copies that differ in content both stay.

    Args:
        previews: Preview media id -> the files found for it
        root: The creator folder the files sit under
        dry_run: Log the deletion without doing it
    """
    for media_id, paths in previews.items():
        by_area: dict[str, list[Path]] = {}
        for path in paths:
            by_area.setdefault(path.relative_to(root).parts[0], []).append(path)
        timeline = by_area.get("Timeline", [])
        for copy in by_area.get("Messages", []) if timeline else []:
            if not await _files_identical(copy, timeline[0]):
                logger.warning(
                    f"preview_repair: {copy} and {timeline[0]} differ in "
                    f"content; keeping both (media_id={media_id})"
                )
            elif dry_run:
                print_info(f"[dry-run] delete {copy} (same as {timeline[0]})")
            else:
                await asyncio.to_thread(copy.unlink)


async def repair_preview_folder_items(
    config: FanslyConfig,
    state: DownloadState,
) -> None:
    """Bring already-downloaded files to their canonical name and folder.

    Walks the creator folder and gives each media file the name the database
    says it should have: the ``preview_id_`` marker and ``Previews/`` folder
    for persisted previews, and the database's UTC timestamp. A file that
    lands on an identical existing one is removed as a duplicate, and a
    preview held under both Timeline and Messages keeps only the Timeline
    copy. ``Media.local_filename`` follows each rename. Honors the three-state
    ``repair_previews`` flag; dry-run logs intentions only. When Stash is
    active, closes with a blocking rescan + cache invalidation over the
    renamed paths.
    """
    if not config.repair_previews:
        return
    if not state.download_path or state.creator_id is None:
        return

    dry_run = config.repair_previews == "dry-run"
    preview_ids = await build_preview_id_set(state.creator_id)

    print_info(
        f"Repairing file names for: {state.download_path}"
        f"{' (dry-run)' if dry_run else ''}"
    )

    renamed_paths: list[str] = []
    # Where each preview ended up, for the Timeline/Messages duplicate check.
    previews: dict[int, list[Path]] = {}
    try:
        all_files = [
            f
            for f in await safe_rglob(state.download_path, "*")
            if await asyncio.to_thread(f.is_file)
        ]
        for file_path in all_files:
            media_id, _ = get_id_from_filename(file_path.name)
            if media_id is None:
                continue
            is_preview = _classify(file_path.name, preview_ids)
            media = await get_store().get(Media, media_id)
            if not is_preview and (media is None or media.createdAt is None):
                # Only the database can say what a file should be called;
                # without it a name in local time would be guessed at.
                continue
            canonical_name = _name_from_database(
                await normalize_filename(
                    file_path.name, config=config, preview_ids=preview_ids
                ),
                media,
            )
            target = (
                _canonical_target(
                    file_path,
                    canonical_name,
                    is_preview=True,
                    separate=config.separate_previews,
                )
                if is_preview
                else file_path.with_name(canonical_name)
            )
            final: Path | None = file_path
            if target != file_path:
                if dry_run:
                    print_info(f"[dry-run] {file_path} -> {target}")
                else:
                    moved = await _safe_move(file_path, target, media_id=media_id)
                    if moved is not None:
                        renamed_paths.append(str(moved))
                    # A source that no longer exists was a duplicate of target.
                    final = moved or (
                        file_path if await asyncio.to_thread(file_path.exists) else None
                    )
            if is_preview and final is not None:
                previews.setdefault(media_id, []).append(final)
        await _drop_message_copies(previews, state.download_path, dry_run=dry_run)
    finally:
        if not dry_run and renamed_paths:
            await _rescan_and_invalidate(config, state, renamed_paths)


async def _rescan_and_invalidate(
    config: FanslyConfig,
    state: DownloadState,
    renamed_paths: list[str],
) -> None:
    """Blocking per-creator Stash rescan of renamed paths, then invalidate the
    cached file/scene/image/gallery types so a later sweep reads post-rename
    state. No-op when Stash is inactive. The store persists across creators, so
    invalidation is required, not optional.
    """
    if not config.stash_active:
        return

    from stash_graphql_client.types import (  # noqa: PLC0415  # deferred with stash
        BasicFile,
        Gallery,
        GalleryChapter,
        GalleryFile,
        Image,
        ImageFile,
        Scene,
        VideoFile,
    )

    from stash import StashProcessing  # noqa: PLC0415  # deferred: optional stash deps

    processor = StashProcessing.from_config(config, state)
    try:
        await processor.context.get_client()
        await processor.scan_creator_folder(paths=renamed_paths)
        for entity_type in (
            Gallery,
            GalleryChapter,
            Scene,
            Image,
            VideoFile,
            ImageFile,
            GalleryFile,
            BasicFile,
        ):
            processor.store.invalidate_type(entity_type)
    finally:
        await processor.cleanup()
