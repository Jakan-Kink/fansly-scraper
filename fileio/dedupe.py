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

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from config import FanslyConfig
from config.decorators import with_database_session
from download.downloadstate import DownloadState
from errors import MediaHashMismatchError
from fileio.fnmanip import get_hash_for_image, get_hash_for_other_content
from fileio.normalize import get_id_from_filename, normalize_filename
from helpers.rich_progress import get_progress_manager
from metadata.database import require_database_config
from metadata.decorators import retry_on_locked_db
from metadata.media import Media
from pathio import set_create_directory_for_download
from textio import json_output, print_info, print_warning


@require_database_config
async def migrate_full_paths_to_filenames(config: FanslyConfig) -> None:
    """Update database records that have full paths stored in local_filename.

    This is a one-time migration to convert any full paths to just filenames.
    It will:
    1. Find all records with path separators in local_filename
    2. Extract just the filename part
    3. Update the database records
    """
    print_info("Starting migration of full paths to filenames in database...")

    async with config._database.async_session_scope() as session:
        # First, let's count how many records need updating
        # Look for records containing path separators (both / and \)
        count_query = (
            select(func.count())  # pylint: disable=not-callable
            .select_from(Media)
            .where(
                or_(Media.local_filename.like("%/%"), Media.local_filename.like("%\\%"))
            )
        )
        count = (await session.execute(count_query)).scalar()

        if count == 0:
            print_info("No records found with full paths. Migration not needed.")
            return

        print_info(f"Found {count} records with full paths to update...")

        # Get all records that need updating
        records_query = select(Media.id, Media.local_filename).where(
            or_(Media.local_filename.like("%/%"), Media.local_filename.like("%\\%"))
        )
        records = (await session.execute(records_query)).fetchall()

        # Update each record and commit immediately to prevent data loss
        updated = 0
        for record in records:
            try:
                # Convert the path to just filename
                new_filename = get_filename_only(record.local_filename)

                # Update the record
                media = await session.get(Media, record.id)
                if media:
                    media.local_filename = new_filename
                    # Commit immediately after each update
                    await session.commit()
                    updated += 1

                    # Print progress every 100 records
                    if updated % 100 == 0:
                        print_info(f"Updated {updated} of {count} records...")

            except Exception as e:
                print_info(f"Error updating record {record.id}: {e}")
                # Rollback the failed transaction
                await session.rollback()

        print_info(f"Migration complete! Updated {updated} of {count} records.")


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
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, lambda: list(base_path.rglob(filename)))


async def file_exists_in_download_path(
    base_path: Path | None,
    filename: str | None,
) -> bool:
    """Check if a filename exists anywhere under the download path."""
    if not base_path or not filename:
        return False

    loop = asyncio.get_running_loop()
    direct_path = base_path / filename
    if await loop.run_in_executor(None, direct_path.is_file):
        return True

    for found_file in await safe_rglob(base_path, filename):
        if await loop.run_in_executor(None, found_file.is_file):
            return True
    return False


async def find_media_records(
    session: AsyncSession,
    conditions: dict[str, Any],
) -> list[Media]:
    """Find media records matching any of the given conditions.

    Args:
        session: SQLAlchemy async session
        conditions: Dict of field -> value pairs to match on

    Returns:
        List of matching Media records
    """
    query = select(Media)
    or_conditions = []
    and_conditions = []

    for field, value in conditions.items():
        if field == "id":
            or_conditions.append(Media.id == value)
        elif field == "content_hash":
            or_conditions.append(Media.content_hash == value)
        elif field == "local_filename":
            or_conditions.extend(
                [
                    # Handle both exact and normalized matches
                    Media.local_filename.ilike(value),
                    Media.local_filename == value,
                ]
            )
        elif field == "accountId":
            and_conditions.append(Media.accountId == int(value))  # Convert to int
        elif field == "is_downloaded":
            and_conditions.append(Media.is_downloaded == value)

    if or_conditions:
        and_conditions.append(or_(*or_conditions))

    if and_conditions:
        query = query.where(and_(*and_conditions))

    result = await session.execute(query)
    return list(result.scalars().all())


