"""Media deduplication and processing utilities."""

import asyncio
import mimetypes
import re
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, TypeVar

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session
from tqdm import tqdm

from config import FanslyConfig
from config.decorators import with_database_session
from download.downloadstate import DownloadState
from errors import MediaHashMismatchError
from fileio.fnmanip import get_hash_for_image, get_hash_for_other_content
from fileio.normalize import get_id_from_filename, normalize_filename
from metadata.database import require_database_config
from metadata.media import Media
from pathio import set_create_directory_for_download
from textio import print_info
from textio.logging import json_output

RT = TypeVar("RT")
HASH2_PATTERN = re.compile(r"_hash2_([a-fA-F0-9]+)")


def get_filename_only(path: Path | str) -> str:
    """Get just the filename part from a path, ensuring it's a string."""
    if isinstance(path, str):
        path = Path(path)
    return path.name


def paths_match(path1: str | Path, path2: str | Path) -> bool:
    """Check if two paths match, considering various formats."""
    str1 = str(path1)
    str2 = str(path2)
    name1 = get_filename_only(path1)
    name2 = get_filename_only(path2)
    return str1 == str2 or name1 == str2 or str1 == name2 or name1 == name2


def safe_rglob(base_path: Path, pattern: str) -> list[Path]:
    """Safely perform rglob with a pattern that might contain path separators.

    Args:
        base_path: The base directory to search in
        pattern: The filename pattern to search for (can contain path separators)

    Returns:
        List of matching Path objects
    """
    # Use rglob with just the filename part
    filename = get_filename_only(pattern)
    return list(base_path.rglob(filename))


def calculate_file_hash(file_path: Path, mimetype: str) -> str | None:
    """Calculate hash for a file based on its mimetype."""
    if "image" in mimetype:
        return get_hash_for_image(file_path)
    if "video" in mimetype or "audio" in mimetype:
        return get_hash_for_other_content(file_path)
    return None


@with_database_session()
def get_account_id(
    state: DownloadState,
    config: FanslyConfig | None = None,
    session: Session | None = None,
) -> int | None:
    """Get account ID from state, looking up by creator_name if creator_id is not available."""
    from metadata.account import Account

    # First try creator_id if available
    if state.creator_id:
        return int(state.creator_id)

    # Then try to find by username
    if state.creator_name:
        account = session.query(Account).filter_by(username=state.creator_name).first()
        if account:
            # Update state.creator_id for future use
            state.creator_id = str(account.id)
            return account.id

        # Create new account if it doesn't exist
        account = Account(username=state.creator_name)
        session.add(account)
        session.flush()  # This will assign an ID
        state.creator_id = str(account.id)
        return account.id

    return None


@with_database_session()
def find_media_by_id(
    media_id: str,
    for_update: bool = False,
    config: FanslyConfig | None = None,
    session: Session | None = None,
) -> Media | None:
    """Find a media record by ID.

    Args:
        media_id: Media ID to search for
        for_update: Whether to lock the row for update
        config: Optional FanslyConfig instance
        session: Optional SQLAlchemy session

    Returns:
        Found Media record or None
    """
    query = select(Media).where(Media.id == media_id)
    if for_update:
        query = query.with_for_update()
    return session.execute(query).scalar_one_or_none()


@with_database_session()
def find_media_by_hash(
    content_hash: str,
    config: FanslyConfig | None = None,
    session: Session | None = None,
) -> Media | None:
    """Find a media record by content hash.

    Args:
        content_hash: Content hash to search for
        config: Optional FanslyConfig instance
        session: Optional SQLAlchemy session

    Returns:
        Found Media record or None
    """
    return session.execute(
        select(Media).where(Media.content_hash == content_hash)
    ).scalar_one_or_none()


@with_database_session()
def update_media_record(
    media: Media,
    file_hash: str | None,
    filename: str,
    mimetype: str,
    state: DownloadState,
    config: FanslyConfig | None = None,
    session: Session | None = None,
) -> None:
    """Update an existing media record.

    Args:
        media: Media record to update
        file_hash: Optional content hash
        filename: Local filename
        mimetype: MIME type
        state: Current download state
        config: Optional FanslyConfig instance
        session: Optional SQLAlchemy session
    """
    media.content_hash = file_hash
    media.local_filename = filename
    media.is_downloaded = True
    media.mimetype = mimetype
    if not media.accountId:
        media.accountId = get_account_id(state=state, session=session)
    session.flush()


