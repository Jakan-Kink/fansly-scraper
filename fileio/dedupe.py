"""Item Deduplication"""

import asyncio
import itertools
import mimetypes
import multiprocessing
import os
import re
import traceback
from pathlib import Path
from typing import Any

from config import FanslyConfig
from download.downloadstate import DownloadState
from errors import MediaHashMismatchError
from fileio.fnmanip import get_hash_for_image, get_hash_for_other_content
from fileio.normalize import get_id_from_filename, normalize_filename
from helpers.rich_progress import get_progress_manager
from metadata import Account, Media
from metadata.models import get_store
from pathio import set_create_directory_for_download
from textio import json_output, print_info, print_warning


async def migrate_full_paths_to_filenames() -> None:
    """Update database records that have full paths stored in local_filename.

    This is a one-time migration to convert any full paths to just filenames.
    It will:
    1. Find all records with path separators in local_filename
    2. Extract just the filename part
    3. Update the database records
    """
    print_info("Starting migration of full paths to filenames in database...")

    store = get_store()

    # Find records containing path separators (both / and \)
    records_with_slash = await store.find(Media, local_filename__contains="/")
    records_with_backslash = await store.find(Media, local_filename__contains="\\")

    # Merge and deduplicate
    seen_ids = set()
    records = []
    for media in records_with_slash + records_with_backslash:
        if media.id not in seen_ids:
            seen_ids.add(media.id)
            records.append(media)

    if not records:
        print_info("No records found with full paths. Migration not needed.")
        return

    print_info(f"Found {len(records)} records with full paths to update...")

    progress = get_progress_manager()
    updated = 0

    with progress.session():
        migrate_task = progress.add_task(
            name="migrate_paths",
            description="Migrating paths to filenames",
            total=len(records),
            show_elapsed=True,
        )

        for media in records:
            try:
                new_filename = get_filename_only(media.local_filename)
                media.local_filename = new_filename
                await store.save(media)
                updated += 1
            except Exception as e:
                print_info(f"Error updating record {media.id}: {e}")
            finally:
                progress.update_task(migrate_task, advance=1)

    print_info(f"Migration complete! Updated {updated} of {len(records)} records.")


def get_filename_only(path: Path | str) -> str:
    """Get just the filename part from a path, ensuring it's a string."""
    if isinstance(path, str):
        path = Path(path)
    return path.name


async def safe_rglob(base_path: Path, pattern: str) -> list[Path]:
    """Safely perform rglob with a pattern that might contain path separators.

    Args:
        base_path: The base directory to search in
        pattern: The filename pattern to search for

    Returns:
        List of matching Path objects
    """
    # Extract just the filename part if pattern contains path separators
    filename = get_filename_only(pattern)

    # Use rglob with just the filename part in a thread pool
    return await asyncio.to_thread(lambda: list(base_path.rglob(filename)))


async def file_exists_in_download_path(
    base_path: Path | None,
    filename: str | None,
) -> bool:
    """Check if a filename exists anywhere under the download path."""
    if not base_path or not filename:
        return False

    direct_path = base_path / filename
    if await asyncio.to_thread(direct_path.is_file):
        return True

    for found_file in await safe_rglob(base_path, filename):
        if await asyncio.to_thread(found_file.is_file):
            return True
    return False


