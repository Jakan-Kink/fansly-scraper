"""Integration tests: FanslyConfig ↔ ConfigSchema bridge.

Verifies the round-trip contract between the typed disk format
(ConfigSchema / config.yaml) and the runtime facade (FanslyConfig).

All tests use real temp files via ``tmp_path``.  No mocking of schema or
config internals — these are end-to-end data-flow tests.
"""

from __future__ import annotations

import argparse
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import SecretStr

from config.args import (
    _handle_boolean_settings,
    _handle_debug_settings,
    _handle_download_mode,
    _handle_monitoring_settings,
    _handle_user_settings,
)
from config.config import load_config
from config.fanslyconfig import FanslyConfig
from config.modes import DownloadMode
from config.schema import ConfigSchema, StashContextSection


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def config_dir(tmp_path: Path) -> Path:
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


# ---------------------------------------------------------------------------
# 1. Round-trip load: yaml → FanslyConfig attributes
# ---------------------------------------------------------------------------


def test_round_trip_load_from_yaml(
    config_dir: Path, fresh_config: FanslyConfig
) -> None:
    """Every schema value survives into the matching FanslyConfig attribute."""
    yaml_path = config_dir / "config.yaml"

    schema = ConfigSchema()
    schema.my_account.user_agent = "Mozilla/5.0 test agent string for validation here"
    schema.options.download_mode = DownloadMode.TIMELINE
    schema.options.timeline_retries = 5
    schema.options.separate_previews = True
    schema.postgres.pg_host = "pg.test.example.com"
    schema.postgres.pg_port = 5433
    schema.cache.device_id = "abc-device-id"
    schema.cache.device_id_timestamp = 999_000_000
    schema.logging.db.level = "DEBUG"
    schema.dump_yaml(yaml_path)

    load_config(fresh_config)

    assert (
        fresh_config.user_agent == "Mozilla/5.0 test agent string for validation here"
    )
    assert fresh_config.download_mode == DownloadMode.TIMELINE
    assert fresh_config.timeline_retries == 5
    assert fresh_config.separate_previews is True
    assert fresh_config.pg_host == "pg.test.example.com"
    assert fresh_config.pg_port == 5433
    assert fresh_config.cached_device_id == "abc-device-id"
    assert fresh_config.cached_device_id_timestamp == 999_000_000
    assert fresh_config.log_levels["sqlalchemy"] == "DEBUG"


# ---------------------------------------------------------------------------
# 2. Round-trip save: FanslyConfig attributes → yaml → re-load
# ---------------------------------------------------------------------------


def test_round_trip_save_and_reload(
    config_dir: Path, fresh_config: FanslyConfig
) -> None:
    """Attributes set on FanslyConfig are written to yaml and reload correctly."""
    yaml_path = config_dir / "config.yaml"

    # First load to initialise config_path
    schema = ConfigSchema()
    schema.dump_yaml(yaml_path)
    load_config(fresh_config)

    # Mutate attributes
    fresh_config.user_names = {"alice", "bob"}
    fresh_config.pg_host = "save-test-host"
    fresh_config.pg_port = 5555
    fresh_config.timeline_retries = 7
    fresh_config.separate_previews = True
    fresh_config.log_levels["json"] = "WARNING"
    fresh_config._save_config()

    yaml_text = yaml_path.read_text(encoding="utf-8")
    # New nested logging shape: json.level instead of flat json:LEVEL.
    assert "level: WARNING" in yaml_text or "level: 'WARNING'" in yaml_text
    assert "json_level:" not in yaml_text

    # Reload into a completely fresh config
    second_config = FanslyConfig(program_version="0.13.0")
    load_config(second_config)

    assert second_config.user_names == {"alice", "bob"}
    assert second_config.pg_host == "save-test-host"
    assert second_config.pg_port == 5555
    assert second_config.timeline_retries == 7
    assert second_config.separate_previews is True
    assert second_config.log_levels["json"] == "WARNING"

    # Legacy flat-shape compatibility (`logging.json_level: WARNING`) is
    # covered by the unit tests in tests/config/unit/test_schema.py:
    # ``test_logging_legacy_json_level_alias_migrates``.


# ---------------------------------------------------------------------------
# 3. SecretStr unwrap: config.token is plain str, schema token is SecretStr
# ---------------------------------------------------------------------------


