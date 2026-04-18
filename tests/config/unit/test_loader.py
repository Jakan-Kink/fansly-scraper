"""Unit tests for config/loader.py — load_or_migrate resolution logic.

Tests here use tmp_path for file I/O and do not touch the real config files.
No network, no database, no mocking of internal functions.
"""

from __future__ import annotations

import shutil
import textwrap
from pathlib import Path

import pytest

from config.loader import load_or_migrate, migrate_ini_to_yaml
from config.schema import ConfigSchema


FIXTURES_DIR = Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------------------
# Test 1: config.yaml exists → load it; no migration
# ---------------------------------------------------------------------------


def test_load_yaml_when_yaml_exists(tmp_path: Path) -> None:
    """When config.yaml is present, return the loaded schema without migrating."""
    # Arrange: copy sample.yaml into tmp_path as config.yaml
    src = FIXTURES_DIR / "sample.yaml"
    yaml_path = tmp_path / "config.yaml"
    shutil.copy(src, yaml_path)

    # Also plant a config.ini — it must be ignored
    ini_path = tmp_path / "config.ini"
    ini_path.write_text(
        "[TargetedCreator]\nusername = shouldnotappear\n", encoding="utf-8"
    )

    schema = load_or_migrate(tmp_path)

    # Loaded from YAML, not the ini
    assert isinstance(schema, ConfigSchema)
    assert "shouldnotappear" not in schema.targeted_creator.usernames
    # sample.yaml has "replaceme" as the username
    assert "replaceme" in schema.targeted_creator.usernames
    # config.ini still exists (no migration happened)
    assert ini_path.exists()


# ---------------------------------------------------------------------------
# Test 2: Neither file exists → return all defaults
# ---------------------------------------------------------------------------


def test_defaults_when_no_files_exist(tmp_path: Path) -> None:
    """When neither config.yaml nor config.ini exists, return a default schema."""
    schema = load_or_migrate(tmp_path)

    assert isinstance(schema, ConfigSchema)
    # Spot-check a few defaults
    assert schema.options.show_downloads is True
    assert schema.options.timeline_retries == 1
    assert schema.postgres.pg_host == "localhost"
    assert schema.postgres.pg_port == 5432
    assert schema.my_account.check_key == "qybZy9-fyszis-bybxyf"


# ---------------------------------------------------------------------------
# Test 3: Both files exist → prefer config.yaml (no migration)
# ---------------------------------------------------------------------------


def test_yaml_preferred_over_ini_when_both_present(tmp_path: Path) -> None:
    """When both config.yaml and config.ini are present, YAML wins and .ini is untouched."""
    src = FIXTURES_DIR / "sample.yaml"
    yaml_path = tmp_path / "config.yaml"
    shutil.copy(src, yaml_path)

    ini_path = tmp_path / "config.ini"
    ini_path.write_text(
        "[TargetedCreator]\nusername = ini_only_user\n", encoding="utf-8"
    )

    schema = load_or_migrate(tmp_path)

    # Must have loaded from YAML
    assert "ini_only_user" not in schema.targeted_creator.usernames
    assert "replaceme" in schema.targeted_creator.usernames

    # .ini must be untouched (not renamed/deleted)
    assert ini_path.exists()
    # No backup should have been created
    bak_files = list(tmp_path.glob("config.ini.bak.*"))
    assert bak_files == [], f"Unexpected backup file(s): {bak_files}"


# ---------------------------------------------------------------------------
# Test 4: Only config.ini exists → triggers migration via load_or_migrate
# ---------------------------------------------------------------------------


def test_load_or_migrate_triggers_migration_when_only_ini_exists(
    tmp_path: Path,
) -> None:
    """When only config.ini is present, load_or_migrate produces config.yaml."""
    ini_path = tmp_path / "config.ini"
    yaml_path = tmp_path / "config.yaml"

    ini_path.write_text(
        textwrap.dedent(
            """
            [TargetedCreator]
            username = onlyini_user

            [MyAccount]
            Authorization_Token = tok_onlyini
            User_Agent = OnlyIniAgent/1
            Check_Key = qybZy9-fyszis-bybxyf

            [Options]
            download_directory = Local_directory
            download_mode = Normal
            metadata_handling = Advanced
            show_downloads = True
            show_skipped_downloads = True
            download_media_previews = True
            open_folder_when_finished = True
            separate_messages = True
            separate_previews = False
            separate_timeline = True
            use_duplicate_threshold = False
            use_folder_suffix = True
            interactive = True
            prompt_on_exit = True
            timeline_retries = 1
            timeline_delay_seconds = 60
            """
        ),
        encoding="utf-8",
    )

    schema = load_or_migrate(tmp_path)

    # Migration must have produced config.yaml
    assert yaml_path.exists(), "config.yaml was not created by load_or_migrate"

    # .ini must have been renamed to a backup
    assert not ini_path.exists(), "config.ini was not renamed"
    bak_files = list(tmp_path.glob("config.ini.bak.*"))
    assert len(bak_files) == 1, f"Expected one backup, found: {bak_files}"

    assert isinstance(schema, ConfigSchema)
    assert "onlyini_user" in schema.targeted_creator.usernames
    assert schema.my_account.authorization_token.get_secret_value() == "tok_onlyini"


# ---------------------------------------------------------------------------
# Test 5: Backup collision raises FileExistsError
# ---------------------------------------------------------------------------


def test_migrate_raises_file_exists_error_on_backup_collision(
    tmp_path: Path,
) -> None:
    """If the backup file already exists, migrate_ini_to_yaml raises FileExistsError."""
    ini_path = tmp_path / "config.ini"
    yaml_path = tmp_path / "config.yaml"
    backup_path = tmp_path / "config.ini.bak.collision"

    ini_path.write_text("[TargetedCreator]\nusername = coltest\n", encoding="utf-8")
    backup_path.write_text("existing backup content", encoding="utf-8")

    with pytest.raises(FileExistsError, match="already exists"):
        migrate_ini_to_yaml(ini_path, yaml_path, backup_suffix="collision")

    # .ini must remain, .yaml must not have been created
    assert ini_path.exists()
    assert not yaml_path.exists()
