"""Integration tests for config.ini → config.yaml migration.

All tests use real temporary files via ``tmp_path``.  No mocking of internal
loader functions.  The migration path is exercised end-to-end: write an ini,
call migrate_ini_to_yaml, inspect the produced YAML and backup file.
"""

from __future__ import annotations

import shutil
import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest
from filelock import FileLock
from pydantic import SecretStr

from config.loader import migrate_ini_to_yaml
from config.schema import ConfigSchema
from errors import ConfigError


FIXTURES_DIR = Path(__file__).parent.parent / "unit" / "fixtures"


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _write_ini(path: Path, content: str) -> None:
    """Write a dedented ini file to *path*."""
    path.write_text(textwrap.dedent(content), encoding="utf-8")


# ---------------------------------------------------------------------------
# Test 4: Basic migration — writes YAML, renames .ini to .bak, schema matches
# ---------------------------------------------------------------------------


def test_migration_writes_yaml_and_renames_ini(tmp_path: Path) -> None:
    """Full migration: config.yaml appears, config.ini is renamed, schema is correct."""
    ini_path = tmp_path / "config.ini"
    yaml_path = tmp_path / "config.yaml"

    _write_ini(
        ini_path,
        """
        [TargetedCreator]
        username = alice

        [MyAccount]
        Authorization_Token = tok_xyz
        User_Agent = TestAgent/1.0
        Check_Key = qybZy9-fyszis-bybxyf

        [Options]
        download_directory = /tmp/fansly
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

        [Logic]
        check_key_pattern = this\\.checkKey_\\s*=\\s*["']([^"']+)["']
        main_js_pattern = \\ssrc\\s*=\\s*"(main\\..*?\\.js)"
        """,
    )

    schema = migrate_ini_to_yaml(ini_path, yaml_path, backup_suffix="20260101_120000")

    # YAML file must exist
    assert yaml_path.exists(), "config.yaml was not created"

    # .ini must be gone (renamed to backup)
    assert not ini_path.exists(), "config.ini was not renamed"

    # Backup must exist
    backup_path = tmp_path / "config.ini.bak.20260101_120000"
    assert backup_path.exists(), f"Backup {backup_path} not found"

    # Returned schema is a ConfigSchema
    assert isinstance(schema, ConfigSchema)
    assert "alice" in schema.targeted_creator.usernames
    assert schema.my_account.authorization_token.get_secret_value() == "tok_xyz"
    assert schema.my_account.user_agent == "TestAgent/1.0"
    assert schema.options.download_directory == "/tmp/fansly"  # noqa: S108

    # Reload the YAML and confirm it round-trips cleanly
    schema2 = ConfigSchema.load_yaml(yaml_path)
    assert schema2.targeted_creator.usernames == schema.targeted_creator.usernames
    assert (
        schema2.my_account.authorization_token.get_secret_value()
        == schema.my_account.authorization_token.get_secret_value()
    )


# ---------------------------------------------------------------------------
# Test 5: Comma-separated usernames become a list
# ---------------------------------------------------------------------------


def test_migration_preserves_comma_separated_usernames(tmp_path: Path) -> None:
    """Comma-separated username string from .ini is migrated as a proper list."""
    ini_path = tmp_path / "config.ini"
    yaml_path = tmp_path / "config.yaml"

    _write_ini(
        ini_path,
        """
        [TargetedCreator]
        username = alice, bob, charlie

        [MyAccount]
        Authorization_Token = ReplaceMe
        User_Agent = ReplaceMe
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
        """,
    )

    schema = migrate_ini_to_yaml(ini_path, yaml_path, backup_suffix="ts1")

    assert schema.targeted_creator.usernames == ["alice", "bob", "charlie"]

    # Confirm the list round-trips through YAML correctly
    schema2 = ConfigSchema.load_yaml(yaml_path)
    assert schema2.targeted_creator.usernames == ["alice", "bob", "charlie"]


# ---------------------------------------------------------------------------
# Test 6: Boolean + int coercion
# ---------------------------------------------------------------------------


