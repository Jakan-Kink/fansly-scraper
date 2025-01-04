"""Configuration File Manipulation"""

from .args import map_args_to_config
from .browser import (
    close_browser_by_name,
    find_leveldb_folders,
    get_auth_token_from_leveldb_folder,
    get_browser_config_paths,
    get_token_from_firefox_db,
    get_token_from_firefox_profile,
    parse_browser_from_string,
)
from .config import (
    copy_old_config_values,
    load_config,
    parse_items_from_line,
    sanitize_creator_names,
    save_config_or_raise,
    username_has_valid_chars,
    username_has_valid_length,
)
from .fanslyconfig import FanslyConfig
from .metadatahandling import MetadataHandling
from .modes import DownloadMode
from .validation import validate_adjust_config

__all__ = [
    "close_browser_by_name",
    "copy_old_config_values",
    "find_leveldb_folders",
    "get_auth_token_from_leveldb_folder",
    "get_browser_config_paths",
    "get_token_from_firefox_db",
    "get_token_from_firefox_profile",
    "load_config",
    "parse_browser_from_string",
    "parse_items_from_line",
    "sanitize_creator_names",
    "save_config_or_raise",
    "username_has_valid_chars",
    "username_has_valid_length",
    "validate_adjust_config",
    "DownloadMode",
    "FanslyConfig",
    "MetadataHandling",
    "map_args_to_config",
]
