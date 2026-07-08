"""Fansly Download Functionality"""

from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
import traceback
from asyncio import sleep as async_sleep
from collections.abc import Sequence
from pathlib import Path
from typing import IO

from rich.progress import BarColumn, Progress, TextColumn
from rich.table import Column

from config import FanslyConfig
from errors import (
    DownloadError,
    DuplicateCountError,
    M3U8Error,
    MediaError,
    MediaFilteredError,
)
from fileio.dedupe import dedupe_media_file, get_filename_only
from fileio.fnmanip import get_hash_for_image, get_hash_for_other_content
from helpers.common import batch_list, expect_dict
from helpers.rich_progress import get_progress_manager
from helpers.timer import timing_jitter
from media import parse_media_info
from metadata import process_media_download
from metadata.media import process_media_info
from metadata.models import AccountMedia, AccountMediaBundle, Media, get_store
from pathio import get_media_save_path, set_create_directory_for_download
from textio import (
    input_enter_continue,
    print_debug,
    print_error,
    print_info,
    print_warning,
)

from .downloadstate import DownloadState
from .m3u8 import download_m3u8
from .mediafilters import (
    check_media_filters,
    estimate_stream_size_gate,
    record_filter_observation,
    resolve_media_filters,
)
from .types import DownloadType


def _media_type_label(mimetype: str | None) -> str:
    """Extract the media-type label from a mimetype for display.

    ``"image/jpeg"`` -> ``"image"``. Returns a safe fallback for a missing or
    slash-less mimetype so progress and dedup messages never crash on an unset
    value (Media.mimetype is typed ``str | None``).
    """
    if not mimetype:
        return "media"
    parts = mimetype.split("/")
    return parts[-2] if len(parts) >= 2 else mimetype


def _print_filtered_skip(config: FanslyConfig, media: Media, reason: str) -> None:
    """Print the standard filtered-skip line, honoring the display toggles."""
    if config.show_downloads and config.show_skipped_downloads:
        print_info(
            f"Filtered [{reason}]: {_media_type_label(media.mimetype)} "
            f"'{media.get_file_name()}' -> skipped"
        )


async def fetch_and_process_media(
    config: FanslyConfig,
    state: DownloadState,
    media_ids: Sequence[int | str],
    post_id: str | None = None,
) -> list[Media]:
    """Fetch accountMedia from API, persist to DB, select download variants.

    Returns:
        List of Media objects with download fields populated, filtered to accessible.
    """
    if not media_ids:
        return []

    api = config.get_api()
    all_media: list[Media] = []
    progress = get_progress_manager()

    with progress.session():
        fetch_task = progress.add_task(
            name="fetch_media",
            description="Fetching media info",
            total=len(media_ids),
            show_elapsed=True,
        )

        for ids in batch_list(media_ids, config.BATCH_SIZE):
            media_ids_str = ",".join(str(mid) for mid in ids)

            response = await api.get_account_media(media_ids_str)
            media_infos = api.get_json_response_contents(response)
            if not isinstance(media_infos, list):
                raise TypeError("Fansly API: expected an account-media array response")

            # Persist Media + AccountMedia via Pydantic pipeline
            await process_media_info(config, {"batch": media_infos})

            # Select best variant for each item
            for info in media_infos:
                try:
                    all_media.append(
                        await parse_media_info(
                            state,
                            expect_dict(info, "media info"),
                            post_id,
                            interactive=config.interactive,
                        )
                    )
                except Exception:
                    print_error(
                        f"Unexpected error parsing "
                        f"{state.download_type_str()} content;"
                        f"\n{traceback.format_exc()}",
                        42,
                    )
                    await input_enter_continue(config.interactive)

            progress.update_task(fetch_task, advance=len(ids))

    return [
        m
        for m in all_media
        if m.download_url and (not m.is_preview or config.download_media_previews)
    ]