def test_migration_preserves_bool_and_int_values(tmp_path: Path) -> None:
    """show_downloads=True and timeline_retries=5 survive the migration faithfully."""
    ini_path = tmp_path / "config.ini"
    yaml_path = tmp_path / "config.yaml"

    _write_ini(
        ini_path,
        """
        [TargetedCreator]
        username = testcreator

        [MyAccount]
        Authorization_Token = ReplaceMe
        User_Agent = ReplaceMe
        Check_Key = qybZy9-fyszis-bybxyf

        [Options]
        download_directory = Local_directory
        download_mode = Normal
        metadata_handling = Advanced
        show_downloads = True
        show_skipped_downloads = False
        download_media_previews = True
        open_folder_when_finished = False
        separate_messages = True
        separate_previews = False
        separate_timeline = True
        use_duplicate_threshold = True
        use_folder_suffix = False
        interactive = False
        prompt_on_exit = False
        timeline_retries = 5
        timeline_delay_seconds = 120
        """,
    )

    schema = migrate_ini_to_yaml(ini_path, yaml_path, backup_suffix="ts2")

    # Boolean fields — must be Python bool, not string
    assert schema.options.show_downloads is True
    assert schema.options.show_skipped_downloads is False
    assert schema.options.open_folder_when_finished is False
    assert schema.options.use_duplicate_threshold is True
    assert schema.options.use_folder_suffix is False
    assert schema.options.interactive is False
    assert schema.options.prompt_on_exit is False

    # Integer fields
    assert schema.options.timeline_retries == 5
    assert schema.options.timeline_delay_seconds == 120

    # Confirm round-trip
    schema2 = ConfigSchema.load_yaml(yaml_path)
    assert schema2.options.timeline_retries == 5
    assert schema2.options.show_downloads is True
    assert schema2.options.interactive is False


# ---------------------------------------------------------------------------
# Test 7: Parity failure raises ValueError, leaves .ini in place
# ---------------------------------------------------------------------------


def test_migration_parity_failure_leaves_ini_intact(tmp_path: Path) -> None:
    """If YAML round-trip diverges from the ini schema, ValueError is raised and .ini is kept."""
    ini_path = tmp_path / "config.ini"
    yaml_path = tmp_path / "config.yaml"

    _write_ini(
        ini_path,
        """
        [TargetedCreator]
        username = parity_test

        [MyAccount]
        Authorization_Token = tok_parity
        User_Agent = ReplaceMe
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
        """,
    )

    # Monkey-patch ConfigSchema.load_yaml to return a schema with a tampered field
    original_load_yaml = ConfigSchema.load_yaml

    def tampered_load_yaml(path: Path | str) -> ConfigSchema:
        good = original_load_yaml(path)
        # Tamper with a field to force a divergence
        good.targeted_creator.usernames = ["TAMPERED"]
        return good

    with (
        patch.object(ConfigSchema, "load_yaml", side_effect=tampered_load_yaml),
        pytest.raises(ValueError, match="parity check failed"),
    ):
        migrate_ini_to_yaml(ini_path, yaml_path, backup_suffix="ts3")

    # .ini must NOT have been renamed
    assert ini_path.exists(), (
        "config.ini was incorrectly renamed despite parity failure"
    )

    # The YAML must have been cleaned up (removed on parity failure)
    assert not yaml_path.exists(), "config.yaml was left behind after parity failure"


# ---------------------------------------------------------------------------
# Test 8: SecretStr fields migrate correctly
# ---------------------------------------------------------------------------


def test_migration_preserves_secret_str_fields(tmp_path: Path) -> None:
    """Plaintext token + password in .ini become SecretStr with correct values."""
    ini_path = tmp_path / "config.ini"
    yaml_path = tmp_path / "config.yaml"

    _write_ini(
        ini_path,
        """
        [TargetedCreator]
        username = secrettest

        [MyAccount]
        Authorization_Token = my_secret_token_abc
        User_Agent = AgentX/2
        Check_Key = qybZy9-fyszis-bybxyf
        username = mylogin
        password = MyP@ssw0rd!

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
        """,
    )

    schema = migrate_ini_to_yaml(ini_path, yaml_path, backup_suffix="ts4")

    # authorization_token must be a SecretStr
    assert isinstance(schema.my_account.authorization_token, SecretStr)
    assert (
        schema.my_account.authorization_token.get_secret_value()
        == "my_secret_token_abc"
    )

    # password must be a SecretStr
    assert isinstance(schema.my_account.password, SecretStr)
    assert schema.my_account.password.get_secret_value() == "MyP@ssw0rd!"

    # username must be a plain str (not a secret)
    assert schema.my_account.username == "mylogin"

    # Confirm the secret survives YAML round-trip
    schema2 = ConfigSchema.load_yaml(yaml_path)
    assert (
        schema2.my_account.authorization_token.get_secret_value()
        == "my_secret_token_abc"
    )
    assert schema2.my_account.password.get_secret_value() == "MyP@ssw0rd!"


