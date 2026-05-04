"""Configuration File Manipulation"""

from typing import Any


# isort: off
from .config import (
    parse_items_from_line,
    sanitize_creator_names,
    save_config_or_raise,
    username_has_valid_chars,
    username_has_valid_length,
    copy_old_config_values,
    load_config,
)
from .fanslyconfig import FanslyConfig

# isort: on
# Browser imports are lazy-loaded via __getattr__ to avoid requiring plyvel
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
from .modes import DownloadMode
from .validation import validate_adjust_config


from .args import map_args_to_config  # isort:skip


# Lazy-loaded browser functions (require optional plyvel dependency)
_BROWSER_FUNCTIONS = {
    "close_browser_by_name",
    "find_leveldb_folders",
    "get_auth_token_from_leveldb_folder",
    "get_browser_config_paths",
    "get_token_from_firefox_db",
    "get_token_from_firefox_profile",
    "parse_browser_from_string",
}

__all__ = [
    "DownloadMode",
    "FanslyConfig",
    "copy_old_config_values",
    "db_logger",
    "get_log_level",
    "init_logging_config",
    "json_logger",
    "load_config",
    "map_args_to_config",
    "parse_items_from_line",
    "sanitize_creator_names",
    "save_config_or_raise",
    "set_debug_enabled",
    "stash_logger",
    "textio_logger",
    "trace_logger",
    "update_logging_config",
    "username_has_valid_chars",
    "username_has_valid_length",
    "validate_adjust_config",
]


def __getattr__(name: str) -> Any:
    """Lazy-load browser functions to avoid requiring optional plyvel dependency."""
    if name in _BROWSER_FUNCTIONS:
        from . import browser  # noqa: PLC0415, I001  # lazy-load: browser pulls optional plyvel dep

        return getattr(browser, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