async def refresh_locked_account_media(
    config: FanslyConfig,
    creator_id: int,
) -> int:
    """Re-fetch AccountMedia / AccountMediaBundle for ``creator_id`` whose
    access state could have flipped after a subscription / follow change.

    Filter — only rows that can actually transition state:
        AccountMedia:        deleted=False AND access=False
        AccountMediaBundle:  deleted=False AND access=False
                             AND purchased=False AND whitelisted=False

    Bundle's own access/purchased/whitelisted columns are not refreshed
    directly (no dedicated bundle endpoint); we re-fetch the bundle's
    constituent AccountMedia and trust the bundle's own state to refresh
    on the next natural pagination encounter.

    Returns:
        Number of AccountMedia IDs queued for refresh (post-dedup).
    """
    store = get_store()

    locked_am = store.filter(
        AccountMedia,
        lambda am: am.accountId == creator_id and not am.deleted and not am.access,
    )
    locked_bundles = store.filter(
        AccountMediaBundle,
        lambda b: (
            b.accountId == creator_id
            and not b.deleted
            and not b.access
            and not b.purchased
            and not b.whitelisted
        ),
    )

    refresh_ids: set[int] = {am.id for am in locked_am if am.id is not None}
    for bundle in locked_bundles:
        for am in bundle.accountMedia or []:
            if am.id is not None:
                refresh_ids.add(am.id)

    if not refresh_ids:
        return 0

    print_info(
        f"Refreshing {len(refresh_ids)} locked AccountMedia for creator "
        f"{creator_id} (access-change detected)."
    )

    api = config.get_api()
    for ids in batch_list(sorted(refresh_ids), config.BATCH_SIZE):
        media_ids_str = ",".join(str(mid) for mid in ids)
        try:
            response = await api.get_account_media(media_ids_str)
            response.raise_for_status()
            media_infos = api.get_json_response_contents(response)
            await process_media_info(config, {"batch": media_infos})
        except Exception:
            print_error(
                f"AM refresh batch failed for creator {creator_id};"
                f"\n{traceback.format_exc()}",
                44,
            )

    return len(refresh_ids)


def _validate_media(media: Media) -> None:
    """Validate media has required download fields."""
    if media.mimetype is None:
        raise MediaError("MIME type for media item not defined. Aborting.")
    if media.download_url is None:
        raise MediaError("Download URL for media item not defined. Aborting.")


def _update_media_type_stats(state: DownloadState, media: Media) -> None:
    """Update in-memory media type statistics."""
    media_id = str(
        media.preview_id if media.is_preview else (media.download_id or media.id)
    )

    mimetype = media.mimetype or ""
    if "image" in mimetype:
        state.recent_photo_media_ids.add(media_id)
    elif "video" in mimetype:
        state.recent_video_media_ids.add(media_id)
    elif "audio" in mimetype:
        state.recent_audio_media_ids.add(media_id)


async def _verify_existing_file(
    config: FanslyConfig,
    state: DownloadState,
    media: Media,
    check_path: Path,
    is_preview: bool = False,
) -> bool:
    """Verify existing file hash. Returns True if file can be skipped."""
    print_debug(
        f"Calculating hash for existing {'preview ' if is_preview else ''}file: {check_path}"
    )

    mimetype = (media.preview_mimetype if is_preview else media.mimetype) or ""

    hash_func = (
        get_hash_for_image if "image" in mimetype else get_hash_for_other_content
    )
    existing_hash = await asyncio.to_thread(hash_func, check_path)
    print_debug(
        f"Existing {'preview ' if is_preview else ''}file hash: {existing_hash}"
    )

    if media.content_hash == existing_hash:
        if config.show_downloads and config.show_skipped_downloads:
            print_info(
                f"Deduplication [Hash]: {_media_type_label(mimetype)} '{check_path.name}' → skipped (hash verified)"
            )
        state.add_duplicate()

        if "image" in mimetype:
            state.pic_count += 1
        elif "video" in mimetype:
            state.vid_count += 1

        return True

    return False