# ---------------------------------------------------------------------------
# Test 9: Legacy spellings — utilise_duplicate_threshold / use_suffix
# ---------------------------------------------------------------------------


def test_migration_handles_legacy_option_spellings(tmp_path: Path) -> None:
    """utilise_duplicate_threshold and use_suffix are migrated under the new names."""
    ini_path = tmp_path / "config.ini"
    yaml_path = tmp_path / "config.yaml"

    _write_ini(
        ini_path,
        """
        [TargetedCreator]
        username = legacytest

        [MyAccount]
        Authorization_Token = ReplaceMe
        User_Agent = ReplaceMe
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
        utilise_duplicate_threshold = True
        use_suffix = False
        interactive = True
        prompt_on_exit = True
        timeline_retries = 1
        timeline_delay_seconds = 60
        """,
    )

    schema = migrate_ini_to_yaml(ini_path, yaml_path, backup_suffix="ts5")

    # Old key "utilise_duplicate_threshold = True" → new field use_duplicate_threshold
    assert schema.options.use_duplicate_threshold is True
    # Old key "use_suffix = False" → new field use_folder_suffix
    assert schema.options.use_folder_suffix is False


# ---------------------------------------------------------------------------
# Test 10: PostgreSQL keys under [Postgres] section (future layout)
# ---------------------------------------------------------------------------


def test_migration_reads_postgres_from_dedicated_section(tmp_path: Path) -> None:
    """When [Postgres] section exists, pg_* keys are read from it (not [Options])."""
    ini_path = tmp_path / "config.ini"
    yaml_path = tmp_path / "config.yaml"

    _write_ini(
        ini_path,
        """
        [TargetedCreator]
        username = pgtest

        [MyAccount]
        Authorization_Token = ReplaceMe
        User_Agent = ReplaceMe
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

        [Postgres]
        pg_host = pg.internal.example.com
        pg_port = 5434
        pg_database = pg_dedicated_db
        pg_user = pg_user_dedicated
        pg_pool_size = 7
        """,
    )

    schema = migrate_ini_to_yaml(ini_path, yaml_path, backup_suffix="ts6")

    assert schema.postgres.pg_host == "pg.internal.example.com"
    assert schema.postgres.pg_port == 5434
    assert schema.postgres.pg_database == "pg_dedicated_db"
    assert schema.postgres.pg_user == "pg_user_dedicated"
    assert schema.postgres.pg_pool_size == 7


# ---------------------------------------------------------------------------
# Test 11: Representative legacy.ini fixture — Cache section is silently skipped
# ---------------------------------------------------------------------------


def test_migration_from_legacy_ini_fixture(tmp_path: Path) -> None:
    """Migrate the representative legacy.ini fixture end-to-end.

    Covers:
    - All standard sections are migrated correctly.
    - [Cache] section (device_id, device_id_timestamp) is migrated into
      schema.cache and round-trips correctly.
    - [Logic] regex patterns survive the round-trip without escaping corruption.
    - pg_* keys under [Options] are correctly read into the postgres section.
    """
    src = FIXTURES_DIR / "legacy.ini"
    ini_path = tmp_path / "config.ini"
    yaml_path = tmp_path / "config.yaml"

    shutil.copy(src, ini_path)

    schema = migrate_ini_to_yaml(ini_path, yaml_path, backup_suffix="ts7")

    # Usernames: "alice, bob" → list
    assert schema.targeted_creator.usernames == ["alice", "bob"]

    # Account credentials
    assert schema.my_account.authorization_token.get_secret_value() == "tok_abc123"
    assert schema.my_account.user_agent == "Mozilla/5.0 (compatible; FanslyDownloader)"

    # Options
    assert schema.options.show_downloads is True
    assert schema.options.show_skipped_downloads is False
    assert schema.options.timeline_retries == 3
    assert schema.options.timeline_delay_seconds == 90

    # pg_* keys were under [Options] in the legacy.ini
    assert schema.postgres.pg_host == "db.example.com"
    assert schema.postgres.pg_port == 5433
    assert schema.postgres.pg_database == "my_fansly_db"
    assert schema.postgres.pg_password is not None
    assert schema.postgres.pg_password.get_secret_value() == "supersecret"

    # [Cache] is now migrated into schema.cache
    assert schema.cache.device_id == "dev_abc123"
    assert schema.cache.device_id_timestamp == 1710000000

    # Logic patterns round-trip without corruption
    assert "checkKey_" in schema.logic.check_key_pattern
    assert "main" in schema.logic.main_js_pattern

    # YAML was written and .ini was renamed
    assert yaml_path.exists()
    assert not ini_path.exists()
    backup = tmp_path / "config.ini.bak.ts7"
    assert backup.exists()