# Function to calculate file hash in a separate process
def calculate_file_hash(
    file_info: tuple[Path, str],
) -> tuple[Path, str | None, dict[str, Any]]:
    """Calculate hash for a file in a separate process.

    This is a synchronous function because it runs in multiprocessing.Pool
    workers which have no event loop. All I/O is direct (no asyncio).

    Args:
        file_info: Tuple of (file_path, mimetype)

    Returns:
        Tuple of (file_path, hash or None, debug_info)
    """
    file_path, mimetype = file_info
    exists = file_path.exists()
    debug_info = {
        "path": str(file_path),
        "mimetype": mimetype,
        "size": file_path.stat().st_size if exists else None,
        "exists": exists,
        "is_file": file_path.is_file() if exists else None,
        "readable": os.access(file_path, os.R_OK) if exists else None,
    }
    try:
        if "image" in mimetype:
            hash_value = get_hash_for_image(file_path)
            debug_info.update(
                {
                    "hash_type": "image",
                    "hash_success": bool(hash_value),
                    "hash_value": hash_value if hash_value else None,
                }
            )
            return file_path, hash_value, debug_info
        if "video" in mimetype or "audio" in mimetype:
            hash_value = get_hash_for_other_content(file_path)
            debug_info.update(
                {
                    "hash_type": "video/audio",
                    "hash_success": bool(hash_value),
                    "hash_value": hash_value if hash_value else None,
                }
            )
            return file_path, hash_value, debug_info
        debug_info.update(
            {
                "hash_type": "unsupported",
                "hash_success": False,
                "reason": "unsupported_mimetype",
            }
        )
    except Exception as e:
        debug_info.update(
            {
                "hash_success": False,
                "error": str(e),
                "error_type": type(e).__name__,
                "traceback": traceback.format_exc(),
            }
        )
    return file_path, None, debug_info


