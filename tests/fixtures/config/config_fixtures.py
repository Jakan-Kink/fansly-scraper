"""Pytest fixtures for FanslyConfig-based unit tests.

These fixtures live here (not inlined in test files) so multiple test
modules can reuse them and an audit can spot drift between callers.

Names are deliberately distinct from the global ``config`` fixture in
``tests/fixtures/database/`` so opt-in is explicit and there is no
silent shadowing.

Fixtures:
- ``unit_config_path`` — temp YAML path for save_config_or_raise round-trips
- ``unit_config`` — minimal real ``FanslyConfig`` with a long-enough token+UA
- ``no_display`` — monkeypatches RateLimiterDisplay.start to a no-op
- ``validation_config`` — real FanslyConfig wired for ``config/validation.py`` tests
- ``config_dir`` — isolated temp cwd for config-file integration tests
- ``fresh_config`` — bare ``FanslyConfig`` with no state
- ``loaded_config`` — ``FanslyConfig`` loaded from a minimal config.yaml in ``config_dir``
- ``sample_yaml_path`` — mutable tmp_path copy of ``data/sample.yaml``
- ``config_with_path`` — mock_config with config_path set for map_args_to_config
- ``default_cli_args`` — full argparse.Namespace with all flags at defaults

``CONFIG_DATA_DIR`` anchors the shared config data files (``sample.yaml``,
``legacy.ini``) consumed by the config loader/schema/migration tests.
"""

import argparse
import os
import shutil
from pathlib import Path

import pytest

from config.config import load_config
from config.fanslyconfig import FanslyConfig
from config.logging import init_logging_config
from config.modes import DownloadMode
from config.schema import ConfigSchema


@pytest.fixture
def unit_config_path(tmp_path):
    """Create a temporary config file path (yaml format)."""
    return tmp_path / "config.yaml"


@pytest.fixture
def unit_config(unit_config_path):
    """Create a FanslyConfig instance for unit testing (no database)."""
    cfg = FanslyConfig(program_version="1.0.0")
    cfg.config_path = unit_config_path
    # Token must be >= 50 chars to pass token_is_valid() check
    cfg.token = "test_token_long_enough_to_pass_validation_checks_here"
    cfg.user_agent = "test_user_agent_long_enough_for_validation"
    cfg.check_key = "test_check_key"
    cfg.user_names = {"user1", "user2"}
    return cfg


@pytest.fixture
def no_display(monkeypatch):
    """Suppress the RateLimiterDisplay background thread.

    ``FanslyConfig.get_api`` constructs a ``RateLimiterDisplay`` and
    ``setup_api`` calls its ``.start()`` which spawns a daemon thread
    running a Rich live progress display. In tests this would (a) hold
    a thread open that pytest can't reliably join, (b) write to
    stderr/stdout in ways that break pytest capture, (c) potentially
    leak between tests. Patching ``.start()`` to a no-op disables the
    thread spawn while leaving the rest of the real wiring intact.
    """
    monkeypatch.setattr(
        "api.rate_limiter_display.RateLimiterDisplay.start",
        lambda _self: None,
    )


@pytest.fixture
def validation_config(tmp_path):
    """Real ``FanslyConfig`` configured for ``config/validation.py`` tests.

    Every field starts at a known real value so the production
    ``token_is_valid()`` / ``useragent_is_valid()`` methods return True
    by default — tests that want the "invalid" branch of those checks
    override the field explicitly (e.g., ``config.token = "short"``).

    The ``config_path`` points at ``tmp_path / "config.yaml"`` so any
    ``save_config_or_raise`` path runs real YAML I/O into a throwaway
    directory — no mocks. Because asserted-on values like ``token`` and
    ``user_agent`` round-trip through YAML and get re-loaded in some
    validators, the size of the strings matches Fansly's real shape
    (60-char token, Mozilla/5.0 UA).
    """
    config = FanslyConfig(program_version="0.13.0-test")
    config.config_path = tmp_path / "config.yaml"
    config.interactive = False
    config.user_names = {"validuser1", "validuser2"}
    # token_is_valid() requires len >= 50 and no "ReplaceMe".
    config.token = "a" * 60
    # useragent_is_valid() requires len >= 40 and no "ReplaceMe".
    config.user_agent = "Mozilla/5.0 " + "A" * 60
    config.check_key = "check-key-placeholder-123"
    config.download_directory = Path.cwd()
    config.download_mode = DownloadMode.TIMELINE
    config.username = None
    config.password = None
    return config


@pytest.fixture
def config_dir(tmp_path):
    """Isolated temp directory used as the working directory for config files."""
    logs = tmp_path / "logs"
    logs.mkdir()
    original_cwd = Path.cwd()
    os.chdir(tmp_path)
    yield tmp_path
    os.chdir(original_cwd)


@pytest.fixture
def fresh_config() -> FanslyConfig:
    """A fresh FanslyConfig with no state."""
    return FanslyConfig(program_version="0.13.0")


@pytest.fixture
def loaded_config(config_dir: Path) -> FanslyConfig:
    """A FanslyConfig loaded from a minimal config.yaml in config_dir."""
    yaml_path = config_dir / "config.yaml"
    ConfigSchema().dump_yaml(yaml_path)
    cfg = FanslyConfig(program_version="0.13.0")
    load_config(cfg)
    init_logging_config(cfg)
    return cfg


CONFIG_DATA_DIR = Path(__file__).parent / "data"


@pytest.fixture
def sample_yaml_path(tmp_path: Path) -> Path:
    """Copy sample.yaml into an isolated tmp_path so tests can mutate freely."""
    src = CONFIG_DATA_DIR / "sample.yaml"
    dst = tmp_path / "config.yaml"
    shutil.copy(src, dst)
    return dst


@pytest.fixture
def config_with_path(mock_config, tmp_path):
    """A mock_config with config_path set (required by map_args_to_config)."""
    config_path = tmp_path / "config.ini"
    mock_config.config_path = config_path
    init_logging_config(mock_config)
    return mock_config


@pytest.fixture
def default_cli_args():
    """An argparse.Namespace with every parse_args() attribute at its non-firing default.

    Mirrors the full attribute surface map_args_to_config expects, including
    PostgreSQL settings and the monitoring/daemon flags. Tests mutate the
    attributes under test.
    """
    return argparse.Namespace(
        verbose=0,
        users=None,
        download_mode_normal=False,
        download_mode_messages=False,
        download_mode_timeline=False,
        download_mode_collection=False,
        download_mode_single=None,
        download_mode_wall_filters=None,
        file_size_min=None,
        file_size_max=None,
        duration_min=None,
        duration_max=None,
        max_resolution=None,
        download_directory=None,
        token=None,
        user_agent=None,
        check_key=None,
        temp_folder=None,
        separate_previews=False,
        use_duplicate_threshold=False,
        non_interactive=False,
        no_prompt_on_exit=False,
        no_folder_suffix=False,
        no_media_previews=False,
        hide_downloads=False,
        hide_skipped_downloads=False,
        no_open_folder=False,
        no_separate_messages=False,
        no_separate_timeline=False,
        timeline_retries=None,
        timeline_delay_seconds=None,
        api_max_retries=None,
        use_following=None,
        use_following_with_pagination=False,
        use_pagination_duplication=False,
        reverse_order=False,
        pg_host=None,
        pg_port=None,
        pg_database=None,
        pg_user=None,
        pg_password=None,
        monitor_since=None,
        full_pass=False,
        daemon_mode=False,
    )
