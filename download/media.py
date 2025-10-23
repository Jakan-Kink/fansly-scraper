"""Fansly Download Functionality"""

from __future__ import annotations

import random
import shutil
import tempfile
from asyncio import sleep as async_sleep
from pathlib import Path

from rich.progress import BarColumn, Progress, TextColumn
from rich.table import Column
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import FanslyConfig
from config.decorators import with_database_session
from errors import DownloadError, DuplicateCountError, M3U8Error, MediaError
from fileio.dedupe import dedupe_media_file
from fileio.fnmanip import get_hash_for_image, get_hash_for_other_content
from helpers.common import batch_list
from media import MediaItem
from metadata import process_media_download
from metadata.media import Media
from pathio import get_media_save_path, set_create_directory_for_download
from textio import print_debug, print_info, print_warning

from .downloadstate import DownloadState
from .m3u8 import download_m3u8
from .types import DownloadType


@with_database_session(async_session=True)
async def download_media_infos(
    config: FanslyConfig,
    state: DownloadState,
    media_ids: list[int | str],
    session: AsyncSession | None = None,
) -> list[dict]:
    """Download media info from API and process it through metadata system.

    Args:
        config: FanslyConfig instance
        state: DownloadState instance
        media_ids: List of media IDs to fetch (integers after ID conversion, or strings for legacy support)
        session: SQLAlchemy async session

    Returns:
        List of processed media info dictionaries
    """
    from metadata.account import process_media_bundles

    media_infos: list[dict] = []

    for ids in batch_list(media_ids, config.BATCH_SIZE):
        async with session.begin_nested():
            media_ids_str = ",".join(str(id) for id in ids)

            # Rate limiting is now handled by RateLimiter in FanslyApi
            # Retry on 429 since RateLimiter will apply backoff for the next attempt
            max_retries = config.api_max_retries
            for attempt in range(max_retries):
                media_info_response = config.get_api().get_account_media(media_ids_str)

                # If we got a 429, the RateLimiter has already recorded it and will
                # apply backoff on the next request. Just retry.
                if media_info_response.status_code == 429:
                    if attempt < max_retries - 1:
                        print_debug(
                            f"Rate limited (429), retrying ({attempt + 1}/{max_retries})..."
                        )
                        continue
                    else:
                        # Final attempt failed, raise the error
                        media_info_response.raise_for_status()

                # For other errors or success, exit retry loop
                break

            # Check status for non-429 errors
            media_info_response.raise_for_status()

            if media_info_response.status_code == 200:
                media_info = config.get_api().get_json_response_contents(
                    media_info_response
                )

                # Process each media item through metadata system
                for info in media_info:
                    # Process through metadata system
                    await process_media_bundles(
                        config=config,
                        account_id=state.creator_id,
                        media_bundles=[info],  # Send a copy instead of original
                        session=session,
                    )
                    media_infos.append(info)

            else:
                raise DownloadError(
                    f"Could not retrieve media info for {media_ids_str} due to an "
                    f"error --> status_code: {media_info_response.status_code} "
                    f"| content: \n{media_info_response.content.decode('utf-8')}"
                )

            # Rate limiting between requests is handled by RateLimiter in FanslyApi

    return media_infos


def _validate_media_item(media_item: MediaItem) -> None:
    """Validate media item has required fields."""
    if media_item.mimetype is None:
        raise MediaError("MIME type for media item not defined. Aborting.")
    if media_item.download_url is None:
        raise MediaError("Download URL for media item not defined. Aborting.")


def _update_media_type_stats(state: DownloadState, media_item: MediaItem) -> None:
    """Update in-memory media type statistics.

    Uses the preview_id for preview media and media_id for primary media to ensure
    correct tracking of downloaded items.
    """
    # Use preview_id if this is preview content, otherwise use media_id
    media_id = str(
        media_item.preview_id if media_item.is_preview else media_item.media_id
    )

    if "image" in media_item.mimetype:
        state.recent_photo_media_ids.add(media_id)
    elif "video" in media_item.mimetype:
        state.recent_video_media_ids.add(media_id)
    elif "audio" in media_item.mimetype:
        state.recent_audio_media_ids.add(media_id)