async def verify_file_existence(
    base_path: Path,
    filenames: list[str],
) -> dict[str, bool]:
    """Verify existence of multiple files at once.

    Args:
        base_path: Base directory to search in
        filenames: List of filenames to check

    Returns:
        Dict mapping filenames to existence booleans
    """
    loop = asyncio.get_running_loop()

    async def check_file(filename: str) -> tuple[str, bool]:
        # First try direct path lookup
        direct_path = base_path / filename
        if await loop.run_in_executor(None, direct_path.is_file):
            return filename, True

        # If not found directly, try rglob
        found = False
        for found_file in await loop.run_in_executor(
            None, safe_rglob, base_path, filename
        ):
            if await loop.run_in_executor(None, found_file.is_file):
                found = True
                break
        return filename, found

    # Run all checks concurrently
    results = await asyncio.gather(*(check_file(f) for f in filenames))
    return dict(results)


# Function to calculate file hash in a separate process
async def calculate_file_hash(
    file_info: tuple[Path, str],
) -> tuple[Path, str | None, dict[str, Any]]:
    """Calculate hash for a file in a separate process.

    Args:
        file_info: Tuple of (file_path, mimetype)

    Returns:
        Tuple of (file_path, hash or None, debug_info)
    """
    file_path, mimetype = file_info
    loop = asyncio.get_running_loop()
    exists = await loop.run_in_executor(None, file_path.exists)
    debug_info = {
        "path": str(file_path),
        "mimetype": mimetype,
        "size": (
            await loop.run_in_executor(None, lambda: file_path.stat().st_size)
            if exists
            else None
        ),
        "exists": exists,
        "is_file": (
            await loop.run_in_executor(None, file_path.is_file) if exists else None
        ),
        "readable": (
            await loop.run_in_executor(None, lambda: os.access(file_path, os.R_OK))
            if exists
            else None
        ),
    }
    try:
        if "image" in mimetype:
            hash_value = await loop.run_in_executor(None, get_hash_for_image, file_path)
            debug_info.update(
                {
                    "hash_type": "image",
                    "hash_success": bool(hash_value),
                    "hash_value": hash_value if hash_value else None,
                }
            )
            return file_path, hash_value, debug_info
        if "video" in mimetype or "audio" in mimetype:
            hash_value = await loop.run_in_executor(
                None, get_hash_for_other_content, file_path
            )
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


