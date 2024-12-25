"""Item Deduplication"""

import mimetypes
import re
from pathlib import Path

from sqlalchemy.orm import Session

from config import FanslyConfig
from download.downloadstate import DownloadState
from fileio.fnmanip import get_hash_for_image, get_hash_for_other_content
from metadata.media import Media
from pathio import set_create_directory_for_download
from textio import print_info


def get_account_id(session: Session, state: DownloadState) -> int | None:
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
        else:
            # Create new account if it doesn't exist
            account = Account(username=state.creator_name)
            session.add(account)
            session.flush()  # This will assign an ID
            state.creator_id = str(account.id)
            return account.id

    return None


def dedupe_init(config: FanslyConfig, state: DownloadState):
    """Initialize deduplication by scanning existing files and updating the database.

    This function:
    1. Creates the download directory if needed
    2. Detects and migrates files using the old hash-in-filename format
    3. Detects files with ID patterns and verifies their hashes
    4. Scans all files and updates the database
    5. Updates the database with file information
    6. Marks files as downloaded in the database
    """

    # This will create the base user path download_directory/creator_name
    set_create_directory_for_download(config, state)

    if not state.download_path or not state.download_path.is_dir():
        return

    print_info(
        f"Initializing database-backed deduplication for:\n{17 * ' '}{state.download_path}"
    )

    # Count existing records
    with config._database.sync_session() as session:
        existing_downloaded = (
            session.query(Media)
            .filter_by(is_downloaded=True, accountId=state.creator_id)
            .count()
        )

    # First pass: Check for files already in database and detect hash/id formats
    hash0_pattern = re.compile(r"_hash_([a-fA-F0-9]+)")
    hash1_pattern = re.compile(r"_hash1_([a-fA-F0-9]+)")
    hash2_pattern = re.compile(r"_hash2_([a-fA-F0-9]+)")
    id_pattern = re.compile(r"_id_(\d+)")
    migrated_count = 0
    preserved_count = 0
    db_matched_count = 0
    id_matched_count = 0

    print_info("Checking existing files and hash/id formats...")

    for file_path in state.download_path.rglob("*"):
        if not file_path.is_file():
            continue

        filename = file_path.name
        new_path = file_path
        file_hash = None
        needs_rehash = False
        already_in_db = False

        # Check if file is already in database by filename or hash
        with config._database.sync_session() as session:
            # First try by filename
            existing_by_name = (
                session.query(Media)
                .filter_by(local_filename=str(file_path), is_downloaded=True)
                .first()
            )

            # Then try by hash if it's a hash2 file (trusted hash)
            existing_by_hash = None
            match = hash2_pattern.search(filename)
            if match:
                hash2_value = match.group(1)
                existing_by_hash = (
                    session.query(Media)
                    .filter_by(content_hash=hash2_value, is_downloaded=True)
                    .first()
                )

            # Check for ID pattern if not found by hash or name
            existing_by_id = None
            if not existing_by_hash and not existing_by_name:
                id_match = id_pattern.search(filename)
                if id_match:
                    media_id = int(id_match.group(1))
                    existing_by_id = session.query(Media).filter_by(id=media_id).first()
                    if existing_by_id:
                        # Calculate hash for verification
                        mimetype, _ = mimetypes.guess_type(file_path)
                        if mimetype:
                            if "image" in mimetype:
                                file_hash = get_hash_for_image(file_path)
                            elif "video" in mimetype or "audio" in mimetype:
                                file_hash = get_hash_for_other_content(file_path)

                            # If we have both hashes and they match, use this record
                            if file_hash and existing_by_id.content_hash == file_hash:
                                existing_by_id.local_filename = str(file_path)
                                existing_by_id.is_downloaded = True
                                session.commit()
                                id_matched_count += 1
                                already_in_db = True
                                continue

            if existing_by_name or existing_by_hash:
                # Prefer hash2's record if both exist
                existing_media = (
                    existing_by_hash if existing_by_hash else existing_by_name
                )
                file_hash = existing_media.content_hash
                already_in_db = True
                db_matched_count += 1

                # If found by hash but filename differs, update the filename
                if existing_by_hash and not existing_by_name:
                    existing_by_hash.local_filename = str(file_path)
                    session.commit()

        if not already_in_db:
            # Check for hash2 first (trusted source)
            match = hash2_pattern.search(filename)
            if match:
                file_hash = match.group(1)
                new_name = hash2_pattern.sub("", filename)
                new_path = file_path.with_name(new_name)
                preserved_count += 1
            else:
                # Check for older hash formats
                match0 = hash0_pattern.search(filename)
                match1 = hash1_pattern.search(filename)
                if match0:
                    new_name = hash0_pattern.sub("", filename)
                    new_path = file_path.with_name(new_name)
                    needs_rehash = True
                    migrated_count += 1
                elif match1:
                    new_name = hash1_pattern.sub("", filename)
                    new_path = file_path.with_name(new_name)
                    needs_rehash = True
                    migrated_count += 1

        if new_path != file_path:
            # Ensure we don't overwrite existing files
            counter = 1
            while new_path.exists():
                base = new_path.stem
                new_path = new_path.with_name(f"{base}_{counter}{new_path.suffix}")
                counter += 1

            # Rename file
            file_path.rename(new_path)

        # Process file for database if needed
        if not already_in_db:
            mimetype, _ = mimetypes.guess_type(new_path)
            if mimetype:
                if needs_rehash or not file_hash:
                    # Calculate new hash for old format files or files without hash
                    if "image" in mimetype:
                        file_hash = get_hash_for_image(new_path)
                    elif "video" in mimetype or "audio" in mimetype:
                        file_hash = get_hash_for_other_content(new_path)

                if file_hash:  # Only add to DB if we have a hash
                    with config._database.sync_session() as session:
                        # Check if hash exists in database
                        media = (
                            session.query(Media)
                            .filter_by(content_hash=file_hash)
                            .first()
                        )
                        if not media:
                            # Check if we have a media ID from filename
                            media_id = None
                            id_match = id_pattern.search(filename)
                            if id_match:
                                media_id = int(id_match.group(1))
                                media = (
                                    session.query(Media).filter_by(id=media_id).first()
                                )

                            if not media:
                                media = Media(
                                    id=media_id,  # Will be None if no ID in filename
                                    content_hash=file_hash,
                                    local_filename=str(new_path),
                                    is_downloaded=True,
                                    mimetype=mimetype,
                                    accountId=get_account_id(session, state),
                                )
                                session.add(media)
                            else:
                                # Update existing record by ID
                                media.content_hash = file_hash
                                media.local_filename = str(new_path)
                                media.is_downloaded = True
                                media.mimetype = mimetype
                        else:
                            # Update filename if it changed
                            media.local_filename = str(new_path)
                            media.is_downloaded = True
                        session.commit()

    if (
        preserved_count > 0
        or migrated_count > 0
        or db_matched_count > 0
        or id_matched_count > 0
    ):
        print_info(
            f"Migration summary:\n"
            f"{17 * ' '}Found {db_matched_count} files already in database\n"
            f"{17 * ' '}Found {id_matched_count} files by ID with matching hash\n"
            f"{17 * ' '}Preserved {preserved_count} hash2 format hashes\n"
            f"{17 * ' '}Migrated {migrated_count} files from old hash formats"
        )

    # Second pass: Scan remaining files and update database
    photo_count = 0
    video_count = 0
    for file_path in state.download_path.rglob("*"):
        if not file_path.is_file():
            continue

        # Skip if file was already processed during migration
        if (
            hash0_pattern.search(file_path.name)
            or hash1_pattern.search(file_path.name)
            or hash2_pattern.search(file_path.name)
            or id_pattern.search(file_path.name)
        ):
            continue

        # Check database for existing records
        with config._database.sync_session() as session:
            # First check by filename
            existing_by_name = (
                session.query(Media).filter_by(local_filename=str(file_path)).first()
            )
            if existing_by_name and existing_by_name.content_hash:
                # If we have a hash, trust it
                continue

            # Check for media ID in filename
            id_match = id_pattern.search(file_path.name)
            existing_by_id = None
            if id_match:
                media_id = int(id_match.group(1))
                existing_by_id = session.query(Media).filter_by(id=media_id).first()
                if existing_by_id and existing_by_id.content_hash:
                    # Update filename if needed
                    if existing_by_id.local_filename != str(file_path):
                        existing_by_id.local_filename = str(file_path)
                        session.commit()
                    continue

        # Only hash if we don't have a record with hash
        mimetype, _ = mimetypes.guess_type(file_path)
        if not mimetype:
            continue

        # Calculate hash only if needed
        if "image" in mimetype:
            file_hash = get_hash_for_image(file_path)
            photo_count += 1
        elif "video" in mimetype or "audio" in mimetype:
            file_hash = get_hash_for_other_content(file_path)
            if "video" in mimetype:
                video_count += 1
        else:
            continue

        # Update database
        with config._database.sync_session() as session:
            # Check if hash exists in any record
            media = session.query(Media).filter_by(content_hash=file_hash).first()
            if not media:
                # Create new record, using existing record if we found one by ID
                if existing_by_id:
                    media = existing_by_id
                    media.content_hash = file_hash
                    media.local_filename = str(file_path)
                    media.is_downloaded = True
                    media.mimetype = mimetype
                else:
                    media = Media(
                        content_hash=file_hash,
                        local_filename=str(file_path),
                        is_downloaded=True,
                        mimetype=mimetype,
                        accountId=get_account_id(session, state),
                    )
                    session.add(media)
            else:
                # Update existing record
                media.local_filename = str(file_path)
                media.is_downloaded = True
            session.commit()

    # Get updated counts
    with config._database.sync_session() as session:
        new_downloaded = (
            session.query(Media)
            .filter_by(is_downloaded=True, accountId=state.creator_id)
            .count()
        )

    print_info(
        f"Database deduplication initialized! Found and processed:"
        f"\n{17 * ' '}{photo_count} photos & {video_count} videos"
        f"\n{17 * ' '}Added {new_downloaded - existing_downloaded} new entries to the database"
        f"\n{17 * ' '}Migrated {migrated_count} files from old format"
        f"\n{17 * ' '}Matched {id_matched_count} files by ID"
    )

    if migrated_count > 0:
        print_info(
            "Successfully migrated files from hash-in-filename format to database tracking."
            "\nOld hashes have been preserved in the database for continuity."
        )

    print_info(
        "Files will now be tracked in the database instead of using filename hashes."
        "\nThis provides better organization and reliable deduplication."
    )


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

    # Calculate file hash based on mimetype
    if "image" in mimetype:
        file_hash = get_hash_for_image(filename)
    else:
        file_hash = get_hash_for_other_content(filename)

    # Check database for existing hash
    with config._database.sync_session() as session:
        session.add(media_record)  # Ensure our media_record is in this session

        # Look for existing media with this hash
        existing_media = (
            session.query(Media)
            .filter(Media.content_hash == file_hash, Media.id != media_record.id)
            .first()
        )

        if existing_media and existing_media.is_downloaded:
            # We found another media record with the same hash
            if (
                existing_media.local_filename
                and Path(existing_media.local_filename).exists()
            ):
                # The other file exists, we can delete this one
                if config.show_downloads and config.show_skipped_downloads:
                    print_info(
                        f"Deduplication [Hash]: {mimetype.split('/')[-2]} '{filename.name}' → "
                        f"skipped (duplicate of {Path(existing_media.local_filename).name})"
                    )
                filename.unlink()
                state.duplicate_count += 1
                return True
            else:
                # The other record's file is missing, update it to point to this file
                existing_media.local_filename = str(filename)
                session.commit()
                return False

        # No duplicates found, update our media record
        media_record.content_hash = file_hash
        media_record.local_filename = str(filename)
        media_record.is_downloaded = True
        session.commit()
        return False
