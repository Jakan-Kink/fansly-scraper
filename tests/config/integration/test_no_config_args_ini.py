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
from pathlib import Path

import pytest

from config.args import map_args_to_config
from config.fanslyconfig import FanslyConfig


# ---------------------------------------------------------------------------
# 1. Removed attributes are gone
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "attribute",
    [
        pytest.param("original_config_path", id="original_config_path"),
        pytest.param("_save_token_to_original_config", id="save_token_helper"),
        pytest.param("_save_checkkey_to_original_config", id="save_checkkey_helper"),
    ],
)
def test_config_args_ini_attributes_removed(attribute: str) -> None:
    """The config_args.ini workaround attributes must not exist on FanslyConfig;
    _save_config() / save_config_or_raise() replaced the per-attribute helpers."""
    cfg = FanslyConfig(program_version="0.13.0")
    assert not hasattr(cfg, attribute), (
        f"{attribute} was removed as part of retiring the config_args.ini "
        "workaround. It must not exist on FanslyConfig."
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
        verbose=1,
        users=["testcreator"],
        download_mode_normal=True,
        download_mode_messages=False,
        download_mode_timeline=False,
        download_mode_collection=False,
        download_mode_single=None,
        download_mode_wall_filters=None,
        download_directory=str(config_dir / "downloads"),
        token=None,
        user_agent=None,
        check_key=None,
        temp_folder=None,
        separate_previews=False,
        use_duplicate_threshold=False,
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
            verbose=0,
            users=None,
            download_mode_normal=False,
            download_mode_messages=False,
            download_mode_timeline=False,
            download_mode_collection=False,
            download_mode_single=None,
            download_mode_wall_filters=None,
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
        map_args_to_config(args, loaded_config)

    assert loaded_config.config_path == baseline_path
    assert not (config_dir / "config_args.ini").exists()