async def get_or_create_media(
    file_path: Path,
    media_id: int | None,
    mimetype: str,
    state: DownloadState,
    file_hash: str | None = None,
    trust_filename: bool = False,
    config: FanslyConfig | None = None,
) -> tuple[Media, bool]:
    """Get or create a media record with optimized database access.

    Strategy:
    1. Look up media by ID and/or hash
    2. Calculate hash only if needed
    3. One save at the end
    """
    store = get_store()
    filename = await normalize_filename(get_filename_only(file_path), config=config)
    hash_verified = False

    json_output(
        1,
        "get_or_create_media",
        {
            "state": "start",
            "media_id": media_id,
            "filename": filename,
            "mimetype": mimetype,
            "initial_hash": file_hash,
            "trust_filename": trust_filename,
        },
    )

    # Find existing media by ID and by hash separately
    existing_by_id = await store.get(Media, media_id) if media_id else None
    existing_by_hash = (
        await store.find_one(Media, content_hash=file_hash) if file_hash else None
    )

    # Collect all found media for iteration
    existing_media = []
    if existing_by_id:
        existing_media.append(existing_by_id)
    if existing_by_hash and (
        not existing_by_id or existing_by_hash.id != existing_by_id.id
    ):
        existing_media.append(existing_by_hash)

    json_output(
        1,
        "get_or_create_media",
        {
            "state": "query_existing",
            "found_count": len(existing_media),
            "found_ids": [m.id for m in existing_media],
        },
    )

    # Fast path for trusted filenames
    if trust_filename and media_id:
        media_by_id = next((m for m in existing_media if m.id == media_id), None)
        if media_by_id:
            preserve_existing = False
            if (
                media_by_id.local_filename
                and media_by_id.local_filename != filename
                and await file_exists_in_download_path(
                    state.download_path, media_by_id.local_filename
                )
            ):
                preserve_existing = True

            # Update filename and mark as downloaded
            if not preserve_existing:
                media_by_id.local_filename = filename
            media_by_id.is_downloaded = True
            media_by_id.mimetype = mimetype
            if not media_by_id.accountId:
                resolved_id = await get_account_id(state)
                if resolved_id is None:
                    raise ValueError(
                        f"Cannot resolve account for media {media_by_id.id}: "
                        f"no account ID in state (creator_id={state.creator_id})"
                    )
                media_by_id.accountId = resolved_id
            await store.save(media_by_id)
            hash_verified = bool(media_by_id.content_hash)
            return media_by_id, hash_verified

    # Regular path for non-trusted files
    media_by_id = (
        next((m for m in existing_media if m.id == media_id), None)
        if media_id
        else None
    )
    if media_by_id:
        json_output(
            1,
            "get_or_create_media",
            {
                "state": "found_by_id",
                "media_id": media_by_id.id,
                "has_hash": bool(media_by_id.content_hash),
                "filename_match": media_by_id.local_filename == filename,
            },
        )

        # If filenames match and we have a hash, we're done
        if media_by_id.local_filename == filename and media_by_id.content_hash:
            hash_verified = True
            json_output(
                1,
                "get_or_create_media",
                {
                    "state": "quick_return",
                    "media_id": media_by_id.id,
                    "reason": "filename_and_hash_match",
                },
            )
            return media_by_id, hash_verified

        # Calculate hash if needed and not provided
        if not file_hash and not trust_filename:
            json_output(
                1,
                "get_or_create_media",
                {
                    "state": "calculating_hash",
                    "media_id": media_by_id.id,
                    "reason": "verify_existing",
                    "mimetype": mimetype,
                },
            )

            if "image" in mimetype:
                file_hash = await asyncio.to_thread(get_hash_for_image, file_path)
            elif "video" in mimetype or "audio" in mimetype:
                file_hash = await asyncio.to_thread(
                    get_hash_for_other_content, file_path
                )

        # If we have a hash now, verify it matches
        if (
            file_hash
            and media_by_id.content_hash
            and media_by_id.content_hash != file_hash
        ):
            json_output(
                1,
                "get_or_create_media",
                {
                    "state": "hash_mismatch",
                    "media_id": media_by_id.id,
                    "db_hash": media_by_id.content_hash,
                    "file_hash": file_hash,
                },
            )
            raise MediaHashMismatchError(
                f"Hash mismatch for media {media_id}: "
                f"DB has {media_by_id.content_hash}, file has {file_hash}"
            )

        preserve_existing = False
        if (
            media_by_id.local_filename
            and media_by_id.local_filename != filename
            and await file_exists_in_download_path(
                state.download_path, media_by_id.local_filename
            )
        ):
            preserve_existing = True

        # Update existing record
        media_by_id.content_hash = file_hash
        if not preserve_existing:
            media_by_id.local_filename = filename
        media_by_id.is_downloaded = True
        media_by_id.mimetype = mimetype
        if not media_by_id.accountId:
            media_by_id.accountId = await get_account_id(state)
        await store.save(media_by_id)

        hash_verified = bool(file_hash)
        json_output(
            1,
            "get_or_create_media",
            {
                "state": "updated_existing",
                "media_id": media_by_id.id,
                "updated_fields": [
                    "content_hash",
                    "local_filename",
                    "is_downloaded",
                    "mimetype",
                ]
                + (["accountId"] if not media_by_id.accountId else []),
            },
        )
        return media_by_id, hash_verified

    # If we found media by hash, use that
    media_by_hash = (
        next((m for m in existing_media if m.content_hash == file_hash), None)
        if file_hash
        else None
    )
    if media_by_hash:
        hash_verified = True
        json_output(
            1,
            "get_or_create_media",
            {
                "state": "found_by_hash",
                "media_id": media_by_hash.id,
                "hash": file_hash,
            },
        )
        return media_by_hash, hash_verified

    # If we get here, we need to create new media
    # Calculate hash if needed and not provided
    if not file_hash and not trust_filename:
        json_output(
            1,
            "get_or_create_media",
            {
                "state": "calculating_hash",
                "reason": "new_media",
                "mimetype": mimetype,
            },
        )
        if "image" in mimetype:
            file_hash = await asyncio.to_thread(get_hash_for_image, file_path)
        elif "video" in mimetype or "audio" in mimetype:
            file_hash = await asyncio.to_thread(get_hash_for_other_content, file_path)

    # Create new media
    account_id = await get_account_id(state)
    if account_id is None:
        raise ValueError(
            "Cannot create Media record: no account ID available in state "
            f"(creator_id={state.creator_id}, creator_name={state.creator_name})"
        )

    media = Media(
        id=media_id,
        content_hash=file_hash,
        local_filename=filename,
        is_downloaded=True,
        mimetype=mimetype,
        accountId=account_id,
    )
    await store.save(media)
    hash_verified = bool(file_hash)

    json_output(
        1,
        "get_or_create_media",
        {
            "state": "created_new",
            "media_id": media_id,
            "has_hash": bool(file_hash),
        },
    )

    return media, hash_verified


