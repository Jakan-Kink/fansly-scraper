"""Pydantic schema for config.yaml — typed, validated, round-trip-safe.

Load with ``ConfigSchema.load_yaml(path)``.  Write back with
``schema.dump_yaml(path)``.  Comments in the YAML file are preserved across
load → modify → dump cycles because the loaded ``ruamel.yaml`` CommentedMap
is stored on the instance and mutated in-place at dump time.

Per-field semantics (defaults, bounds, retired-key history, intended use)
live in ``docs/configuration/config_options.md`` — when adding a new field
or changing an existing one, update that doc instead of inlining rationale
here.
"""

from __future__ import annotations

import io
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PrivateAttr,
    SecretStr,
    ValidationError,
    field_validator,
    model_validator,
)
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap
from ruamel.yaml.error import YAMLError

from config.modes import DownloadMode


# Render-policy marker used in ``Field(json_schema_extra=_ALWAYS)``. A field
# tagged "always" is written to YAML even when its value matches the default
# and the operator never explicitly set it; the cascade-up rule in
# ``_sync_to_map`` then forces the containing section to render. Default
# policy is "conditional" — fields render only when in ``model_fields_set``.
# See docs/configuration/render-policy.md (TODO) for full semantics.
_ALWAYS: dict[str, str] = {"render": "always"}


def _is_always(field_info: Any) -> bool:
    """Return True iff a field's json_schema_extra marks it as render=always."""
    extra = getattr(field_info, "json_schema_extra", None)
    return isinstance(extra, dict) and extra.get("render") == "always"


def _make_yaml() -> YAML:
    """Return a round-trip YAML instance with consistent settings."""
    y = YAML(typ="rt")
    y.preserve_quotes = True
    return y


def _format_validation_error(exc: ValidationError, path: Path) -> str:
    """Render a ValidationError as ``N problem(s) in <path>:`` + per-error lines.

    Unknown error types fall through to Pydantic's own ``msg`` so
    diagnostic detail is preserved.
    """
    errors = exc.errors()
    lines = [f"{len(errors)} problem(s) in {path}:"]
    for err in errors:
        loc = ".".join(str(part) for part in err["loc"])
        err_type = err["type"]
        message = _pretty_error_message(err)
        lines.append(f"  - {loc}: {message} (error type: {err_type})")
    return "\n".join(lines)


# Pydantic error-type → formatter(value, ctx) → sentence. Module-level
# dict dispatch avoids a PLR0911 return-cascade in _pretty_error_message;
# unknown types fall through to Pydantic's own ``msg``.
_ERROR_FORMATTERS: dict[str, Any] = {
    "extra_forbidden": lambda value, _ctx: (
        f"unknown key (value was {value!r}). Either a typo, a key "
        "that belongs in a different section, or a field that was "
        "retired in a newer version — remove the line to resolve."
    ),
    "missing": lambda _value, _ctx: (
        "required field is missing. Add it or restore the default."
    ),
    "bool_parsing": lambda value, _ctx: f"expected true or false; got {value!r}.",
    "int_parsing": lambda value, _ctx: f"expected a whole number; got {value!r}.",
    "int_type": lambda value, _ctx: f"expected a whole number; got {value!r}.",
    "float_parsing": lambda value, _ctx: f"expected a decimal number; got {value!r}.",
    "float_type": lambda value, _ctx: f"expected a decimal number; got {value!r}.",
    "string_type": lambda value, _ctx: f"expected non-empty text; got {value!r}.",
    "string_too_short": lambda value, _ctx: f"expected non-empty text; got {value!r}.",
    "enum": lambda value, ctx: (
        f"must be one of "
        f"{ctx.get('expected') or ctx.get('permitted') or 'a valid option'}; "
        f"got {value!r}."
    ),
    "literal_error": lambda value, ctx: (
        f"must be one of "
        f"{ctx.get('expected') or ctx.get('permitted') or 'a valid option'}; "
        f"got {value!r}."
    ),
    "url_parsing": lambda value, _ctx: f"not a valid URL: {value!r}.",
    "url_scheme": lambda value, _ctx: f"not a valid URL: {value!r}.",
    "url_syntax_invalid": lambda value, _ctx: f"not a valid URL: {value!r}.",
}


