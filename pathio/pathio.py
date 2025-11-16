"""Path and Directory Management

This module is the single source of truth for all path-related operations.
It handles:
1. Directory creation and structure
2. Path determination for all file types
3. Consistent application of path-related config settings
"""

import contextlib
import os
import sys
import time
from pathlib import Path


# Optional GUI dependency
try:
    from tkinter import Tk, filedialog

    TKINTER_AVAILABLE = True
except ImportError:
    TKINTER_AVAILABLE = False
    Tk = None  # type: ignore[assignment,misc]
    filedialog = None  # type: ignore[assignment]

from config.logging import textio_logger
from download.downloadstate import DownloadState
from download.types import DownloadType
from media import MediaItem

from .types import PathConfig


# if the users custom provided filepath is invalid; a tkinter dialog will open during runtime, asking to adjust download path
def ask_correct_dir() -> Path:
    # Try GUI dialog if tkinter is available
    if TKINTER_AVAILABLE:
        root = Tk()
        root.withdraw()

        while True:
            directory_name = filedialog.askdirectory()

            if Path(directory_name).is_dir():
                textio_logger.opt(depth=1).log(
                    "INFO", f"Folder path chosen: {directory_name}"
                )
                return Path(directory_name)

            textio_logger.opt(depth=1).log(
                "ERROR",
                "<red>[5]</red> You did not choose a valid folder. Please try again!",
            )

    # Fallback to text-based input if interactive
    if sys.stdin and sys.stdin.isatty():
        textio_logger.opt(depth=1).log(
            "INFO", "tkinter not available - using text-based input"
        )

        while True:
            try:
                directory_name = input("Enter valid download directory path: ").strip()
                path = Path(directory_name)

                if path.is_dir():
                    textio_logger.opt(depth=1).log(
                        "INFO", f"Folder path chosen: {directory_name}"
                    )
                    return path

                textio_logger.opt(depth=1).log(
                    "ERROR",
                    "<red>[5]</red> You did not choose a valid folder. Please try again!",
                )
            except (KeyboardInterrupt, EOFError):
                textio_logger.opt(depth=1).log("ERROR", "Directory selection cancelled")
                raise

    # Not interactive - raise error
    raise RuntimeError(
        "Invalid download directory and unable to prompt for correction. "
        "Please fix the download_directory in config.ini or install tkinter for GUI dialogs."
    )


def set_create_directory_for_download(config: PathConfig, state: DownloadState) -> Path:
    """Sets and creates the appropriate download directory according to
    download type for storing media from a distinct creator.

    Args:
        config: Configuration object providing path settings
        state: Current download session's state

    Returns:
        The created path for current media downloads

    Raises:
        RuntimeError: If download directory or creator name not set
    """
    if config.download_directory is None:
        message = (
            "Internal error during directory creation - download directory not set."
        )
        raise RuntimeError(message)

    if state.creator_name is None:
        message = "Internal error during directory creation - creator name not set."
        raise RuntimeError(message)

    # Get base path with case-insensitive matching
    user_base_path = get_creator_base_path(config, state.creator_name)

    # Default directory if download types don't match in check below
    download_directory = user_base_path

    if state.download_type == DownloadType.COLLECTIONS:
        download_directory = config.download_directory / "Collections"

    elif state.download_type == DownloadType.MESSAGES and config.separate_messages:
        download_directory = user_base_path / "Messages"

    elif (
        (state.download_type == DownloadType.TIMELINE and config.separate_timeline)
        or (state.download_type == DownloadType.SINGLE and config.separate_timeline)
        or (state.download_type == DownloadType.WALL and config.separate_timeline)
    ):
        download_directory = user_base_path / "Timeline"

    # Save state
    state.base_path = user_base_path
    state.download_path = download_directory

    # Create the directory
    download_directory.mkdir(parents=True, exist_ok=True)

    return download_directory


def get_creator_base_path(config: PathConfig, creator_name: str) -> Path:
    """Get the base path for a creator's content.

    This function checks for existing case-insensitive matches to avoid
    creating duplicate directories on case-sensitive filesystems.

    Args:
        config: The program configuration
        creator_name: Name of the creator

    Returns:
        Base directory path for the creator's content
    """
    suffix = "_fansly" if config.use_folder_suffix else ""
    target_name = f"{creator_name}{suffix}"
    if config.download_directory is None:
        raise RuntimeError("Download directory is not set in configuration.")
    target_path = config.download_directory / target_name

    # Check for existing case-insensitive match
    if not target_path.exists():
        lower_target = target_name.lower()
        for entry in config.download_directory.iterdir():
            if entry.is_dir() and entry.name.lower() == lower_target:
                return entry

    return target_path


