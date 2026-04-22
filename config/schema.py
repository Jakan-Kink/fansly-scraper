"""Pydantic schema for config.yaml — typed, validated, round-trip-safe.

Load with ``ConfigSchema.load_yaml(path)``.  Write back with
``schema.dump_yaml(path)``.  Comments in the YAML file are preserved across
load → modify → dump cycles because the loaded ``ruamel.yaml`` CommentedMap
is stored on the instance and mutated in-place at dump time.
"""

from __future__ import annotations

import io
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar

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


class TargetedCreatorSection(BaseModel):
    """Settings for the creator(s) to download.

    ``usernames`` is a list because the CLI (``-u alice bob``) and the
    runtime state (``FanslyConfig.user_names: set[str] | None``) both
    support multiple creators. A comma-separated string — the legacy
    config.ini format — is coerced into a list at parse time.
    """

    model_config = ConfigDict(extra="forbid")

    usernames: list[str] = Field(default_factory=lambda: ["replaceme"])
    use_following: bool = False
    use_following_with_pagination: bool = False

    @field_validator("usernames", mode="before")
    @classmethod
    def _coerce_usernames(cls, v: Any) -> list[str]:
        """Accept a comma-separated string (config.ini legacy) or a real list."""
        if isinstance(v, str):
            return [name.strip() for name in v.split(",") if name.strip()]
        return v


class MyAccountSection(BaseModel):
    """Fansly account credentials and authentication."""

    model_config = ConfigDict(extra="forbid")

    # authorization_token is SecretStr so it is never accidentally printed
    # in logs or tracebacks; call .get_secret_value() to read the raw token.
    authorization_token: SecretStr = SecretStr("ReplaceMe")
    # user_agent is a plain str — it's a public HTTP header, not a secret.
    user_agent: str = "ReplaceMe"
    check_key: str = "qybZy9-fyszis-bybxyf"
    username: str | None = None
    password: SecretStr | None = None


class OptionsSection(BaseModel):
    """Download behaviour and output options."""

    model_config = ConfigDict(extra="forbid")

    # Retired fields silently dropped during load so upgrading doesn't
    # require manual config edits. Keep entries for at least one release.
    _DROPPED_FIELDS: ClassVar[frozenset[str]] = frozenset(
        {
            # Removed: legacy SQLite-era flag that was no-op under Postgres.
            # _build_connection_url never used creator_name, so the flag had
            # no effect regardless of setting. See git history for details.
            "separate_metadata",
            # Removed: no runtime code branches on SIMPLE vs ADVANCED.
            "metadata_handling",
            # Removed: SQLite-era write-sync tuning knobs, never read
            # under Postgres. CLI flags (--db-sync-commits etc.) and
            # INI keys are still silently accepted for backward compat.
            "db_sync_commits",
            "db_sync_seconds",
            "db_sync_min_size",
        }
    )

    @model_validator(mode="before")
    @classmethod
    def _drop_retired_fields(cls, data: Any) -> Any:
        """Strip retired keys from incoming YAML/dict before extra="forbid" bites."""
        if isinstance(data, dict):
            for key in cls._DROPPED_FIELDS:
                data.pop(key, None)
        return data

    download_directory: str = "Local_directory"
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
    debug: bool = False
    trace: bool = False
    timeline_retries: int = 1
    timeline_delay_seconds: int = 60
    api_max_retries: int = 10
    rate_limiting_enabled: bool = True
    rate_limiting_adaptive: bool = True
    rate_limiting_requests_per_minute: int = 60
    rate_limiting_burst_size: int = 10
    rate_limiting_retry_after_seconds: int = 30
    rate_limiting_backoff_factor: float = 1.5
    rate_limiting_max_backoff_seconds: int = 300
    temp_folder: str | None = None

    @field_validator("download_mode", mode="before")
    @classmethod
    def _coerce_download_mode(cls, v: Any) -> DownloadMode:
        """Accept any case spelling, e.g. 'normal', 'NORMAL', 'Normal'."""
        if isinstance(v, str):
            return DownloadMode(v.upper())
        return v


class PostgresSection(BaseModel):
    """PostgreSQL connection configuration for asyncpg.

    SSL settings are accepted but currently NOT wired to
    ``asyncpg.create_pool`` — see ``metadata/database.py``. A follow-up
    task should pass ``ssl=`` to the pool when any ``pg_ssl*`` value is
    set. ``pg_max_overflow`` and ``pg_pool_timeout`` are legacy SQLAlchemy
    pool settings kept for round-trip parity with config.ini; asyncpg's pool
    uses only ``min_size``/``max_size`` so these values are stored but not
    wired to the pool.
    """

    model_config = ConfigDict(extra="forbid")

    pg_host: str = "localhost"
    pg_port: int = 5432
    pg_database: str = "fansly_metadata"
    pg_user: str = "fansly_user"
    pg_password: SecretStr | None = None
    pg_sslmode: str = "prefer"
    pg_sslcert: str | None = None
    pg_sslkey: str | None = None
    pg_sslrootcert: str | None = None
    pg_pool_size: int = 5
    pg_max_overflow: int = 10
    pg_pool_timeout: int = 30


class CacheSection(BaseModel):
    """Device ID cache for Fansly API authentication.

    These values are managed at runtime by the API layer; users should not
    need to edit them manually.
    """

    model_config = ConfigDict(extra="forbid")

    device_id: str | None = None
    device_id_timestamp: int | None = None


