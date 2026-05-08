"""Path and Directory Management

This module is the single source of truth for all path-related operations.
It handles:
1. Directory creation and structure
2. Path determination for all file types
3. Consistent application of path-related config settings
"""

import sys
from pathlib import Path

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import PathCompleter

from config.logging import textio_logger
from download.downloadstate import DownloadState
from download.types import DownloadType
from metadata.models import Media

from .types import PathConfig


def ask_correct_dir() -> Path:
    """Prompt the user (TTY-only) for a valid download directory.

    Uses prompt_toolkit so the user gets path completion, ~/expansion, and
    arrow-key history. Non-interactive runs raise RuntimeError so the caller
    can surface a clear "fix config.yaml" error instead of hanging.
    """
    if not (sys.stdin and sys.stdin.isatty()):
        raise RuntimeError(
            "Invalid download directory and unable to prompt for correction. "
            "Please fix the download_directory in config.yaml."
        )

    session: PromptSession[str] = PromptSession(
        completer=PathCompleter(only_directories=True, expanduser=True),
    )
    while True:
        try:
            directory_name = session.prompt(
                "Enter valid download directory path: "
            ).strip()
        except (KeyboardInterrupt, EOFError):
            textio_logger.opt(depth=1).log("ERROR", "Directory selection cancelled")
            raise

        path = Path(directory_name).expanduser()
        if path.is_dir():
            textio_logger.opt(depth=1).log("INFO", f"Folder path chosen: {path}")
            return path

        textio_logger.opt(depth=1).log(
            "ERROR",
            "<red>[5]</red> You did not choose a valid folder. Please try again!",
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


def get_stash_path(local_path: Path, config: PathConfig) -> str:
    """Translate a local filesystem path to the path Stash sees.

    When Stash runs in Docker/NFS with a different mount prefix than the
    scraper, this replaces the download_directory prefix with stash_mapped_path.
    Falls back to str(local_path) when no mapping is configured.

    - override + mapped_path → ``str(mapped_path)`` (local_path ignored).
    - mapped_path only → prefix-substitute ``download_directory`` → ``mapped_path``.
    - neither → ``str(local_path)`` unchanged.

    Args:
        local_path: The local Path object to translate.
        config: The program configuration.

    Returns:
        String path in Stash's coordinate system.
    """
    if config.stash_override_dldir_w_mapped and config.stash_mapped_path is not None:
        return str(config.stash_mapped_path)

    local_str = str(local_path)
    if config.stash_mapped_path is not None and config.download_directory is not None:
        local_prefix = str(config.download_directory)
        if local_str.startswith(local_prefix):
            return str(config.stash_mapped_path) + local_str[len(local_prefix) :]
    return local_str


def get_media_save_path(
    config: PathConfig, state: DownloadState, media_item: Media
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