# ---------------------------------------------------------------------------
# Test 12 (S3): File lock — happy path: single migration acquires and releases
# ---------------------------------------------------------------------------


def test_migration_lock_happy_path(tmp_path: Path) -> None:
    """Migration acquires a .migrating.lock file, completes, and releases it.

    The lock is released so another migration can proceed immediately after.
    filelock cleans up the on-disk lock file on release; the acquire-after-
    release test below verifies the lock state, not the file's persistence.
    """
    ini_path = tmp_path / "config.ini"
    yaml_path = tmp_path / "config.yaml"
    lock_path = tmp_path / "config.ini.migrating.lock"

    _write_ini(
        ini_path,
        """
        [TargetedCreator]
        username = locktest

        [MyAccount]
        Authorization_Token = ReplaceMe
        User_Agent = ReplaceMe
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
        """,
    )

    schema = migrate_ini_to_yaml(ini_path, yaml_path, backup_suffix="lock_ok")

    # Migration succeeded
    assert isinstance(schema, ConfigSchema)
    assert yaml_path.exists()

    # The lock is RELEASED — we can acquire it ourselves immediately.
    # This must not raise (non-blocking exclusive acquire succeeds).
    verify_lock = FileLock(str(lock_path), blocking=False)
    verify_lock.acquire()
    verify_lock.release()


# ---------------------------------------------------------------------------
# Test 13 (S3): File lock — contention: second process raises ConfigError
# ---------------------------------------------------------------------------


def test_migration_lock_contention_raises_config_error(tmp_path: Path) -> None:
    """When the lock is held by another process, ConfigError is raised."""
    ini_path = tmp_path / "config.ini"
    yaml_path = tmp_path / "config.yaml"
    lock_path = tmp_path / "config.ini.migrating.lock"

    _write_ini(
        ini_path,
        """
        [TargetedCreator]
        username = contention_test

        [MyAccount]
        Authorization_Token = ReplaceMe
        User_Agent = ReplaceMe
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
        """,
    )

    # Simulate "another process" by acquiring the lock ourselves before calling migrate
    holder = FileLock(str(lock_path), blocking=False)
    holder.acquire()
    try:
        with pytest.raises(ConfigError, match=r"(?i)another process is migrating"):
            migrate_ini_to_yaml(ini_path, yaml_path, backup_suffix="contention")

        # .ini must be untouched — migration was blocked before any file writes
        assert ini_path.exists()
        assert not yaml_path.exists()
    finally:
        holder.release()


# ---------------------------------------------------------------------------
# Test 14 (S4): Permissive migration — unknown top-level section is dropped
# ---------------------------------------------------------------------------