async def _verify_temp_download(
    config: FanslyConfig,
    state: DownloadState,
    media: Media,
    check_path: Path,
    is_preview: bool = False,
) -> bool:
    """Download to temp file and verify hash. Returns True if can be skipped."""
    store = get_store()
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w+b",
            suffix=check_path.suffix,
            dir=config.temp_folder,
            delete=False,
        ) as temp_file:
            temp_path = Path(temp_file.name)

            url = media.preview_url if is_preview else media.download_url
            mimetype = (media.preview_mimetype if is_preview else media.mimetype) or ""

            if url is None:
                return False
            await _download_file(config, url, temp_file)

        hash_func = (
            get_hash_for_image if "image" in mimetype else get_hash_for_other_content
        )
        temp_hash = await asyncio.to_thread(hash_func, temp_path)

        if temp_hash == media.content_hash:
            media.content_hash = temp_hash
            media.local_filename = get_filename_only(check_path)
            media.is_downloaded = True
            await store.save(media)

            await asyncio.to_thread(
                check_path.parent.mkdir, parents=True, exist_ok=True
            )
            await asyncio.to_thread(shutil.move, str(temp_path), str(check_path))

            if config.show_downloads and config.show_skipped_downloads:
                print_info(
                    f"Deduplication [File]: {_media_type_label(mimetype)} '{check_path.name}' → skipped (hash verified)"
                )
            state.add_duplicate()

            if "image" in mimetype:
                state.pic_count += 1
            elif "video" in mimetype:
                state.vid_count += 1

            return True

    finally:
        if temp_path is not None and await asyncio.to_thread(temp_path.exists):
            await asyncio.to_thread(temp_path.unlink)

    return False


async def _download_file(
    config: FanslyConfig, url: str, output_file: IO[bytes]
) -> None:
    """Download file from URL to output file."""
    response = None
    try:
        response = await config.get_api().get_with_ngsw(
            url=url,
            stream=True,
            add_fansly_headers=False,
        )
        if response.status_code != 200:
            body = await response.aread()
            raise DownloadError(
                f"Download failed due to an "
                f"error --> status_code: {response.status_code} "
                f"| content: \n{body.decode('utf-8', errors='replace')} [13]"
            )

        async for chunk in response.aiter_bytes(chunk_size=1_048_576):
            if chunk:
                output_file.write(chunk)
        output_file.flush()
    finally:
        if response is not None:
            await response.aclose()


async def _download_regular_file(
    config: FanslyConfig,
    state: DownloadState,
    media: Media,
    file_save_path: Path,
) -> None:
    """Download a regular media file with progress bar."""
    download_url = media.download_url
    if download_url is None:
        raise DownloadError(
            f"Cannot download {media.get_file_name()}: no download URL resolved."
        )
    response = None
    try:
        response = await config.get_api().get_with_ngsw(
            url=download_url,
            stream=True,
            add_fansly_headers=False,
        )
        if response.status_code == 200:
            text_column = TextColumn("", table_column=Column(ratio=1))
            bar_column = BarColumn(bar_width=60, table_column=Column(ratio=5))
            file_size = int(response.headers.get("content-length", 0))

            filters = resolve_media_filters(config, state)
            if filters is not None and file_size > 0:
                reason = filters.size_verdict(file_size)
                if reason:
                    raise MediaFilteredError(reason, observed=file_size)

            disable_loading_bar = file_size < 20_000_000

            # Stream into a sibling temp file so a mid-stream crash doesn't
            # leave a partial file at file_save_path that dedupe later trusts.
            tmp_kwargs: dict = {
                "dir": str(file_save_path.parent),
                "prefix": f".{file_save_path.name}.",
                "suffix": ".part",
                "delete": False,
            }
            tmp_path: Path | None = None
            try:
                with Progress(
                    text_column,
                    bar_column,
                    expand=True,
                    transient=True,
                    disable=disable_loading_bar,
                ) as progress:
                    task_id = progress.add_task("", total=file_size)

                    with tempfile.NamedTemporaryFile(**tmp_kwargs) as temp_file:
                        tmp_path = Path(temp_file.name)
                        async for chunk in response.aiter_bytes(chunk_size=1_048_576):
                            if chunk:
                                temp_file.write(chunk)
                                progress.advance(task_id, len(chunk))

                shutil.move(str(tmp_path), str(file_save_path))
                tmp_path = None  # ownership transferred
            finally:
                if tmp_path is not None and tmp_path.exists():
                    tmp_path.unlink(missing_ok=True)

            ts = media.created_at_timestamp
            if ts:
                os.utime(file_save_path, (ts, ts))
        else:
            body = await response.aread()
            raise DownloadError(
                f"Download failed on filename {media.get_file_name()} due to an "
                f"error --> status_code: {response.status_code} "
                f"| content: \n{body.decode('utf-8', errors='replace')} [13]"
            )
    finally:
        if response is not None:
            await response.aclose()


