"""Filesystem backfill that repairs already-downloaded preview files.

Reconstructs the persisted preview set from ``AccountMedia.previewId`` /
``AccountMediaBundle.previewId``, then renames/moves bug-era preview files to
the canonical ``preview_id_`` marker (and ``Previews/`` folder when
``separate_previews``), updating ``Media.local_filename``. Gated by the
``repair_previews`` flag; a sibling pass to ``dedupe_init``.
"""

import asyncio
import filecmp
from pathlib import Path

from loguru import logger

from config import FanslyConfig
from download.downloadstate import DownloadState
from fileio.dedupe import safe_rglob
from fileio.normalize import get_id_from_filename, normalize_filename
from metadata import AccountMedia, AccountMediaBundle, Media
from metadata.models import get_store
from textio import print_info


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
            await asyncio.to_thread(source.unlink)
        else:
            logger.warning(
                f"preview_repair: target exists with different content, "
                f"skipping {source.name} -> {target} (media_id={media_id})"
            )
        return None

    await asyncio.to_thread(target.parent.mkdir, parents=True, exist_ok=True)
    await asyncio.to_thread(source.rename, target)

    store = get_store()
    media = await store.get(Media, media_id)
    if media is not None:
        media.local_filename = target.name
        await store.save(media)
    return target


async def repair_preview_folder_items(
    config: FanslyConfig,
    state: DownloadState,
) -> None:
    """Backfill preview-marker + Previews/ folder for already-downloaded files.

    Walks the creator folder, classifies each file via the persisted preview
    set, asks normalize_filename for the canonical (preview-correct) basename,
    and renames/moves into place (DB local_filename updated). Honors the
    three-state ``repair_previews`` flag; dry-run logs intentions only. When
    Stash is active, closes with a blocking rescan + cache invalidation over
    the renamed paths.
    """
    if not config.repair_previews:
        return
    if not state.download_path or state.creator_id is None:
        return

    dry_run = config.repair_previews == "dry-run"
    preview_ids = await build_preview_id_set(state.creator_id)
    if not preview_ids:
        return

    print_info(
        f"Repairing preview files for: {state.download_path}"
        f"{' (dry-run)' if dry_run else ''}"
    )

    renamed_paths: list[str] = []
    try:
        all_files = [
            f
            for f in await safe_rglob(state.download_path, "*")
            if await asyncio.to_thread(f.is_file)
        ]
        for file_path in all_files:
            if not _classify(file_path.name, preview_ids):
                continue
            media_id, _ = get_id_from_filename(file_path.name)
            if media_id is None:
                continue
            canonical_name = await normalize_filename(
                file_path.name, config=config, preview_ids=preview_ids
            )
            target = _canonical_target(
                file_path,
                canonical_name,
                is_preview=True,
                separate=config.separate_previews,
            )
            if target == file_path:
                continue  # already canonical - idempotent
            if dry_run:
                print_info(f"[dry-run] {file_path} -> {target}")
                continue
            moved = await _safe_move(file_path, target, media_id=media_id)
            if moved is not None:
                renamed_paths.append(str(moved))
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