def _pretty_error_message(err: dict[str, Any]) -> str:
    """Render one Pydantic error dict as a plain-English sentence.

    Dispatches via ``_ERROR_FORMATTERS`` for known types. ``value_error``
    (raised by field validators) gets special handling to strip
    Pydantic's ``"Value error, "`` prefix so the validator's own
    wording reads naturally. Unknown types return the raw Pydantic
    ``msg`` so diagnostic detail survives.
    """
    err_type = err["type"]
    value = err.get("input")
    ctx = err.get("ctx") or {}

    formatter = _ERROR_FORMATTERS.get(err_type)
    if formatter is not None:
        return formatter(value, ctx)
    if err_type == "value_error":
        # Strip Pydantic's "Value error, " prefix so the validator's wording reads naturally.
        msg = err.get("msg", "value rejected by custom validator.")
        return msg.removeprefix("Value error, ")
    # Unknown error type — fall back to Pydantic's own message
    return err.get("msg", str(err))


class _BaseSection(BaseModel):
    """Schema-section base providing retired-field auto-stripping.

    Subclasses with retired YAML keys override ``_DROPPED_FIELDS`` to a
    frozenset of those keys; the inherited ``_drop_retired_fields``
    validator pops them from the incoming dict before ``extra="forbid"``
    rejects them, so old config.yaml files keep loading on upgrade.
    """

    _DROPPED_FIELDS: ClassVar[frozenset[str]] = frozenset()

    @model_validator(mode="before")
    @classmethod
    def _drop_retired_fields(cls, data: Any) -> Any:
        """Strip retired keys from incoming YAML/dict before extra="forbid" bites."""
        if isinstance(data, dict):
            for key in cls._DROPPED_FIELDS:
                data.pop(key, None)
        return data


class TargetedCreatorSection(_BaseSection):
    """Settings for the creator(s) to download.

    ``usernames`` is a list because the CLI (``-u alice bob``) and the
    runtime state (``FanslyConfig.user_names: set[str] | None``) both
    support multiple creators. A comma-separated string — the legacy
    config.ini format — is coerced into a list at parse time.
    """

    model_config = ConfigDict(extra="forbid")

    # ``use_following_with_pagination`` was removed from the schema in v0.14:
    # it's a CLI-macro (``-ufp``) that toggles ``use_following`` AND
    # ``use_pagination_duplication`` together at runtime, never a real YAML
    # setting. Legacy YAMLs that still carry it are silently dropped on load.
    _DROPPED_FIELDS: ClassVar[frozenset[str]] = frozenset(
        {"use_following_with_pagination"}
    )

    # ALWAYS-rendered: usernames is the central pivot of the program; the YAML
    # always carries a usernames: line so operators see the slot. Type is
    # nullable because a fresh scaffold has no creators yet (renders as null);
    # CLI ``-u alice bob`` populates it at runtime.
    usernames: list[str] | None = Field(default=None, json_schema_extra=_ALWAYS)
    use_following: bool = False

    @field_validator("usernames", mode="before")
    @classmethod
    def _coerce_usernames(cls, v: Any) -> list[str] | None:
        """Accept a comma-separated string (config.ini legacy), a real list, or None."""
        if v is None:
            return None
        if isinstance(v, str):
            coerced = [name.strip() for name in v.split(",") if name.strip()]
            return coerced or None
        return v


class MyAccountSection(_BaseSection):
    """Fansly account credentials and authentication."""

    model_config = ConfigDict(extra="forbid")

    authorization_token: SecretStr = Field(
        default=SecretStr("ReplaceMe"), json_schema_extra=_ALWAYS
    )
    user_agent: str = Field(default="ReplaceMe", json_schema_extra=_ALWAYS)
    check_key: str = Field(default="qybZy9-fyszis-bybxyf", json_schema_extra=_ALWAYS)
    # username/password are conditional: only used by the user/pass auto-login
    # flow. Browser-import or hand-pasted ``authorization_token`` skip this
    # path entirely, in which case these slots stay absent from YAML.
    username: str | None = None
    password: SecretStr | None = None