@with_database_session()
def create_media_record(
    media_id: str | None,
    file_hash: str | None,
    filename: str,
    mimetype: str,
    state: DownloadState,
    config: FanslyConfig | None = None,
    session: Session | None = None,
) -> Media:
    """Create a new media record.

    Args:
        media_id: Optional media ID
        file_hash: Optional content hash
        filename: Local filename
        mimetype: MIME type
        state: Current download state
        config: Optional FanslyConfig instance
        session: Optional SQLAlchemy session

    Returns:
        Newly created Media record
    """
    media = Media(
        id=media_id,
        content_hash=file_hash,
        local_filename=filename,
        is_downloaded=True,
        mimetype=mimetype,
        accountId=get_account_id(state=state, session=session),
    )
    session.add(media)
    session.flush()
    return media


@with_database_session()
def handle_exact_filename_match(
    media: Media,
    file_path: Path,
    filename: str,
    mimetype: str,
    config: FanslyConfig | None = None,
    session: Session | None = None,
) -> tuple[Media, bool]:
    """Handle case where filenames match exactly."""
    if media.content_hash:
        # json_output(
        #     1,
        #     "dedupe-get_or_create_media",
        #     f"file_path: {file_path} -- media_id: {media.id} -- media.local_filename: {media.local_filename} -- media.is_downloaded: {media.is_downloaded} -- media.content_hash: {media.content_hash}",
        # )
        return media, True

    # If no hash, calculate it
    calculated_hash = calculate_file_hash(file_path=file_path, mimetype=mimetype)
    if calculated_hash:
        other_media = find_media_by_hash(content_hash=calculated_hash, session=session)
        if other_media and other_media.id != media.id:
            # Merge the records - keep the one with the correct ID
            update_media_with_hash(media, calculated_hash, session=session)
            session.delete(other_media)
            session.flush()
        else:
            update_media_with_hash(media, calculated_hash, session=session)
        return media, True

    return media, False


@with_database_session()
def handle_missing_filename(
    media: Media,
    file_path: Path,
    filename: str,
    mimetype: str,
    config: FanslyConfig | None = None,
    session: Session | None = None,
) -> tuple[Media, bool]:
    """Handle case where media has no filename."""
    media.local_filename = filename
    media.is_downloaded = True
    calculated_hash = calculate_file_hash(file_path=file_path, mimetype=mimetype)
    if calculated_hash:
        media.content_hash = calculated_hash
    session.flush()
    return media, bool(calculated_hash)


@with_database_session()
def handle_path_normalization(
    media: Media,
    file_path: Path,
    filename: str,
    config: FanslyConfig | None = None,
    session: Session | None = None,
) -> tuple[Media, bool]:
    """Handle case where paths need normalization."""
    media.local_filename = filename
    media.is_downloaded = True
    session.flush()
    return media, bool(media.content_hash)


@with_database_session()
def update_media_with_hash(
    media: Media,
    calculated_hash: str,
    filename: str | None = None,
    config: FanslyConfig | None = None,
    session: Session | None = None,
) -> None:
    """Update media record with hash and optionally filename."""
    media.content_hash = calculated_hash
    media.is_downloaded = True
    if filename:
        media.local_filename = filename
    session.flush()


@with_database_session()
def handle_existing_media(
    media: Media,
    file_path: Path,
    filename: str,
    mimetype: str,
    file_hash: str | None,
    trust_filename: bool,
    config: FanslyConfig | None = None,
    session: Session | None = None,
) -> tuple[Media, bool]:
    """Handle an existing media record.

    Args:
        media: Existing Media record
        file_path: Path to the file
        filename: Normalized filename
        mimetype: MIME type
        file_hash: Optional pre-calculated hash
        trust_filename: Whether to trust the filename without verification
        config: Optional FanslyConfig instance
        session: Optional SQLAlchemy session

    Returns:
        tuple of (Media record, bool indicating if hash was verified)
    """
    # First check exact filename match
    if media.local_filename == filename:
        return handle_exact_filename_match(
            media=media,
            file_path=file_path,
            filename=filename,
            mimetype=mimetype,
            session=session,
        )

    # Handle missing filename
    if media.local_filename is None:
        return handle_missing_filename(
            media=media,
            file_path=file_path,
            filename=filename,
            mimetype=mimetype,
            session=session,
        )

    # Handle path normalization
    if media.local_filename == str(file_path) or get_filename_only(
        path=media.local_filename
    ) == str(file_path):
        return handle_path_normalization(
            media=media,
            file_path=file_path,
            filename=filename,
            session=session,
        )

    # Check content match if hash exists
    if media.content_hash:
        calculated_hash = file_hash or calculate_file_hash(
            file_path=file_path, mimetype=mimetype
        )
        if calculated_hash and calculated_hash == media.content_hash:
            # Same content but wrong filename - check if DB's file exists
            db_file_exists = False
            for found_file in safe_rglob(
                base_path=file_path.parent, pattern=media.local_filename
            ):
                if found_file.is_file():
                    db_file_exists = True
                    break

            print_info(f"db_filename: {media.local_filename} -- filename: {file_path}")

            if db_file_exists:
                # DB's file exists, this is a duplicate - remove it
                file_path.unlink()
                return media, True

            # DB's file is missing but content matches - update DB filename
            update_media_with_hash(media, calculated_hash, filename, session=session)
            return media, True

    if trust_filename:
        return media, False

    return media, False