def test_secretstr_unwrap_on_load(config_dir: Path, fresh_config: FanslyConfig) -> None:
    """authorization_token is stored as SecretStr; config.token is plain str."""
    yaml_path = config_dir / "config.yaml"

    schema = ConfigSchema()
    raw_token = "x" * 60  # long enough to pass token_is_valid()
    schema.my_account.authorization_token = SecretStr(raw_token)
    schema.dump_yaml(yaml_path)

    load_config(fresh_config)

    # FanslyConfig.token is a plain str (SecretStr was unwrapped)
    assert isinstance(fresh_config.token, str)
    assert fresh_config.token == raw_token

    # Schema still holds the SecretStr
    assert fresh_config._schema is not None
    assert isinstance(fresh_config._schema.my_account.authorization_token, SecretStr)
    assert (
        fresh_config._schema.my_account.authorization_token.get_secret_value()
        == raw_token
    )


# ---------------------------------------------------------------------------
# 4. Postgres section: config.pg_host == schema.postgres.pg_host
# ---------------------------------------------------------------------------


def test_postgres_section_bridge(config_dir: Path, fresh_config: FanslyConfig) -> None:
    """All postgres fields on FanslyConfig match the schema postgres section."""
    yaml_path = config_dir / "config.yaml"

    schema = ConfigSchema()
    schema.postgres.pg_host = "db.bridge-test.local"
    schema.postgres.pg_port = 5499
    schema.postgres.pg_database = "bridgedb"
    schema.postgres.pg_user = "bridge_user"
    schema.postgres.pg_pool_size = 8
    schema.postgres.pg_max_overflow = 12
    schema.postgres.pg_pool_timeout = 45
    schema.dump_yaml(yaml_path)

    load_config(fresh_config)

    assert fresh_config.pg_host == schema.postgres.pg_host
    assert fresh_config.pg_port == schema.postgres.pg_port
    assert fresh_config.pg_database == schema.postgres.pg_database
    assert fresh_config.pg_user == schema.postgres.pg_user
    assert fresh_config.pg_pool_size == schema.postgres.pg_pool_size
    assert fresh_config.pg_max_overflow == schema.postgres.pg_max_overflow
    assert fresh_config.pg_pool_timeout == schema.postgres.pg_pool_timeout


# ---------------------------------------------------------------------------
# 5. Legacy: no _parser attribute on FanslyConfig
# ---------------------------------------------------------------------------


def test_no_parser_attribute(fresh_config: FanslyConfig) -> None:
    """FanslyConfig no longer has a _parser (ConfigParser) attribute."""
    assert not hasattr(fresh_config, "_parser"), (
        "_parser (legacy ConfigParser) must not exist on FanslyConfig; "
        "it was replaced by _schema (ConfigSchema)"
    )


# ---------------------------------------------------------------------------
# 6. StashContext round-trip
# ---------------------------------------------------------------------------


def test_stash_context_round_trip(config_dir: Path, fresh_config: FanslyConfig) -> None:
    """StashContext settings round-trip through config.yaml."""
    yaml_path = config_dir / "config.yaml"

    schema = ConfigSchema()
    schema.stash_context = StashContextSection(
        scheme="https",
        host="stash.example.com",
        port=9998,
        apikey="secret-api-key",
    )
    schema.dump_yaml(yaml_path)

    load_config(fresh_config)

    assert fresh_config.stash_context_conn is not None
    assert fresh_config.stash_context_conn["scheme"] == "https"
    assert fresh_config.stash_context_conn["host"] == "stash.example.com"
    assert fresh_config.stash_context_conn["port"] == 9998
    assert fresh_config.stash_context_conn["apikey"] == "secret-api-key"


def test_stash_mapped_path_round_trip(
    config_dir: Path, fresh_config: FanslyConfig
) -> None:
    """stash_context.mapped_path round-trips through config.yaml."""
    yaml_path = config_dir / "config.yaml"

    schema = ConfigSchema()
    schema.stash_context = StashContextSection(
        scheme="http",
        host="localhost",
        port=9999,
        apikey="",
        mapped_path="/data/fansly",
    )
    schema.dump_yaml(yaml_path)

    load_config(fresh_config)

    assert fresh_config.stash_mapped_path == Path("/data/fansly")


def test_stash_mapped_path_none_when_absent(
    config_dir: Path, fresh_config: FanslyConfig
) -> None:
    """stash_mapped_path is None when mapped_path is not set in schema."""
    yaml_path = config_dir / "config.yaml"

    schema = ConfigSchema()
    schema.stash_context = StashContextSection(
        scheme="http",
        host="localhost",
        port=9999,
        apikey="",
    )
    schema.dump_yaml(yaml_path)

    load_config(fresh_config)

    assert fresh_config.stash_mapped_path is None


