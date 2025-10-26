"""Path and Directory Management Module

This module provides centralized path handling for:
1. Directory creation and structure
2. Path determination for all file types
3. Consistent application of path-related config settings
"""

from .pathio import (
    ask_correct_dir,
    delete_temporary_pyinstaller_files,
    get_creator_base_path,
    get_creator_database_path,
    get_creator_metadata_path,
    get_media_save_path,
    set_create_directory_for_download,
)
from .types import PathConfig


__all__ = [
    "PathConfig",
    "ask_correct_dir",
    "delete_temporary_pyinstaller_files",
    "get_creator_base_path",
    "get_creator_database_path",
    "get_creator_metadata_path",
    "get_media_save_path",
    "set_create_directory_for_download",
]