class LoggingSection(BaseModel):
    """Log level configuration for named loggers.

    The YAML key for the JSON logger is ``json:``, but the Python
    attribute is ``json_level`` because ``json`` would shadow Pydantic's
    built-in ``BaseModel.json()`` serialisation method — accessing
    ``section.json`` would silently return the log-level string instead
    of a serialiser. ``populate_by_name=True`` lets callers use either
    name when constructing from code.
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    sqlalchemy: str = "INFO"
    stash_console: str = "INFO"
    stash_file: str = "INFO"
    textio: str = "INFO"
    websocket: str = "INFO"
    json_level: str = Field("INFO", alias="json", serialization_alias="json")


class StashContextSection(BaseModel):
    """Stash media server connection settings.

    This section is optional — it is only written when the Stash integration
    is active. If absent from the config file, the Stash integration is
    disabled.
    """

    model_config = ConfigDict(extra="forbid")

    scheme: str = "http"
    host: str = "localhost"
    port: int = 9999
    apikey: str = ""


class MonitoringSection(BaseModel):
    """Monitoring daemon configuration (WebSocket + polling loop).

    INTERRUPT_EVENTS is NOT exposed here — it's a protocol constant
    (which Fansly WebSocket events wake the daemon from hidden state)
    that lives in ``daemon/simulator.py``. Editing it breaks the daemon,
    so it's intentionally not user-tunable.
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    # When True, enter the post-batch monitoring daemon after the normal
    # batch download completes.  Mirrors the --daemon / -d CLI flag.
    daemon_mode: bool = False
    active_duration_minutes: int = 60
    idle_duration_minutes: int = 120
    hidden_duration_minutes: int = 300
    timeline_poll_active_seconds: int = 180
    timeline_poll_idle_seconds: int = 600
    story_poll_active_seconds: int = 30
    story_poll_idle_seconds: int = 300
    # Optional per-run baseline. When set, should_process_creator uses this
    # value instead of each creator's stored lastCheckedAt. A very old value
    # (e.g. 2000-01-01) effectively forces a full pass for every creator.
    # When None (default), uses each creator's own MonitorState.lastCheckedAt.
    session_baseline: datetime | None = None
    # Fatal error escalation: if the daemon has had NO successful operation
    # for this many seconds, exit with DAEMON_UNRECOVERABLE. Rate-limiter,
    # transient 5xx, and network blips are NOT escalated as long as some
    # operation (poll, WS ping-pong, or dispatch) still succeeds within the
    # window. Default 1 hour.
    unrecoverable_error_timeout_seconds: int = 3600
    # Rich-based live dashboard showing simulator state and per-loop
    # countdown bars while the daemon runs. Default on — set False to
    # disable when piping output through tools that don't render ANSI
    # escape sequences cleanly.
    dashboard_enabled: bool = True

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


class LogicSection(BaseModel):
    """Regex patterns for extracting check-key and main.js URL from Fansly."""

    model_config = ConfigDict(extra="forbid")

    check_key_pattern: str = r"this\.checkKey_\s*=\s*[\"']([^\"']+)[\"']"
    main_js_pattern: str = r"\ssrc\s*=\s*\"(main\..*?\.js)\""


class ConfigSchema(BaseModel):
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

    targeted_creator: TargetedCreatorSection = Field(
        default_factory=TargetedCreatorSection
    )
    my_account: MyAccountSection = Field(default_factory=MyAccountSection)
    options: OptionsSection = Field(default_factory=OptionsSection)
    postgres: PostgresSection = Field(default_factory=PostgresSection)
    cache: CacheSection = Field(default_factory=CacheSection)
    logging: LoggingSection = Field(default_factory=LoggingSection)
    stash_context: StashContextSection | None = None
    monitoring: MonitoringSection = Field(default_factory=MonitoringSection)
    logic: LogicSection = Field(default_factory=LogicSection)

    # Internal storage for the live CommentedMap so we can preserve comments.
    _yaml_map: CommentedMap | None = PrivateAttr(default=None)

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
    """
    # Explicit annotation: Pylance's inference on ruamel's partial stubs
    # flags the subsequent `target[field_name] = …` as "None is not
    # subscriptable" without it.
    target: CommentedMap = (
        existing if isinstance(existing, CommentedMap) else CommentedMap()
    )
    for field_name, field_value in section.model_dump(mode="python").items():
        raw_value = _python_to_yaml_value(field_value, getattr(section, field_name))
        target[field_name] = raw_value
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
    """Synchronise all schema sections into *root* CommentedMap in-place."""
    _section_map: dict[str, BaseModel | None] = {
        "targeted_creator": schema.targeted_creator,
        "my_account": schema.my_account,
        "options": schema.options,
        "postgres": schema.postgres,
        "cache": schema.cache,
        "logging": schema.logging,
        "monitoring": schema.monitoring,
        "logic": schema.logic,
    }
    # stash_context is optional — only write it when configured
    if schema.stash_context is not None:
        _section_map["stash_context"] = schema.stash_context
    elif "stash_context" in root:
        # If stash_context was removed at runtime, drop it from the map too
        del root["stash_context"]

    for key, section in _section_map.items():
        if section is None:
            continue
        existing = root.get(key) if isinstance(root.get(key), CommentedMap) else None
        root[key] = _section_to_map(section, existing)