# ---------------------------------------------------------------------------
# 7. Rate limiting fields round-trip
# ---------------------------------------------------------------------------


def test_rate_limiting_round_trip(config_dir: Path, fresh_config: FanslyConfig) -> None:
    """Rate limiting settings are persisted and reloaded correctly."""
    yaml_path = config_dir / "config.yaml"

    schema = ConfigSchema()
    schema.options.rate_limiting_enabled = False
    schema.options.rate_limiting_requests_per_minute = 30
    schema.options.rate_limiting_burst_size = 5
    schema.options.rate_limiting_backoff_factor = 2.0
    schema.dump_yaml(yaml_path)

    load_config(fresh_config)

    assert fresh_config.rate_limiting_enabled is False
    assert fresh_config.rate_limiting_requests_per_minute == 30
    assert fresh_config.rate_limiting_burst_size == 5
    assert fresh_config.rate_limiting_backoff_factor == 2.0


# ---------------------------------------------------------------------------
# 8. _schema is populated after load_config
# ---------------------------------------------------------------------------


def test_schema_is_populated_after_load(
    config_dir: Path, fresh_config: FanslyConfig
) -> None:
    """config._schema is set and is a ConfigSchema after load_config()."""
    yaml_path = config_dir / "config.yaml"
    ConfigSchema().dump_yaml(yaml_path)

    load_config(fresh_config)

    assert fresh_config._schema is not None
    assert isinstance(fresh_config._schema, ConfigSchema)


# ---------------------------------------------------------------------------
# 9. Session baseline: CLI --full-pass is runtime-only, never persists to YAML
# ---------------------------------------------------------------------------


def test_full_pass_runtime_only_schema_never_mutated(
    config_dir: Path, fresh_config: FanslyConfig
) -> None:
    """CLI --full-pass sets ONLY ``config.monitoring_session_baseline``.

    The schema field ``monitoring.session_baseline`` must remain None so
    that subsequent ``_save_config`` calls do not persist the CLI flag
    into ``config.yaml``. Persisting it would silently turn ``--full-pass``
    into a permanent setting (the regression that produced "every
    invocation does a full pass forever").

    The daemon consumes the runtime baseline once per creator
    (``baseline_consumed`` set in daemon/runner.py) and advances
    ``MonitorState.lastCheckedAt`` in the DB on success — the CLI value
    self-extinguishes within the run.
    """
    yaml_path = config_dir / "config.yaml"
    ConfigSchema().dump_yaml(yaml_path)
    load_config(fresh_config)

    # Simulate --full-pass via the handler
    args = argparse.Namespace(full_pass=True, monitor_since=None, daemon_mode=False)
    result = _handle_monitoring_settings(args, fresh_config)

    assert result is True

    expected = datetime(2000, 1, 1, tzinfo=UTC)
    assert fresh_config.monitoring_session_baseline == expected, (
        "Runtime baseline must reflect the CLI flag for the daemon to consume"
    )

    # Schema must NOT be mutated — the runtime baseline lives only on
    # ``config.monitoring_session_baseline`` for the duration of the session.
    assert fresh_config._schema is not None
    assert fresh_config._schema.monitoring.session_baseline is None, (
        "CLI --full-pass must not write the baseline into the YAML schema; "
        "doing so makes the next invocation silently re-trigger a full pass"
    )

    # Saving must preserve the YAML's None — re-load the file and verify.
    assert fresh_config._save_config() is True
    reloaded = ConfigSchema.load_yaml(yaml_path)
    assert reloaded.monitoring.session_baseline is None


# ---------------------------------------------------------------------------
# 10. Session baseline: YAML-loaded value survives into config.monitoring_session_baseline
# ---------------------------------------------------------------------------


