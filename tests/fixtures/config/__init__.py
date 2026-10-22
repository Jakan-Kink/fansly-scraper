"""Config fixtures for unit tests.

Names are deliberately distinct from the global ``config`` fixture in
``tests/fixtures/database/`` (DB-backed). Tests opt in by taking
``unit_config`` / ``unit_config_path`` as parameters; the database
``config`` continues to serve every other consumer unchanged.
"""

from .browser_fixtures import (
    LEVELDB_SESSION_KEY,
    FakeBrowserProcess,
    SqliteConnectSpy,
    browser_processes,
    firefox_session_row,
    hold_exclusive_lock,
    leveldb_session_value,
    linux_browser_home,
    opened_urls,
    sqlite_connect_spy,
    write_firefox_store,
    write_leveldb_store,
    write_non_utf8_store,
)
from .config_fixtures import (
    CONFIG_DATA_DIR,
    config_dir,
    config_with_path,
    default_cli_args,
    fresh_config,
    loaded_config,
    no_display,
    sample_yaml_path,
    unit_config,
    unit_config_path,
    validation_config,
)


__all__ = [
    "CONFIG_DATA_DIR",
    "LEVELDB_SESSION_KEY",
    "FakeBrowserProcess",
    "SqliteConnectSpy",
    "browser_processes",
    "config_dir",
    "config_with_path",
    "default_cli_args",
    "firefox_session_row",
    "fresh_config",
    "hold_exclusive_lock",
    "leveldb_session_value",
    "linux_browser_home",
    "loaded_config",
    "no_display",
    "opened_urls",
    "sample_yaml_path",
    "sqlite_connect_spy",
    "unit_config",
    "unit_config_path",
    "validation_config",
    "write_firefox_store",
    "write_leveldb_store",
    "write_non_utf8_store",
]