async def get_account_id(state: DownloadState) -> int | None:
    """Get account ID from state, looking up by creator_name if creator_id is not available."""
    store = get_store()

    # First try creator_id if available
    if state.creator_id:
        return state.creator_id

    # Then try to find by username
    if state.creator_name:
        account = await store.find_one(Account, username=state.creator_name)
        if account:
            state.creator_id = account.id
            return account.id
        # TODO: Query Fansly API by username to get the real account with
        # a proper snowflake ID, then persist via process_account_data().
        # For now, return None — caller should have set state.creator_id
        # from the API before reaching this path.
        return None

    return None


async def categorize_file(
    file_path: Path,
    hash2_pattern: re.Pattern[str],
) -> tuple[str, tuple] | None:
    """Categorize a file into 'hash2', 'media_id', or 'needs_hash'.

    Returns:
        Tuple of (category, file_info) or None if file should be skipped
    """

    filename = file_path.name
    media_id, _ = get_id_from_filename(filename)
    mimetype, _ = mimetypes.guess_type(file_path)

    if not mimetype:
        return None

    match2 = hash2_pattern.search(filename)
    if match2:
        return "hash2", (file_path, media_id, mimetype, match2.group(1))
    if media_id:
        return "media_id", (file_path, media_id, mimetype)
    return "needs_hash", (file_path, mimetype)