def test_yaml_session_baseline_consumed_and_reset(
    config_dir: Path, fresh_config: FanslyConfig
) -> None:
    """A YAML-authored ``session_baseline`` is a one-shot directive.

    On load, the value flows into ``config.monitoring_session_baseline``
    for the daemon to consume, AND the schema field is cleared so the
    immediate ``save_config_or_raise`` at the end of ``load_config``
    writes ``session_baseline: null`` back to disk. Two effects:

      1. Honors users who hand-author a baseline (it applies once).
      2. Heals YAMLs left in a permanent-full-pass state by the prior
         bug where CLI ``--full-pass`` / ``--monitor-since`` wrote into
         the schema field.
    """
    yaml_path = config_dir / "config.yaml"

    schema = ConfigSchema()
    schema.monitoring.session_baseline = datetime(2026, 4, 15, 0, 0, 0, tzinfo=UTC)
    schema.dump_yaml(yaml_path)

    load_config(fresh_config)

    # (1) Runtime gets the value — daemon will consume it once per creator.
    assert fresh_config.monitoring_session_baseline is not None
    runtime = fresh_config.monitoring_session_baseline
    assert runtime.tzinfo is not None
    assert runtime.astimezone(UTC) == datetime(2026, 4, 15, 0, 0, 0, tzinfo=UTC)

    # (2) On-disk YAML has been reset to None — re-read the file to verify
    # this is real persistence, not just an in-memory schema mutation.
    reloaded_schema = ConfigSchema.load_yaml(yaml_path)
    assert reloaded_schema.monitoring.session_baseline is None, (
        "YAML session_baseline must be cleared after consume; otherwise "
        "every subsequent invocation re-applies the same baseline"
    )


# ---------------------------------------------------------------------------
# 11. Session baseline: CLI takes precedence over YAML-loaded value
# ---------------------------------------------------------------------------


def test_cli_baseline_takes_precedence_over_yaml(
    config_dir: Path, fresh_config: FanslyConfig
) -> None:
    """CLI ``--monitor-since`` overrides a YAML-authored ``session_baseline``.

    Combined behavior under consume-and-reset:

    1. YAML's baseline loads into ``config.monitoring_session_baseline``
       and is immediately cleared from the schema (consume-and-reset).
    2. CLI handler then overwrites the runtime value with its own baseline.
    3. Schema field stays None throughout — neither YAML's nor CLI's
       baseline is persisted to disk.
    """
    yaml_path = config_dir / "config.yaml"

    schema = ConfigSchema()
    # YAML has an older baseline
    yaml_baseline = datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC)
    schema.monitoring.session_baseline = yaml_baseline
    schema.dump_yaml(yaml_path)

    load_config(fresh_config)

    # After load: YAML value flowed into runtime, schema cleared (one-shot).
    assert fresh_config.monitoring_session_baseline == yaml_baseline
    assert fresh_config._schema is not None
    assert fresh_config._schema.monitoring.session_baseline is None

    # CLI overrides via the production handler, not direct mutation —
    # mirrors what map_args_to_config does at startup.
    cli_baseline = datetime(2026, 6, 1, 0, 0, 0, tzinfo=UTC)
    args = argparse.Namespace(
        full_pass=False, monitor_since=cli_baseline, daemon_mode=False
    )
    _handle_monitoring_settings(args, fresh_config)

    # Runtime reflects CLI; schema still clean (CLI must not write to schema).
    assert fresh_config.monitoring_session_baseline == cli_baseline
    assert fresh_config._schema.monitoring.session_baseline is None

    # Persistence guard: saving must keep YAML's session_baseline as null.
    assert fresh_config._save_config() is True
    reloaded = ConfigSchema.load_yaml(yaml_path)
    assert reloaded.monitoring.session_baseline is None


# ---------------------------------------------------------------------------
# 12. daemon_mode: YAML monitoring.daemon_mode → config.daemon_mode
# ---------------------------------------------------------------------------


def test_yaml_daemon_mode_survives_into_config(
    config_dir: Path, fresh_config: FanslyConfig
) -> None:
    """monitoring.daemon_mode: true in config.yaml populates config.daemon_mode."""
    yaml_path = config_dir / "config.yaml"

    schema = ConfigSchema()
    schema.monitoring.daemon_mode = True
    schema.dump_yaml(yaml_path)

    load_config(fresh_config)

    assert fresh_config.daemon_mode is True


def test_yaml_daemon_mode_false_is_default(
    config_dir: Path, fresh_config: FanslyConfig
) -> None:
    """monitoring.daemon_mode absent from YAML leaves config.daemon_mode False."""
    yaml_path = config_dir / "config.yaml"

    schema = ConfigSchema()
    # daemon_mode defaults to False; do not set it explicitly
    schema.dump_yaml(yaml_path)

    load_config(fresh_config)

    assert fresh_config.daemon_mode is False


# ---------------------------------------------------------------------------
# 13. daemon_mode: CLI -d overrides YAML monitoring.daemon_mode: false
# ---------------------------------------------------------------------------


