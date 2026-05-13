"""Configuration File Manipulation"""

import configparser
import os
from pathlib import Path
from typing import Any

from config.fanslyconfig import FanslyConfig
from config.loader import load_or_migrate
from config.logging import init_logging_config, set_debug_enabled, textio_logger
from config.schema import ConfigSchema
from errors import ConfigError


def save_config_or_raise(config: FanslyConfig) -> bool:
    """Tries to save the configuration to ``config.yaml`` (or ``config.ini``
    for legacy setups) or raises a ``ConfigError`` otherwise.

    :param config: The program configuration.
    :type config: FanslyConfig

    :return: True if configuration was successfully written.
    :rtype: bool

    :raises ConfigError: When the configuration file could not be saved.
        This may be due to invalid path issues or permission/security
        software problems.
    """
    if not config._save_config():
        raise ConfigError(
            f"Internal error: Configuration data could not be saved to '{config.config_path}'. "
            "Invalid path or permission/security software problem."
        )
    return True


def parse_items_from_line(line: str) -> list[str]:
    """Parses a list of items (eg. creator names) from a single line
    as eg. read from a configuration file.

    :param str line: A single line containing eg. user names
        separated by either spaces or commas (,).

    :return: A list of items (eg. names) parsed from the line.
    :rtype: list[str]
    """
    names = line.split(",") if "," in line else line.split()
    return names


def sanitize_creator_names(names: list[str]) -> set[str]:
    """Sanitizes a list of creator names after they have been
    parsed from a configuration file.

    This will:

    * remove empty names
    * remove leading/trailing whitespace from a name
    * remove a leading @ from a name
    * remove duplicates
    * lower-case each name (for de-duplication to work)

    :param list[str] names: A list of names to process.

    :return: A set of unique, sanitized creator names.
    :rtype: set[str]
    """
    return {name.strip().removeprefix("@").lower() for name in names if name.strip()}


def username_has_valid_length(name: str) -> bool:
    if name is None:
        return False

    return len(name) >= 4 and len(name) <= 30


def username_has_valid_chars(name: str) -> bool:
    if name is None:
        return False

    invalid_chars = set(name) - set(
        "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
    )

    return not invalid_chars


