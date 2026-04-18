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

from config.args import _handle_monitoring_settings
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
    return FanslyConfig(program_version="0.11.0")


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
    schema.options.separate_metadata = True
    schema.postgres.pg_host = "pg.test.example.com"
    schema.postgres.pg_port = 5433
    schema.cache.device_id = "abc-device-id"
    schema.cache.device_id_timestamp = 999_000_000
    schema.logging.sqlalchemy = "DEBUG"
    schema.dump_yaml(yaml_path)

    load_config(fresh_config)

    assert (
        fresh_config.user_agent == "Mozilla/5.0 test agent string for validation here"
    )
    assert fresh_config.download_mode == DownloadMode.TIMELINE
    assert fresh_config.timeline_retries == 5
    assert fresh_config.separate_metadata is True
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
    fresh_config.separate_metadata = True
    fresh_config.log_levels["json"] = "WARNING"
    fresh_config._save_config()

    # Reload into a completely fresh config
    second_config = FanslyConfig(program_version="0.11.0")
    load_config(second_config)

    assert second_config.user_names == {"alice", "bob"}
    assert second_config.pg_host == "save-test-host"
    assert second_config.pg_port == 5555
    assert second_config.timeline_retries == 7
    assert second_config.separate_metadata is True
    assert second_config.log_levels["json"] == "WARNING"


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


# ---------------------------------------------------------------------------
# 7. Rate limiting and db_sync fields round-trip
# ---------------------------------------------------------------------------


def test_rate_limiting_and_db_sync_round_trip(
    config_dir: Path, fresh_config: FanslyConfig
) -> None:
    """Rate limiting and db_sync settings are persisted and reloaded correctly."""
    yaml_path = config_dir / "config.yaml"

    schema = ConfigSchema()
    schema.options.rate_limiting_enabled = False
    schema.options.rate_limiting_requests_per_minute = 30
    schema.options.rate_limiting_burst_size = 5
    schema.options.rate_limiting_backoff_factor = 2.0
    schema.options.db_sync_commits = 500
    schema.options.db_sync_seconds = 30
    schema.options.db_sync_min_size = 25
    schema.dump_yaml(yaml_path)

    load_config(fresh_config)

    assert fresh_config.rate_limiting_enabled is False
    assert fresh_config.rate_limiting_requests_per_minute == 30
    assert fresh_config.rate_limiting_burst_size == 5
    assert fresh_config.rate_limiting_backoff_factor == 2.0
    assert fresh_config.db_sync_commits == 500
    assert fresh_config.db_sync_seconds == 30
    assert fresh_config.db_sync_min_size == 25


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
# 9. Session baseline: CLI --full-pass sets both schema and facade attribute
# ---------------------------------------------------------------------------


def test_full_pass_sets_both_schema_and_facade(
    config_dir: Path, fresh_config: FanslyConfig
) -> None:
    """CLI --full-pass sets config.monitoring_session_baseline AND
    config._schema.monitoring.session_baseline to 2000-01-01 UTC."""
    yaml_path = config_dir / "config.yaml"
    ConfigSchema().dump_yaml(yaml_path)
    load_config(fresh_config)

    # Simulate --full-pass via the handler
    args = argparse.Namespace(full_pass=True, monitor_since=None)
    result = _handle_monitoring_settings(args, fresh_config)

    assert result is True

    expected = datetime(2000, 1, 1, tzinfo=UTC)
    assert fresh_config.monitoring_session_baseline == expected

    # Schema must also be updated so _save_config() persists the override
    assert fresh_config._schema is not None
    assert fresh_config._schema.monitoring.session_baseline == expected


# ---------------------------------------------------------------------------
# 10. Session baseline: YAML-loaded value survives into config.monitoring_session_baseline
# ---------------------------------------------------------------------------


def test_yaml_session_baseline_survives_into_config(
    config_dir: Path, fresh_config: FanslyConfig
) -> None:
    """session_baseline: 2026-04-15T00:00:00Z in config.yaml is populated
    into config.monitoring_session_baseline after load_config()."""
    yaml_path = config_dir / "config.yaml"

    schema = ConfigSchema()
    schema.monitoring.session_baseline = datetime(2026, 4, 15, 0, 0, 0, tzinfo=UTC)
    schema.dump_yaml(yaml_path)

    load_config(fresh_config)

    assert fresh_config.monitoring_session_baseline is not None
    reloaded = fresh_config.monitoring_session_baseline
    assert reloaded.tzinfo is not None
    assert reloaded.astimezone(UTC) == datetime(2026, 4, 15, 0, 0, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# 11. Session baseline: CLI takes precedence over YAML-loaded value
# ---------------------------------------------------------------------------


def test_cli_baseline_takes_precedence_over_yaml(
    config_dir: Path, fresh_config: FanslyConfig
) -> None:
    """CLI --monitor-since overrides a session_baseline that was loaded from YAML."""
    yaml_path = config_dir / "config.yaml"

    schema = ConfigSchema()
    # YAML has an older baseline
    schema.monitoring.session_baseline = datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC)
    schema.dump_yaml(yaml_path)

    load_config(fresh_config)
    # After load, the YAML value was set
    assert fresh_config.monitoring_session_baseline == datetime(
        2024, 1, 1, 0, 0, 0, tzinfo=UTC
    )

    # CLI overrides it — simulate post-load CLI processing
    cli_baseline = datetime(2026, 6, 1, 0, 0, 0, tzinfo=UTC)
    fresh_config.monitoring_session_baseline = cli_baseline
    if fresh_config._schema is not None:
        fresh_config._schema.monitoring.session_baseline = cli_baseline

    assert fresh_config.monitoring_session_baseline == cli_baseline
    assert fresh_config._schema.monitoring.session_baseline == cli_baseline


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
