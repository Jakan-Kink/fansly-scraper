"""Item Deduplication"""

import asyncio
import concurrent.futures
import mimetypes
import multiprocessing
import re
from pathlib import Path

from aiomultiprocess import Pool
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
from textio import print_error, print_info
from textio.logging import json_output


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

        # Update each record
        updated = 0
        async for record in records:
            try:
                # Convert the path to just filename
                new_filename = get_filename_only(record.local_filename)

                # Update the record
                media = await session.get(Media, record.id)
                if media:
                    media.local_filename = new_filename
                updated += 1

                # Commit every 100 records to avoid large transactions
                if updated % 100 == 0:
                    await session.commit()
                    print_info(f"Updated {updated} of {count} records...")

            except Exception as e:
                print_info(f"Error updating record {record.id}: {e}")
                continue

        # Final commit for any remaining records
        session.commit()

        print_info(f"Migration complete! Updated {updated} of {count} records.")


def get_filename_only(path: Path | str) -> str:
    """Get just the filename part from a path, ensuring it's a string."""
    if isinstance(path, str):
        path = Path(path)
    return path.name


def safe_rglob(base_path: Path, pattern: str) -> list[Path]:
    """Safely perform rglob with a pattern that might contain path separators.

    Args:
        base_path: The base directory to search in
        pattern: The filename pattern to search for

    Returns:
        List of matching Path objects
    """
    # Extract just the filename part if pattern contains path separators
    filename = get_filename_only(pattern)

    # Use rglob with just the filename part
    return list(base_path.rglob(filename))


@with_database_session(async_session=True)
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
    filename = normalize_filename(get_filename_only(file_path), config=config)
    hash_verified = False

    # First try by ID if available
    if media_id:
        media = (
            await session.execute(
                select(Media).where(Media.id == media_id).with_for_update()
            )
        ).scalar_one_or_none()
        if media:
            # If filenames match
            if media.local_filename == filename:
                # If record has a hash, we can trust it
                # json_output(
                #     1,
                #     "dedupe-get_or_create_media",
                #     f"file_path: {file_path} -- media_id: {media_id} -- media.local_filename: {media.local_filename} -- media.is_downloaded: {media.is_downloaded} -- media.content_hash: {media.content_hash}",
                # )
                if media.content_hash:
                    hash_verified = True
                    return media, hash_verified
                # If no hash, calculate it regardless of trust_filename
                calculated_hash = None
                if "image" in mimetype:
                    calculated_hash = get_hash_for_image(file_path)
                elif "video" in mimetype or "audio" in mimetype:
                    calculated_hash = get_hash_for_other_content(file_path)

                if calculated_hash:
                    # Get all media with this hash using optimized lookup
                    existing_row = config._database.find_media_by_hash(calculated_hash)
                    other_media_list = []
                    if existing_row and existing_row[0] != media.id:
                        # Convert tuple to Media object
                        other_media = await session.get(Media, existing_row[0])
                        if other_media:
                            other_media_list = [other_media]

                    if other_media_list:
                        # Keep the first one and delete the rest
                        other_media = other_media_list[0]
                        for duplicate in other_media_list[1:]:
                            await session.delete(duplicate)

                        # Now handle the first duplicate as before
                        if other_media.id != media.id:
                            # Merge the records - keep the one with the correct ID
                            media.content_hash = calculated_hash
                            media.is_downloaded = True
                            await session.delete(other_media)
                            await session.flush()
                    else:
                        media.content_hash = calculated_hash
                        media.is_downloaded = True
                        await session.flush()
                    hash_verified = True
                    return media, hash_verified
                # If we couldn't calculate hash but trust filename
                elif trust_filename:
                    return media, hash_verified

    # Try by hash if we have one using optimized lookup
    if file_hash:
        existing_row = config._database.find_media_by_hash(file_hash)
        if existing_row:
            media = await session.get(Media, existing_row[0])
            if media:
                hash_verified = True
                return media, hash_verified
    elif not trust_filename:
        # Calculate hash if needed and not provided
        calculated_hash = None
        if "image" in mimetype:
            calculated_hash = get_hash_for_image(file_path)
        elif "video" in mimetype or "audio" in mimetype:
            calculated_hash = get_hash_for_other_content(file_path)

        if calculated_hash:
            existing_row = config._database.find_media_by_hash(calculated_hash)
            if existing_row:
                media = await session.get(Media, existing_row[0])
                if media:
                    hash_verified = True
                    return media, hash_verified
            file_hash = calculated_hash  # Use for new record if needed

    # Double-check for ID before creating new record
    if media_id:
        # One final check with row lock before creating
        media = (
            await session.execute(
                select(Media).where(Media.id == media_id).with_for_update()
            )
        ).scalar_one_or_none()
        if media:
            # If we have a hash in the DB, verify it matches
            if media.content_hash:
                calculated_hash = None
                if not file_hash:  # Only calculate if we don't already have it
                    if "image" in mimetype:
                        calculated_hash = get_hash_for_image(file_path)
                    elif "video" in mimetype or "audio" in mimetype:
                        calculated_hash = get_hash_for_other_content(file_path)
                else:
                    calculated_hash = file_hash

                if calculated_hash and media.content_hash != calculated_hash:
                    raise MediaHashMismatchError(
                        f"Hash mismatch for media {media_id}: "
                        f"DB has {media.content_hash}, file has {calculated_hash}"
                    )

            # Update existing record instead of creating new
            media.content_hash = file_hash
            media.local_filename = filename
            media.is_downloaded = True
            media.mimetype = mimetype
            if not media.accountId:
                media.accountId = await get_account_id(session, state)
            await session.flush()
            return media, hash_verified

    # Create new if definitely not found
    media = Media(
        id=media_id,
        content_hash=file_hash,  # Might be None if trust_filename=True
        local_filename=filename,
        is_downloaded=True,
        mimetype=mimetype,
        accountId=(await get_account_id(session, state)),
    )
    session.add(media)
    await session.flush()

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
            await session.execute(select(Account).where(username=state.creator_name))
        ).scalar_one_or_none()
        if account:
            # Update state.creator_id for future use
            state.creator_id = str(account.id)
            return account.id
        else:
            # Create new account if it doesn't exist
            account = Account(username=state.creator_name)
            session.add(account)
            await session.flush()  # This will assign an ID
            state.creator_id = str(account.id)
            return account.id

    return None


