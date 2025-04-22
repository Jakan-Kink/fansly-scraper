"""Filename normalization utilities."""

import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select

from config import FanslyConfig
from metadata.database import require_database_config
from metadata.media import Media


@require_database_config
def normalize_filename(filename: str, config: FanslyConfig | None = None) -> str:
    """Normalize filename to handle timezone differences.

    Converts filenames with different timezone formats to a standard format:
    - Extracts the ID and extension
    - Handles both preview and non-preview IDs
    - Handles both UTC and non-UTC timestamps
    - Handles hash patterns (_hash_, _hash1_, _hash2_)
    - If created_at is provided and ID matches, uses it to determine correct timezone offset

    Args:
        filename: The filename to normalize
        config: Optional config object for database access

    Returns:
        The normalized filename
    """
    # First check for hash patterns
    hash_match = re.search(r"_(hash2?|hash1)_([a-fA-F0-9]+)", filename)
    if hash_match:
        # Keep the hash pattern as is, it's used for deduplication
        return filename

    # Extract ID and extension
    id_match = re.search(r"_((?:preview_)?id_\d+)\.([^.]+)$", filename)
    if not id_match:
        return filename

    id_part = id_match.group(1)  # Includes preview_ if present
    extension = id_match.group(2)

    # Extract timestamp
    dt_match = re.match(
        r"(\d{4}-\d{2}-\d{2})_at_(\d{2})-(\d{2})(?:_([A-Z]+))?_", filename
    )
    if not dt_match:
        return filename

    date_str = dt_match.group(1)
    hour = int(dt_match.group(2))
    minute = int(dt_match.group(3))
    tz_str = dt_match.group(4)  # Will be None if no timezone in filename

    # If already has UTC suffix, return unchanged
    if tz_str == "UTC":
        return filename

    # Initialize created_at to None
    created_at = None

    # Parse timestamp
    try:
        # Parse date but we only need it for validation, not using the actual datetime object
        datetime.strptime(f"{date_str} {hour:02d}:{minute:02d}", "%Y-%m-%d %H:%M")

        # For files without timezone indicator
        if config and id_part:
            # Try to get createdAt from database if we have an ID
            id_number_match = re.search(r"(?:preview_)?id_(\d+)", id_part)
            if id_number_match:
                media_id = int(id_number_match.group(1))  # Extract just the numeric ID
                with config._database.session_scope() as session:
                    media = session.execute(
                        select(Media).where(Media.id == media_id)
                    ).scalar_one_or_none()
                    if media and media.createdAt:
                        created_at = media.createdAt

        # When we have a database match, use the database timestamp
        if created_at:
            # Use the database created_at timestamp for the normalized filename
            ts_str = created_at.strftime("%Y-%m-%d_at_%H-%M_UTC")
            return f"{ts_str}_{id_part}.{extension}"

        # If we have a local time (no timezone) and config is available, try to convert to UTC
        if not tz_str and config:
            # Assume the local time is in EST (UTC-4) if no timezone is specified
            # This is the most common case for Fansly downloads
            local_dt = datetime.strptime(
                f"{date_str} {hour:02d}:{minute:02d}", "%Y-%m-%d %H:%M"
            )
            local_dt = local_dt.replace(
                tzinfo=ZoneInfo("America/New_York")
            )  # EST/EDT timezone
            utc_dt = local_dt.astimezone(timezone.utc)
            ts_str = utc_dt.strftime("%Y-%m-%d_at_%H-%M_UTC")
            return f"{ts_str}_{id_part}.{extension}"

        # No database match, return original filename
        return filename

    except ValueError:
        return filename


def get_id_from_filename(filename: str) -> tuple[int | None, bool]:
    """Extract media ID and preview flag from filename.

    Returns:
        tuple[int | None, bool]: (media_id, is_preview)
        - media_id will be None if no ID found
        - is_preview will be True if it's a preview ID
    """
    id_match = re.search(r"_(?:(preview)_)?id_(\d+)", filename)
    if not id_match:
        return None, False

    is_preview = bool(id_match.group(1))
    media_id = int(id_match.group(2))
    return media_id, is_preview