async def _verify_existing_file(
    config: FanslyConfig,
    state: DownloadState,
    media_item: MediaItem,
    check_path: Path,
    media_record: Media,
    is_preview: bool = False,
) -> bool:
    """Verify existing file hash and update database if needed.
    Returns True if file is verified and can be skipped.

    Args:
        config: FanslyConfig instance
        state: Current download state
        media_item: Media item to verify
        check_path: Path to existing file
        media_record: Media database record
        is_preview: If True, verify preview content instead of regular content

    Returns:
        True if file is verified and can be skipped, False otherwise
    """
    from textio import print_debug

    print_debug(
        f"Calculating hash for existing {'preview ' if is_preview else ''}file: {check_path}"
    )

    # Use appropriate mimetype based on content type
    mimetype = media_item.preview_mimetype if is_preview else media_item.mimetype

    existing_hash = (
        get_hash_for_image(check_path)
        if "image" in mimetype
        else get_hash_for_other_content(check_path)
    )
    print_debug(
        f"Existing {'preview ' if is_preview else ''}file hash: {existing_hash}"
    )

    # If we already have this hash in the database record, skip download
    if media_record.content_hash == existing_hash:
        if config.show_downloads and config.show_skipped_downloads:
            print_info(
                f"Deduplication [Hash]: {mimetype.split('/')[-2]} '{check_path.name}' → skipped (hash verified)"
            )
        # Mark as duplicate but also count as downloaded since we're verifying a good file
        state.add_duplicate()

        # Count verified files too
        if "image" in mimetype:
            state.pic_count += 1
        elif "video" in mimetype:
            state.vid_count += 1

        return True

    return False


@with_database_session(async_session=True)  # Uses config._database directly
async def _verify_temp_download(
    config: FanslyConfig,
    state: DownloadState,
    media_item: MediaItem,
    check_path: Path,
    media_record: Media,
    session: AsyncSession | None = None,
    is_preview: bool = False,
) -> bool:
    """Download to temp file and verify hash.
    Returns True if file matches and can be skipped.

    Args:
        config: FanslyConfig instance
        state: Current download state
        media_item: Media item to verify
        check_path: Path to existing file
        media_record: Media database record
        session: Optional AsyncSession for database operations
        is_preview: If True, verify preview content instead of regular content

    Returns:
        True if file matches and can be skipped, False otherwise
    """
    temp_path = None
    try:
        kwargs = {"suffix": check_path.suffix, "delete": False}
        if config.temp_folder:
            kwargs["dir"] = config.temp_folder
        with tempfile.NamedTemporaryFile(**kwargs) as temp_file:
            temp_path = Path(temp_file.name)

            # Use appropriate URL and mimetype based on content type
            url = media_item.preview_url if is_preview else media_item.download_url
            mimetype = (
                media_item.preview_mimetype if is_preview else media_item.mimetype
            )

            # Create temporary MediaItem for download
            temp_item = MediaItem(
                media_id=media_item.media_id,
                download_url=url,
                mimetype=mimetype,
                is_preview=is_preview,
            )
            _download_file(config, temp_item, temp_file)

        # Calculate hash of downloaded file
        print_debug(
            f"Calculating hash for downloaded {'preview ' if is_preview else ''}file: {temp_path}"
        )
        temp_hash = (
            get_hash_for_image(temp_path)
            if "image" in mimetype
            else get_hash_for_other_content(temp_path)
        )
        print_debug(
            f"Downloaded {'preview ' if is_preview else ''}file hash: {temp_hash}"
        )

        # Compare hashes
        if temp_hash == media_record.content_hash:
            # Update database with verified hash (but don't mark as downloaded yet)
            media_record.content_hash = temp_hash
            media_record.local_filename = str(check_path)
            session.add(media_record)
            await session.flush()

            # Move temp file to final location
            check_path.parent.mkdir(parents=True, exist_ok=True)
            print_debug(f"Moving from temp: {temp_path}")
            print_debug(f"To final location: {check_path}")
            shutil.move(str(temp_path), str(check_path))
            print_debug(f"Successfully moved to: {check_path}")

            # Only mark as downloaded after successful move
            media_record.is_downloaded = True
            session.add(media_record)
            await session.flush()

            if config.show_downloads and config.show_skipped_downloads:
                print_info(
                    f"Deduplication [File]: {mimetype.split('/')[-2]} '{check_path.name}' → skipped (hash verified)"
                )
            # Mark as duplicate but also count as downloaded since we're verifying a good file
            state.add_duplicate()

            # Count verified files too
            if "image" in mimetype:
                state.pic_count += 1
            elif "video" in mimetype:
                state.vid_count += 1

            return True

    finally:
        # Clean up temporary file
        if temp_path and temp_path.exists():
            temp_path.unlink()

    return False