async def dedupe_init(
    config: FanslyConfig,
    state: DownloadState,
) -> None:
    """Initialize deduplication by scanning existing files and updating the database.

    This function:
    1. Creates the download directory if needed
    2. Migrates any full paths in database to filenames only
    3. Detects and migrates files using the old hash-in-filename format
    4. Detects files with ID patterns and verifies their hashes
    5. Scans all files and updates the database
    6. Updates the database with file information
    7. Marks files as downloaded in the database
    """
    store = get_store()

    # Use function attribute to track pass count (avoids global variable)
    if not hasattr(dedupe_init, "pass_count"):
        dedupe_init.pass_count = 0
    dedupe_init.pass_count += 1
    call_count = dedupe_init.pass_count

    json_output(
        1,
        "dedupe_init",
        {
            "pass": call_count,
            "state": "starting",
            "download_path": str(state.download_path) if state.download_path else None,
            "creator_id": state.creator_id,
            "creator_name": state.creator_name,
        },
    )

    # First, migrate any full paths in the database to filenames only
    await migrate_full_paths_to_filenames()

    # Create the base user path download_directory/creator_name
    set_create_directory_for_download(config, state)

    if not state.download_path or not await asyncio.to_thread(
        state.download_path.is_dir
    ):
        json_output(
            1,
            "dedupe_init",
            {
                "pass": call_count,
                "state": "early_return",
                "reason": (
                    "no_download_path" if not state.download_path else "not_a_directory"
                ),
                "path": str(state.download_path) if state.download_path else None,
            },
        )
        return

    print_info(
        f"Initializing database-backed deduplication for:\n{17 * ' '}{state.download_path}"
    )

    # Count existing records
    existing_media = await store.find(
        Media,
        accountId=state.creator_id,
        is_downloaded=True,
    )
    existing_downloaded = len(existing_media)
    print_info(f"Existing downloaded records: {existing_downloaded}")

    # Initialize patterns and counters
    processed_count = 0
    preserved_count = 0
    hash2_pattern = re.compile(r"_hash2_([a-fA-F0-9]+)")

    # Use half of available cores, but ensure at least 2 workers
    max_workers = max(2, multiprocessing.cpu_count() // 2)

    # First, collect all files that need hashing
    all_files = [
        f
        for f in await safe_rglob(state.download_path, "*")
        if await asyncio.to_thread(f.is_file)
    ]
    file_batches = {
        "hash2": [],  # (file_path, media_id, mimetype, hash2_value)
        "media_id": [],  # (file_path, media_id, mimetype)
        "needs_hash": [],  # (file_path, mimetype)
    }

    # Categorize files with Rich progress
    progress_mgr = get_progress_manager()

    with progress_mgr.session():
        tasks = [categorize_file(f, hash2_pattern) for f in all_files]
        categorize_task = progress_mgr.add_task(
            name="categorize_files",
            description="Categorizing files",
            total=len(all_files),
            show_elapsed=False,
        )

        for task in asyncio.as_completed(tasks):
            if result := await task:
                category, file_info = result
                file_batches[category].append(file_info)
            progress_mgr.update_task(categorize_task, advance=1)

    # Process hash2 files (files with known hashes)
    if file_batches["hash2"]:
        with progress_mgr.session():
            hash2_task = progress_mgr.add_task(
                name="process_hash2",
                description="Processing hash2 files",
                total=len(file_batches["hash2"]),
                show_elapsed=False,
            )
            for file_path, media_id, mimetype, hash2_value in file_batches["hash2"]:
                new_name = hash2_pattern.sub("", file_path.name)
                _, hash_verified = await get_or_create_media(
                    file_path=file_path.with_name(new_name),
                    media_id=media_id,
                    mimetype=mimetype,
                    state=state,
                    file_hash=hash2_value,
                    trust_filename=True,
                    config=config,
                )
                if hash_verified:
                    preserved_count += 1
                progress_mgr.update_task(hash2_task, advance=1)

    # Process files with media IDs in parallel
    if file_batches["media_id"]:
        with progress_mgr.session():
            media_id_task = progress_mgr.add_task(
                name="process_media_ids",
                description="Processing media ID files",
                total=len(file_batches["media_id"]),
                show_elapsed=False,
            )

            # Create semaphore to limit concurrent tasks
            max_concurrent = min(25, int(os.getenv("FDLNG_MAX_CONCURRENT", "25")))
            semaphore = asyncio.Semaphore(max_concurrent)

            async def process_file(file_info: tuple[Path, str, str]) -> None:
                file_path, media_id, mimetype = file_info
                async with semaphore:
                    try:
                        result = await get_or_create_media(
                            file_path=file_path,
                            media_id=media_id,
                            mimetype=mimetype,
                            state=state,
                            trust_filename=True,
                            config=config,
                        )
                        return result
                    finally:
                        progress_mgr.update_task(media_id_task, advance=1)

            # Process in chunks to avoid too many pending tasks
            chunk_size = 50  # Adjust based on testing
            for i in range(0, len(file_batches["media_id"]), chunk_size):
                chunk = file_batches["media_id"][i : i + chunk_size]
                tasks = [process_file(file_info) for file_info in chunk]
                results = await asyncio.gather(*tasks)
                processed_count += sum(
                    1 for _, hash_verified in results if hash_verified
                )

    # Process files needing hashes
    if file_batches["needs_hash"]:
        with progress_mgr.session():
            hash_task = progress_mgr.add_task(
                name="process_hashing",
                description=f"Hashing files ({max_workers} workers)",
                total=len(file_batches["needs_hash"]),
                show_elapsed=False,
            )

            # Process files with a limited number of active tasks
            active_tasks = max_workers + 4

            with multiprocessing.Pool(processes=max_workers) as pool:
                try:
                    # Create an iterator that will process files as needed
                    iterator = pool.imap_unordered(
                        calculate_file_hash,
                        file_batches["needs_hash"],
                        chunksize=1,
                    )

                    # Take only active_tasks items at a time from the iterator
                    while True:
                        # Get the next batch of results (non-blocking)
                        batch = list(itertools.islice(iterator, active_tasks))
                        if not batch:  # No more files to process
                            break

                        # Process the current batch
                        for file_path, file_hash, debug_info in batch:
                            if file_hash is None:
                                progress_mgr.update_task(hash_task, advance=1)
                                continue

                            try:
                                _, hash_verified = await get_or_create_media(
                                    file_path=file_path,
                                    media_id=get_id_from_filename(file_path.name)[0],
                                    mimetype=mimetypes.guess_type(file_path)[0],
                                    state=state,
                                    file_hash=file_hash,
                                    trust_filename=False,
                                    config=config,
                                )
                                if hash_verified:
                                    processed_count += 1
                            except Exception as e:
                                json_output(
                                    1,
                                    "dedupe_init",
                                    {
                                        "pass": call_count,
                                        "state": "file_process_error",
                                        "file_info": debug_info,
                                        "error": str(e),
                                        "error_type": type(e).__name__,
                                        "traceback": traceback.format_exc(),
                                    },
                                )
                            finally:
                                progress_mgr.update_task(hash_task, advance=1)
                finally:
                    # Clean up resources
                    try:
                        pool.terminate()
                        pool.join(timeout=1.0)
                    except Exception as e:
                        print_warning(f"Error cleaning up process pool: {e}")
                    finally:
                        pool.close()

    downloaded_list = await store.find(
        Media,
        is_downloaded=True,
        accountId=state.creator_id,
    )
    # Log start of database check
    json_output(
        1,
        "dedupe_init",
        {
            "pass": call_count,
            "state": "checking_database",
            "downloaded_count": len(downloaded_list),
        },
    )

    with progress_mgr.session():
        db_check_task = progress_mgr.add_task(
            name="check_db_files",
            description="Checking DB files",
            total=len(downloaded_list),
            show_elapsed=True,  # Show elapsed time for verification tasks
        )

        # Process each record
        for media in downloaded_list:
            # Update progress description with current ID
            progress_mgr.update_task(
                db_check_task, description=f"Checking DB - ID: {media.id}"
            )

            # Log each record check
            json_output(
                2,  # More detailed logging level
                "dedupe_init",
                {
                    "pass": call_count,
                    "state": "checking_record",
                    "media_id": media.id,
                    "filename": media.local_filename,
                    "hash": media.content_hash,
                },
            )

            needs_update = False
            if media.local_filename:
                if any(f.name == media.local_filename for f in all_files):
                    progress_mgr.update_task(db_check_task, advance=1)
                    continue
                # File marked as downloaded but not found - clean up record
                json_output(
                    1,
                    "dedupe_init",
                    {
                        "pass": call_count,
                        "state": "file_missing",
                        "media_id": media.id,
                        "filename": media.local_filename,
                    },
                )
                media.is_downloaded = False
                media.content_hash = None
                media.local_filename = None
                needs_update = True
            else:
                media.is_downloaded = False
                media.content_hash = None
                needs_update = True

            if needs_update:
                await store.save(media)

            progress_mgr.update_task(db_check_task, advance=1)

    # Get updated counts
    result = await store.find(
        Media,
        is_downloaded=True,
        accountId=state.creator_id,
    )
    final_downloaded = len(result)

    # Log final statistics
    json_output(
        1,
        "dedupe_init",
        {
            "pass": call_count,
            "state": "finished",
            "initial_records": existing_downloaded,
            "final_records": final_downloaded,
            "new_records": final_downloaded - existing_downloaded,
            "processed_count": processed_count,
            "preserved_count": preserved_count,
            "total_files": len(all_files),
            "files_hashed": len(file_batches["needs_hash"]),
        },
    )

    print_info(
        f"Database deduplication initialized!"
        f"\n{17 * ' '}Added {final_downloaded - existing_downloaded} new entries to the database"
        f"\n{17 * ' '}Processed {processed_count} files with content verification"
        f"\n{17 * ' '}Preserved {preserved_count} trusted hash2 format entries"
    )

    print_info(
        "Files will now be tracked in the database instead of using filename hashes."
    )


async def _calculate_hash_for_file(
    filename: Path,
    mimetype: str,
) -> str | None:
    """Calculate hash for a file based on its mimetype.

    Args:
        filename: Path to the file
        mimetype: MIME type of the file

    Returns:
        Hash string or None if hash couldn't be calculated
    """
    try:
        if "image" in mimetype:
            return await asyncio.to_thread(get_hash_for_image, filename)
        if "video" in mimetype or "audio" in mimetype:
            return await asyncio.to_thread(get_hash_for_other_content, filename)
    except Exception as e:
        json_output(
            1,
            "dedupe_media_file",
            {
                "state": "hash_error",
                "error": str(e),
                "error_type": type(e).__name__,
                "traceback": traceback.format_exc(),
            },
        )
    return None


async def _check_file_exists(
    base_path: Path,
    filename: str,
) -> bool:
    """Check if a file exists in the given base path.

    Args:
        base_path: Base directory to search in
        filename: Filename to look for

    Returns:
        True if file exists, False otherwise
    """
    found_files = await safe_rglob(base_path, filename)
    for found_file in found_files:
        if await asyncio.to_thread(found_file.is_file):
            return True
    return False


async def dedupe_media_file(  # noqa: PLR0911 - Complex deduplication logic with many edge cases
    config: FanslyConfig,
    state: DownloadState,
    mimetype: str,
    filename: Path,
    media_record: Media,
) -> bool:
    """Update a Media record with file information and check for duplicates.

    This function:
    1. Calculates the file hash
    2. Checks if the hash exists in the database
    3. If it exists and is downloaded, skips the file
    4. Updates the Media record with the file information

    Args:
        config: The current configuration
        state: The current download state, for statistics
        mimetype: The MIME type of the media item
        filename: The full path of the file to examine
        media_record: Media record to update

    Returns:
        bool: True if it is a duplicate or False otherwise
    """
    store = get_store()

    json_output(
        1,
        "dedupe_media_file",
        {
            "state": "starting",
            "media_id": media_record.id if media_record else None,
            "filename": str(filename),
            "mimetype": mimetype,
        },
    )

    # First try by ID
    media_id, _ = get_id_from_filename(filename.name)
    if media_id:
        existing_by_id = await store.get(Media, media_id)
        if existing_by_id:
            # Calculate hash if needed
            file_hash = None
            if existing_by_id.content_hash is None:
                file_hash = await _calculate_hash_for_file(filename, mimetype)

            # First check if filenames match
            if existing_by_id.local_filename == get_filename_only(filename):
                if file_hash:
                    existing_by_id.content_hash = file_hash
                existing_by_id.is_downloaded = True
                await store.save(existing_by_id)
                return True

            # Handle missing filename
            if existing_by_id.local_filename is None:
                file_hash = None
                if not file_hash:
                    file_hash = await _calculate_hash_for_file(filename, mimetype)

                # Before marking as not duplicate, check if hash matches another media
                if file_hash:
                    duplicates = await store.find(
                        Media,
                        content_hash=file_hash,
                        id__ne=existing_by_id.id,
                        is_downloaded=True,
                    )
                    duplicate_media = duplicates[0] if duplicates else None
                    if duplicate_media:
                        # Found duplicate by hash - check if its file exists
                        db_file_exists = await _check_file_exists(
                            state.download_path, duplicate_media.local_filename
                        )
                        if db_file_exists:
                            # Duplicate exists - update this record to reference it
                            existing_by_id.content_hash = file_hash
                            existing_by_id.local_filename = (
                                duplicate_media.local_filename
                            )
                            existing_by_id.is_downloaded = True
                            await store.save(existing_by_id)
                            # Remove the new file since it's a duplicate
                            await asyncio.to_thread(filename.unlink)
                            return True

                # No duplicate found - update with new file info
                existing_by_id.local_filename = get_filename_only(filename)
                existing_by_id.is_downloaded = True
                if file_hash:
                    existing_by_id.content_hash = file_hash
                await store.save(existing_by_id)
                return False

            # Handle path normalization (compare strings, not str vs Path)
            if existing_by_id.local_filename == str(filename) or get_filename_only(
                existing_by_id.local_filename
            ) == get_filename_only(filename):
                existing_by_id.local_filename = get_filename_only(filename)
                existing_by_id.is_downloaded = True
                await store.save(existing_by_id)
                return True

            # Different filename but same ID - check if it's actually the same file
            if existing_by_id.content_hash:  # Only if we have a hash to compare
                if not file_hash:
                    file_hash = await _calculate_hash_for_file(filename, mimetype)

                if file_hash and file_hash == existing_by_id.content_hash:
                    # Same content but wrong filename - check if DB's file exists
                    db_filename = existing_by_id.local_filename
                    if db_filename == str(
                        filename
                    ):  # pragma: no cover — unreachable after str/Path fix at line 1029
                        existing_by_id.local_filename = get_filename_only(filename)
                        existing_by_id.is_downloaded = True
                        await store.save(existing_by_id)
                        return True

                    db_file_exists = await _check_file_exists(
                        state.download_path, db_filename
                    )
                    json_output(
                        1,
                        "dedupe_media_file",
                        {
                            "state": "checking_db_file",
                            "db_filename": db_filename,
                            "filename": str(filename),
                            "exists": db_file_exists,
                        },
                    )

                    if db_file_exists:
                        # DB's file exists, this is a duplicate with wrong name - remove it
                        await asyncio.to_thread(filename.unlink)
                        return True
                    # DB's file is missing but this is the same content - update DB filename
                    existing_by_id.local_filename = get_filename_only(filename)
                    existing_by_id.is_downloaded = True
                    await store.save(existing_by_id)
                    return True

    # Try by normalized filename (runs for ALL files, not just those with media_id)
    normalized_path = await normalize_filename(filename.name, config=config)

    # If original and normalized paths are different, check both
    paths_to_check = [normalized_path]
    if normalized_path != filename.name:
        paths_to_check.append(filename.name)

    # Also check for other files with same media ID but different timestamp format
    id_part_match = re.search(r"_((?:preview_)?id_\d+)\.[^.]+$", filename.name)
    if id_part_match:
        id_part = id_part_match.group(1)
        # Search for any files with this ID part
        existing_by_id_pattern = await store.find(
            Media,
            local_filename__contains=f"{id_part}.",
            is_downloaded=True,
        )
        for existing in existing_by_id_pattern:
            if existing.local_filename not in paths_to_check:
                paths_to_check.append(existing.local_filename)

    for path_to_check in paths_to_check:
        existing_by_name = await store.find_one(
            Media,
            local_filename__iexact=path_to_check,
            is_downloaded=True,
        )
        if existing_by_name:
            # First check if filenames match
            if existing_by_name.local_filename == get_filename_only(filename):
                # Same filename - perfect match
                return True

            # Different filename - check if it's actually the same file
            if existing_by_name.content_hash:  # Only if we have a hash to compare
                file_hash = await _calculate_hash_for_file(filename, mimetype)

                if file_hash and file_hash == existing_by_name.content_hash:
                    # Same content but wrong filename - check if DB's file exists
                    db_file_exists = await _check_file_exists(
                        state.download_path, existing_by_name.local_filename
                    )

                    if db_file_exists:
                        # DB's file exists, this is a duplicate - remove it
                        await asyncio.to_thread(filename.unlink)
                        return True
                    # DB's file is missing but content matches - update DB filename
                    existing_by_name.local_filename = get_filename_only(filename)
                    await store.save(existing_by_name)
                    return True

    # If not in DB or no hash match, calculate hash and update DB
    file_hash = await _calculate_hash_for_file(filename, mimetype)
    if file_hash:
        # Check if hash exists in database
        media = await store.find_one(Media, content_hash=file_hash)
        if media:
            # Found by hash - check if DB's file exists
            db_file_exists = await _check_file_exists(
                state.download_path, media.local_filename
            )

            if db_file_exists:
                # Update current record to reference the existing duplicate file
                media_record.content_hash = file_hash
                media_record.local_filename = media.local_filename
                media_record.is_downloaded = True
                await store.save(media_record)

                # DB's file exists, this is a duplicate - remove it
                await asyncio.to_thread(filename.unlink)
                return True
            # DB's file is missing but this is the same content - keep new file
            # Update both the old record and current record to point to new file
            media.local_filename = get_filename_only(filename)
            media.is_downloaded = True
            await store.save(media)

            media_record.content_hash = file_hash
            media_record.local_filename = get_filename_only(filename)
            media_record.is_downloaded = True
            await store.save(media_record)
            return True

        # No match found, update our media record
        # Verify file exists before marking as downloaded
        if await _check_file_exists(state.download_path, get_filename_only(filename)):
            media_record.content_hash = file_hash
            media_record.local_filename = get_filename_only(filename)
            media_record.is_downloaded = True
            await store.save(media_record)
        else:
            # File disappeared between download and verification
            media_record.is_downloaded = False
            media_record.content_hash = None
            media_record.local_filename = None
            await store.save(media_record)

    return False