def get_creator_metadata_path(config: PathConfig, creator_name: str) -> Path:
    """Get the metadata directory path for a creator.

    Args:
        config: The program configuration
        creator_name: Name of the creator

    Returns:
        Path to the creator's metadata directory
    """
    base_path = get_creator_base_path(config, creator_name)
    meta_dir = base_path / "meta"
    meta_dir.mkdir(exist_ok=True)
    return meta_dir


def get_creator_database_path(config: PathConfig, creator_name: str) -> Path:
    """Get the database path for a specific creator.

    DEPRECATED: This function is for SQLite compatibility only.
    PostgreSQL uses connection strings and schemas, not file paths.

    Args:
        config: The program configuration
        creator_name: Name of the creator

    Returns:
        Path to the creator's database file (SQLite only)

    Note:
        **SQLite Only** - This function returns file paths for SQLite databases.
        With PostgreSQL, the application uses connection parameters instead.

        If separate_metadata is False:
        1. Uses config.metadata_db_file if set
        2. Otherwise uses <creator_base_path>/metadata/<creator_name>.db

        If separate_metadata is True:
        Uses <creator_meta_path>/metadata.sqlite3
    """
    if config.separate_metadata:
        # For separate metadata, use creator's meta directory
        return get_creator_metadata_path(config, creator_name) / "metadata.sqlite3"
    # For global metadata, first check metadata_db_file
    if config.metadata_db_file:
        return Path(config.metadata_db_file)

    # Otherwise use metadata dir in creator's base path
    if config.download_directory is None:
        raise RuntimeError("Download directory is not set in configuration.")
    metadata_dir = config.download_directory / "metadata"
    metadata_dir.mkdir(parents=True, exist_ok=True)
    return metadata_dir / "shared.db"


def get_media_save_path(
    config: PathConfig, state: DownloadState, media_item: MediaItem
) -> tuple[Path, Path]:
    """Get the save directory and full path for a media item.

    This function determines the appropriate save location based on:
    1. Download type (collections, messages, timeline)
    2. Media type (image, video, audio)
    3. Config settings (separate_messages, separate_timeline, separate_previews)

    Args:
        config: The program configuration
        state: Current download state
        media_item: Media item to determine path for

    Returns:
        tuple[Path, Path]: (save_directory, full_save_path)

    Raises:
        ValueError: If media type is unknown
    """
    # Get base directory based on download type
    base_directory = set_create_directory_for_download(config, state)

    if state.download_type == DownloadType.COLLECTIONS:
        save_dir = base_directory
    else:
        # Get media type directory
        if "image" in media_item.mimetype:
            save_dir = base_directory / "Pictures"
        elif "video" in media_item.mimetype:
            save_dir = base_directory / "Videos"
        elif "audio" in media_item.mimetype:
            save_dir = base_directory / "Audio"
        else:
            raise ValueError(f"Unknown mimetype: {media_item.mimetype}")

        # Add preview subdirectory if needed
        if media_item.is_preview and config.separate_previews:
            save_dir = save_dir / "Previews"

    # Create full path
    save_path = save_dir / media_item.get_file_name(for_preview=media_item.is_preview)
    return save_dir, save_path


def delete_temporary_pyinstaller_files() -> None:
    """Delete old files from the PyInstaller temporary folder.

    Files older than an hour will be deleted.
    """
    try:
        base_path = getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)

    except Exception:
        return

    temp_dir = Path(base_path).parent
    current_time = time.time()

    for folder in temp_dir.iterdir():
        with contextlib.suppress(Exception):
            if (
                folder.name.startswith("_MEI")
                and folder.is_dir()
                and (current_time - folder.stat().st_ctime) > 3600
            ):
                for root, dirs, files in os.walk(folder, topdown=False):
                    for file in files:
                        Path(root).joinpath(file).unlink()

                    for ind_dir in dirs:
                        Path(root).joinpath(ind_dir).rmdir()

                folder.rmdir()