async def _download_m3u8_file(
    config: FanslyConfig,
    state: DownloadState,
    media: Media,
    check_path: Path,
) -> bool:
    """Download and process an m3u8 file. Returns True if duplicate."""
    store = get_store()
    download_url = media.download_url
    if download_url is None:
        raise DownloadError(
            f"Cannot download {media.get_file_name()}: no download URL resolved."
        )
    temp_folder = config.temp_folder
    if temp_folder:
        # mkdtemp does not create intermediates; ensure the parent
        # exists or we crash with FileNotFoundError mid-download.
        await asyncio.to_thread(Path(temp_folder).mkdir, parents=True, exist_ok=True)
    temp_dir = Path(tempfile.mkdtemp(dir=temp_folder))
    temp_path = temp_dir / f"temp_{check_path.name}"

    try:
        await estimate_stream_size_gate(config, state, media)
        filters = resolve_media_filters(config, state)

        # Run synchronous HLS download in thread to avoid blocking
        # the event loop (progress bars, rate limiter would freeze otherwise)
        temp_path = await asyncio.to_thread(
            download_m3u8,
            config,
            download_url,
            temp_path,
            media.created_at_timestamp,
            max_bytes=filters.file_size_max if filters else None,
        )

        filters = resolve_media_filters(config, state)
        if filters is not None:
            final_size = (await asyncio.to_thread(temp_path.stat)).st_size
            reason = filters.size_verdict(final_size)
            if reason:
                raise MediaFilteredError(reason, observed=final_size)

        new_hash = await asyncio.to_thread(get_hash_for_other_content, temp_path)

        existing_by_hash = await store.find_one(
            Media, content_hash=new_hash, is_downloaded=True
        )
        effective_id = media.download_id or media.id
        if existing_by_hash and existing_by_hash.id == effective_id:
            existing_by_hash = None

        if existing_by_hash:
            media.content_hash = new_hash
            media.local_filename = existing_by_hash.local_filename
            media.is_downloaded = True
            await store.save(media)

            if config.show_downloads and config.show_skipped_downloads:
                print_info(
                    f"Deduplication [Hash]: {_media_type_label(media.mimetype)} '{temp_path.name}' → "
                    f"skipped (duplicate of {Path(existing_by_hash.local_filename or '').name})"
                )
            state.add_duplicate()
            return True

        await asyncio.to_thread(check_path.parent.mkdir, parents=True, exist_ok=True)
        await asyncio.to_thread(shutil.move, str(temp_path), str(check_path))

        media.content_hash = new_hash
        media.local_filename = get_filename_only(check_path)
        media.is_downloaded = True
        await store.save(media)

        state.vid_count += 1
        return False

    finally:
        if await asyncio.to_thread(temp_dir.exists):
            await asyncio.to_thread(shutil.rmtree, temp_dir)


