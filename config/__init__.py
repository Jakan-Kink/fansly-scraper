"""Configuration File Manipulation"""

from .config import (  # isort:skip
    parse_items_from_line,
    sanitize_creator_names,
    save_config_or_raise,
    username_has_valid_chars,
    username_has_valid_length,
    copy_old_config_values,
    load_config,
)
from .fanslyconfig import FanslyConfig  # isort:skip
from .browser import (  # isort:skip
    close_browser_by_name,
    find_leveldb_folders,
    get_auth_token_from_leveldb_folder,
    get_browser_config_paths,
    get_token_from_firefox_db,
    get_token_from_firefox_profile,
    parse_browser_from_string,
)
from .logging import (
    db_logger,
    get_log_level,
    init_logging_config,
    json_logger,
    set_debug_enabled,
    stash_logger,
    textio_logger,
    trace_logger,
    update_logging_config,
)
from .metadatahandling import MetadataHandling
from .modes import DownloadMode
from .validation import validate_adjust_config

from .args import map_args_to_config  # isort:skip
from .decorators import with_database_session  # isort:skip

__all__ = [
    "close_browser_by_name",
    "copy_old_config_values",
    "find_leveldb_folders",
    "get_auth_token_from_leveldb_folder",
    "get_browser_config_paths",
    # Configuration functions
    "get_log_level",
    "get_token_from_firefox_db",
    "get_token_from_firefox_profile",
    "init_logging_config",
    "load_config",
    "parse_browser_from_string",
    "parse_items_from_line",
    "set_debug_enabled",
    "sanitize_creator_names",
    "save_config_or_raise",
    "update_logging_config",
    "username_has_valid_chars",
    # Pre-configured loggers
    "textio_logger",
    "json_logger",
    "stash_logger",
    "db_logger",
    "trace_logger",
    "username_has_valid_length",
    "validate_adjust_config",
    "DownloadMode",
    "FanslyConfig",
    "MetadataHandling",
    "map_args_to_config",
    "with_database_session",
]