@with_database_session()
def get_or_create_media(
    file_path: Path,
    media_id: str | None,
    mimetype: str,
    state: DownloadState,
    file_hash: str | None = None,
    trust_filename: bool = False,
    config: FanslyConfig | None = None,
    session: Session | None = None,
) -> tuple[Media, bool]:
    """Get or create a media record.

    Args:
        file_path: Path to the media file
        media_id: Optional media ID from filename
        mimetype: MIME type of the file
        state: Current download state
        file_hash: Optional pre-calculated hash
        trust_filename: Whether to trust the filename without verification
        config: Optional FanslyConfig instance
        session: Optional SQLAlchemy session

    Returns:
        tuple of (Media instance, bool indicating if hash was verified)
    """
    filename = normalize_filename(get_filename_only(file_path), config=config)

    # Try by ID if available
    if media_id:
        media = find_media_by_id(media_id=media_id, session=session)
        if media:
            return handle_existing_media(
                media=media,
                file_path=file_path,
                filename=filename,
                mimetype=mimetype,
                file_hash=file_hash,
                trust_filename=trust_filename,
                session=session,
            )

    # Try by hash if we have one or need to calculate it
    if not trust_filename:
        if not file_hash:
            file_hash = calculate_file_hash(file_path=file_path, mimetype=mimetype)

        if file_hash:
            media = find_media_by_hash(content_hash=file_hash, session=session)
            if media:
                return media, True

    # Double-check for ID before creating new record
    if media_id:
        media = find_media_by_id(media_id=media_id, for_update=True, session=session)
        if media:
            # Verify hash if present
            if media.content_hash:
                calculated_hash = file_hash or calculate_file_hash(
                    file_path=file_path, mimetype=mimetype
                )
                if calculated_hash and media.content_hash != calculated_hash:
                    raise MediaHashMismatchError(
                        f"Hash mismatch for media {media_id}: "
                        f"DB has {media.content_hash}, file has {calculated_hash}"
                    )

            # Update existing record
            update_media_record(
                media=media,
                file_hash=file_hash,
                filename=filename,
                mimetype=mimetype,
                state=state,
                session=session,
            )
            return media, False

    # Create new if definitely not found
    media = create_media_record(
        media_id=media_id,
        file_hash=file_hash,
        filename=filename,
        mimetype=mimetype,
        state=state,
        session=session,
    )
    return media, False


@require_database_config
@with_database_session()
def migrate_full_paths_to_filenames(
    config: FanslyConfig,
    session: Session | None = None,
) -> None:
    """Update database records that have full paths stored in local_filename."""
    print_info("Starting migration of full paths to filenames in database...")

    # First, let's count how many records need updating
    count_query = (
        select(func.count())  # pylint: disable=not-callable
        .select_from(Media)
        .where(or_(Media.local_filename.like("%/%"), Media.local_filename.like("%\\%")))
    )
    count = session.execute(count_query).scalar()

    if count == 0:
        print_info("No records found with full paths. Migration not needed.")
        return

    print_info(f"Found {count} records with full paths to update...")

    # Get all records that need updating
    records_query = select(Media.id, Media.local_filename).where(
        or_(Media.local_filename.like("%/%"), Media.local_filename.like("%\\%"))
    )
    records = session.execute(records_query).fetchall()

    # Update each record
    updated = 0
    for record in records:
        try:
            # Convert the path to just filename
            new_filename = get_filename_only(path=record.local_filename)

            # Update the record
            media = session.get(Media, record.id)
            if media:
                media.local_filename = new_filename
            updated += 1

            # Commit every 100 records to avoid large transactions
            if updated % 100 == 0:
                session.commit()
                print_info(f"Updated {updated} of {count} records...")

        except Exception as e:
            print_info(f"Error updating record {record.id}: {e}")
            continue

    # Final commit for any remaining records
    session.commit()

    print_info(f"Migration complete! Updated {updated} of {count} records.")


