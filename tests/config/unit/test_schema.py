"""Tests for config/schema.py — Pydantic config schema + ruamel.yaml plumbing.

All tests are pure unit tests: no mocking, no database, no network.
File I/O uses ``tmp_path`` (pytest built-in).
"""

from __future__ import annotations

import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import SecretStr, ValidationError

from config.metadatahandling import MetadataHandling
from config.modes import DownloadMode
from config.schema import (
    ConfigSchema,
    LogicSection,
    MonitoringSection,
    MyAccountSection,
    OptionsSection,
    PostgresSection,
    TargetedCreatorSection,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def sample_yaml_path(tmp_path: Path) -> Path:
    """Copy sample.yaml into an isolated tmp_path so tests can mutate freely."""
    src = FIXTURES_DIR / "sample.yaml"
    dst = tmp_path / "config.yaml"
    shutil.copy(src, dst)
    return dst


# ---------------------------------------------------------------------------
# Test 1: Default instantiation — all sections have valid defaults
# ---------------------------------------------------------------------------


def test_default_instantiation_is_valid() -> None:
    """ConfigSchema() with no arguments must succeed and expose correct defaults."""
    schema = ConfigSchema()

    # Root sections present
    assert isinstance(schema.targeted_creator, TargetedCreatorSection)
    assert isinstance(schema.my_account, MyAccountSection)
    assert isinstance(schema.options, OptionsSection)
    assert isinstance(schema.postgres, PostgresSection)
    assert isinstance(schema.monitoring, MonitoringSection)
    assert isinstance(schema.logic, LogicSection)

    # MonitoringSection defaults match the architecture plan
    mon = schema.monitoring
    assert mon.enabled is False
    assert mon.active_duration_minutes == 60
    assert mon.idle_duration_minutes == 120
    assert mon.hidden_duration_minutes == 300
    assert mon.timeline_poll_active_seconds == 180
    assert mon.timeline_poll_idle_seconds == 600
    assert mon.story_poll_active_seconds == 30
    assert mon.story_poll_idle_seconds == 300


# ---------------------------------------------------------------------------
# Test 2: extra="forbid" rejects unknown keys with a clear error
# ---------------------------------------------------------------------------


def test_extra_forbid_root_unknown_key() -> None:
    """Unknown key at root level raises ValidationError, not a silent pass."""
    with pytest.raises(ValidationError) as exc_info:
        ConfigSchema(unknown_top_level_key="oops")  # type: ignore[call-arg]

    assert "unknown_top_level_key" in str(exc_info.value)


def test_extra_forbid_section_unknown_key(tmp_path: Path) -> None:
    """Unknown key inside a section also raises ValidationError."""
    bad_yaml = tmp_path / "bad.yaml"
    bad_yaml.write_text(
        "monitoring:\n  enabled: false\n  typo_key: 99\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError) as exc_info:
        ConfigSchema.load_yaml(bad_yaml)

    assert "typo_key" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Test 3: Round-trip — comments preserved exactly across load→dump→reload
# ---------------------------------------------------------------------------


def test_round_trip_comment_preservation(
    sample_yaml_path: Path, tmp_path: Path
) -> None:
    """Loading then dumping the YAML preserves comment lines verbatim."""
    original_text = sample_yaml_path.read_text(encoding="utf-8")

    # Collect lines that are pure comments (start with optional whitespace + #)
    comment_lines = [
        line for line in original_text.splitlines() if line.strip().startswith("#")
    ]
    assert comment_lines, "Fixture must contain at least one comment line"

    schema = ConfigSchema.load_yaml(sample_yaml_path)
    out_path = tmp_path / "out.yaml"
    schema.dump_yaml(out_path)

    written_text = out_path.read_text(encoding="utf-8")
    for comment_line in comment_lines:
        assert comment_line in written_text, (
            f"Comment line lost after dump: {comment_line!r}"
        )


def test_round_trip_values_survive(sample_yaml_path: Path, tmp_path: Path) -> None:
    """Mutating a value and dumping → reloading recovers the new value."""
    schema = ConfigSchema.load_yaml(sample_yaml_path)

    # Mutate something
    schema.options.timeline_retries = 5
    out_path = tmp_path / "mutated.yaml"
    schema.dump_yaml(out_path)

    reloaded = ConfigSchema.load_yaml(out_path)
    assert reloaded.options.timeline_retries == 5


# ---------------------------------------------------------------------------
# Test 4: SecretStr round-trip — value survives write/load cycle
# ---------------------------------------------------------------------------


def test_secret_str_round_trip(tmp_path: Path) -> None:
    """SecretStr fields are written as plaintext and re-read correctly."""
    schema = ConfigSchema()
    schema.my_account.authorization_token = SecretStr("my-secret-token-123")
    # user_agent is a plain str — verify it survives alongside the SecretStr fields.
    schema.my_account.user_agent = "TestAgent/1.0"
    schema.postgres.pg_password = SecretStr("super-secret-pg-pass")

    out_path = tmp_path / "secrets.yaml"
    schema.dump_yaml(out_path)

    # The file must contain the plaintext (not '**********')
    written = out_path.read_text(encoding="utf-8")
    assert "my-secret-token-123" in written
    assert "super-secret-pg-pass" in written

    # Re-load and recover values
    reloaded = ConfigSchema.load_yaml(out_path)
    assert (
        reloaded.my_account.authorization_token.get_secret_value()
        == "my-secret-token-123"
    )
    assert reloaded.postgres.pg_password is not None
    assert reloaded.postgres.pg_password.get_secret_value() == "super-secret-pg-pass"


# ---------------------------------------------------------------------------
# Test 5: usernames coercion — comma-separated string (legacy .ini) → list
# ---------------------------------------------------------------------------


def test_usernames_accepts_list_from_yaml(tmp_path: Path) -> None:
    """Native YAML list of usernames is preserved as-is."""
    yaml_content = "targeted_creator:\n  usernames:\n    - alice\n    - bob\n"
    cfg_path = tmp_path / "users.yaml"
    cfg_path.write_text(yaml_content, encoding="utf-8")

    schema = ConfigSchema.load_yaml(cfg_path)
    assert schema.targeted_creator.usernames == ["alice", "bob"]


def test_usernames_coerces_comma_string(tmp_path: Path) -> None:
    """Comma-separated string (legacy config.ini format) is split into a list.

    This matters during the one-shot .ini → YAML migration: the old config
    stored username as `username = alice, bob, carol`. That string goes
    through the validator and comes out as a proper list.
    """
    yaml_content = "targeted_creator:\n  usernames: alice, bob, carol\n"
    cfg_path = tmp_path / "users.yaml"
    cfg_path.write_text(yaml_content, encoding="utf-8")

    schema = ConfigSchema.load_yaml(cfg_path)
    assert schema.targeted_creator.usernames == ["alice", "bob", "carol"]


def test_usernames_comma_string_strips_whitespace_and_empty() -> None:
    """Validator trims whitespace and drops empty entries (e.g. trailing commas)."""
    section = TargetedCreatorSection(usernames="  alice  , , bob ,")  # type: ignore[arg-type]
    assert section.usernames == ["alice", "bob"]


# ---------------------------------------------------------------------------
# Test 6: download_mode is case-insensitive
# ---------------------------------------------------------------------------


def test_download_mode_case_insensitive_lower(tmp_path: Path) -> None:
    """download_mode accepts lowercase 'normal' from YAML."""
    yaml_content = "options:\n  download_mode: normal\n"
    cfg_path = tmp_path / "dm.yaml"
    cfg_path.write_text(yaml_content, encoding="utf-8")

    schema = ConfigSchema.load_yaml(cfg_path)
    assert schema.options.download_mode == DownloadMode.NORMAL


def test_download_mode_case_insensitive_mixed(tmp_path: Path) -> None:
    """download_mode accepts mixed-case 'Timeline'."""
    yaml_content = "options:\n  download_mode: Timeline\n"
    cfg_path = tmp_path / "dm2.yaml"
    cfg_path.write_text(yaml_content, encoding="utf-8")

    schema = ConfigSchema.load_yaml(cfg_path)
    assert schema.options.download_mode == DownloadMode.TIMELINE


def test_download_mode_case_insensitive_upper() -> None:
    """OptionsSection accepts uppercase directly via model_validate."""
    section = OptionsSection.model_validate({"download_mode": "MESSAGES"})
    assert section.download_mode == DownloadMode.MESSAGES


# ---------------------------------------------------------------------------
# Test 7: Nested section defaults when section is omitted in YAML
# ---------------------------------------------------------------------------


def test_missing_section_gets_defaults(tmp_path: Path) -> None:
    """A YAML with only one section leaves all other sections at defaults."""
    yaml_content = "targeted_creator:\n  usernames:\n    - alice\n"
    cfg_path = tmp_path / "partial.yaml"
    cfg_path.write_text(yaml_content, encoding="utf-8")

    schema = ConfigSchema.load_yaml(cfg_path)

    # Explicitly supplied section
    assert schema.targeted_creator.usernames == ["alice"]

    # All omitted sections fall back to defaults
    assert schema.monitoring.enabled is False
    assert schema.monitoring.active_duration_minutes == 60
    assert schema.postgres.pg_host == "localhost"
    assert schema.postgres.pg_port == 5432
    assert schema.options.download_mode == DownloadMode.NORMAL
    assert schema.logic.check_key_pattern != ""


# ---------------------------------------------------------------------------
# Test 8: load_yaml on malformed YAML raises a clean error
# ---------------------------------------------------------------------------


def test_load_yaml_malformed_raises_value_error(tmp_path: Path) -> None:
    """Malformed YAML raises ValueError with the file path in the message."""
    bad_path = tmp_path / "broken.yaml"
    # A tab character in a YAML mapping is a scanner error
    bad_path.write_text(
        "targeted_creator:\n\tusernames: bad_indent\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError) as exc_info:
        ConfigSchema.load_yaml(bad_path)

    # Error should mention the file path
    assert "broken.yaml" in str(exc_info.value)


def test_load_yaml_missing_file_raises_file_not_found(tmp_path: Path) -> None:
    """FileNotFoundError for a non-existent path passes through unchanged."""
    with pytest.raises(FileNotFoundError):
        ConfigSchema.load_yaml(tmp_path / "does_not_exist.yaml")


# ---------------------------------------------------------------------------
# Test 9: download_mode validator passes through non-string (already a DownloadMode)
# ---------------------------------------------------------------------------


def test_download_mode_validator_passthrough_non_string() -> None:
    """When download_mode is already a DownloadMode instance, it passes through."""
    section = OptionsSection(download_mode=DownloadMode.COLLECTION)
    assert section.download_mode == DownloadMode.COLLECTION


def test_download_mode_rejects_invalid_string() -> None:
    """An unknown download_mode value raises ValidationError (not silent pass).

    The validator's ``DownloadMode(v.upper())`` lookup raises ValueError on
    anything that isn't a valid mode name; Pydantic wraps that as
    ValidationError. This is the safety net against typos in config.yaml
    (e.g. ``download_mode: normall``).
    """
    with pytest.raises(ValidationError):
        OptionsSection(download_mode="bogus_mode")  # type: ignore[arg-type]


def test_metadata_handling_validator_passthrough_non_string() -> None:
    """When metadata_handling is already a MetadataHandling instance, it passes through."""
    section = OptionsSection(metadata_handling=MetadataHandling.SIMPLE)
    assert section.metadata_handling == MetadataHandling.SIMPLE


def test_retired_field_separate_metadata_silently_dropped() -> None:
    """Old config.yaml files with removed keys load cleanly.

    separate_metadata was removed (legacy SQLite-era flag that was no-op
    under Postgres). An upgrade from an older version must not fail
    validation just because the file still carries the key — the
    _drop_retired_fields validator strips it before extra="forbid"
    runs. Re-add any future removed fields to _DROPPED_FIELDS.
    """
    section = OptionsSection.model_validate(
        {"separate_metadata": False, "download_mode": "NORMAL"}
    )
    assert not hasattr(section, "separate_metadata")
    assert section.download_mode == DownloadMode.NORMAL


def test_retired_field_survives_true_value() -> None:
    """The value of the retired field is irrelevant — it's dropped either way."""
    section = OptionsSection.model_validate({"separate_metadata": True})
    assert not hasattr(section, "separate_metadata")


def test_unknown_non_retired_field_still_rejected() -> None:
    """The drop list is explicit — novel unknown keys still fail validation."""
    with pytest.raises(ValidationError):
        OptionsSection.model_validate({"unicorn_mode": True})


# ---------------------------------------------------------------------------
# Error-formatter tests: verify the human-readable rendering of Pydantic errors
# ---------------------------------------------------------------------------


def test_validation_error_formatter_extra_forbidden(tmp_path) -> None:
    """Unknown key error mentions the value and suggests removing the line."""

    yaml_path = tmp_path / "config.yaml"
    yaml_path.write_text("options:\n  unicorn_mode: true\n")
    with pytest.raises(ValueError) as excinfo:
        ConfigSchema.load_yaml(yaml_path)
    msg = str(excinfo.value)
    assert "1 problem(s) in" in msg
    assert "options.unicorn_mode" in msg
    assert "unknown key" in msg
    assert "remove the line" in msg


def test_validation_error_formatter_bool_parsing(tmp_path) -> None:
    """bool_parsing error tells the user true/false is expected."""

    yaml_path = tmp_path / "config.yaml"
    yaml_path.write_text("options:\n  open_folder_when_finished: maybe\n")
    with pytest.raises(ValueError) as excinfo:
        ConfigSchema.load_yaml(yaml_path)
    msg = str(excinfo.value)
    assert "options.open_folder_when_finished" in msg
    assert "expected true or false" in msg
    assert "maybe" in msg


def test_validation_error_formatter_int_parsing(tmp_path) -> None:
    """int_parsing error asks for a whole number."""

    yaml_path = tmp_path / "config.yaml"
    yaml_path.write_text("options:\n  timeline_retries: not_a_number\n")
    with pytest.raises(ValueError) as excinfo:
        ConfigSchema.load_yaml(yaml_path)
    msg = str(excinfo.value)
    assert "options.timeline_retries" in msg
    assert "expected a whole number" in msg


def test_validation_error_formatter_multiple_errors(tmp_path) -> None:
    """Multiple problems all surface — no early-return on the first."""

    yaml_path = tmp_path / "config.yaml"
    yaml_path.write_text(
        "options:\n"
        "  open_folder_when_finished: maybe\n"
        "  timeline_retries: not_a_number\n"
        "  unicorn_mode: true\n"
    )
    with pytest.raises(ValueError) as excinfo:
        ConfigSchema.load_yaml(yaml_path)
    msg = str(excinfo.value)
    assert "3 problem(s) in" in msg
    assert "options.open_folder_when_finished" in msg
    assert "options.timeline_retries" in msg
    assert "options.unicorn_mode" in msg


def test_validation_error_formatter_value_error_strips_prefix(tmp_path) -> None:
    """Field validators raising ValueError shouldn't show 'Value error, ' prefix."""

    yaml_path = tmp_path / "config.yaml"
    yaml_path.write_text("options:\n  download_mode: bogus_mode\n")
    with pytest.raises(ValueError) as excinfo:
        ConfigSchema.load_yaml(yaml_path)
    msg = str(excinfo.value)
    assert "options.download_mode" in msg
    assert "not a valid DownloadMode" in msg
    # The Pydantic "Value error, " prefix should be stripped
    assert "Value error, " not in msg


# ---------------------------------------------------------------------------
# Test 10: load_yaml on empty YAML file returns all-defaults schema
# ---------------------------------------------------------------------------


def test_load_yaml_empty_file_returns_defaults(tmp_path: Path) -> None:
    """An empty YAML file should produce a fully-defaulted schema."""
    empty_path = tmp_path / "empty.yaml"
    empty_path.write_text("", encoding="utf-8")

    schema = ConfigSchema.load_yaml(empty_path)

    # Should equal a default-constructed instance
    assert schema.monitoring.enabled is False
    assert schema.postgres.pg_host == "localhost"
    assert schema.targeted_creator.usernames == ["replaceme"]


# ---------------------------------------------------------------------------
# Test 11: dump_yaml_string produces valid YAML that round-trips correctly
# ---------------------------------------------------------------------------


def test_dump_yaml_string(tmp_path: Path) -> None:
    """dump_yaml_string() returns valid YAML with section keys present."""
    schema = ConfigSchema()
    schema.options.timeline_retries = 7

    yaml_str = schema.dump_yaml_string()

    assert "targeted_creator:" in yaml_str
    assert "monitoring:" in yaml_str
    assert "7" in yaml_str  # mutated value appears


# ---------------------------------------------------------------------------
# Test 12: MonitoringSection.session_baseline — default and round-trip
# ---------------------------------------------------------------------------


def test_monitoring_session_baseline_default_is_none() -> None:
    """MonitoringSection.session_baseline defaults to None."""
    section = MonitoringSection()
    assert section.session_baseline is None


def test_monitoring_session_baseline_default_via_schema() -> None:
    """ConfigSchema().monitoring.session_baseline is None by default."""
    schema = ConfigSchema()
    assert schema.monitoring.session_baseline is None


def test_monitoring_session_baseline_round_trip(tmp_path: Path) -> None:
    """session_baseline datetime round-trips through dump_yaml → load_yaml correctly."""
    baseline = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)

    schema = ConfigSchema()
    schema.monitoring.session_baseline = baseline
    out_path = tmp_path / "monitoring_baseline.yaml"
    schema.dump_yaml(out_path)

    reloaded = ConfigSchema.load_yaml(out_path)
    assert reloaded.monitoring.session_baseline is not None
    # Recover aware datetime — ruamel may write as offset-aware or UTC
    reloaded_dt = reloaded.monitoring.session_baseline
    assert reloaded_dt.tzinfo is not None, "Reloaded datetime must be timezone-aware"
    # Normalise to UTC for comparison
    assert reloaded_dt.astimezone(UTC) == baseline


def test_monitoring_session_baseline_naive_coerced_to_utc() -> None:
    """A naive datetime passed to session_baseline is coerced to UTC-aware."""
    # DTZ001 is the POINT of this test — we specifically construct a
    # naive datetime to verify the validator coerces it to UTC.
    naive = datetime(2026, 1, 1, 0, 0, 0)  # noqa: DTZ001 # intentionally naive
    section = MonitoringSection(session_baseline=naive)
    assert section.session_baseline is not None
    assert section.session_baseline.tzinfo is not None
    assert section.session_baseline == datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)


def test_monitoring_section_none_session_baseline_round_trip(tmp_path: Path) -> None:
    """session_baseline=None round-trips: absent in YAML → still None after reload."""
    schema = ConfigSchema()
    assert schema.monitoring.session_baseline is None
    out_path = tmp_path / "no_baseline.yaml"
    schema.dump_yaml(out_path)

    reloaded = ConfigSchema.load_yaml(out_path)
    assert reloaded.monitoring.session_baseline is None


# ---------------------------------------------------------------------------
# Test 13: MonitoringSection.unrecoverable_error_timeout_seconds — default and round-trip
# ---------------------------------------------------------------------------


def test_monitoring_unrecoverable_error_timeout_default() -> None:
    """MonitoringSection.unrecoverable_error_timeout_seconds defaults to 3600."""
    section = MonitoringSection()
    assert section.unrecoverable_error_timeout_seconds == 3600


def test_monitoring_unrecoverable_error_timeout_default_via_schema() -> None:
    """ConfigSchema().monitoring.unrecoverable_error_timeout_seconds is 3600 by default."""
    schema = ConfigSchema()
    assert schema.monitoring.unrecoverable_error_timeout_seconds == 3600


def test_monitoring_unrecoverable_error_timeout_round_trip(tmp_path: Path) -> None:
    """Custom unrecoverable_error_timeout_seconds survives a YAML dump/load round-trip."""
    schema = ConfigSchema()
    schema.monitoring.unrecoverable_error_timeout_seconds = 7200
    out_path = tmp_path / "urt.yaml"
    schema.dump_yaml(out_path)

    reloaded = ConfigSchema.load_yaml(out_path)
    assert reloaded.monitoring.unrecoverable_error_timeout_seconds == 7200
