"""Path helpers for livestream recording output."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from config.fanslyconfig import FanslyConfig
from metadata.models import StreamChannel


def _get_livestreams_dir(config: FanslyConfig, username: str) -> Path:
    """Return (and create) the Livestreams subfolder for a creator."""
    if config.download_directory is None:
        raise RuntimeError("download_directory is not set in configuration.")
    suffix = "_fansly" if config.use_folder_suffix else ""
    creator_dir = config.download_directory / f"{username}{suffix}"
    livestreams_dir = creator_dir / "Livestreams"
    livestreams_dir.mkdir(parents=True, exist_ok=True)
    return livestreams_dir


def _get_segments_base(config: FanslyConfig) -> Path:
    """Return the directory where livestream segment dirs are written.

    Uses ``config.temp_folder`` when set; falls back to
    ``<download_directory>/temp`` so segments are always near the final
    output without cluttering the Livestreams folder.
    """
    if config.temp_folder is not None:
        return Path(config.temp_folder)
    if config.download_directory is None:
        raise RuntimeError("download_directory is not set in configuration.")
    return Path(config.download_directory) / "temp"


def _build_output_path(
    config: FanslyConfig,
    username: str,
    channel: StreamChannel,
) -> Path:
    """Build the output MP4 path for a live recording.

    Format: ``<download_dir>/<username>_fansly/Livestreams/
    <username>_<YYYYMMDD_HHMMSS>_live.mp4``

    The timestamp comes from ``channel.stream.startedAt`` when available,
    falling back to the current UTC time.
    """
    base_dir = _get_livestreams_dir(config, username)

    if channel.stream and channel.stream.startedAt:
        ts = channel.stream.startedAt
    else:
        ts = datetime.now(UTC)

    ts_str = ts.strftime("%Y%m%d_%H%M%S")
    filename = f"{username}_{ts_str}_live.mp4"
    return base_dir / filename
