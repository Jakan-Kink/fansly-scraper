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
from tests.fixtures.config import CONFIG_DATA_DIR


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _write_ini(path: Path, content: str) -> None:
    """Write a dedented ini file to *path*."""
    path.write_text(textwrap.dedent(content), encoding="utf-8")


# Shared ini skeleton fragments for the happy-path migration table below.
_STD_ACCOUNT = """\
[TargetedCreator]
username = {username}

[MyAccount]
Authorization_Token = {token}
User_Agent = {user_agent}
Check_Key = qybZy9-fyszis-bybxyf
"""

_STD_OPTIONS = """
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

_BOOL_INT_OPTIONS = """
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
"""

_LOGIC_SECTION = """
[Logic]
check_key_pattern = this\\.checkKey_\\s*=\\s*["']([^"']+)["']
main_js_pattern = \\ssrc\\s*=\\s*"(main\\..*?\\.js)"
"""

_POSTGRES_SECTION = """
[Postgres]
pg_host = pg.internal.example.com
pg_port = 5434
pg_database = pg_dedicated_db
pg_user = pg_user_dedicated
pg_pool_size = 7
"""


# ---------------------------------------------------------------------------
# Tests 4-6, 8-10: Happy-path migrations (shared [Options] skeleton)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("ini_content", "expected", "roundtrip_paths"),
    [
        pytest.param(
            _STD_ACCOUNT.format(
                username="alice", token="tok_xyz", user_agent="TestAgent/1.0"
            )
            + _STD_OPTIONS.replace(
                "download_directory = Local_directory",
                "download_directory = /tmp/fansly",
            )
            + _LOGIC_SECTION,
            {
                "targeted_creator.usernames": ["alice"],
                "my_account.authorization_token": SecretStr("tok_xyz"),
                "my_account.user_agent": "TestAgent/1.0",
                "options.download_directory": "/tmp/fansly",  # noqa: S108
            },
            ("targeted_creator.usernames", "my_account.authorization_token"),
            id="basic_full_migration",
        ),
        pytest.param(
            _STD_ACCOUNT.format(
                username="alice, bobby, charlie",
                token="ReplaceMe",
                user_agent="ReplaceMe",
            )
            + _STD_OPTIONS,
            {"targeted_creator.usernames": ["alice", "bobby", "charlie"]},
            ("targeted_creator.usernames",),
            id="comma_separated_usernames",
        ),
        pytest.param(
            _STD_ACCOUNT.format(
                username="testcreator", token="ReplaceMe", user_agent="ReplaceMe"
            )
            + _BOOL_INT_OPTIONS,
            {
                "options.show_downloads": True,
                "options.show_skipped_downloads": False,
                "options.open_folder_when_finished": False,
                "options.use_duplicate_threshold": True,
                "options.use_folder_suffix": False,
                "options.interactive": False,
                "options.prompt_on_exit": False,
                "options.timeline_retries": 5,
                "options.timeline_delay_seconds": 120,
            },
            (
                "options.timeline_retries",
                "options.show_downloads",
                "options.interactive",
            ),
            id="bool_and_int_coercion",
        ),
        pytest.param(
            _STD_ACCOUNT.format(
                username="secrettest",
                token="my_secret_token_abc",
                user_agent="AgentX/2",
            )
            + "username = mylogin\npassword = MyP@ssw0rd!\n"
            + _STD_OPTIONS,
            {
                "my_account.authorization_token": SecretStr("my_secret_token_abc"),
                "my_account.password": SecretStr("MyP@ssw0rd!"),
                "my_account.username": "mylogin",
            },
            ("my_account.authorization_token", "my_account.password"),
            id="secret_str_fields",
        ),
        pytest.param(
            _STD_ACCOUNT.format(
                username="legacytest", token="ReplaceMe", user_agent="ReplaceMe"
            )
            + _STD_OPTIONS.replace(
                "use_duplicate_threshold = False",
                "utilise_duplicate_threshold = True",
            ).replace("use_folder_suffix = True", "use_suffix = False"),
            {
                "options.use_duplicate_threshold": True,
                "options.use_folder_suffix": False,
            },
            (),
            id="legacy_option_spellings",
        ),
        pytest.param(
            _STD_ACCOUNT.format(
                username="pgtest", token="ReplaceMe", user_agent="ReplaceMe"
            )
            + _STD_OPTIONS
            + _POSTGRES_SECTION,
            {
                "postgres.pg_host": "pg.internal.example.com",
                "postgres.pg_port": 5434,
                "postgres.pg_database": "pg_dedicated_db",
                "postgres.pg_user": "pg_user_dedicated",
                "postgres.pg_pool_size": 7,
            },
            (),
            id="postgres_dedicated_section",
        ),
    ],
)
def test_migration_happy_paths(
    tmp_path: Path,
    ini_content: str,
    expected: dict[str, object],
    roundtrip_paths: tuple[str, ...],
) -> None:
    """Happy-path migrations: YAML written, .ini renamed to a backup, and every
    expected field slice lands on the returned schema (SecretStr-aware, bool-strict).
    Fields listed in *roundtrip_paths* are re-verified on a fresh YAML reload."""
    ini_path = tmp_path / "config.ini"
    yaml_path = tmp_path / "config.yaml"
    _write_ini(ini_path, ini_content)

    schema = migrate_ini_to_yaml(ini_path, yaml_path, backup_suffix="happy")

    # Shared side-effect contract for every successful migration.
    assert isinstance(schema, ConfigSchema)
    assert yaml_path.exists(), "config.yaml was not created"
    assert not ini_path.exists(), "config.ini was not renamed"
    backup_path = tmp_path / "config.ini.bak.happy"
    assert backup_path.exists(), f"Backup {backup_path} not found"

    def resolve(root: ConfigSchema, dotted: str) -> object:
        value: object = root
        for part in dotted.split("."):
            value = getattr(value, part)
        return value

    def check(root: ConfigSchema, dotted: str, want: object) -> None:
        got = resolve(root, dotted)
        if isinstance(want, SecretStr):
            # SecretStr fields must be SecretStr instances, not plain strings
            assert isinstance(got, SecretStr), dotted
            assert got.get_secret_value() == want.get_secret_value(), dotted
        elif isinstance(want, bool):
            # Boolean fields must be Python bool, not string / int
            assert got is want, dotted
        else:
            assert got == want, dotted

    for dotted, want in expected.items():
        check(schema, dotted, want)

    # Confirm the flagged fields round-trip through YAML correctly
    schema2 = ConfigSchema.load_yaml(yaml_path)
    for dotted in roundtrip_paths:
        check(schema2, dotted, expected[dotted])


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
    src = CONFIG_DATA_DIR / "legacy.ini"
    ini_path = tmp_path / "config.ini"
    yaml_path = tmp_path / "config.yaml"

    shutil.copy(src, ini_path)

    schema = migrate_ini_to_yaml(ini_path, yaml_path, backup_suffix="ts7")

    # Usernames: "alice, bobby" → list
    assert schema.targeted_creator.usernames == ["alice", "bobby"]

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
    assert schema.cache is not None
    assert schema.cache.device_id == "dev_abc123"
    assert schema.cache.device_id_timestamp == 1710000000

    # Logic patterns round-trip without corruption
    assert schema.logic is not None
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
# Tests 14+15 (S4): Permissive migration — unknown section / unknown key dropped
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("ini_content", "warning_needle", "absent_from_yaml", "expected_token"),
    [
        pytest.param(
            _STD_ACCOUNT.format(
                username="droptest", token="ReplaceMe", user_agent="ReplaceMe"
            )
            + _STD_OPTIONS
            + "\n[OldSection]\nlegacy_flag = 1\nanother_legacy_key = something\n",
            "OldSection",
            ("OldSection", "legacy_flag"),
            None,
            id="unknown_top_level_section",
        ),
        pytest.param(
            _STD_ACCOUNT.format(
                username="keytest", token="tok_keytest", user_agent="ReplaceMe"
            )
            + "removed_field = old_value\n"
            + _STD_OPTIONS,
            "removed_field",
            ("removed_field",),
            "tok_keytest",
            id="unknown_key_in_known_section",
        ),
    ],
)
def test_migration_unknown_entries_dropped_with_warning(
    tmp_path: Path,
    ini_content: str,
    warning_needle: str,
    absent_from_yaml: tuple[str, ...],
    expected_token: str | None,
) -> None:
    """Unknown top-level sections and unknown keys in known sections are dropped
    with a warning; migration succeeds and the dropped names never reach the YAML."""
    ini_path = tmp_path / "config.ini"
    yaml_path = tmp_path / "config.yaml"
    _write_ini(ini_path, ini_content)

    warning_calls: list[tuple] = []

    with patch("config.loader.logger") as mock_logger:
        mock_logger.warning.side_effect = lambda msg, *args, **_kw: (
            warning_calls.append((msg, args))
        )
        schema = migrate_ini_to_yaml(ini_path, yaml_path, backup_suffix="drop")

    # Migration must succeed
    assert isinstance(schema, ConfigSchema)
    assert yaml_path.exists()

    # Known fields still migrated correctly (unknown-key row only)
    if expected_token is not None:
        assert schema.my_account.authorization_token.get_secret_value() == (
            expected_token
        )

    # At least one warning must mention the dropped name (in the args, since loguru
    # uses {}-style lazy formatting: logger.warning("... [{}] ...", section, path))
    def _any_call_mentions(needle: str) -> bool:
        return any(
            needle in str(msg) or any(needle in str(a) for a in args)
            for msg, args in warning_calls
        )

    assert _any_call_mentions(warning_needle), (
        f"Expected {warning_needle} warning; got: {warning_calls}"
    )

    # The YAML must not contain the dropped section/key names
    yaml_contents = yaml_path.read_text(encoding="utf-8")
    for needle in absent_from_yaml:
        assert needle not in yaml_contents


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