@require_database_config
async def dedupe_init(config: FanslyConfig, state: DownloadState):
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
    await migrate_full_paths_to_filenames(config)

    # Create the base user path download_directory/creator_name
    set_create_directory_for_download(config, state)

    if not state.download_path or not state.download_path.is_dir():
        return

    print_info(
        f"Initializing database-backed deduplication for:\n{17 * ' '}{state.download_path}"
    )

    # Count existing records
    async with config._database.async_session_scope() as session:
        result = await session.execute(
            select(func.count())  # pylint: disable=not-callable
            .select_from(Media)
            .where(
                Media.is_downloaded == True,  # noqa: E712
                Media.accountId == state.creator_id,
            )
        )
        existing_downloaded = result.scalar_one()
        print_info(f"Existing downloaded records: {existing_downloaded}")

    # Initialize patterns and counters
    processed_count = 0
    preserved_count = 0
    hash2_pattern = re.compile(r"_hash2_([a-fA-F0-9]+)")

    # Get all files
    all_files = list(safe_rglob(state.download_path, "*"))

    # Function to calculate file hash in a separate process
    def calculate_file_hash(file_info: tuple[Path, str]) -> tuple[Path, str | None]:
        """Calculate hash for a file in a separate process.

        Args:
            file_info: Tuple of (file_path, mimetype)

        Returns:
            Tuple of (file_path, hash or None)
        """
        file_path, mimetype = file_info
        try:
            if "image" in mimetype:
                return file_path, get_hash_for_image(file_path)
            elif "video" in mimetype or "audio" in mimetype:
                return file_path, get_hash_for_other_content(file_path)
        except Exception:
            pass
        return file_path, None

    max_workers = max(
        1, multiprocessing.cpu_count() // 2
    )  # Use half of available cores

    # First, collect all files that need hashing
    files_to_hash = []
    pbar = tqdm(
        total=len(all_files), desc="Processing files", dynamic_ncols=True, unit="files"
    )

    for file_path in all_files:
        if not file_path.is_file():
            pbar.update(1)
            continue

        filename = file_path.name
        # Extract media ID if present
        media_id, is_preview = get_id_from_filename(filename)

        # Determine mimetype
        mimetype, _ = mimetypes.guess_type(file_path)
        if not mimetype:
            pbar.update(1)
            continue

        # Update progress bar description with current file
        extension = file_path.suffix.lower()
        pbar.set_description(
            f"Processing {file_path.name[:(40 - len(extension))]}..{extension}",
            refresh=True,
        )

        # Handle files with hash2 format (trusted source)
        match2 = hash2_pattern.search(filename)
        if match2:
            hash2_value = match2.group(1)
            new_name = hash2_pattern.sub("", filename)
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
            pbar.update(1)
            continue

        if media_id:
            _, hash_verified = await get_or_create_media(
                file_path=file_path,
                media_id=media_id,
                mimetype=mimetype,
                state=state,
                trust_filename=True,
                config=config,
            )
            if hash_verified:
                processed_count += 1
            pbar.update(1)
            continue

        # Add file to hash list
        files_to_hash.append((file_path, mimetype))
        pbar.update(1)

    pbar.close()

    # Hash files in parallel using multiprocessing
    with tqdm(
        total=len(files_to_hash),
        desc=f"Processing files ({max_workers} workers)",
        dynamic_ncols=True,
        unit="files",
    ) as pbar:
        # Create a chunksize that balances overhead and parallelism
        chunksize = max(1, len(files_to_hash) // (max_workers * 4))

        with multiprocessing.Pool(processes=max_workers) as pool:
            try:
                # Use imap_unordered with a reasonable chunksize for better progress updates
                for file_path, file_hash in pool.imap_unordered(
                    calculate_file_hash, files_to_hash, chunksize=chunksize
                ):
                    if file_hash is None:
                        pbar.update(1)
                        continue

                    # Update progress bar description with current file
                    extension = file_path.suffix.lower()
                    pbar.set_description(
                        f"Processing {file_path.name[:(40 - len(extension))]}..{extension}",
                        refresh=True,
                    )

                    # Process the file with its hash
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

                    # Update progress bar in main process
                    pbar.update(1)
                    # Periodically refresh to show current speed
                    if pbar.n % 10 == 0:
                        pbar.refresh()

            except Exception as e:
                print_error(f"Error processing files: {e}")
                # Let the main process handle cleanup

    async with config._database.async_session_scope() as session:
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
            total=len(downloaded_list), desc="Checking DB files...", dynamic_ncols=True
        ) as pbar:
            for media in downloaded_list:
                pbar.set_description(f"Checking DB - ID: {media.id}", refresh=True)
                # print_info(
                #     f"Checking DB - ID: {media.id} -- filename: {media.local_filename} -- hash: {media.content_hash}"
                # )
                if media.local_filename:
                    if any(f.name == media.local_filename for f in all_files):
                        pbar.update(1)
                        continue
                    else:
                        media.is_downloaded = False
                        media.content_hash = None
                        media.local_filename = None
                else:
                    media.is_downloaded = False
                    media.content_hash = None
                await session.commit()
                pbar.update(1)

    # Get updated counts
    async with config._database.async_session_scope() as session:
        new_downloaded = (
            await session.execute(
                select(func.count())  # pylint: disable=not-callable
                .select_from(Media)
                .where(
                    Media.is_downloaded == True,  # noqa: E712
                    Media.accountId == state.creator_id,
                )
            )
        ).scalar_one()

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
def dedupe_media_file(
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
    from metadata.media import Media

    # Check database for existing records
    with config._database.session_scope() as session:
        session.add(media_record)  # Ensure our media_record is in this session

        # First try by ID
        media_id, is_preview = get_id_from_filename(filename.name)
        if media_id:
            existing_by_id = session.query(Media).filter_by(id=media_id).first()
            if existing_by_id:
                file_hash = None
                if existing_by_id.content_hash is None:
                    if "image" in mimetype:
                        file_hash = get_hash_for_image(filename)
                    elif "video" in mimetype or "audio" in mimetype:
                        file_hash = get_hash_for_other_content(filename)

                # First check if filenames match
                if existing_by_id.local_filename == get_filename_only(filename):
                    # Same filename for same ID - perfect match
                    if file_hash:
                        existing_by_id.content_hash = file_hash
                    existing_by_id.is_downloaded = True
                    session.commit()
                    return True

                if existing_by_id.local_filename is None:
                    existing_by_id.local_filename = get_filename_only(filename)
                    existing_by_id.is_downloaded = True
                    file_hash = None
                    if "image" in mimetype:
                        file_hash = get_hash_for_image(filename)
                    elif "video" in mimetype or "audio" in mimetype:
                        file_hash = get_hash_for_other_content(filename)
                    if file_hash:
                        existing_by_id.content_hash = file_hash
                    session.commit()
                    return True

                if (
                    existing_by_id.local_filename == filename
                    or get_filename_only(existing_by_id.local_filename) == filename
                ):
                    # Same filename for same ID - perfect match, but with full path in DB
                    existing_by_id.local_filename = get_filename_only(filename)
                    existing_by_id.is_downloaded = True
                    session.commit()
                    return True

                # Different filename but same ID - check if it's actually the same file
                if existing_by_id.content_hash:  # Only if we have a hash to compare
                    file_hash = None
                    if "image" in mimetype:
                        file_hash = get_hash_for_image(filename)
                    elif "video" in mimetype or "audio" in mimetype:
                        file_hash = get_hash_for_other_content(filename)

                    if file_hash and file_hash == existing_by_id.content_hash:
                        # Same content but wrong filename - check if DB's file exists
                        db_file_exists = False
                        db_filename = existing_by_id.local_filename
                        if db_filename == str(filename):
                            existing_by_id.local_filename = get_filename_only(filename)
                            existing_by_id.is_downloaded = True
                            session.commit()
                            return True
                        for found_file in safe_rglob(state.download_path, db_filename):
                            if found_file.is_file():
                                db_file_exists = True
                                break
                        print_info(
                            f"db_filename: {db_filename} -- filename: {filename}"
                        )

                        if db_file_exists:
                            # DB's file exists, this is a duplicate with wrong name - remove it
                            filename.unlink()
                            return True
                        else:
                            # DB's file is missing but this is the same content - update DB filename
                            existing_by_id.local_filename = get_filename_only(filename)
                            existing_by_id.is_downloaded = True
                            session.commit()
                            return True

        # Try by normalized filename
        normalized_path = normalize_filename(filename.name, config=config)

        existing_by_name = (
            session.query(Media)
            .filter(
                Media.local_filename.ilike(normalized_path),
                Media.is_downloaded,
            )
            .first()
        )
        if existing_by_name:
            # First check if filenames match
            if existing_by_name.local_filename == get_filename_only(filename):
                # Same filename - perfect match
                return True

            # Different filename - check if it's actually the same file
            if existing_by_name.content_hash:  # Only if we have a hash to compare
                file_hash = None
                if "image" in mimetype:
                    file_hash = get_hash_for_image(filename)
                elif "video" in mimetype or "audio" in mimetype:
                    file_hash = get_hash_for_other_content(filename)

                if file_hash and file_hash == existing_by_name.content_hash:
                    # Same content but wrong filename - check if DB's file exists
                    db_file_exists = False
                    db_filename = existing_by_name.local_filename
                    for found_file in safe_rglob(state.download_path, db_filename):
                        if found_file.is_file():
                            db_file_exists = True
                            break

                    if db_file_exists:
                        # DB's file exists, this is a duplicate with wrong name - remove it
                        filename.unlink()
                        return True
                    else:
                        # DB's file is missing but this is the same content - update DB filename
                        existing_by_name.local_filename = get_filename_only(filename)
                        session.commit()
                        return True

        # If not in DB or no hash match, calculate hash and update DB
        file_hash = None
        if "image" in mimetype:
            file_hash = get_hash_for_image(filename)
        elif "video" in mimetype or "audio" in mimetype:
            file_hash = get_hash_for_other_content(filename)

        if file_hash:
            # Check if hash exists in database
            media = session.query(Media).filter_by(content_hash=file_hash).first()
            if media:
                # Found by hash - check if DB's file exists
                db_file_exists = False
                db_filename = media.local_filename
                for found_file in safe_rglob(state.download_path, db_filename):
                    if found_file.is_file():
                        db_file_exists = True
                        break

                if db_file_exists:
                    # DB's file exists, this is a duplicate - remove it
                    filename.unlink()
                    return True
                else:
                    # DB's file is missing but this is the same content - update DB filename
                    media.local_filename = get_filename_only(filename)
                    session.commit()
                    return True

            # No match found, update our media record
            media_record.content_hash = file_hash
            media_record.local_filename = get_filename_only(filename)
            media_record.is_downloaded = True
            session.commit()
            return False