def test_cli_daemon_flag_overrides_yaml_false(
    config_dir: Path, fresh_config: FanslyConfig
) -> None:
    """CLI -d sets config.daemon_mode=True even when YAML has daemon_mode: false."""
    yaml_path = config_dir / "config.yaml"

    schema = ConfigSchema()
    schema.monitoring.daemon_mode = False
    schema.dump_yaml(yaml_path)

    load_config(fresh_config)
    assert fresh_config.daemon_mode is False  # YAML value loaded

    # Simulate CLI -d / --daemon processing
    cli_args = argparse.Namespace(full_pass=False, monitor_since=None, daemon_mode=True)
    result = _handle_monitoring_settings(cli_args, fresh_config)

    assert result is True
    assert fresh_config.daemon_mode is True


def test_yaml_daemon_mode_forces_interactive_false(
    config_dir: Path, fresh_config: FanslyConfig
) -> None:
    """monitoring.daemon_mode: true in YAML forces config.interactive to False."""
    yaml_path = config_dir / "config.yaml"

    schema = ConfigSchema()
    schema.monitoring.daemon_mode = True
    schema.options.interactive = True
    schema.dump_yaml(yaml_path)

    load_config(fresh_config)

    assert fresh_config.daemon_mode is True
    assert fresh_config.interactive is False


def test_cli_daemon_flag_forces_interactive_false(
    config_dir: Path, fresh_config: FanslyConfig
) -> None:
    """CLI --daemon forces config.interactive=False regardless of YAML."""
    yaml_path = config_dir / "config.yaml"

    schema = ConfigSchema()
    schema.monitoring.daemon_mode = False
    schema.options.interactive = True
    schema.dump_yaml(yaml_path)

    load_config(fresh_config)
    assert fresh_config.interactive is True  # YAML value loaded

    cli_args = argparse.Namespace(full_pass=False, monitor_since=None, daemon_mode=True)
    _handle_monitoring_settings(cli_args, fresh_config)

    assert fresh_config.daemon_mode is True
    assert fresh_config.interactive is False


# ---------------------------------------------------------------------------
# 14. unrecoverable_error_timeout_seconds: YAML value → config attribute
# ---------------------------------------------------------------------------


def test_unrecoverable_error_timeout_populated_from_schema(
    config_dir: Path, fresh_config: FanslyConfig
) -> None:
    """config.unrecoverable_error_timeout_seconds is populated from
    schema.monitoring.unrecoverable_error_timeout_seconds after load_config()."""
    yaml_path = config_dir / "config.yaml"

    schema = ConfigSchema()
    schema.monitoring.unrecoverable_error_timeout_seconds = 1800
    schema.dump_yaml(yaml_path)

    load_config(fresh_config)

    assert fresh_config.unrecoverable_error_timeout_seconds == 1800


def test_unrecoverable_error_timeout_default_survives_load(
    config_dir: Path, fresh_config: FanslyConfig
) -> None:
    """When unrecoverable_error_timeout_seconds is absent from YAML,
    config attribute defaults to 3600."""
    yaml_path = config_dir / "config.yaml"

    schema = ConfigSchema()
    # Do not set unrecoverable_error_timeout_seconds; let it use the default
    schema.dump_yaml(yaml_path)

    load_config(fresh_config)

    assert fresh_config.unrecoverable_error_timeout_seconds == 3600


def test_heartbeat_interval_populated_from_schema(
    config_dir: Path, fresh_config: FanslyConfig
) -> None:
    """config.monitoring_heartbeat_interval_minutes is populated from
    schema.monitoring.heartbeat_interval_minutes after load_config()."""
    yaml_path = config_dir / "config.yaml"

    schema = ConfigSchema()
    schema.monitoring.heartbeat_interval_minutes = 5
    schema.dump_yaml(yaml_path)

    load_config(fresh_config)

    assert fresh_config.monitoring_heartbeat_interval_minutes == 5


def test_heartbeat_interval_default_survives_load(
    config_dir: Path, fresh_config: FanslyConfig
) -> None:
    """When heartbeat_interval_minutes is absent from YAML,
    config attribute defaults to 15."""
    yaml_path = config_dir / "config.yaml"

    schema = ConfigSchema()
    schema.dump_yaml(yaml_path)

    load_config(fresh_config)

    assert fresh_config.monitoring_heartbeat_interval_minutes == 15


# ---------------------------------------------------------------------------
# 16. CLI mode flags (--stash-only etc.) must NOT leak into config.yaml
# ---------------------------------------------------------------------------