def test_migration_unknown_section_is_dropped_with_warning(tmp_path: Path) -> None:
    """A legacy [OldSection] in the ini is silently dropped; migration succeeds."""
    ini_path = tmp_path / "config.ini"
    yaml_path = tmp_path / "config.yaml"

    _write_ini(
        ini_path,
        """
        [TargetedCreator]
        username = droptest

        [MyAccount]
        Authorization_Token = ReplaceMe
        User_Agent = ReplaceMe
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

        [OldSection]
        legacy_flag = 1
        another_legacy_key = something
        """,
    )

    warning_calls: list[tuple] = []

    with patch("config.loader.logger") as mock_logger:
        mock_logger.warning.side_effect = lambda msg, *args, **_kw: (
            warning_calls.append((msg, args))
        )
        schema = migrate_ini_to_yaml(ini_path, yaml_path, backup_suffix="drop_section")

    # Migration must succeed
    assert isinstance(schema, ConfigSchema)
    assert yaml_path.exists()

    # At least one warning must mention the unknown section (in the args, since loguru
    # uses {}-style lazy formatting: logger.warning("... [{}] ...", section, path))
    def _any_call_mentions(needle: str) -> bool:
        return any(
            needle in str(msg) or any(needle in str(a) for a in args)
            for msg, args in warning_calls
        )

    assert _any_call_mentions("OldSection"), (
        f"Expected OldSection warning; got: {warning_calls}"
    )

    # The YAML must not contain the unknown section name
    yaml_contents = yaml_path.read_text(encoding="utf-8")
    assert "OldSection" not in yaml_contents
    assert "legacy_flag" not in yaml_contents


# ---------------------------------------------------------------------------
# Test 15 (S4): Permissive migration — unknown key in known section is dropped
# ---------------------------------------------------------------------------


def test_migration_unknown_key_in_known_section_is_dropped_with_warning(
    tmp_path: Path,
) -> None:
    """An unknown key inside a known section ([MyAccount]) is dropped with a warning."""
    ini_path = tmp_path / "config.ini"
    yaml_path = tmp_path / "config.yaml"

    _write_ini(
        ini_path,
        """
        [TargetedCreator]
        username = keytest

        [MyAccount]
        Authorization_Token = tok_keytest
        User_Agent = ReplaceMe
        Check_Key = qybZy9-fyszis-bybxyf
        removed_field = old_value

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
        """,
    )

    warning_calls: list[tuple] = []

    with patch("config.loader.logger") as mock_logger:
        mock_logger.warning.side_effect = lambda msg, *args, **_kw: (
            warning_calls.append((msg, args))
        )
        schema = migrate_ini_to_yaml(ini_path, yaml_path, backup_suffix="drop_key")

    # Migration must succeed and the known field still migrated correctly
    assert isinstance(schema, ConfigSchema)
    assert schema.my_account.authorization_token.get_secret_value() == "tok_keytest"
    assert yaml_path.exists()

    # At least one warning must mention the removed_field key (loguru {}-style args)
    def _any_call_mentions(needle: str) -> bool:
        return any(
            needle in str(msg) or any(needle in str(a) for a in args)
            for msg, args in warning_calls
        )

    assert _any_call_mentions("removed_field"), (
        f"Expected removed_field warning; got: {warning_calls}"
    )

    # The YAML must not contain the dropped key
    yaml_contents = yaml_path.read_text(encoding="utf-8")
    assert "removed_field" not in yaml_contents


# ---------------------------------------------------------------------------
# Test 16 (S4): Parity check still fires for real mismatches (regression guard)
# ---------------------------------------------------------------------------


def test_parity_check_still_fires_for_real_mismatches(tmp_path: Path) -> None:
    """Unknown keys do NOT suppress the parity check; real mismatches still raise."""
    ini_path = tmp_path / "config.ini"
    yaml_path = tmp_path / "config.yaml"

    _write_ini(
        ini_path,
        """
        [TargetedCreator]
        username = parityguard

        [MyAccount]
        Authorization_Token = tok_guard
        User_Agent = ReplaceMe
        Check_Key = qybZy9-fyszis-bybxyf
        removed_field = will_be_dropped

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
        """,
    )

    original_load_yaml = ConfigSchema.load_yaml

    def tampered_load_yaml(path: Path | str) -> ConfigSchema:
        good = original_load_yaml(path)
        good.targeted_creator.usernames = ["TAMPERED"]
        return good

    with (
        patch.object(ConfigSchema, "load_yaml", side_effect=tampered_load_yaml),
        pytest.raises(ValueError, match="parity check failed"),
    ):
        migrate_ini_to_yaml(ini_path, yaml_path, backup_suffix="parity_guard")

    # .ini must NOT have been renamed
    assert ini_path.exists()
    # YAML must have been removed
    assert not yaml_path.exists()