@with_database_session(async_session=True)
@retry_on_locked_db(
    retries=5, delay=0.2
)  # More retries and longer delay for media creation
async def get_or_create_media(
    file_path: Path,
    media_id: str | None,
    mimetype: str,
    state: DownloadState,
    file_hash: str | None = None,
    trust_filename: bool = False,
    config: FanslyConfig | None = None,
    session: AsyncSession | None = None,
) -> tuple[Media, bool]:
    """Get or create a media record with optimized database access.

    Strategy:
    1. One query to get media by ID and/or hash
    2. Calculate hash only if needed
    3. One update/insert at the end
    """
    filename = normalize_filename(get_filename_only(file_path), config=config)
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

    # Build query to find existing media
    query = select(Media)
    conditions = []

    if media_id:
        conditions.append(Media.id == media_id)
    if file_hash:
        conditions.append(Media.content_hash == file_hash)

    # If we have conditions, try to find existing media
    if conditions:
        query = query.where(or_(*conditions))
        result = await session.execute(query)
        existing_media = result.scalars().all()
        json_output(
            1,
            "get_or_create_media",
            {
                "state": "query_existing",
                "conditions": [str(c) for c in conditions],
                "found_count": len(existing_media),
                "found_ids": [m.id for m in existing_media],
            },
        )
    else:
        existing_media = []
        json_output(
            1,
            "get_or_create_media",
            {
                "state": "no_query_conditions",
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
                # print_warning(
                #     "Preserving existing filename for media "
                #     f"{media_id} because it exists on disk: "
                #     f"{media_by_id.local_filename}"
                # )

            # Update filename and mark as downloaded
            if not preserve_existing:
                media_by_id.local_filename = filename
            media_by_id.is_downloaded = True
            media_by_id.mimetype = mimetype
            if not media_by_id.accountId:
                media_by_id.accountId = await get_account_id(session, state)
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
            loop = asyncio.get_running_loop()
            if "image" in mimetype:
                file_hash = await loop.run_in_executor(
                    None, get_hash_for_image, file_path
                )
            elif "video" in mimetype or "audio" in mimetype:
                file_hash = await loop.run_in_executor(
                    None, get_hash_for_other_content, file_path
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
            # print_warning(
            #     "Preserving existing filename for media "
            #     f"{media_id} because it exists on disk: "
            #     f"{media_by_id.local_filename}"
            # )

        # Update existing record
        media_by_id.content_hash = file_hash
        if not preserve_existing:
            media_by_id.local_filename = filename
        media_by_id.is_downloaded = True
        media_by_id.mimetype = mimetype
        if not media_by_id.accountId:
            media_by_id.accountId = await get_account_id(session, state)

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
        loop = asyncio.get_running_loop()
        if "image" in mimetype:
            file_hash = await loop.run_in_executor(None, get_hash_for_image, file_path)
        elif "video" in mimetype or "audio" in mimetype:
            file_hash = await loop.run_in_executor(
                None, get_hash_for_other_content, file_path
            )

    # Create new media
    media = Media(
        id=media_id,
        content_hash=file_hash,
        local_filename=filename,
        is_downloaded=True,
        mimetype=mimetype,
        accountId=(await get_account_id(session, state)),
    )
    session.add(media)
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


async def get_account_id(session: AsyncSession, state: DownloadState) -> int | None:
    """Get account ID from state, looking up by creator_name if creator_id is not available."""
    from metadata.account import Account

    # First try creator_id if available
    if state.creator_id:
        return int(state.creator_id)

    # Then try to find by username
    if state.creator_name:
        account = (
            await session.execute(
                select(Account).where(Account.username == state.creator_name)
            )
        ).scalar_one_or_none()
        if account:
            # Update state.creator_id for future use
            state.creator_id = str(account.id)
            return account.id
        # Create new account if it doesn't exist
        account = Account(username=state.creator_name)
        session.add(account)
        await session.flush()  # This will assign an ID
        state.creator_id = str(account.id)
        return account.id

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


@require_database_config
@with_database_session(async_session=True)
async def dedupe_init(
    config: FanslyConfig,
    state: DownloadState,
    session: AsyncSession | None = None,
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
    # The @with_database_session decorator ensures session is not None
    if session is None:
        raise RuntimeError(
            "Session must be provided by @with_database_session decorator"
        )

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
    await migrate_full_paths_to_filenames(config)

    # Create the base user path download_directory/creator_name
    set_create_directory_for_download(config, state)

    if not state.download_path or not await asyncio.get_running_loop().run_in_executor(
        None, state.download_path.is_dir
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
    existing_media = await find_media_records(
        session,
        {
            "accountId": state.creator_id,
            "is_downloaded": True,
        },
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
        if await asyncio.get_running_loop().run_in_executor(None, f.is_file)
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
                    session=session,
                )
                # Commit after each get_or_create_media to prevent autoflush warnings
                await session.commit()
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
                        # Don't pass session - let get_or_create_media create its own
                        # session via @with_database_session to avoid concurrent
                        # access to the same PostgreSQL connection
                        result = await get_or_create_media(
                            file_path=file_path,
                            media_id=media_id,
                            mimetype=mimetype,
                            state=state,
                            trust_filename=True,
                            config=config,
                            # session=session,  # Not passed - each task gets its own session
                        )
                        # No commit needed - decorator handles session lifecycle
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
                                    session=session,
                                )
                                # Commit after each get_or_create_media to prevent autoflush warnings
                                await session.commit()
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

    downloaded_list = await find_media_records(
        session,
        {
            "is_downloaded": True,
            "accountId": state.creator_id,
        },
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

        # Process each record and commit immediately to prevent data loss
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

            # Commit immediately after each update to persist changes
            if needs_update:
                await session.commit()

            progress_mgr.update_task(db_check_task, advance=1)

    # Get updated counts
    result = await find_media_records(
        session=session,
        conditions={
            "is_downloaded": True,
            "accountId": state.creator_id,
        },
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
        loop = asyncio.get_running_loop()
        if "image" in mimetype:
            return await loop.run_in_executor(None, get_hash_for_image, filename)
        if "video" in mimetype or "audio" in mimetype:
            return await loop.run_in_executor(
                None, get_hash_for_other_content, filename
            )
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
    loop = asyncio.get_running_loop()
    found_files = await safe_rglob(base_path, filename)
    for found_file in found_files:
        if await loop.run_in_executor(None, found_file.is_file):
            return True
    return False


@require_database_config  # Uses config._database directly
@with_database_session(async_session=True)
async def dedupe_media_file(  # noqa: PLR0911 - Complex deduplication logic with many edge cases
    config: FanslyConfig,
    state: DownloadState,
    mimetype: str,
    filename: Path,
    media_record: Media,
    session: AsyncSession | None = None,
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
        session: Optional AsyncSession for database operations

    Returns:
        bool: True if it is a duplicate or False otherwise
    """
    # The @with_database_session decorator ensures session is not None
    if session is None:
        raise RuntimeError(
            "Session must be provided by @with_database_session decorator"
        )

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
        result = await session.execute(select(Media).filter_by(id=media_id))
        existing_by_id = result.scalar_one_or_none()
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
                session.add(existing_by_id)
                await session.flush()
                return True

            # Handle missing filename
            if existing_by_id.local_filename is None:
                existing_by_id.local_filename = get_filename_only(filename)
                existing_by_id.is_downloaded = True
                file_hash = None
                if not file_hash:
                    file_hash = await _calculate_hash_for_file(filename, mimetype)
                if file_hash:
                    existing_by_id.content_hash = file_hash
                session.add(existing_by_id)
                await session.flush()
                return False

            # Handle path normalization
            if (
                existing_by_id.local_filename == filename
                or get_filename_only(existing_by_id.local_filename) == filename
            ):
                existing_by_id.local_filename = get_filename_only(filename)
                existing_by_id.is_downloaded = True
                session.add(existing_by_id)
                await session.flush()
                return True

            # Different filename but same ID - check if it's actually the same file
            if existing_by_id.content_hash:  # Only if we have a hash to compare
                if not file_hash:
                    file_hash = await _calculate_hash_for_file(filename, mimetype)

                if file_hash and file_hash == existing_by_id.content_hash:
                    # Same content but wrong filename - check if DB's file exists
                    db_filename = existing_by_id.local_filename
                    if db_filename == str(filename):
                        existing_by_id.local_filename = get_filename_only(filename)
                        existing_by_id.is_downloaded = True
                        session.add(existing_by_id)
                        await session.flush()
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
                        await asyncio.get_running_loop().run_in_executor(
                            None, filename.unlink
                        )
                        return True
                    # DB's file is missing but this is the same content - update DB filename
                    existing_by_id.local_filename = get_filename_only(filename)
                    existing_by_id.is_downloaded = True
                    session.add(existing_by_id)
                    await session.flush()
                    return True

        # Try by normalized filename
        normalized_path = normalize_filename(filename.name, config=config)

        # If original and normalized paths are different, check both
        paths_to_check = [normalized_path]
        if normalized_path != filename.name:
            paths_to_check.append(filename.name)

        # Also check for other files with same media ID but different timestamp format
        id_part_match = re.search(r"_((?:preview_)?id_\d+)\.[^.]+$", filename.name)
        if id_part_match:
            id_part = id_part_match.group(1)
            # Search for any files with this ID part
            result = await session.execute(
                select(Media).filter(
                    Media.local_filename.like(f"%{id_part}.%"),
                    Media.is_downloaded,
                )
            )
            existing_by_id_pattern = result.scalars().all()
            for existing in existing_by_id_pattern:
                if existing.local_filename not in paths_to_check:
                    paths_to_check.append(existing.local_filename)

        for path_to_check in paths_to_check:
            result = await session.execute(
                select(Media).filter(
                    Media.local_filename.ilike(path_to_check),
                    Media.is_downloaded,
                )
            )
            existing_by_name = result.scalar_one_or_none()
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
                            await asyncio.get_running_loop().run_in_executor(
                                None, filename.unlink
                            )
                            return True
                        # DB's file is missing but content matches - update DB filename
                        existing_by_name.local_filename = get_filename_only(filename)
                        session.add(existing_by_name)
                        await session.flush()
                        return True

        # If not in DB or no hash match, calculate hash and update DB
        file_hash = await _calculate_hash_for_file(filename, mimetype)
        if file_hash:
            # Check if hash exists in database
            result = await session.execute(
                select(Media).filter_by(content_hash=file_hash)
            )
            media = result.scalar_one_or_none()
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
                    session.add(media_record)
                    await session.flush()

                    # DB's file exists, this is a duplicate - remove it
                    await asyncio.get_running_loop().run_in_executor(
                        None, filename.unlink
                    )
                    return True
                # DB's file is missing but this is the same content - keep new file
                # Update both the old record and current record to point to new file
                media.local_filename = get_filename_only(filename)
                media.is_downloaded = True
                session.add(media)

                media_record.content_hash = file_hash
                media_record.local_filename = get_filename_only(filename)
                media_record.is_downloaded = True
                session.add(media_record)
                await session.flush()
                return True

            # No match found, update our media record
            # Verify file exists before marking as downloaded
            if await _check_file_exists(
                state.download_path, get_filename_only(filename)
            ):
                media_record.content_hash = file_hash
                media_record.local_filename = get_filename_only(filename)
                media_record.is_downloaded = True
                session.add(media_record)
                await session.flush()
            else:
                # File disappeared between download and verification
                media_record.is_downloaded = False
                media_record.content_hash = None
                media_record.local_filename = None
                session.add(media_record)
                await session.flush()

    return False