async def download_media(
    config: FanslyConfig,
    state: DownloadState,
    accessible_media: list[Media],
) -> None:
    """Downloads all media items to their respective target folders."""
    if state.download_type == DownloadType.NOTSET:
        raise RuntimeError(
            "Internal error during media download - download type not set on state."
        )

    if not accessible_media:
        return

    set_create_directory_for_download(config, state)

    progress = get_progress_manager()
    dl_type = state.download_type_str()

    with progress.session():
        dl_task = progress.add_task(
            name="download_media",
            description=f"Downloading {dl_type} media",
            total=len(accessible_media),
            show_elapsed=False,
        )

        for media in accessible_media:
            try:
                if (
                    config.use_duplicate_threshold
                    and state.duplicate_count > config.DUPLICATE_THRESHOLD
                    and config.DUPLICATE_THRESHOLD >= 50
                ):
                    raise DuplicateCountError(state.duplicate_count)

                if media.is_preview and not config.download_media_previews:
                    continue

                filter_reason = check_media_filters(config, state, media)
                if filter_reason:
                    await record_filter_observation(media, reason=filter_reason)
                    _print_filtered_skip(config, media, filter_reason)
                    state.filtered_count += 1
                    continue

                try:
                    _validate_media(media)
                except MediaError as e:
                    print_warning(f"Skipping download: {e}")
                    continue

                # Persist media to DB — returns None if already downloaded
                try:
                    result = await process_media_download(config, state, media)
                    if result is None:
                        if config.show_downloads and config.show_skipped_downloads:
                            print_info(
                                f"Deduplication [Database]: {_media_type_label(media.mimetype)} '{media.get_file_name()}' → skipped (already downloaded)"
                            )
                        state.add_duplicate()
                        continue
                except Exception as e:
                    print_warning(f"Skipping download: {e}")
                    continue

                _update_media_type_stats(state, media)

                try:
                    file_save_dir, file_save_path = get_media_save_path(
                        config, state, media
                    )
                    filename = media.get_file_name()

                    if media.file_extension == "m3u8":
                        file_save_path = (
                            file_save_path.parent / f"{file_save_path.stem}.mp4"
                        )
                        filename = f"{Path(filename).stem}.mp4"
                except ValueError as e:
                    print_warning(f"Skipping download: {e}")
                    continue

                if not await asyncio.to_thread(file_save_dir.exists):
                    await asyncio.to_thread(file_save_dir.mkdir, parents=True)

                check_path = file_save_path
                media.local_path = str(check_path)

                if await asyncio.to_thread(check_path.exists):
                    if await _verify_existing_file(config, state, media, check_path):
                        continue

                    if media.file_extension != "m3u8" and await _verify_temp_download(
                        config, state, media, check_path
                    ):
                        continue

                if config.show_downloads:
                    print_info(
                        f"Downloading {_media_type_label(media.mimetype)} '{filename}'"
                    )

                try:
                    if media.file_extension == "m3u8":
                        is_dupe = await _download_m3u8_file(
                            config=config,
                            state=state,
                            media=media,
                            check_path=file_save_path,
                        )
                        if is_dupe:
                            continue
                        # _download_m3u8_file already increments vid_count
                    else:
                        await _download_regular_file(
                            config, state, media, file_save_path
                        )

                        if not await asyncio.to_thread(file_save_path.exists):
                            print_warning(
                                f"File not found at expected path: {file_save_path}"
                            )
                            continue

                        media_mime = media.mimetype or ""
                        is_dupe = await dedupe_media_file(
                            config, state, media_mime, file_save_path, media
                        )

                        state.pic_count += 1 if "image" in media_mime else 0
                        state.vid_count += 1 if "video" in media_mime else 0

                        if is_dupe:
                            state.add_duplicate()

                except MediaFilteredError as e:
                    await record_filter_observation(
                        media,
                        reason=e.reason,
                        observed=e.observed,
                        estimated=e.estimated,
                    )
                    _print_filtered_skip(config, media, e.reason)
                    state.filtered_count += 1
                    continue
                except M3U8Error as ex:
                    print_warning(f"Skipping invalid item: {ex}")

                await async_sleep(timing_jitter(0.4, 0.75))
            finally:
                progress.update_task(dl_task, advance=1)