async def process_file_batch(
    files: list[Path],
    config: FanslyConfig,
    state: DownloadState,
    executor: ThreadPoolExecutor,
    pbar: tqdm,
    session: AsyncSession,
) -> tuple[int, int]:
    """Process a batch of files concurrently."""
    tasks = []
    for file_path in files:
        if not file_path.is_file():
            continue

        # Determine mimetype
        mimetype, _ = mimetypes.guess_type(file_path)
        if not mimetype:
            pbar.update(1)
            continue

        # Create task for file processing
        task = asyncio.create_task(
            process_single_file(
                file_path=file_path,
                mimetype=mimetype,
                config=config,
                state=state,
                executor=executor,
                pbar=pbar,
                session=session,
            )
        )
        tasks.append(task)

    # Wait for all tasks to complete
    results = await asyncio.gather(*tasks)

    # Sum up results
    processed = sum(r[0] for r in results)
    preserved = sum(r[1] for r in results)

    return processed, preserved


async def process_single_file(
    file_path: Path,
    mimetype: str,
    config: FanslyConfig,
    state: DownloadState,
    executor: ThreadPoolExecutor,
    pbar: tqdm,
    session: AsyncSession,
) -> tuple[int, int]:
    """Process a single file for deduplication."""
    filename = file_path.name
    extension = file_path.suffix.lower()

    pbar.set_description(
        f"Processing {filename[:(40 - len(extension))]}..{extension}",
        refresh=True,
    )  # Show current file

    # Extract media ID if present
    media_id, is_preview = get_id_from_filename(filename)
    # json_output(
    #     1,
    #     "dedupe-process_single_file",
    #     f"file_path: {file_path} -- media_id: {media_id} -- is_preview: {is_preview}"
    # )

    # Process file based on type
    processed = 0
    preserved = 0

    loop = asyncio.get_event_loop()

    # Handle files with hash2 format (trusted source)
    match2 = HASH2_PATTERN.search(filename)
    if match2:
        hash2_value = match2.group(1)
        new_name = HASH2_PATTERN.sub("", filename)

        _, hash_verified = await loop.run_in_executor(
            executor,
            get_or_create_media,
            file_path.with_name(new_name),
            media_id,
            mimetype,
            state,
            hash2_value,
            True,
            config,
            session,
        )
        if hash_verified:
            preserved += 1

    elif media_id:
        _, hash_verified = await loop.run_in_executor(
            executor,
            get_or_create_media,
            file_path,
            media_id,
            mimetype,
            state,
            None,
            True,
            config,
            session,
        )
        if hash_verified:
            processed += 1

    else:
        # For all other files, process normally without trusting the filename
        _, hash_verified = await loop.run_in_executor(
            executor,
            get_or_create_media,
            file_path,
            media_id,
            mimetype,
            state,
            None,
            False,
            config,
            session,
        )
        if hash_verified:
            processed += 1

    pbar.update(1)
    return processed, preserved


@require_database_config
@with_database_session(async_session=True)
async def count_downloaded_media(
    session: AsyncSession,
    creator_id: str,
) -> int:
    """Count existing downloaded media records for a creator."""
    return (
        await session.execute(
            select(func.count())  # pylint: disable=not-callable
            .select_from(Media)
            .where(
                Media.is_downloaded == True,  # noqa: E712
                Media.accountId == creator_id,
            )
        )
    ).scalar_one()


async def verify_db_files(
    session: AsyncSession,
    state: DownloadState,
    all_files: list[Path],
) -> None:
    """Verify database files exist and update records accordingly."""
    downloaded_list = (
        (
            await session.execute(
                select(Media).where(
                    Media.is_downloaded == True,  # noqa: E712
                    Media.accountId == state.creator_id,
                )
            )
        )
        .scalars()
        .all()
    )

    with tqdm(
        total=len(downloaded_list),
        desc="Checking DB files...",
        dynamic_ncols=True,
    ) as pbar:
        for media in downloaded_list:
            pbar.set_description(f"Checking DB - ID: {media.id}", refresh=True)
            # json_output(
            #     1,
            #     "dedupe-verify_db_files",
            #     f"Checking DB - ID: {media.id} -- filename: {media.local_filename} -- hash: {media.content_hash}"
            # )

            if media.local_filename:
                if any(f.name == media.local_filename for f in all_files):
                    pbar.update(1)
                    continue

                media.is_downloaded = False
                media.content_hash = None
                media.local_filename = None
            else:
                media.is_downloaded = False
                media.content_hash = None

            pbar.update(1)

        await session.commit()