def test_stash_only_cli_does_not_leak_to_yaml(
    config_dir: Path, fresh_config: FanslyConfig
) -> None:
    """``--stash-only`` overrides ``download_mode`` for the session only.

    Two-pronged regression guard, per the YAML-migration bug where each
    invocation with ``--stash-only`` pinned ``stash_only`` as the new
    YAML default — forcing the user to remember ``--normal`` next run
    just to undo the previous flag.

    Asserts BOTH halves so a future regression that breaks either side
    fails loudly:
      1. Runtime (``config.download_mode``) is the CLI-overlayed value.
      2. Persisted YAML (``download_mode`` field) is the original
         YAML-loaded value, NOT the CLI overlay.
    """
    yaml_path = config_dir / "config.yaml"

    # Author YAML with download_mode: normal (the user's chosen default).
    schema = ConfigSchema()
    schema.options.download_mode = DownloadMode.NORMAL
    schema.dump_yaml(yaml_path)

    # load_config also runs an immediate save_config_or_raise; we want the
    # post-args save to be the one under test.
    load_config(fresh_config)
    assert fresh_config.download_mode == DownloadMode.NORMAL

    # Simulate `--stash-only` via the production handler.
    args = argparse.Namespace(
        stash_only=True,
        download_mode_normal=False,
        download_mode_messages=False,
        download_mode_timeline=False,
        download_mode_collection=False,
        download_mode_single=None,
    )
    config_overridden, download_mode_set = _handle_download_mode(args, fresh_config)
    assert config_overridden is True
    assert download_mode_set is True

    # (1) Runtime must reflect the CLI overlay — the daemon/downloader
    # logic depends on this for the current session.
    assert fresh_config.download_mode == DownloadMode.STASH_ONLY

    # Trigger a post-args save (mirrors what setup_api / login / device-id
    # rotation / get_stash_context all do during normal startup).
    assert fresh_config._save_config() is True

    # (2) YAML must still hold NORMAL — the next invocation without any
    # mode flag must default to the user's persisted choice, not the
    # previous run's CLI flag.
    reloaded = ConfigSchema.load_yaml(yaml_path)
    assert reloaded.options.download_mode == DownloadMode.NORMAL, (
        "CLI --stash-only must not mutate the persisted download_mode; "
        "users would otherwise need --normal on every subsequent invocation"
    )


# ---------------------------------------------------------------------------
# 17. Per-run CLI flags do not leak into config.yaml (full Category B audit)
# ---------------------------------------------------------------------------


def _full_args_namespace(**overrides) -> argparse.Namespace:
    """Build the complete argparse.Namespace shape ``map_args_to_config``
    family expects, with all flags defaulted to their non-firing value.

    Mirrors the Namespace shape in ``tests/config/unit/test_args.py``.
    Pass keyword overrides for the flags under test.
    """
    defaults = {
        "debug": False,
        "users": None,
        "download_mode_normal": False,
        "download_mode_messages": False,
        "download_mode_timeline": False,
        "download_mode_collection": False,
        "download_mode_single": None,
        "stash_only": False,
        "download_directory": None,
        "token": None,
        "user_agent": None,
        "check_key": None,
        "temp_folder": None,
        "separate_previews": False,
        "use_duplicate_threshold": False,
        "non_interactive": False,
        "no_prompt_on_exit": False,
        "no_folder_suffix": False,
        "no_media_previews": False,
        "hide_downloads": False,
        "hide_skipped_downloads": False,
        "no_open_folder": False,
        "no_separate_messages": False,
        "no_separate_timeline": False,
        "timeline_retries": None,
        "timeline_delay_seconds": None,
        "api_max_retries": None,
        "use_following": False,
        "use_following_with_pagination": False,
        "use_pagination_duplication": False,
        "reverse_order": False,
        "pg_host": None,
        "pg_port": None,
        "pg_database": None,
        "pg_user": None,
        "pg_password": None,
        "monitor_since": None,
        "full_pass": False,
        "daemon_mode": False,
    }
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def test_debug_cli_does_not_clobber_yaml_true(
    config_dir: Path, fresh_config: FanslyConfig
) -> None:
    """``--debug`` is per-run; YAML's ``debug: true`` survives an invocation
    that does NOT pass ``--debug``.

    Pre-fix, ``_handle_debug_settings`` did ``config.debug = args.debug``
    unconditionally. With ``args.debug=False`` (the argparse default when
    ``--debug`` is omitted), that overwrote a YAML-set True with False on
    every invocation — silently disabling the user's persisted debug mode.
    """
    yaml_path = config_dir / "config.yaml"
    schema = ConfigSchema()
    schema.options.debug = True
    schema.dump_yaml(yaml_path)

    load_config(fresh_config)
    assert fresh_config.debug is True

    # Simulate invocation WITHOUT --debug (the regression scenario).
    _handle_debug_settings(_full_args_namespace(debug=False), fresh_config)
    assert fresh_config.debug is True, (
        "Omitting --debug must not clobber the YAML-loaded debug=True"
    )

    fresh_config._save_config()
    reloaded = ConfigSchema.load_yaml(yaml_path)
    assert reloaded.options.debug is True


