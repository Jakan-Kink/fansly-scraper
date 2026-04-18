"""Integration tests: config_args.ini workaround has been retired.

Verifies that:
- FanslyConfig no longer has an original_config_path attribute
- FanslyConfig no longer has _save_token_to_original_config
- FanslyConfig no longer has _save_checkkey_to_original_config
- Running map_args_to_config with CLI overrides does NOT create config_args.ini
- The real config_path is never swapped out during argument mapping
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

import pytest

from config.args import map_args_to_config
from config.config import load_config
from config.fanslyconfig import FanslyConfig
from config.logging import init_logging_config
from config.schema import ConfigSchema


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def config_dir(tmp_path: Path):
    """Isolated temp directory as the working directory for config files."""
    logs = tmp_path / "logs"
    logs.mkdir()
    original_cwd = Path.cwd()
    os.chdir(tmp_path)
    yield tmp_path
    os.chdir(original_cwd)


@pytest.fixture
def loaded_config(config_dir: Path) -> FanslyConfig:
    """A FanslyConfig loaded from a minimal config.yaml in config_dir."""
    yaml_path = config_dir / "config.yaml"
    ConfigSchema().dump_yaml(yaml_path)
    cfg = FanslyConfig(program_version="0.11.0")
    load_config(cfg)
    init_logging_config(cfg)
    return cfg


# ---------------------------------------------------------------------------
# 1. Removed attributes are gone
# ---------------------------------------------------------------------------


def test_original_config_path_attribute_removed() -> None:
    """FanslyConfig must not have an original_config_path attribute."""
    cfg = FanslyConfig(program_version="0.11.0")
    assert not hasattr(cfg, "original_config_path"), (
        "original_config_path was removed as part of retiring the "
        "config_args.ini workaround. It must not exist on FanslyConfig."
    )


def test_save_token_to_original_config_removed() -> None:
    """_save_token_to_original_config helper must not exist on FanslyConfig."""
    cfg = FanslyConfig(program_version="0.11.0")
    assert not hasattr(cfg, "_save_token_to_original_config"), (
        "_save_token_to_original_config was removed; use _save_config() instead."
    )


def test_save_checkkey_to_original_config_removed() -> None:
    """_save_checkkey_to_original_config helper must not exist on FanslyConfig."""
    cfg = FanslyConfig(program_version="0.11.0")
    assert not hasattr(cfg, "_save_checkkey_to_original_config"), (
        "_save_checkkey_to_original_config was removed; use save_config_or_raise() instead."
    )


# ---------------------------------------------------------------------------
# 2. config_args.ini is never created during CLI-args mapping
# ---------------------------------------------------------------------------


def test_no_config_args_ini_created_with_cli_overrides(
    config_dir: Path, loaded_config: FanslyConfig
) -> None:
    """map_args_to_config with CLI overrides must not create config_args.ini."""
    # Build an args namespace that overrides several settings
    args = argparse.Namespace(
        debug=True,
        users=["testcreator"],
        download_mode_normal=True,
        download_mode_messages=False,
        download_mode_timeline=False,
        download_mode_collection=False,
        download_mode_single=None,
        metadata_handling=None,
        download_directory=str(config_dir / "downloads"),
        token=None,
        user_agent=None,
        check_key=None,
        updated_to=None,
        db_sync_commits=None,
        db_sync_seconds=None,
        db_sync_min_size=None,
        temp_folder=None,
        separate_previews=False,
        use_duplicate_threshold=False,
        separate_metadata=False,
        non_interactive=True,
        no_prompt_on_exit=True,
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

    original_config_path = loaded_config.config_path

    map_args_to_config(args, loaded_config)

    # config_args.ini must not appear anywhere in config_dir
    config_args_ini = config_dir / "config_args.ini"
    assert not config_args_ini.exists(), (
        f"config_args.ini was created at {config_args_ini}; "
        "the config_args.ini workaround was retired and must not be recreated."
    )

    # config_path must remain pointing to the real config file
    assert loaded_config.config_path == original_config_path, (
        f"config_path was swapped from {original_config_path} to "
        f"{loaded_config.config_path}; the path-swap workaround must not exist."
    )


# ---------------------------------------------------------------------------
# 3. config_path is stable across multiple map_args_to_config calls
# ---------------------------------------------------------------------------


def test_config_path_stable_across_multiple_calls(
    config_dir: Path, loaded_config: FanslyConfig
) -> None:
    """config_path must be the same before and after repeated arg mapping."""
    baseline_path = loaded_config.config_path

    for _ in range(3):
        args = argparse.Namespace(
            debug=False,
            users=None,
            download_mode_normal=False,
            download_mode_messages=False,
            download_mode_timeline=False,
            download_mode_collection=False,
            download_mode_single=None,
            metadata_handling=None,
            download_directory=None,
            token=None,
            user_agent=None,
            check_key=None,
            updated_to=None,
            db_sync_commits=None,
            db_sync_seconds=None,
            db_sync_min_size=None,
            temp_folder=None,
            separate_previews=False,
            use_duplicate_threshold=False,
            separate_metadata=False,
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
        map_args_to_config(args, loaded_config)

    assert loaded_config.config_path == baseline_path
    assert not (config_dir / "config_args.ini").exists()
