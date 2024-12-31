"""Filename normalization utilities."""

import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

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

    # Parse timestamp
    try:
        dt = datetime.strptime(f"{date_str} {hour:02d}:{minute:02d}", "%Y-%m-%d %H:%M")
        if not tz_str:
            # For files without timezone indicator
            created_at = None
            if config and id_part:
                # Try to get createdAt from database if we have an ID
                media_id = int(id_match.group(2))  # Extract just the numeric ID
                with config._database.sync_session() as session:
                    media = session.query(Media).filter_by(id=media_id).first()
                    if media and media.created_at:
                        created_at = media.created_at

            if created_at:
                # If we have createdAt, calculate the actual offset
                if created_at.hour != hour:
                    # Calculate offset based on the difference between createdAt (UTC) and local time
                    offset_hours = (created_at.hour - hour) % 24
                    # Create a timezone with the calculated offset
                    local_tz = timezone(timedelta(hours=-offset_hours))
                    dt = dt.replace(tzinfo=local_tz)
                else:
                    # If hours match, assume UTC
                    dt = dt.replace(tzinfo=timezone.utc)
            else:
                # Fallback to US/Eastern if no createdAt available
                dt = dt.replace(tzinfo=ZoneInfo("America/New_York"))

            # Convert to UTC
            dt = dt.astimezone(timezone.utc)
            dt = dt.replace(tzinfo=None)  # Remove timezone info after conversion

        # Format the timestamp part consistently in UTC
        ts_str = dt.strftime("%Y-%m-%d_at_%H-%M_UTC")

        # For comparison purposes, we'll strip the extension to .mp4
        # since .m3u8 and .ts files are just different formats of the same content
        if extension in ("m3u8", "ts"):
            extension = "mp4"

        # Return normalized filename with original ID part
        return f"{ts_str}_{id_part}.{extension}"
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