def test_debug_cli_overlay_does_not_persist(
    config_dir: Path, fresh_config: FanslyConfig
) -> None:
    """``--debug`` enables debug for the session but does not write to YAML."""
    yaml_path = config_dir / "config.yaml"
    schema = ConfigSchema()
    schema.options.debug = False
    schema.dump_yaml(yaml_path)

    load_config(fresh_config)

    _handle_debug_settings(_full_args_namespace(debug=True), fresh_config)
    assert fresh_config.debug is True

    fresh_config._save_config()
    reloaded = ConfigSchema.load_yaml(yaml_path)
    assert reloaded.options.debug is False, (
        "CLI --debug must be per-run only; YAML's debug=False stays as the default"
    )


def test_negative_bool_cli_flags_do_not_persist(
    config_dir: Path, fresh_config: FanslyConfig
) -> None:
    """``--non-interactive``, ``-npox``, ``--no-previews`` etc. are per-run.

    All negative-bool CLI flags flip a YAML-persisted positive setting to
    False for the session. None should mutate the YAML default.
    """
    yaml_path = config_dir / "config.yaml"
    schema = ConfigSchema()
    # User's authored YAML has all the user-friendly defaults turned on.
    schema.options.interactive = True
    schema.options.prompt_on_exit = True
    schema.options.use_folder_suffix = True
    schema.options.download_media_previews = True
    schema.options.show_downloads = True
    schema.options.show_skipped_downloads = True
    schema.options.open_folder_when_finished = True
    schema.options.separate_messages = True
    schema.options.separate_timeline = True
    schema.dump_yaml(yaml_path)

    load_config(fresh_config)

    # Simulate a CI-flavoured invocation that flips many things off.
    _handle_boolean_settings(
        _full_args_namespace(
            non_interactive=True,
            no_prompt_on_exit=True,
            no_folder_suffix=True,
            no_media_previews=True,
            hide_downloads=True,
            hide_skipped_downloads=True,
            no_open_folder=True,
            no_separate_messages=True,
            no_separate_timeline=True,
        ),
        fresh_config,
    )

    # (1) Runtime reflects the CI overlay this session.
    assert fresh_config.interactive is False
    assert fresh_config.prompt_on_exit is False
    assert fresh_config.use_folder_suffix is False
    assert fresh_config.download_media_previews is False
    assert fresh_config.show_downloads is False
    assert fresh_config.show_skipped_downloads is False
    assert fresh_config.open_folder_when_finished is False
    assert fresh_config.separate_messages is False
    assert fresh_config.separate_timeline is False

    # (2) YAML still has every original value — re-read from disk.
    fresh_config._save_config()
    reloaded = ConfigSchema.load_yaml(yaml_path)
    assert reloaded.options.interactive is True
    assert reloaded.options.prompt_on_exit is True
    assert reloaded.options.use_folder_suffix is True
    assert reloaded.options.download_media_previews is True
    assert reloaded.options.show_downloads is True
    assert reloaded.options.show_skipped_downloads is True
    assert reloaded.options.open_folder_when_finished is True
    assert reloaded.options.separate_messages is True
    assert reloaded.options.separate_timeline is True


def test_positive_bool_cli_flags_do_not_persist(
    config_dir: Path, fresh_config: FanslyConfig
) -> None:
    """``--separate-previews``, ``--use-duplicate-threshold``,
    ``--use-pagination-duplication`` flip per-run; YAML defaults survive."""
    yaml_path = config_dir / "config.yaml"
    schema = ConfigSchema()
    schema.options.separate_previews = False
    schema.options.use_duplicate_threshold = False
    schema.options.use_pagination_duplication = False
    schema.dump_yaml(yaml_path)

    load_config(fresh_config)

    _handle_boolean_settings(
        _full_args_namespace(
            separate_previews=True,
            use_duplicate_threshold=True,
            use_pagination_duplication=True,
        ),
        fresh_config,
    )

    assert fresh_config.separate_previews is True
    assert fresh_config.use_duplicate_threshold is True
    assert fresh_config.use_pagination_duplication is True

    fresh_config._save_config()
    reloaded = ConfigSchema.load_yaml(yaml_path)
    assert reloaded.options.separate_previews is False
    assert reloaded.options.use_duplicate_threshold is False
    assert reloaded.options.use_pagination_duplication is False


