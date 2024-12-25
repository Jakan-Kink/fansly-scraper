"""Item Deduplication"""

from pathlib import Path

from config import FanslyConfig
from download.downloadstate import DownloadState
from fileio.fnmanip import get_hash_for_image, get_hash_for_other_content
from pathio import set_create_directory_for_download
from textio import print_info


def dedupe_init(config: FanslyConfig, state: DownloadState):
    """Initialize deduplication by scanning existing files and updating the database.

    This function:
    1. Creates the download directory if needed
    2. Detects and migrates files using the old hash-in-filename format
    3. Scans all files and updates the database
    4. Updates the database with file information
    5. Marks files as downloaded in the database
    """
    import mimetypes
    import re

    from metadata.media import Media

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

    # First pass: Check for files already in database and detect hash formats
    hash0_pattern = re.compile(r"_hash_([a-fA-F0-9]+)")
    hash1_pattern = re.compile(r"_hash1_([a-fA-F0-9]+)")
    hash2_pattern = re.compile(r"_hash2_([a-fA-F0-9]+)")
    migrated_count = 0
    preserved_count = 0
    db_matched_count = 0

    print_info("Checking existing files and hash formats...")

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
                if needs_rehash:
                    # Calculate new hash for old format files
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
                            media = Media(
                                content_hash=file_hash,
                                local_filename=str(new_path),
                                is_downloaded=True,
                                mimetype=mimetype,
                                accountId=state.creator_id,
                            )
                            session.add(media)
                        else:
                            # Update filename if it changed
                            media.local_filename = str(new_path)
                            media.is_downloaded = True
                        session.commit()

    if preserved_count > 0 or migrated_count > 0 or db_matched_count > 0:
        print_info(
            f"Migration summary:\n"
            f"{17 * ' '}Found {db_matched_count} files already in database\n"
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
        ):
            continue

        # Skip if file is already in database by filename
        with config._database.sync_session() as session:
            if session.query(Media).filter_by(local_filename=str(file_path)).first():
                continue

        # Guess mimetype
        mimetype, _ = mimetypes.guess_type(file_path)
        if not mimetype:
            continue

        # Calculate hash and update database
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
            # Check if hash exists
            media = session.query(Media).filter_by(content_hash=file_hash).first()
            if not media:
                # Create new record
                media = Media(
                    content_hash=file_hash,
                    local_filename=str(file_path),
                    is_downloaded=True,
                    mimetype=mimetype,
                    accountId=state.creator_id,
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
    media_id: int | None = None,
) -> bool:
    """Hashes media file data and checks if it's a duplicate using the database.

    Instead of storing hashes in filenames, this function:
    1. Calculates the file hash
    2. Checks if the hash exists in the database
    3. If it exists and is downloaded, skips the file
    4. If it exists but not downloaded, updates the local filename
    5. If it doesn't exist, adds it to the database

    Args:
        config: The current configuration
        state: The current download state, for statistics
        mimetype: The MIME type of the media item
        filename: The full path of the file to examine
        media_id: Optional media ID to link with the hash

    Returns:
        bool: True if it is a duplicate or False otherwise
    """
    import re

    from metadata.media import Media

    # Define hash2 pattern for checking filenames
    hash2_pattern = re.compile(r"_hash2_([a-fA-F0-9]+)")

    # Check if file is already in database by filename or hash
    with config._database.sync_session() as session:
        # First try by filename
        existing_by_name = (
            session.query(Media)
            .filter_by(local_filename=str(filename), is_downloaded=True)
            .first()
        )

        # Then try by hash if it's a hash2 file (trusted hash)
        existing_by_hash = None
        match = hash2_pattern.search(str(filename))
        if match:
            hash2_value = match.group(1)
            existing_by_hash = (
                session.query(Media)
                .filter_by(content_hash=hash2_value, is_downloaded=True)
                .first()
            )

        if existing_by_name or existing_by_hash:
            # Prefer hash2's record if both exist
            existing_media = existing_by_hash if existing_by_hash else existing_by_name

            if config.show_downloads and config.show_skipped_downloads:
                match_type = "hash" if existing_by_hash else "filename"
                print_info(
                    f"Deduplication [Database]: {mimetype.split('/')[-2]} '{filename.name}' → "
                    f"skipped (matched by {match_type})"
                )

            # If found by hash but filename differs, update the filename before deleting
            if existing_by_hash and not existing_by_name:
                existing_by_hash.local_filename = str(filename)
                session.commit()

            filename.unlink()
            state.duplicate_count += 1
            return True

    # Calculate file hash based on mimetype
    if "image" in mimetype:
        file_hash = get_hash_for_image(filename)
    else:
        file_hash = get_hash_for_other_content(filename)

    # Check database for existing hash
    with config._database.sync_session() as session:
        existing_media = session.query(Media).filter_by(content_hash=file_hash).first()

        if existing_media and existing_media.is_downloaded:
            # File already exists and is downloaded
            if config.show_downloads and config.show_skipped_downloads:
                print_info(
                    f"Deduplication [Database]: {mimetype.split('/')[-2]} '{filename.name}' → skipped (already downloaded)"
                )
            filename.unlink()
            state.duplicate_count += 1
            return True

        # If we have a media_id, try to find or update the media record
        if media_id:
            media = session.query(Media).filter_by(id=media_id).first()
            if media:
                media.content_hash = file_hash
                media.local_filename = str(filename)
                media.is_downloaded = True
                session.commit()
                return False

        # If no media_id or no existing record, create a new one
        if not existing_media:
            media = Media(
                content_hash=file_hash,
                local_filename=str(filename),
                is_downloaded=True,
                mimetype=mimetype,
                accountId=state.creator_id,  # Assuming creator_id is available in state
            )
            session.add(media)
            session.commit()
            return False
        else:
            # Hash exists but not downloaded - update the record
            existing_media.local_filename = str(filename)
            existing_media.is_downloaded = True
            session.commit()
            return False