async def process_files(
    files: list[Path],
    config: FanslyConfig,
    state: DownloadState,
    executor: ThreadPoolExecutor,
    session: AsyncSession,
) -> tuple[int, int]:
    """Process files in parallel batches."""
    file_count = len([f for f in files if f.is_file()])
    processed_count = 0
    preserved_count = 0
    batch_size = 50  # Adjust based on system capabilities

    with tqdm(total=file_count, desc="Processing files", dynamic_ncols=True) as pbar:
        # Process files in batches
        for i in range(0, len(files), batch_size):
            batch = files[i : i + batch_size]
            proc_count, pres_count = await process_file_batch(
                files=batch,
                config=config,
                state=state,
                executor=executor,
                pbar=pbar,
                session=session,
            )
            processed_count += proc_count
            preserved_count += pres_count

    return processed_count, preserved_count


async def dedupe_init_async(
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
    # First, migrate any full paths in the database to filenames only
    migrate_full_paths_to_filenames(config)

    # Create the base user path download_directory/creator_name
    set_create_directory_for_download(config, state)

    if not state.download_path or not state.download_path.is_dir():
        return

    print_info(
        f"Initializing database-backed deduplication for:\n{17 * ' '}{state.download_path}"
    )

    # Count existing records
    existing_downloaded = await count_downloaded_media(
        session=session, creator_id=state.creator_id
    )
    print_info(f"Existing downloaded records: {existing_downloaded}")

    print_info("Processing files in optimized order (parallel processing)...")

    # Get total file count first for tqdm
    all_files = list(safe_rglob(base_path=state.download_path, pattern="*"))
    file_count = len([f for f in all_files if f.is_file()])

    # Process files in parallel batches
    with ThreadPoolExecutor(max_workers=min(32, (file_count + 49) // 50)) as executor:
        processed_count, preserved_count = await process_files(
            files=all_files,
            config=config,
            state=state,
            executor=executor,
            session=session,
        )

    # Verify database files and update records
    await verify_db_files(
        session=session,
        state=state,
        all_files=all_files,
    )

    # Get updated counts
    new_downloaded = await count_downloaded_media(
        session=session, creator_id=state.creator_id
    )

    print_info(
        f"Database deduplication initialized!"
        f"\n{17 * ' '}Added {new_downloaded - existing_downloaded} new entries to the database"
        f"\n{17 * ' '}Processed {processed_count} files with content verification"
        f"\n{17 * ' '}Preserved {preserved_count} trusted hash2 format entries"
    )

    print_info(
        "Files will now be tracked in the database instead of using filename hashes."
        "\nThis provides better organization and reliable deduplication."
    )


@require_database_config
@with_database_session(async_session=True)
async def check_file_exists(
    base_path: Path,
    filename: str,
) -> bool:
    """Check if a file exists in the given base path."""
    for found_file in safe_rglob(base_path=base_path, pattern=filename):
        if found_file.is_file():
            return True
    return False


async def handle_hash_match(
    media: Media,
    filename: Path,
    state: DownloadState,
    session: AsyncSession,
) -> bool:
    """Handle case where content hash matches but filenames differ."""
    # First check for direct path match
    db_filename = media.local_filename
    if paths_match(db_filename, filename):
        media.local_filename = get_filename_only(path=filename)
        media.is_downloaded = True
        await session.commit()
        return True

    # Then check if file exists
    db_file_exists = await check_file_exists(
        base_path=state.download_path,
        filename=media.local_filename,
    )

    # json_output(
    #     1,
    #     "dedupe-handle_hash_match",
    #     f"db_filename: {media.local_filename} -- filename: {filename} -- exists: {db_file_exists}"
    # )
    print_info(f"db_filename: {media.local_filename} -- filename: {filename}")

    if db_file_exists:
        # DB's file exists, this is a duplicate - remove it
        filename.unlink()
        return True

    # DB's file is missing but content matches - update DB filename
    media.local_filename = get_filename_only(path=filename)
    media.is_downloaded = True
    await session.commit()
    return True


async def calculate_hash_async(
    loop: asyncio.AbstractEventLoop,
    filename: Path,
    mimetype: str,
) -> str | None:
    """Calculate file hash asynchronously."""
    return await loop.run_in_executor(
        None,
        calculate_file_hash,
        filename,
        mimetype,
    )


async def handle_id_match(
    media: Media,
    filename: Path,
    mimetype: str,
    state: DownloadState,
    loop: asyncio.AbstractEventLoop,
    session: AsyncSession,
) -> tuple[bool, str | None]:
    """Handle case where media ID matches."""
    # First check if filenames match
    if media.local_filename == get_filename_only(path=filename):
        # Same filename for same ID - perfect match
        # json_output(
        #     1,
        #     "dedupe-handle_id_match",
        #     f"file_path: {file_path} -- media_id: {media.id} -- media.local_filename: {media.local_filename} -- media.is_downloaded: {media.is_downloaded} -- media.content_hash: {media.content_hash}",
        # )
        if not media.content_hash:
            file_hash = await calculate_hash_async(loop, filename, mimetype)
            if file_hash:
                media.content_hash = file_hash
        media.is_downloaded = True
        await session.commit()
        return True, None

    # Handle missing filename
    if media.local_filename is None:
        media.local_filename = get_filename_only(path=filename)
        media.is_downloaded = True
        file_hash = await calculate_hash_async(loop, filename, mimetype)
        if file_hash:
            media.content_hash = file_hash
        await session.commit()
        return True, None

    # Handle path normalization
    if paths_match(media.local_filename, filename):
        # Same filename but with full path in DB
        media.local_filename = get_filename_only(path=filename)
        media.is_downloaded = True
        await session.commit()
        return True, None

    # Different filename but same ID - check if it's actually the same file
    if media.content_hash:  # Only if we have a hash to compare
        file_hash = await calculate_hash_async(loop, filename, mimetype)
        if file_hash and file_hash == media.content_hash:
            # Same content but wrong filename - check if DB's file exists
            if await handle_hash_match(media, filename, state, session):
                return True, None

    return False, None


async def handle_normalized_filename(
    normalized: str,
    filename: Path,
    mimetype: str,
    state: DownloadState,
    loop: asyncio.AbstractEventLoop,
    session: AsyncSession,
) -> bool:
    """Handle lookup by normalized filename."""
    # print_info(f"Checking normalized filename: {normalized}")
    media = await loop.run_in_executor(
        None,
        lambda: session.execute(
            select(Media).where(
                Media.local_filename.ilike(normalized),
                Media.is_downloaded == True,  # noqa: E712
            )
        ).scalar_one_or_none(),
    )
    if media and media.content_hash:
        file_hash = await calculate_hash_async(loop, filename, mimetype)
        if file_hash and file_hash == media.content_hash:
            return await handle_hash_match(media, filename, state, session)
    return False


async def dedupe_media_file_async(
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
        session: Optional database session

    Returns:
        bool: True if it is a duplicate or False otherwise
    """
    loop = asyncio.get_event_loop()
    session.add(media_record)

    # Try by ID first
    media_id, _ = get_id_from_filename(filename.name)
    if media_id:
        media = await loop.run_in_executor(
            None,
            lambda: session.execute(
                select(Media).where(Media.id == media_id)
            ).scalar_one_or_none(),
        )
        if media:
            is_match, file_hash = await handle_id_match(
                media=media,
                filename=filename,
                mimetype=mimetype,
                state=state,
                loop=loop,
                session=session,
            )
            if is_match:
                return True

    # Try by normalized filename
    normalized = normalize_filename(filename.name, config=config)
    if await handle_normalized_filename(
        normalized=normalized,
        filename=filename,
        mimetype=mimetype,
        state=state,
        loop=loop,
        session=session,
    ):
        return True

    # If not in DB or no hash match, calculate hash and update DB
    file_hash = await calculate_hash_async(loop, filename, mimetype)
    if file_hash:
        # Check if hash exists in database
        media = await loop.run_in_executor(
            None,
            lambda: session.execute(
                select(Media).where(Media.content_hash == file_hash)
            ).scalar_one_or_none(),
        )
        if media:
            if await handle_hash_match(media, filename, state, session):
                return True

        # No match found, update our media record
        media_record.content_hash = file_hash
        media_record.local_filename = get_filename_only(path=filename)
        media_record.is_downloaded = True
        await session.commit()

    return False


# For backward compatibility
dedupe_init = dedupe_init_async  # type: ignore
dedupe_media_file = dedupe_media_file_async  # type: ignore