def test_use_following_cli_does_not_persist(
    config_dir: Path, fresh_config: FanslyConfig
) -> None:
    """``-uf`` enables following-mode for the session; YAML stays False."""
    yaml_path = config_dir / "config.yaml"
    schema = ConfigSchema()
    schema.targeted_creator.use_following = False
    schema.dump_yaml(yaml_path)

    load_config(fresh_config)

    _handle_user_settings(_full_args_namespace(use_following=True), fresh_config)
    assert fresh_config.use_following is True

    fresh_config._save_config()
    reloaded = ConfigSchema.load_yaml(yaml_path)
    assert reloaded.targeted_creator.use_following is False


def test_user_names_cli_does_not_overwrite_yaml_list(
    config_dir: Path, fresh_config: FanslyConfig
) -> None:
    """``-u alice`` targets alice this run; YAML's full creator list stays."""
    yaml_path = config_dir / "config.yaml"
    schema = ConfigSchema()
    schema.targeted_creator.usernames = ["alice", "bob", "carol"]
    schema.dump_yaml(yaml_path)

    load_config(fresh_config)
    assert fresh_config.user_names == {"alice", "bob", "carol"}

    _handle_user_settings(
        _full_args_namespace(use_following=False, users=["alice"]),
        fresh_config,
    )
    assert fresh_config.user_names == {"alice"}

    fresh_config._save_config()
    reloaded = ConfigSchema.load_yaml(yaml_path)
    assert sorted(reloaded.targeted_creator.usernames) == ["alice", "bob", "carol"], (
        "CLI -u must target a subset for this run only; YAML's full list stays "
        "as the persisted authoritative set"
    )


def test_uf_protects_user_names_from_refresh_following_overwrite(
    config_dir: Path, fresh_config: FanslyConfig
) -> None:
    """``-uf`` blocks daemon's ``_refresh_following`` from overwriting YAML.

    Scenario: user authored ``usernames: [alice, bob]`` in YAML, runs with
    ``-uf`` (use-following mode), and during the daemon run
    ``_refresh_following`` fetches the live following list = [alice, bob,
    carol, dave] and assigns it to ``config.user_names``. Without protection,
    the next ``_save_config`` would clobber the user's curated list with the
    API-fetched superset.

    The fix marks ``user_names`` ephemeral preemptively when ``-uf`` (or
    ``-ufp``) fires, so any runtime mutation (CLI ``-u``, programmatic, or
    daemon-fetch) stays runtime-only and the YAML curation survives.
    """
    yaml_path = config_dir / "config.yaml"
    schema = ConfigSchema()
    schema.targeted_creator.usernames = ["alice", "bob"]
    schema.dump_yaml(yaml_path)

    load_config(fresh_config)

    # Apply -uf via the production handler.
    _handle_user_settings(_full_args_namespace(use_following=True), fresh_config)
    assert fresh_config.use_following is True

    # Simulate ``_refresh_following`` setting user_names to the fetched list.
    fresh_config.user_names = {"alice", "bob", "carol", "dave"}

    # Save and verify YAML still has the original curated list, not the fetch.
    fresh_config._save_config()
    reloaded = ConfigSchema.load_yaml(yaml_path)
    assert sorted(reloaded.targeted_creator.usernames) == ["alice", "bob"], (
        "When -uf is active, the daemon's auto-fetched following list must NOT "
        "propagate to YAML; the user's curated usernames: list stays sacred"
    )


def test_ufp_protects_user_names_from_refresh_following_overwrite(
    config_dir: Path, fresh_config: FanslyConfig
) -> None:
    """``-ufp`` (combined flag) provides the same user_names protection as ``-uf``."""
    yaml_path = config_dir / "config.yaml"
    schema = ConfigSchema()
    schema.targeted_creator.usernames = ["alice", "bob"]
    schema.dump_yaml(yaml_path)

    load_config(fresh_config)

    _handle_user_settings(
        _full_args_namespace(use_following_with_pagination=True), fresh_config
    )
    assert fresh_config.use_following is True
    assert fresh_config.use_pagination_duplication is True

    fresh_config.user_names = {"alice", "bob", "carol", "dave"}

    fresh_config._save_config()
    reloaded = ConfigSchema.load_yaml(yaml_path)
    assert sorted(reloaded.targeted_creator.usernames) == ["alice", "bob"]