class OptionsSection(_BaseSection):
    """Download behaviour and output options."""

    model_config = ConfigDict(extra="forbid")

    # Retired fields silently dropped during load (see config_options.md).
    # ``debug`` and ``trace`` were retired in v0.14 in favour of the CLI
    # ``-v`` / ``-vv`` runtime overrides and persistent per-handler level
    # configuration under ``logging.global.default_level``. Legacy YAMLs
    # carrying them load cleanly; the toggles silently no-op.
    _DROPPED_FIELDS: ClassVar[frozenset[str]] = frozenset(
        {
            "separate_metadata",
            "metadata_handling",
            "db_sync_commits",
            "db_sync_seconds",
            "db_sync_min_size",
            "debug",
            "trace",
        }
    )

    # ALWAYS-rendered: download_directory has a placeholder default that
    # MUST be edited; temp_folder lets operators see the override slot even
    # when blank. Everything else in this section is conditional — present
    # only when explicitly set.
    download_directory: str = Field(
        default="Local_directory", json_schema_extra=_ALWAYS
    )
    download_mode: DownloadMode = DownloadMode.NORMAL
    show_downloads: bool = True
    show_skipped_downloads: bool = True
    download_media_previews: bool = True
    open_folder_when_finished: bool = True
    separate_messages: bool = True
    separate_previews: bool = False
    separate_timeline: bool = True
    use_duplicate_threshold: bool = False
    use_pagination_duplication: bool = False
    use_folder_suffix: bool = True
    interactive: bool = True
    prompt_on_exit: bool = True
    timeline_retries: int = 1
    timeline_delay_seconds: int = 60
    api_max_retries: int = 10
    # Set to ``false`` to ignore the creator_content_unchanged short-circuit
    # in download/timeline.py and download/wall.py — forces a full scan even
    # when TimelineStats counts and wall structure match the DB. Conditional
    # by default; absent from scaffolded YAML.
    respect_timeline_stats: bool = True
    rate_limiting_enabled: bool = True
    rate_limiting_adaptive: bool = True
    rate_limiting_requests_per_minute: int = 60
    rate_limiting_burst_size: int = 10
    rate_limiting_retry_after_seconds: int = 30
    rate_limiting_backoff_factor: float = 1.5
    rate_limiting_max_backoff_seconds: int = 300
    temp_folder: str | None = Field(default=None, json_schema_extra=_ALWAYS)

    @field_validator("download_mode", mode="before")
    @classmethod
    def _coerce_download_mode(cls, v: Any) -> DownloadMode:
        """Accept any case spelling, e.g. 'normal', 'NORMAL', 'Normal'."""
        if isinstance(v, str):
            return DownloadMode(v.upper())
        return v


class PostgresSection(_BaseSection):
    """PostgreSQL connection configuration for asyncpg."""

    model_config = ConfigDict(extra="forbid")

    # ALWAYS-rendered: connection coordinates operators almost always need
    # to see/edit. pg_user is nullable to support trust-auth setups (no
    # explicit user). pg_password is nullable until set.
    pg_host: str = Field(default="localhost", json_schema_extra=_ALWAYS)
    pg_port: int = 5432
    pg_database: str = Field(default="fansly_metadata", json_schema_extra=_ALWAYS)
    pg_user: str | None = Field(default="fansly_user", json_schema_extra=_ALWAYS)
    pg_password: SecretStr | None = Field(default=None, json_schema_extra=_ALWAYS)
    pg_sslmode: str = "prefer"
    pg_sslcert: str | None = None
    pg_sslkey: str | None = None
    pg_sslrootcert: str | None = None
    pg_pool_size: int = 5
    pg_max_overflow: int = 10
    pg_pool_timeout: int = 30


class CacheSection(_BaseSection):
    """Device ID cache for Fansly API authentication.

    These values are managed at runtime by the API layer; users should not
    need to edit them manually.
    """

    model_config = ConfigDict(extra="forbid")

    device_id: str | None = None
    device_id_timestamp: int | None = None


