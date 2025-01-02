"""Fansly Download Functionality"""

import random
import shutil
import tempfile
from pathlib import Path
from time import sleep

from rich.progress import BarColumn, Progress, TextColumn
from rich.table import Column

from config import FanslyConfig
from errors import ApiError, DownloadError, DuplicateCountError, M3U8Error, MediaError
from fileio.dedupe import dedupe_media_file
from fileio.fnmanip import get_hash_for_image, get_hash_for_other_content
from helpers.common import batch_list
from media import MediaItem
from metadata import process_media_download, require_database_config
from pathio import get_media_save_path, set_create_directory_for_download
from textio import print_info, print_warning

from .downloadstate import DownloadState
from .m3u8 import download_m3u8
from .types import DownloadType


@require_database_config
def download_media_infos(
    config: FanslyConfig, state: DownloadState, media_ids: list[str]
) -> list[dict]:
    """Download media info from API and process it through metadata system.

    Args:
        config: FanslyConfig instance
        media_ids: List of media IDs to fetch

    Returns:
        List of processed media info dictionaries
    """
    from metadata.account import process_media_bundles

    media_infos: list[dict] = []

    for ids in batch_list(media_ids, config.BATCH_SIZE):
        media_ids_str = ",".join(ids)

        media_info_response = config.get_api().get_account_media(media_ids_str)

        media_info_response.raise_for_status()

        if media_info_response.status_code == 200:
            media_info = media_info_response.json()

            if not media_info["success"]:
                raise ApiError(
                    f"Could not retrieve media info for {media_ids_str} due to an "
                    f"API error - unsuccessful "
                    f"| content: \n{media_info}"
                )

            # Process each media item through metadata system
            for info in media_info["response"]:
                # Process through metadata system
                with config._database.sync_session() as session:
                    process_media_bundles(
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

        # Slow down a bit to be sure
        sleep(random.uniform(0.4, 0.75))

    return media_infos


@require_database_config
def download_media(
    config: FanslyConfig, state: DownloadState, accessible_media: list[MediaItem]
):
    """Downloads all media items to their respective target folders."""
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

        # "None" safeguards
        if media_item.mimetype is None:
            raise MediaError("MIME type for media item not defined. Aborting.")
        if media_item.download_url is None:
            raise MediaError("Download URL for media item not defined. Aborting.")

        # Process media in database and get Media record
        media_record = process_media_download(config, state, media_item)
        if media_record is None:
            if config.show_downloads and config.show_skipped_downloads:
                print_info(
                    f"Deduplication [Database]: {media_item.mimetype.split('/')[-2]} '{media_item.get_file_name()}' → skipped (already downloaded)"
                )
            state.add_duplicate()
            continue

        # Add to in-memory sets for legacy compatibility
        if "image" in media_item.mimetype:
            state.recent_photo_media_ids.add(media_item.media_id)
        elif "video" in media_item.mimetype:
            state.recent_video_media_ids.add(media_item.media_id)
        elif "audio" in media_item.mimetype:
            state.recent_audio_media_ids.add(media_item.media_id)

        try:
            # Get save paths from pathio
            file_save_dir, file_save_path = get_media_save_path(
                config, state, media_item
            )
            filename = media_item.get_file_name()
        except ValueError as e:
            print_warning(f"Skipping download: {e}")
            continue

        # Create directory if needed
        if not file_save_dir.exists():
            file_save_dir.mkdir(parents=True)

        # Check if file exists and verify hash if it does
        # For m3u8 files, check for the resulting mp4 file instead
        check_path = file_save_path
        if media_item.file_extension == "m3u8":
            check_path = file_save_path.parent / f"{file_save_path.stem}.mp4"

        if check_path.exists():

            from textio import print_debug

            # Calculate hash for existing file
            print_debug(f"Calculating hash for existing file: {check_path}")
            if "image" in media_item.mimetype:
                existing_hash = get_hash_for_image(check_path)
            else:
                existing_hash = get_hash_for_other_content(check_path)
            print_debug(f"Existing file hash: {existing_hash}")

            # If we already have this hash in the database record, skip download
            if media_record.content_hash == existing_hash:
                if config.show_downloads and config.show_skipped_downloads:
                    print_info(
                        f"Deduplication [Hash]: {media_item.mimetype.split('/')[-2]} '{check_path.name}' → skipped (hash verified)"
                    )
                state.add_duplicate()
                continue

            # For regular files, verify by downloading to temp file
            if media_item.file_extension != "m3u8":
                temp_path = None
                try:
                    kwargs = {"suffix": check_path.suffix, "delete": False}
                    if config.temp_folder:
                        kwargs["dir"] = config.temp_folder
                    with tempfile.NamedTemporaryFile(**kwargs) as temp_file:
                        temp_path = Path(temp_file.name)
                        # Download to temp file
                        with config.get_api().get_with_ngsw(
                            url=media_item.download_url,
                            stream=True,
                            add_fansly_headers=False,
                        ) as response:
                            if response.status_code != 200:
                                raise DownloadError(
                                    f"Download failed on filename {filename} due to an "
                                    f"error --> status_code: {response.status_code} "
                                    f"| content: \n{response.content.decode('utf-8')} [13]"
                                )

                            for chunk in response.iter_content(chunk_size=1_048_576):
                                if chunk:
                                    temp_file.write(chunk)
                            temp_file.flush()

                    # Calculate hash of downloaded file
                    print_debug(f"Calculating hash for downloaded file: {temp_path}")
                    temp_hash = (
                        get_hash_for_image(temp_path)
                        if "image" in media_item.mimetype
                        else get_hash_for_other_content(temp_path)
                    )
                    print_debug(f"Downloaded file hash: {temp_hash}")

                    # Compare hashes
                    if temp_hash == existing_hash:
                        # Update database with verified hash
                        with config._database.sync_session() as session:
                            session.add(media_record)
                            media_record.content_hash = existing_hash
                            media_record.local_filename = str(check_path)
                            media_record.is_downloaded = True
                            session.commit()

                        if config.show_downloads and config.show_skipped_downloads:
                            print_info(
                                f"Deduplication [File]: {media_item.mimetype.split('/')[-2]} '{check_path.name}' → skipped (hash verified)"
                            )
                        state.add_duplicate()
                        continue
                finally:
                    # Clean up temporary file
                    if temp_path and temp_path.exists():
                        temp_path.unlink()
            else:
                # For m3u8 files, if we have a matching hash in the database, trust it
                if media_record.content_hash == existing_hash:
                    if config.show_downloads and config.show_skipped_downloads:
                        print_info(
                            f"Deduplication [Hash]: {media_item.mimetype.split('/')[-2]} '{check_path.name}' → skipped (hash verified)"
                        )
                    state.add_duplicate()
                    continue

        # Show download progress
        if config.show_downloads:
            print_info(f"Downloading {media_item.mimetype.split('/')[-2]} '{filename}'")

        try:
            # Download the file
            if media_item.file_extension == "m3u8":
                # For m3u8, download to temp location first
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

                    # Check if this hash exists in database
                    with config._database.sync_session() as session:
                        from metadata.media import Media

                        session.add(media_record)
                        existing_by_hash = (
                            session.query(Media)
                            .filter(
                                Media.content_hash == new_hash,
                                Media.id != media_record.id,
                            )
                            .first()
                        )

                        if existing_by_hash and existing_by_hash.is_downloaded:
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

                        shutil.move(str(temp_path), str(check_path))
                        file_save_path = check_path

                        # Update database record
                        media_record.content_hash = new_hash
                        media_record.local_filename = str(check_path)
                        media_record.is_downloaded = True
                        session.commit()
                finally:
                    # Clean up temp directory
                    if temp_dir.exists():
                        shutil.rmtree(temp_dir)
            else:
                # Handle normal media file download
                with config.get_api().get_with_ngsw(
                    url=media_item.download_url,
                    stream=True,
                    add_fansly_headers=False,
                ) as response:
                    if response.status_code == 200:
                        text_column = TextColumn("", table_column=Column(ratio=1))
                        bar_column = BarColumn(
                            bar_width=60, table_column=Column(ratio=5)
                        )
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
                            for chunk in response.iter_content(chunk_size=1_048_576):
                                if chunk:
                                    output_file.write(chunk)
                                    progress.advance(task_id, len(chunk))

                        progress.refresh()
                        progress.stop()

                        # Set file timestamps
                        if media_item.created_at:
                            import os

                            created_timestamp = media_item.created_at
                            os.utime(
                                file_save_path, (created_timestamp, created_timestamp)
                            )
                    else:
                        raise DownloadError(
                            f"Download failed on filename {filename} due to an "
                            f"error --> status_code: {response.status_code} "
                            f"| content: \n{response.content.decode('utf-8')} [13]"
                        )

            # Update database with file info
            is_dupe = dedupe_media_file(
                config, state, media_item.mimetype, file_save_path, media_record
            )

            if not is_dupe:
                # Count only if file was kept
                state.pic_count += 1 if "image" in media_item.mimetype else 0
                state.vid_count += 1 if "video" in media_item.mimetype else 0

        except M3U8Error as ex:
            print_warning(f"Skipping invalid item: {ex}")

        # Slow down a bit to be sure
        sleep(random.uniform(0.4, 0.75))