def _download_file(config: FanslyConfig, media_item: MediaItem, output_file) -> None:
    """Download file from URL to output file."""
    response = None
    try:
        response = config.get_api().get_with_ngsw(
            url=media_item.download_url,
            stream=True,
            add_fansly_headers=False,
        )
        if response.status_code != 200:
            raise DownloadError(
                f"Download failed on filename {media_item.get_file_name()} due to an "
                f"error --> status_code: {response.status_code} "
                f"| content: \n{response.content.decode('utf-8')} [13]"
            )

        for chunk in response.iter_bytes(chunk_size=1_048_576):
            if chunk:
                output_file.write(chunk)
        output_file.flush()
    finally:
        # Close streaming response to free resources
        if response is not None:
            response.close()


def _download_regular_file(
    config: FanslyConfig,
    media_item: MediaItem,
    file_save_path: Path,
) -> None:
    """Download a regular media file with progress bar."""
    response = None
    try:
        response = config.get_api().get_with_ngsw(
            url=media_item.download_url,
            stream=True,
            add_fansly_headers=False,
        )
        if response.status_code == 200:
            text_column = TextColumn("", table_column=Column(ratio=1))
            bar_column = BarColumn(bar_width=60, table_column=Column(ratio=5))
            file_size = int(response.headers.get("content-length", 0))
            disable_loading_bar = False if file_size >= 20_000_000 else True

            progress = Progress(
                text_column,
                bar_column,
                expand=True,
                transient=True,
                disable=disable_loading_bar,
            )
            task_id = progress.add_task("", total=file_size)
            progress.start()

            with open(file_save_path, "wb") as output_file:
                for chunk in response.iter_bytes(chunk_size=1_048_576):
                    if chunk:
                        output_file.write(chunk)
                        progress.advance(task_id, len(chunk))

            progress.refresh()
            progress.stop()

            # Set file timestamps
            if media_item.created_at:
                import os

                os.utime(file_save_path, (media_item.created_at, media_item.created_at))
        else:
            raise DownloadError(
                f"Download failed on filename {media_item.get_file_name()} due to an "
                f"error --> status_code: {response.status_code} "
                f"| content: \n{response.content.decode('utf-8')} [13]"
            )
    finally:
        # Close streaming response to free resources
        if response is not None:
            response.close()


@with_database_session(async_session=True)  # Uses config._database directly
async def _download_m3u8_file(
    config: FanslyConfig,
    state: DownloadState,
    media_item: MediaItem,
    check_path: Path,
    media_record: Media,
    session: AsyncSession | None = None,
) -> bool:
    """Download and process an m3u8 file.
    Returns True if file was skipped as duplicate.

    Args:
        config: FanslyConfig instance
        state: Current download state
        media_item: Media item to download
        check_path: Path to save file
        media_record: Media database record
        session: Optional AsyncSession for database operations

    Returns:
        True if file was skipped as duplicate, False otherwise
    """
    kwargs = {}
    if config.temp_folder:
        kwargs["dir"] = config.temp_folder
    temp_dir = Path(tempfile.mkdtemp(**kwargs))
    temp_path = temp_dir / f"temp_{check_path.name}"

    try:
        # Download and create the video file
        temp_path = download_m3u8(
            config,
            m3u8_url=media_item.download_url,
            save_path=temp_path,
            created_at=media_item.created_at,
        )

        # Calculate hash of the new file
        new_hash = get_hash_for_other_content(temp_path)

        # Query for existing media with this hash (excluding current record)
        result = await session.execute(
            select(Media)
            .where(
                Media.content_hash == new_hash,
                Media.id != media_record.id,
                Media.is_downloaded.is_(True),
            )
            .limit(1)
        )
        existing_by_hash = result.scalar_one_or_none()

        if existing_by_hash:
            # Update current record to reference the existing duplicate file
            media_record.content_hash = new_hash
            media_record.local_filename = existing_by_hash.local_filename
            media_record.is_downloaded = True
            session.add(media_record)
            await session.flush()

            # We found a duplicate
            if config.show_downloads and config.show_skipped_downloads:
                print_info(
                    f"Deduplication [Hash]: {media_item.mimetype.split('/')[-2]} '{temp_path.name}' → "
                    f"skipped (duplicate of {Path(existing_by_hash.local_filename).name})"
                )
            state.add_duplicate()
            return True

        # No duplicate found, move file to final location
        check_path.parent.mkdir(parents=True, exist_ok=True)
        print_debug(f"Moving from temp: {temp_path}")
        print_debug(f"To final location: {check_path}")
        shutil.move(str(temp_path), str(check_path))
        print_debug(f"Successfully moved to: {check_path}")

        # Update database record only after successful move
        media_record.content_hash = new_hash
        media_record.local_filename = str(check_path)
        media_record.is_downloaded = True  # Set this after successful move
        session.add(media_record)
        await session.flush()

        # Increment video count - since this is m3u8, it's always a video
        state.vid_count += 1

        return False

    finally:
        # Clean up temp directory
        if temp_dir.exists():
            shutil.rmtree(temp_dir)