def _populate_config_from_schema(config: FanslyConfig, schema: ConfigSchema) -> None:
    """Copy all values from a loaded ``ConfigSchema`` onto ``FanslyConfig`` attributes.

    This is the bridge layer: ``FanslyConfig`` is a runtime-mutable facade;
    ``ConfigSchema`` is the disk format.  Fields in the schema that carry
    ``SecretStr`` values are unwrapped to plain ``str`` here so that the
    existing call-sites that read ``config.token`` etc. continue to work
    unchanged.
    """
    # --- TargetedCreator ---
    # Only override if not already set via command-line. ``usernames`` is
    # nullable in the schema (fresh scaffold has no creators yet) — skip
    # the sanitize call when it's None so config.user_names stays None
    # until something actually populates it.
    if config.user_names is None:
        raw_names = schema.targeted_creator.usernames
        if raw_names:
            config.user_names = sanitize_creator_names(raw_names)

    config.use_following = schema.targeted_creator.use_following

    # --- MyAccount ---
    config.token = schema.my_account.authorization_token.get_secret_value()
    config.user_agent = schema.my_account.user_agent

    # Default check key (current as of 2025-10-25)
    default_check_key = "oybZy8-fySzis-bubayf"
    raw_check_key = schema.my_account.check_key
    # Replace known outdated check keys with current default
    if raw_check_key in (
        "negwij-zyZnek-wavje1",
        "negwij-zyZnak-wavje1",
        "qybZy9-fyszis-bybxyf",  # Old default from schema default
    ):
        config.check_key = default_check_key
    else:
        config.check_key = raw_check_key

    config.username = schema.my_account.username
    if schema.my_account.password is not None:
        config.password = schema.my_account.password.get_secret_value()
    else:
        config.password = None

    # --- Options ---
    opts = schema.options

    config.download_directory = Path(opts.download_directory)

    config.download_mode = opts.download_mode

    # Booleans
    config.download_media_previews = opts.download_media_previews
    config.open_folder_when_finished = opts.open_folder_when_finished
    config.separate_messages = opts.separate_messages
    config.separate_previews = opts.separate_previews
    config.separate_timeline = opts.separate_timeline
    config.show_downloads = opts.show_downloads
    config.show_skipped_downloads = opts.show_skipped_downloads
    config.use_duplicate_threshold = opts.use_duplicate_threshold
    config.use_pagination_duplication = opts.use_pagination_duplication
    config.use_folder_suffix = opts.use_folder_suffix
    config.respect_timeline_stats = opts.respect_timeline_stats
    config.interactive = opts.interactive
    config.prompt_on_exit = opts.prompt_on_exit
    # ``debug`` and ``trace`` are no longer schema-backed. They're runtime
    # attributes driven by the ``-v`` / ``-vv`` CLI verbosity count (see
    # config/args.py::_handle_verbosity_settings); start at False here so
    # that loading config.yaml never silently reactivates a prior CLI flip.

    # Numeric options
    config.timeline_retries = opts.timeline_retries
    config.timeline_delay_seconds = opts.timeline_delay_seconds
    config.api_max_retries = opts.api_max_retries

    # Rate limiting
    config.rate_limiting_enabled = opts.rate_limiting_enabled
    config.rate_limiting_adaptive = opts.rate_limiting_adaptive
    config.rate_limiting_requests_per_minute = opts.rate_limiting_requests_per_minute
    config.rate_limiting_burst_size = opts.rate_limiting_burst_size
    config.rate_limiting_retry_after_seconds = opts.rate_limiting_retry_after_seconds
    config.rate_limiting_backoff_factor = opts.rate_limiting_backoff_factor
    config.rate_limiting_max_backoff_seconds = opts.rate_limiting_max_backoff_seconds

    # Temp folder
    if opts.temp_folder:
        config.temp_folder = Path(opts.temp_folder)

    # --- Postgres ---
    pg = schema.postgres
    config.pg_host = pg.pg_host
    config.pg_port = pg.pg_port
    config.pg_database = pg.pg_database
    config.pg_user = pg.pg_user

    # Password: env var overrides config file value
    config.pg_password = os.getenv("FANSLY_PG_PASSWORD") or (
        pg.pg_password.get_secret_value() if pg.pg_password is not None else None
    )

    config.pg_sslmode = pg.pg_sslmode
    if pg.pg_sslcert:
        config.pg_sslcert = Path(pg.pg_sslcert)
    if pg.pg_sslkey:
        config.pg_sslkey = Path(pg.pg_sslkey)
    if pg.pg_sslrootcert:
        config.pg_sslrootcert = Path(pg.pg_sslrootcert)
    config.pg_pool_size = pg.pg_pool_size
    config.pg_max_overflow = pg.pg_max_overflow
    config.pg_pool_timeout = pg.pg_pool_timeout

    # --- Cache ---
    config.cached_device_id = schema.cache.device_id
    config.cached_device_id_timestamp = schema.cache.device_id_timestamp

    # --- Logging ---
    log = schema.logging
    config.logging = log

    def _entry_level(entry: Any) -> str:
        """Resolve a handler entry's level, falling through to global default."""
        return entry.level or log.global_.default_level

    # log_levels dict preserves the legacy flat key→level shape for
    # ``get_log_level()`` consumers. "textio" historically governed both
    # the rich console and the main file handler; since the new schema
    # splits them, we resolve "textio" from main_log (the file handler is
    # the primary "textio" surface; rich_handler runs in parallel with
    # its own per-handler key below).
    config.log_levels = {
        "sqlalchemy": _entry_level(log.db),
        "stash_console": _entry_level(log.stash_console),
        "stash_file": _entry_level(log.stash_file),
        "textio": _entry_level(log.main_log),
        "rich_handler": _entry_level(log.rich_handler),
        "main_log": _entry_level(log.main_log),
        "websocket": _entry_level(log.websocket),
        "json": _entry_level(log.json_),
        "trace": _entry_level(log.trace),
        "db": _entry_level(log.db),
    }

    # Validate log levels
    valid_levels = {"TRACE", "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
    for logger_name, level in config.log_levels.items():
        if level not in valid_levels:
            textio_logger.opt(depth=1).log(
                "WARNING",
                f"Invalid log level '{level}' for logger '{logger_name}', using 'INFO'",
            )
            config.log_levels[logger_name] = "INFO"

    # --- Monitoring ---
    # session_baseline is a one-shot directive: any value present in YAML
    # (either hand-authored, or left over from the prior bug where CLI
    # --full-pass / --monitor-since wrote into the schema) is consumed into
    # the runtime field once and then cleared from the schema. The
    # ``save_config_or_raise`` call at the end of ``load_config`` then
    # writes ``session_baseline: null`` back to disk.
    #
    # Rationale: the daemon consumes ``monitoring_session_baseline`` once
    # per creator (``baseline_consumed`` set in daemon/runner.py) and
    # advances ``MonitorState.lastCheckedAt`` in the DB on success. So the
    # baseline is meaningful exactly once per run. Persisting it across
    # runs silently re-triggers a full pass on every invocation — the
    # regression this consume-and-reset heals.
    if config.monitoring_session_baseline is None:
        config.monitoring_session_baseline = schema.monitoring.session_baseline
    if schema.monitoring.session_baseline is not None:
        schema.monitoring.session_baseline = None
    # daemon_mode: only populate from schema if not already enabled via CLI
    if not config.daemon_mode:
        config.daemon_mode = schema.monitoring.daemon_mode
    # Daemon mode is non-interactive by definition — it runs unattended.
    if config.daemon_mode:
        config.interactive = False
    config.unrecoverable_error_timeout_seconds = (
        schema.monitoring.unrecoverable_error_timeout_seconds
    )
    config.monitoring_dashboard_enabled = schema.monitoring.dashboard_enabled
    config.monitoring_active_duration_minutes = (
        schema.monitoring.active_duration_minutes
    )
    config.monitoring_idle_duration_minutes = schema.monitoring.idle_duration_minutes
    config.monitoring_hidden_duration_minutes = (
        schema.monitoring.hidden_duration_minutes
    )
    config.monitoring_timeline_poll_active_seconds = (
        schema.monitoring.timeline_poll_active_seconds
    )
    config.monitoring_timeline_poll_idle_seconds = (
        schema.monitoring.timeline_poll_idle_seconds
    )
    config.monitoring_story_poll_active_seconds = (
        schema.monitoring.story_poll_active_seconds
    )
    config.monitoring_story_poll_idle_seconds = (
        schema.monitoring.story_poll_idle_seconds
    )
    config.monitoring_heartbeat_interval_minutes = (
        schema.monitoring.heartbeat_interval_minutes
    )
    config.monitoring_livestream_recording_enabled = (
        schema.monitoring.livestream_recording_enabled
    )
    config.monitoring_livestream_poll_interval_seconds = (
        schema.monitoring.livestream_poll_interval_seconds
    )
    config.monitoring_livestream_manifest_poll_interval_seconds = (
        schema.monitoring.livestream_manifest_poll_interval_seconds
    )

    # --- StashContext (optional) ---
    if schema.stash_context is not None:
        config.stash_context_conn = {
            "scheme": schema.stash_context.scheme,
            "host": schema.stash_context.host,
            "port": schema.stash_context.port,
            "apikey": schema.stash_context.apikey,
        }
        if schema.stash_context.mapped_path is not None:
            config.stash_mapped_path = Path(schema.stash_context.mapped_path)
        config.stash_override_dldir_w_mapped = (
            schema.stash_context.override_dldir_w_mapped
        )
        config.stash_require_stash_only_mode = (
            schema.stash_context.require_stash_only_mode
        )


def _handle_config_error(e: Exception) -> None:
    """Handle configuration errors with appropriate messages.

    Pydantic ValidationErrors arrive here already pre-formatted by
    ``config/schema.py::_format_validation_error`` — a multi-line human
    readable block listing each problem with its location and suggested
    fix. We pass that through intact. Other error types (configparser,
    missing keys) get a short fallback message.
    """
    error_string = str(e)

    if isinstance(e, configparser.NoOptionError):
        raise ConfigError(
            f"config.yaml is invalid — please check the file and correct the offending value.\n{error_string}"
        )
    if isinstance(e, ValueError):
        # Pydantic-derived multi-line messages start with "N problem(s) in "
        # — pass them through intact so the user sees each issue listed.
        if " problem(s) in " in error_string and error_string[0].isdigit():
            raise ConfigError(f"Configuration file needs editing:\n{error_string}")
        if "a boolean" in error_string:
            raise ConfigError(
                f"'{error_string.rsplit('boolean: ')[1]}' is malformed in config.yaml — must be true or false."
            )
        raise ConfigError(f"Invalid value in config.yaml:\n  {error_string}")
    if isinstance(e, (KeyError, NameError)):
        raise ConfigError(f"'{e}' is missing or malformed in config.yaml.")
    raise ConfigError(f"An error occurred while reading config.yaml: {error_string}")


def load_config(config: FanslyConfig) -> None:
    """Loads the program configuration from file.

    Delegates to ``load_or_migrate`` which handles config.yaml (preferred) or
    migrates from legacy config.ini on first run.  All values are copied from
    the typed ``ConfigSchema`` onto ``FanslyConfig`` attributes so that
    existing call-sites continue to work unchanged.

    :param FanslyConfig config: The configuration object to fill.
    """
    init_logging_config(config)
    set_debug_enabled(config.debug)

    textio_logger.opt(depth=1).log("INFO", "Reading configuration file ...")

    config_dir = Path.cwd()

    try:
        schema = load_or_migrate(config_dir)
        config._schema = schema

        # Store config_path — points at config.yaml after migration
        yaml_path = config_dir / "config.yaml"
        ini_path = config_dir / "config.ini"
        if yaml_path.exists():
            config.config_path = yaml_path
        elif ini_path.exists():
            # load_or_migrate would have created yaml; this branch is a safety net
            config.config_path = yaml_path
        else:
            # Neither file existed; load_or_migrate returned defaults — write yaml
            config.config_path = yaml_path

        _populate_config_from_schema(config, schema)

        # Safe to save! :-)
        save_config_or_raise(config)

    except (configparser.NoOptionError, ValueError, KeyError, NameError) as e:
        _handle_config_error(e)