# ── Logging type aliases ────────────────────────────────────────
LogLevel = Literal["TRACE", "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
RotationWhen = Literal["s", "m", "h", "d", "midnight"]
Compression = Literal["gz", "bz2", "xz"]


class ConsoleLoggerEntry(_BaseSection):
    """Console-style logger (Rich-formatted stdout/stderr).

    Rejects ``level: TRACE`` — TRACE never hits the console, it's a
    file-only level.
    """

    enabled: bool = True
    level: LogLevel | None = None
    format: str | None = None

    @field_validator("level")
    @classmethod
    def _reject_trace_on_console(cls, v: str | None) -> str | None:
        if v == "TRACE":
            raise ValueError(
                "Console loggers cannot have level='TRACE' (TRACE is file-only)"
            )
        return v


class FileLoggerEntry(_BaseSection):
    """Rotating-file logger.

    Size and time rotation axes are orthogonal — both can be active
    simultaneously (matches ``SizeTimeRotatingHandler``). Either or both
    may be ``None`` to disable that axis; per-field ``None`` falls
    through to the matching ``default_*`` on ``LoggingGlobalSection``.
    """

    enabled: bool = True
    filename: str
    level: LogLevel | None = None
    format: str | None = None
    max_size: int | None = None
    rotation_when: RotationWhen | None = None
    rotation_interval: int | None = None
    utc: bool | None = None
    backup_count: int | None = None
    compression: Compression | None = None
    keep_uncompressed: int | None = None


class MainLogEntry(FileLoggerEntry):
    filename: str = "fansly_downloader_ng.log"


class JsonLogEntry(FileLoggerEntry):
    filename: str = "fansly_downloader_ng_json.log"


class StashFileEntry(FileLoggerEntry):
    filename: str = "stash.log"


class DbLogEntry(FileLoggerEntry):
    filename: str = "sqlalchemy.log"


class TraceLogEntry(FileLoggerEntry):
    enabled: bool = False
    filename: str = "trace.log"
    level: LogLevel | None = "TRACE"


class WebsocketLogEntry(FileLoggerEntry):
    filename: str = "websocket.log"


class LoggingGlobalSection(_BaseSection):
    """Cross-cutting defaults inherited by any per-logger field that is ``None``.

    Defaults match FDNG's pre-config-driven behavior: 100 MB size cap,
    hourly UTC time rotation, 5 backups, gzip compression, 2 most-recent
    kept uncompressed for live tail-ability.

    ``trace`` is a file-only toggle — when true the trace handler is
    enabled at TRACE; console handlers ignore it (console rejects TRACE
    by schema validation). Runtime ``-vv`` flips the same switch and also
    forces every other handler to TRACE; YAML toggle alone only opens the
    trace file sink at TRACE without overriding peer handlers.
    """

    # ``debug`` was retired in v0.14: it duplicated ``default_level: DEBUG``
    # at YAML level and ``-v`` at runtime, with no behavioral difference.
    _DROPPED_FIELDS: ClassVar[frozenset[str]] = frozenset({"verbose", "debug"})

    directory: str | None = Field(default=None, json_schema_extra=_ALWAYS)
    trace: bool = False
    default_level: LogLevel = "INFO"
    default_format: str | None = None
    default_max_size: int | None = 100 * 1024 * 1024  # 100 MB
    default_rotation_when: RotationWhen | None = "h"
    default_rotation_interval: int = 1
    default_utc: bool = True
    default_backup_count: int | None = 5
    default_compression: Compression | None = "gz"
    default_keep_uncompressed: int = 2


class LoggingSection(_BaseSection):
    """Eight named loggers + global defaults.

    Two console handlers (``rich_handler``, ``stash_console``) and six
    file handlers (``main_log``, ``json``, ``stash_file``, ``db``,
    ``trace``, ``websocket``). Each entry has its own ``enabled`` /
    ``level`` / ``format`` plus — for file entries — independent size
    and time rotation axes. Unset per-logger fields fall through to
    ``global.default_*``.

    The attribute ``global_`` aliases YAML key ``global`` because
    ``global`` is a Python keyword; same for ``json_`` aliasing ``json``
    (which would also shadow Pydantic's ``BaseModel.json()`` serialiser).
    ``populate_by_name=True`` lets either form load.
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    global_: LoggingGlobalSection = Field(
        default_factory=LoggingGlobalSection,
        alias="global",
        json_schema_extra=_ALWAYS,
    )
    rich_handler: ConsoleLoggerEntry = Field(
        default_factory=ConsoleLoggerEntry,
        json_schema_extra=_ALWAYS,
    )
    main_log: MainLogEntry = Field(
        default_factory=MainLogEntry,
        json_schema_extra=_ALWAYS,
    )
    json_: JsonLogEntry = Field(
        default_factory=JsonLogEntry,
        alias="json",
        json_schema_extra=_ALWAYS,
    )
    stash_console: ConsoleLoggerEntry = Field(
        default_factory=ConsoleLoggerEntry,
        json_schema_extra=_ALWAYS,
    )
    stash_file: StashFileEntry = Field(
        default_factory=StashFileEntry,
        json_schema_extra=_ALWAYS,
    )
    db: DbLogEntry = Field(
        default_factory=DbLogEntry,
        json_schema_extra=_ALWAYS,
    )
    trace: TraceLogEntry = Field(
        default_factory=TraceLogEntry,
        json_schema_extra=_ALWAYS,
    )
    websocket: WebsocketLogEntry = Field(
        default_factory=WebsocketLogEntry,
        json_schema_extra=_ALWAYS,
    )

    @model_validator(mode="before")
    @classmethod
    def _migrate_legacy_flat_levels(cls, data: Any) -> Any:
        """Lift the pre-v0.14 flat shape into the nested per-logger entries.

        Old::

            logging:
              sqlalchemy: INFO
              stash_console: INFO
              stash_file: INFO
              textio: INFO
              websocket: INFO
              json: INFO

        New::

            logging:
              global: {default_level: INFO, ...}
              db: {level: INFO}
              stash_console: {level: INFO}
              stash_file: {level: INFO}
              main_log: {level: INFO}
              rich_handler: {level: INFO}
              websocket: {level: INFO}
              json: {level: INFO}

        Note ``textio`` seeds BOTH ``main_log.level`` and
        ``rich_handler.level`` because the legacy key drove both the
        main file handler and the rich console handler.
        """
        if not isinstance(data, dict):
            return data
        single_target: dict[str, tuple[str, str]] = {
            "sqlalchemy": ("db", "level"),
            "stash_console": ("stash_console", "level"),
            "stash_file": ("stash_file", "level"),
            "websocket": ("websocket", "level"),
            "json_level": ("json", "level"),
        }
        migrated = dict(data)
        for legacy_key, (section, field) in single_target.items():
            if legacy_key in migrated and isinstance(migrated[legacy_key], str):
                value = migrated.pop(legacy_key)
                target = migrated.setdefault(section, {})
                if isinstance(target, dict):
                    target.setdefault(field, value)
        # textio seeds two targets (main file + rich console)
        if "textio" in migrated and isinstance(migrated["textio"], str):
            value = migrated.pop("textio")
            for section in ("main_log", "rich_handler"):
                target = migrated.setdefault(section, {})
                if isinstance(target, dict):
                    target.setdefault("level", value)
        # Legacy `json: INFO` (string) → `json: {level: INFO}`; leave dict alone
        if isinstance(migrated.get("json"), str):
            migrated["json"] = {"level": migrated["json"]}

        # Sanitize per-entry level strings before Literal validation. Pre-v0.14
        # ``load_config`` accepted any string + soft-fell-back to ``INFO`` on
        # invalid values; preserve that lenient behavior so legacy YAMLs with
        # typos still load.
        valid_levels = {"TRACE", "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        for entry_key in (
            "rich_handler",
            "main_log",
            "json",
            "stash_console",
            "stash_file",
            "db",
            "trace",
            "websocket",
        ):
            entry = migrated.get(entry_key)
            if not isinstance(entry, dict):
                continue
            level = entry.get("level")
            if not isinstance(level, str):
                continue
            normalized = level.upper()
            entry["level"] = normalized if normalized in valid_levels else "INFO"
        return migrated

    @model_validator(mode="after")
    def _link_trace_toggles(self) -> Self:
        """Keep ``global.trace`` and ``trace.enabled`` in sync.

        Either toggle being True means trace logging is active. Setting
        one in YAML implies the other — operators can write whichever
        feels natural and they end up consistent.
        """
        if self.global_.trace or self.trace.enabled:
            self.global_.trace = True
            self.trace.enabled = True
        return self


class StashContextSection(_BaseSection):
    """Stash media server connection settings.

    This section is optional — it is only written when the Stash integration
    is active. If absent from the config file, the Stash integration is
    disabled.
    """

    model_config = ConfigDict(extra="forbid")

    # When the section is present, all four connection fields are ALWAYS-
    # rendered (operators opted in to Stash; they need to see all four
    # slots). mapped_path stays conditional because it's optional even
    # within an active Stash configuration.
    scheme: str = Field(default="http", json_schema_extra=_ALWAYS)
    host: str = Field(default="localhost", json_schema_extra=_ALWAYS)
    port: int = Field(default=9999, json_schema_extra=_ALWAYS)
    apikey: str = Field(default="", json_schema_extra=_ALWAYS)
    mapped_path: str | None = None
    override_dldir_w_mapped: bool = False
    require_stash_only_mode: bool = False

    @model_validator(mode="after")
    def _override_requires_mapped_path(self) -> StashContextSection:
        """override_dldir_w_mapped only has meaning when mapped_path is set.

        Without a mapped_path, the override has nothing to widen the path
        filter to — the flag would silently no-op. Reject at load time so
        the user fixes one knob, not chases a behavior that never engages.
        """
        if self.override_dldir_w_mapped and self.mapped_path is None:
            raise ValueError(
                "stash_context.override_dldir_w_mapped=true requires "
                "stash_context.mapped_path to be set. Either set mapped_path "
                "to your Stash-visible fansly root, or set "
                "override_dldir_w_mapped=false."
            )
        return self


class MonitoringSection(_BaseSection):
    """Monitoring daemon configuration (WebSocket + polling loop)."""

    model_config = ConfigDict(extra="forbid")

    # Retired fields silently dropped during load (see config_options.md).
    _DROPPED_FIELDS: ClassVar[frozenset[str]] = frozenset(
        {
            "enabled",
            "websocket_subprocess",
        }
    )

    daemon_mode: bool = False
    active_duration_minutes: int = 60
    idle_duration_minutes: int = 120
    hidden_duration_minutes: int = 300
    timeline_poll_active_seconds: int = 180
    timeline_poll_idle_seconds: int = 600
    story_poll_active_seconds: int = 30
    story_poll_idle_seconds: int = 300
    session_baseline: datetime | None = None
    unrecoverable_error_timeout_seconds: int = 3600
    dashboard_enabled: bool = True
    heartbeat_interval_minutes: int = 15
    livestream_recording_enabled: bool = False
    livestream_poll_interval_seconds: int = 30
    livestream_manifest_poll_interval_seconds: int = Field(default=3, ge=1, le=15)

    @field_validator("session_baseline", mode="before")
    @classmethod
    def _coerce_session_baseline(cls, v: Any) -> datetime | None:
        """Coerce naive datetimes to UTC-aware; pass through aware datetimes."""
        if v is None:
            return None
        if isinstance(v, datetime):
            if v.tzinfo is None:
                return v.replace(tzinfo=UTC)
            return v
        return v


class LogicSection(_BaseSection):
    """Regex patterns for extracting check-key and main.js URL from Fansly."""

    model_config = ConfigDict(extra="forbid")

    check_key_pattern: str = r"this\.checkKey_\s*=\s*[\"']([^\"']+)[\"']"
    main_js_pattern: str = r"\ssrc\s*=\s*\"(main\..*?\.js)\""


class ConfigSchema(_BaseSection):
    """Root configuration schema for config.yaml.

    All sections are optional at parse time; defaults are used when a section
    is absent from the file.  ``extra="forbid"`` is enforced on both the root
    and every section model so typos raise a clear ``ValidationError`` rather
    than being silently ignored.

    Usage::

        schema = ConfigSchema.load_yaml("config.yaml")
        schema.options.download_mode  # DownloadMode.NORMAL
        schema.my_account.authorization_token.get_secret_value()  # "abc123"
        schema.dump_yaml("config.yaml")  # writes back, preserving comments
    """

    model_config = ConfigDict(extra="forbid")

    # Sections with ALWAYS-rendered leaves stay non-Optional: the cascade-
    # up rule in _sync_to_map keeps these blocks visible by virtue of their
    # always-leaves. ``logging`` has no always-leaves but stays default_factory
    # for ergonomic runtime access; cascade-up still omits it from YAML when
    # nothing is set inside.
    targeted_creator: TargetedCreatorSection = Field(
        default_factory=TargetedCreatorSection
    )
    my_account: MyAccountSection = Field(default_factory=MyAccountSection)
    options: OptionsSection = Field(default_factory=OptionsSection)
    postgres: PostgresSection = Field(default_factory=PostgresSection)
    logging: LoggingSection = Field(default_factory=LoggingSection)
    # Optional, like stash_context — present in YAML only when something
    # has been written to them. Auto-instantiated via default_factory so
    # runtime code (``schema.cache.device_id = ...``) doesn't need null
    # guards before mutating; the dump path's cascade-up determines whether
    # the section appears on disk based on whether any child field is set.
    cache: CacheSection | None = Field(default_factory=CacheSection)
    monitoring: MonitoringSection | None = Field(default_factory=MonitoringSection)
    logic: LogicSection | None = Field(default_factory=LogicSection)
    stash_context: StashContextSection | None = None

    # Internal storage for the live CommentedMap so we can preserve comments.
    _yaml_map: CommentedMap | None = PrivateAttr(default=None)

    @model_validator(mode="after")
    def _instantiate_managed_optional_sections(self) -> ConfigSchema:
        """Coerce explicit YAML ``null`` for managed sections back to instances.

        ``cache``/``monitoring``/``logic`` are typed Optional so the dump
        path can omit them, but runtime code accesses ``schema.cache.X``
        without null guards. If a YAML file has ``cache: null`` literally,
        coerce that back to an empty ``CacheSection()`` so the runtime
        invariant holds. ``stash_context`` is genuinely Optional and is
        intentionally NOT coerced — None there means "Stash not configured."
        """
        if self.cache is None:
            self.cache = CacheSection()
        if self.monitoring is None:
            self.monitoring = MonitoringSection()
        if self.logic is None:
            self.logic = LogicSection()
        return self

    @classmethod
    def load_yaml(cls, path: Path | str) -> ConfigSchema:
        """Load a ConfigSchema from a YAML file, preserving comments.

        Args:
            path: Path to the YAML file.

        Returns:
            Fully validated schema instance with comment map retained.

        Raises:
            ValueError: YAML is malformed or schema validation failed.
            FileNotFoundError: *path* does not exist.
        """
        path = Path(path)
        y = _make_yaml()
        try:
            with path.open("r", encoding="utf-8") as fh:
                data: CommentedMap = y.load(fh)
        except YAMLError as exc:
            raise ValueError(f"Malformed YAML in {path}: {exc}") from exc

        if data is None:
            data = CommentedMap()

        # Convert CommentedMap → plain dict for Pydantic
        raw: dict[str, Any] = _commentedmap_to_dict(data)

        try:
            instance = cls.model_validate(raw)
        except ValidationError as exc:
            raise ValueError(_format_validation_error(exc, path)) from exc
        except Exception as exc:
            # Non-Pydantic errors (shouldn't happen here, but keep a
            # catch so the user still gets a message rather than a raw
            # traceback at the top level).
            raise ValueError(f"Configuration error in {path}: {exc}") from exc

        instance._yaml_map = data
        return instance

    def dump_yaml(self, path: Path | str) -> None:
        """Write the schema back to *path*, preserving any loaded comments.

        If this instance was created in-memory (not via ``load_yaml``), a fresh
        CommentedMap is built from the current model state.

        Args:
            path: Destination path. The file is created or overwritten.
        """
        path = Path(path)
        y = _make_yaml()

        if self._yaml_map is None:
            self._yaml_map = CommentedMap()

        _sync_to_map(self, self._yaml_map)

        with path.open("w", encoding="utf-8") as fh:
            y.dump(self._yaml_map, fh)

    def dump_yaml_string(self) -> str:
        """Return the YAML representation as a string (useful for tests)."""
        y = _make_yaml()
        if self._yaml_map is None:
            self._yaml_map = CommentedMap()
        _sync_to_map(self, self._yaml_map)
        buf = io.StringIO()
        y.dump(self._yaml_map, buf)
        return buf.getvalue()


def _commentedmap_to_dict(obj: Any) -> Any:
    """Recursively convert CommentedMap / CommentedSeq to plain Python types."""
    if isinstance(obj, dict):
        return {k: _commentedmap_to_dict(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_commentedmap_to_dict(item) for item in obj]
    return obj


def _section_to_map(section: BaseModel, existing: CommentedMap | None) -> CommentedMap:
    """Convert a Pydantic section model into a CommentedMap.

    If *existing* is provided its keys are updated in-place so that YAML
    comments on each key survive the writeback.

    Render policy:
      - Fields in ``model_fields_set`` (operator explicitly set them or
        runtime mutated them) are always written.
      - Fields tagged ``json_schema_extra={"render": "always"}`` are written
        even at default value — they're the visible operator-knob slots.
      - Everything else is conditional and stays absent from YAML.

    Fields previously written but no longer in either set are deleted from
    *existing* so the on-disk file converges to the schema's view of truth.
    """
    # Explicit annotation: Pylance's inference on ruamel's partial stubs
    # flags the subsequent `target[field_name] = …` as "None is not
    # subscriptable" without it.
    target: CommentedMap = (
        existing if isinstance(existing, CommentedMap) else CommentedMap()
    )
    fields_set = section.model_fields_set
    field_infos = section.__class__.model_fields
    written: set[str] = set()
    dumped = section.model_dump(mode="python")
    for field_name, field_value in dumped.items():
        info = field_infos.get(field_name)
        if field_name not in fields_set and not _is_always(info):
            continue
        raw_attr = getattr(section, field_name)
        # Match `model_dump(by_alias=True)`'s key priority so YAML round-trips.
        out_key = (
            (info.serialization_alias or info.alias or field_name)
            if info
            else field_name
        )
        raw_value = _python_to_yaml_value(field_value, raw_attr)
        target[out_key] = raw_value
        written.add(out_key)
    # Delete keys that previously existed but are no longer rendered. Without
    # this, a field that flips from "user-set" → "back to default+conditional"
    # would leave a stale value behind in the YAML.
    for key in [k for k in list(target.keys()) if k not in written]:
        del target[key]
    return target


def _python_to_yaml_value(dump_value: Any, raw_attr: Any) -> Any:
    """Convert a Python value to a YAML-safe form.

    - ``SecretStr`` → plain string (the secret text)
    - ``DownloadMode`` → string value
    - ``datetime`` → pass through (ruamel.yaml rt mode serialises natively)
    - ``list[tuple[int, int]]`` → list of lists
    - Everything else: pass through
    """
    # SecretStr: use the actual secret value
    if isinstance(raw_attr, SecretStr):
        return raw_attr.get_secret_value()
    if isinstance(raw_attr, DownloadMode):
        return str(raw_attr)
    # datetime: pass through unchanged — ruamel.yaml rt mode handles it natively
    if isinstance(raw_attr, datetime):
        return raw_attr
    if isinstance(dump_value, list) and all(
        isinstance(item, (list, tuple)) for item in dump_value
    ):
        return [list(item) for item in dump_value]
    return dump_value


def _sync_to_map(schema: ConfigSchema, root: CommentedMap) -> None:
    """Synchronise all schema sections into *root* CommentedMap in-place.

    Cascade-up rule: a section's key is written only when ``_section_to_map``
    produces a non-empty map for it. Sections with always-rendered leaves
    naturally satisfy this; sections like ``logging`` and the optional
    ``cache`` / ``monitoring`` / ``logic`` are omitted entirely until at
    least one of their fields has been explicitly set or marked always.
    """
    _section_map: dict[str, BaseModel | None] = {
        "targeted_creator": schema.targeted_creator,
        "my_account": schema.my_account,
        "options": schema.options,
        "postgres": schema.postgres,
        "cache": schema.cache,
        "logging": schema.logging,
        "monitoring": schema.monitoring,
        "logic": schema.logic,
        # stash_context is appended last so it lands at the bottom of the
        # YAML when first written. Conditional: only included when not None.
        "stash_context": schema.stash_context,
    }

    for key, section in _section_map.items():
        if section is None:
            # Optional section absent from runtime — drop the YAML key too
            # so the file converges to the schema's view of truth.
            if key in root:
                del root[key]
            continue
        existing = root.get(key) if isinstance(root.get(key), CommentedMap) else None
        new_map = _section_to_map(section, existing)
        if not new_map:
            # Cascade-up: empty section dump → omit the section entirely.
            if key in root:
                del root[key]
            continue
        root[key] = new_map