@with_database_session(async_session=True)
async def download_media(
    config: FanslyConfig,
    state: DownloadState,
    accessible_media: list[MediaItem],
    session: AsyncSession | None = None,
):
    """Downloads all media items to their respective target folders.

    Args:
        config: FanslyConfig instance
        state: Current download state
        accessible_media: List of media items to download
        session: SQLAlchemy async session
    """
    if state.download_type == DownloadType.NOTSET:
        raise RuntimeError(
            "Internal error during media download - download type not set on state."
        )

    # Create base directory for downloads
    set_create_directory_for_download(config, state)

    # loop through the accessible_media and download the media files
    for media_item in accessible_media:
        # Verify that the duplicate count has not drastically spiked
        if (
            config.use_duplicate_threshold
            and state.duplicate_count > config.DUPLICATE_THRESHOLD
            and config.DUPLICATE_THRESHOLD >= 50
        ):
            raise DuplicateCountError(state.duplicate_count)

        # Skip if this is a preview and we don't want previews
        if media_item.is_preview and not config.download_media_previews:
            continue

        # Validate media item
        try:
            _validate_media_item(media_item)
        except MediaError as e:
            print_warning(f"Skipping download: {e}")
            continue

        try:
            # Process media in database and get Media record
            media_record = await process_media_download(
                config,
                state,
                media_item,
                session=session,
            )
            if media_record is None:
                if config.show_downloads and config.show_skipped_downloads:
                    print_info(
                        f"Deduplication [Database]: {media_item.mimetype.split('/')[-2]} '{media_item.get_file_name()}' → skipped (already downloaded)"
                    )
                state.add_duplicate()
                continue
        except Exception as e:
            print_warning(f"Skipping download: {e}")
            continue

        # Update media type statistics
        _update_media_type_stats(state, media_item)

        try:
            # Get save paths from pathio
            file_save_dir, file_save_path = get_media_save_path(
                config, state, media_item
            )
            filename = media_item.get_file_name()

            # For m3u8 files, adjust paths to use mp4 extension
            if media_item.file_extension == "m3u8":
                file_save_path = file_save_path.parent / f"{file_save_path.stem}.mp4"
                filename = f"{Path(filename).stem}.mp4"
        except ValueError as e:
            print_warning(f"Skipping download: {e}")
            continue

        # Create directory if needed
        if not file_save_dir.exists():
            file_save_dir.mkdir(parents=True)

        # Use file_save_path for checking existence since we've already adjusted it for m3u8
        check_path = file_save_path

        if check_path.exists():
            # Verify existing file
            if await _verify_existing_file(
                config,
                state,
                media_item,
                check_path,
                media_record,
            ):
                continue

            # For regular files, verify by downloading to temp file
            if media_item.file_extension != "m3u8" and await _verify_temp_download(
                config,
                state,
                media_item,
                check_path,
                media_record,
                session=session,
            ):
                continue

        # Show download progress
        if config.show_downloads:
            print_info(f"Downloading {media_item.mimetype.split('/')[-2]} '{filename}'")

        try:
            # Download the file
            if media_item.file_extension == "m3u8":
                # For m3u8, we download to a temp file first, then move to final location
                is_dupe = await _download_m3u8_file(
                    config=config,
                    state=state,
                    media_item=media_item,
                    check_path=file_save_path,
                    media_record=media_record,
                    session=session,
                )
                if is_dupe:
                    continue
            else:
                _download_regular_file(config, media_item, file_save_path)

            # Verify file exists before deduping
            if not file_save_path.exists():
                print_warning(f"File not found at expected path: {file_save_path}")
                continue

            # Update database with file info
            is_dupe = await dedupe_media_file(
                config,
                state,
                media_item.mimetype,
                file_save_path,
                media_record,
                session=session,
            )

            # File was successfully downloaded and processed, increment counts
            # regardless of deduplication status (since the file was actually downloaded)
            state.pic_count += 1 if "image" in media_item.mimetype else 0
            state.vid_count += 1 if "video" in media_item.mimetype else 0

            # If it was a duplicate, also increment duplicate counter
            if is_dupe:
                state.add_duplicate()

        except M3U8Error as ex:
            print_warning(f"Skipping invalid item: {ex}")

        # Slow down a bit to be sure
        await async_sleep(random.uniform(0.4, 0.75))
