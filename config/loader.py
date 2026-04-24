"""YAML config loader with one-shot migration from legacy config.ini.

Public entry point:  ``load_or_migrate(config_dir)``

Resolution order:
  1. ``{config_dir}/config.yaml`` exists  → load and return
  2. Only ``{config_dir}/config.ini`` exists → migrate, write yaml, rename ini
  3. Neither file exists → return ``ConfigSchema()`` (all defaults)

Sections migrated from config.ini:
  - ``[TargetedCreator]``, ``[MyAccount]``, ``[Options]``, ``[Postgres]``,
    ``[Logic]``, ``[Monitoring]``, ``[Cache]``, ``[Logging]``, ``[StashContext]``

Sections intentionally NOT migrated from config.ini:
  - ``[Other]``       — only held a ``version`` key that config.py already
                         strips on load.

Environment variables:
  - ``FANSLY_PG_PASSWORD`` is intentionally ignored during migration.  It is
    a runtime override (CLI overlay) not a disk value.  The migrator copies
    only what is written in the .ini file.
"""

from __future__ import annotations

import configparser
import copy
from datetime import UTC, datetime
from pathlib import Path

from filelock import FileLock, Timeout
from loguru import logger
from pydantic import SecretStr

from config.schema import ConfigSchema
from errors import ConfigError


# Section → known keys map for unknown-key warning detection.
# Options includes both legacy and current spellings; unknown keys warn
# but never fail.
_KNOWN_INI_KEYS: dict[str, set[str]] = {
    "TargetedCreator": {
        "username",
        "use_following",
        "use_following_with_pagination",
    },
    "MyAccount": {
        "authorization_token",
        "user_agent",
        "check_key",
        "username",
        "password",
    },
    "Options": {
        "download_directory",
        "download_mode",
        "metadata_handling",
        "show_downloads",
        "show_skipped_downloads",
        "download_media_previews",
        "open_folder_when_finished",
        "separate_messages",
        "separate_previews",
        "separate_timeline",
        # both legacy and current spellings
        "utilise_duplicate_threshold",
        "use_duplicate_threshold",
        "use_pagination_duplication",
        # both legacy and current spellings
        "use_suffix",
        "use_folder_suffix",
        "interactive",
        "prompt_on_exit",
        "debug",
        "trace",
        "timeline_retries",
        "timeline_delay_seconds",
        "temp_folder",
        "api_max_retries",
        "rate_limiting_enabled",
        "rate_limiting_adaptive",
        "rate_limiting_requests_per_minute",
        "rate_limiting_burst_size",
        "rate_limiting_retry_after_seconds",
        "rate_limiting_backoff_factor",
        "rate_limiting_max_backoff_seconds",
        "db_sync_commits",
        "db_sync_seconds",
        "db_sync_min_size",
        # pg_* keys may live under [Options] in legacy files
        "pg_host",
        "pg_port",
        "pg_database",
        "pg_user",
        "pg_password",
        "pg_sslmode",
        "pg_sslcert",
        "pg_sslkey",
        "pg_sslrootcert",
        "pg_pool_size",
        "pg_max_overflow",
        "pg_pool_timeout",
    },
    "Postgres": {
        "pg_host",
        "pg_port",
        "pg_database",
        "pg_user",
        "pg_password",
        "pg_sslmode",
        "pg_sslcert",
        "pg_sslkey",
        "pg_sslrootcert",
        "pg_pool_size",
        "pg_max_overflow",
        "pg_pool_timeout",
    },
    "Logic": {
        "check_key_pattern",
        "main_js_pattern",
    },
    "Cache": {
        "device_id",
        "device_id_timestamp",
    },
    "Logging": {
        "sqlalchemy",
        "stash_console",
        "stash_file",
        "textio",
        "json",
    },
    "StashContext": {
        "scheme",
        "host",
        "port",
        "apikey",
    },
    "Monitoring": set(),  # reserved — no keys read yet
    # [Other] is intentionally absent; it is fully ignored
}


def load_or_migrate(config_dir: Path | str | None = None) -> ConfigSchema:
    """Load config.yaml, or migrate config.ini if only the legacy file exists.

    Parameters
    ----------
    config_dir:
        Directory to search for ``config.yaml`` / ``config.ini``.
        Defaults to the current working directory when ``None``.

    Returns
    -------
    ConfigSchema
        A fully-validated schema instance.

    Raises
    ------
    ValueError
        Propagated from ``migrate_ini_to_yaml`` when parity check fails.
    """
    config_dir = Path(config_dir) if config_dir is not None else Path.cwd()
    yaml_path = config_dir / "config.yaml"
    ini_path = config_dir / "config.ini"

    if yaml_path.exists():
        return ConfigSchema.load_yaml(yaml_path)

    if ini_path.exists():
        return migrate_ini_to_yaml(ini_path, yaml_path)

    return ConfigSchema()


def migrate_ini_to_yaml(
    ini_path: Path,
    yaml_path: Path,
    *,
    backup_suffix: str | None = None,
) -> ConfigSchema:
    """Read config.ini, build a ConfigSchema, write config.yaml, rename the .ini.

    Parameters
    ----------
    ini_path:
        Path to the legacy ``config.ini`` file.
    yaml_path:
        Destination path for the new ``config.yaml``.
    backup_suffix:
        Override the timestamp suffix for the backup file name.  Pass a fixed
        string in tests to make backup names deterministic.  When ``None``
        (default), a UTC timestamp ``YYYYMMDD_HHMMSS`` is used.

    Returns
    -------
    ConfigSchema
        The parsed schema produced from the ini file, ready for immediate use.

    Raises
    ------
    ConfigError
        If another process is currently migrating the same file (lock busy).
    ValueError
        If the parity check fails (YAML round-trip does not reproduce the
        in-memory schema built from the ini).  The .ini is left in place when
        this exception is raised.
    FileExistsError
        If the backup file already exists (avoids silent clobber).
    """
    suffix = backup_suffix or datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    backup_path = ini_path.with_name(f"{ini_path.name}.bak.{suffix}")

    if backup_path.exists():
        raise FileExistsError(
            f"Backup file already exists: {backup_path}. "
            "Remove it or wait one second before re-running."
        )

    # --- Acquire an exclusive non-blocking file lock to prevent concurrent migration ---
    # filelock chooses the right platform primitive (fcntl.flock on POSIX,
    # msvcrt.locking on Windows), so this path works on both.
    lock_path = ini_path.with_name(f"{ini_path.name}.migrating.lock")
    lock = FileLock(str(lock_path), blocking=False)
    try:
        lock.acquire()
    except Timeout as exc:
        raise ConfigError(
            f"Another process is migrating {ini_path}. "
            f"Lock held by: {lock_path}. Retry after the other process finishes."
        ) from exc

    try:
        # --- Step 1: Parse the ini file ---
        parser = configparser.ConfigParser(interpolation=None)
        parser.read(ini_path, encoding="utf-8")

        # Warn about unknown sections/keys (they are already silently ignored by
        # the curated _get_* helpers, but a warning helps operators spot typos).
        _warn_unknown_ini_keys(parser, ini_path)

        schema_from_ini = _build_schema_from_parser(parser)

        # --- Step 2: Write the YAML ---
        schema_from_ini.dump_yaml(yaml_path)

        # --- Step 3: Parity check — reload YAML and compare ---
        try:
            schema_reloaded = ConfigSchema.load_yaml(yaml_path)
        except Exception as exc:
            yaml_path.unlink(missing_ok=True)
            raise ValueError(
                f"Migration parity check failed: could not reload {yaml_path}: {exc}"
            ) from exc

        _assert_parity(schema_from_ini, schema_reloaded, yaml_path)

        # --- Step 4: Rename the ini to a timestamped backup ---
        ini_path.rename(backup_path)
    finally:
        # Release the lock; filelock cleans up the on-disk lock file safely
        # (no TOCTOU window — the unlink happens while we still hold the lock).
        lock.release()

    return schema_from_ini


def _warn_unknown_ini_keys(
    parser: configparser.ConfigParser,
    ini_path: Path,
) -> None:
    """Log a warning for any sections or keys in the ini that the migrator does not read.

    Unknown sections and keys are already silently dropped by the curated
    ``_get_*`` helpers; this function makes the omission visible to operators
    so they can verify nothing important was lost.

    The ``[DEFAULT]`` pseudo-section that ConfigParser injects is always skipped.
    """
    # Sections in the ini that we do not recognise at all
    known_sections = set(_KNOWN_INI_KEYS)
    for section in parser.sections():
        if section == "DEFAULT":
            continue
        if section not in known_sections:
            logger.warning(
                "config.ini migration: unknown section [{}] will be dropped. File: {}",
                section,
                ini_path,
            )
            continue
        # Section is known — check its individual keys
        known_keys = _KNOWN_INI_KEYS[section]
        for key in parser.options(section):
            if key not in known_keys:
                logger.warning(
                    "config.ini migration: unknown key '{}' in [{}] will be dropped. "
                    "File: {}",
                    key,
                    section,
                    ini_path,
                )


def _get_str(
    parser: configparser.ConfigParser,
    section: str,
    key: str,
    fallback: str | None = None,
) -> str | None:
    """Return a string value or ``fallback`` if the section/key is absent."""
    if not parser.has_section(section):
        return fallback
    return parser.get(section, key, fallback=fallback)  # type: ignore[arg-type]


def _get_bool(
    parser: configparser.ConfigParser,
    section: str,
    key: str,
    fallback: bool,
) -> bool:
    """Return a boolean value using ConfigParser's boolean coercion."""
    if not parser.has_section(section):
        return fallback
    return parser.getboolean(section, key, fallback=fallback)


def _get_int(
    parser: configparser.ConfigParser,
    section: str,
    key: str,
    fallback: int,
) -> int:
    """Return an integer value using ConfigParser's int coercion."""
    if not parser.has_section(section):
        return fallback
    return parser.getint(section, key, fallback=fallback)


def _build_schema_from_parser(parser: configparser.ConfigParser) -> ConfigSchema:
    """Build a ConfigSchema from an already-parsed ConfigParser.

    All string values from the ini are coerced to the correct Python type via
    ``getboolean`` / ``getint`` before being handed to Pydantic, so we never
    rely on Pydantic's string-to-bool / string-to-int coercion.

    Note: PostgreSQL connection keys (``pg_*``) live under ``[Options]`` in
    legacy config.ini files — ``_handle_postgresql_options`` in config.py
    confirms this.  We check ``[Postgres]`` first (future-proof) then fall
    back to ``[Options]``.
    """
    # [TargetedCreator]
    tc_section = "TargetedCreator"
    username_raw = _get_str(parser, tc_section, "Username", fallback="replaceme")
    use_following = _get_bool(parser, tc_section, "use_following", fallback=False)
    use_following_with_pagination = _get_bool(
        parser, tc_section, "use_following_with_pagination", fallback=False
    )

    # [MyAccount]
    acct_section = "MyAccount"
    authorization_token = _get_str(
        parser, acct_section, "Authorization_Token", fallback="ReplaceMe"
    )
    user_agent = _get_str(parser, acct_section, "User_Agent", fallback="ReplaceMe")
    check_key = _get_str(
        parser, acct_section, "Check_Key", fallback="qybZy9-fyszis-bybxyf"
    )
    username = _get_str(parser, acct_section, "username", fallback=None)
    password = _get_str(parser, acct_section, "password", fallback=None)

    # ------------------------------------------------------------------
    # [Options] — contains both general and PostgreSQL settings
    # ------------------------------------------------------------------
    opts_section = "Options"

    download_directory = _get_str(
        parser, opts_section, "download_directory", fallback="Local_directory"
    )
    download_mode = _get_str(parser, opts_section, "download_mode", fallback="Normal")
    show_downloads = _get_bool(parser, opts_section, "show_downloads", fallback=True)
    show_skipped_downloads = _get_bool(
        parser, opts_section, "show_skipped_downloads", fallback=True
    )
    download_media_previews = _get_bool(
        parser, opts_section, "download_media_previews", fallback=True
    )
    open_folder_when_finished = _get_bool(
        parser, opts_section, "open_folder_when_finished", fallback=True
    )
    separate_messages = _get_bool(
        parser, opts_section, "separate_messages", fallback=True
    )
    separate_previews = _get_bool(
        parser, opts_section, "separate_previews", fallback=False
    )
    separate_timeline = _get_bool(
        parser, opts_section, "separate_timeline", fallback=True
    )
    # Support the old spelling (utilise_) transparently
    if parser.has_option(opts_section, "utilise_duplicate_threshold"):
        use_duplicate_threshold = _get_bool(
            parser, opts_section, "utilise_duplicate_threshold", fallback=False
        )
    else:
        use_duplicate_threshold = _get_bool(
            parser, opts_section, "use_duplicate_threshold", fallback=False
        )
    # Support the old spelling (use_suffix)
    if parser.has_option(opts_section, "use_suffix"):
        use_folder_suffix = _get_bool(parser, opts_section, "use_suffix", fallback=True)
    else:
        use_folder_suffix = _get_bool(
            parser, opts_section, "use_folder_suffix", fallback=True
        )
    interactive = _get_bool(parser, opts_section, "interactive", fallback=True)
    prompt_on_exit = _get_bool(parser, opts_section, "prompt_on_exit", fallback=True)
    timeline_retries = _get_int(parser, opts_section, "timeline_retries", fallback=1)
    timeline_delay_seconds = _get_int(
        parser, opts_section, "timeline_delay_seconds", fallback=60
    )
    temp_folder = _get_str(parser, opts_section, "temp_folder", fallback=None)

    # ------------------------------------------------------------------
    # PostgreSQL — prefer [Postgres] section; fall back to [Options]
    # ------------------------------------------------------------------
    pg_section = "Postgres" if parser.has_section("Postgres") else opts_section

    pg_host = _get_str(parser, pg_section, "pg_host", fallback="localhost")
    pg_port = _get_int(parser, pg_section, "pg_port", fallback=5432)
    pg_database = _get_str(
        parser, pg_section, "pg_database", fallback="fansly_metadata"
    )
    pg_user = _get_str(parser, pg_section, "pg_user", fallback="fansly_user")
    # FANSLY_PG_PASSWORD env var is intentionally NOT consulted here —
    # it is a runtime override, not a disk value.
    pg_password_raw = _get_str(parser, pg_section, "pg_password", fallback=None)
    pg_sslmode = _get_str(parser, pg_section, "pg_sslmode", fallback="prefer")
    pg_sslcert = _get_str(parser, pg_section, "pg_sslcert", fallback=None)
    pg_sslkey = _get_str(parser, pg_section, "pg_sslkey", fallback=None)
    pg_sslrootcert = _get_str(parser, pg_section, "pg_sslrootcert", fallback=None)
    pg_pool_size = _get_int(parser, pg_section, "pg_pool_size", fallback=5)
    # pg_max_overflow and pg_pool_timeout: kept for round-trip parity with
    # existing config.ini files; the asyncpg pool does not use them at runtime.
    pg_max_overflow = _get_int(parser, pg_section, "pg_max_overflow", fallback=10)
    pg_pool_timeout = _get_int(parser, pg_section, "pg_pool_timeout", fallback=30)

    # ------------------------------------------------------------------
    # [Options] — extended: rate limiting, db sync, debug, retries
    # ------------------------------------------------------------------
    use_pagination_duplication = _get_bool(
        parser, opts_section, "use_pagination_duplication", fallback=False
    )
    debug = _get_bool(parser, opts_section, "debug", fallback=False)
    trace = _get_bool(parser, opts_section, "trace", fallback=False)
    api_max_retries = _get_int(parser, opts_section, "api_max_retries", fallback=10)
    rate_limiting_enabled = _get_bool(
        parser, opts_section, "rate_limiting_enabled", fallback=True
    )
    rate_limiting_adaptive = _get_bool(
        parser, opts_section, "rate_limiting_adaptive", fallback=True
    )
    rate_limiting_requests_per_minute = _get_int(
        parser, opts_section, "rate_limiting_requests_per_minute", fallback=60
    )
    rate_limiting_burst_size = _get_int(
        parser, opts_section, "rate_limiting_burst_size", fallback=10
    )
    rate_limiting_retry_after_seconds = _get_int(
        parser, opts_section, "rate_limiting_retry_after_seconds", fallback=30
    )
    rate_limiting_max_backoff_seconds = _get_int(
        parser, opts_section, "rate_limiting_max_backoff_seconds", fallback=300
    )
    # rate_limiting_backoff_factor is a float — no helper, use raw parser
    if parser.has_section(opts_section) and parser.has_option(
        opts_section, "rate_limiting_backoff_factor"
    ):
        rate_limiting_backoff_factor = parser.getfloat(
            opts_section, "rate_limiting_backoff_factor"
        )
    else:
        rate_limiting_backoff_factor = 1.5
    # ------------------------------------------------------------------
    # [Logic]
    # ------------------------------------------------------------------
    logic_section = "Logic"
    check_key_pattern = _get_str(
        parser,
        logic_section,
        "check_key_pattern",
        fallback=r"this\.checkKey_\s*=\s*[\"']([^\"']+)[\"']",
    )
    main_js_pattern = _get_str(
        parser,
        logic_section,
        "main_js_pattern",
        fallback=r"\ssrc\s*=\s*\"(main\..*?\.js)\"",
    )

    # ------------------------------------------------------------------
    # [Cache]
    # ------------------------------------------------------------------
    cache_section = "Cache"
    cache_device_id = _get_str(parser, cache_section, "device_id", fallback=None)
    cache_device_id_timestamp_raw = _get_str(
        parser, cache_section, "device_id_timestamp", fallback=None
    )
    cache_device_id_timestamp: int | None = None
    if cache_device_id_timestamp_raw is not None:
        try:
            cache_device_id_timestamp = int(cache_device_id_timestamp_raw)
        except (ValueError, TypeError):
            cache_device_id_timestamp = None

    # ------------------------------------------------------------------
    # [Logging]
    # ------------------------------------------------------------------
    log_section = "Logging"
    log_sqlalchemy = (
        _get_str(parser, log_section, "sqlalchemy", fallback="INFO") or "INFO"
    ).upper()
    log_stash_console = (
        _get_str(parser, log_section, "stash_console", fallback="INFO") or "INFO"
    ).upper()
    log_stash_file = (
        _get_str(parser, log_section, "stash_file", fallback="INFO") or "INFO"
    ).upper()
    log_textio = (
        _get_str(parser, log_section, "textio", fallback="INFO") or "INFO"
    ).upper()
    log_json = (
        _get_str(parser, log_section, "json", fallback="INFO") or "INFO"
    ).upper()

    # ------------------------------------------------------------------
    # [StashContext] — optional section
    # ------------------------------------------------------------------
    stash_section = "StashContext"
    stash_context: dict | None = None
    if parser.has_section(stash_section):
        stash_context = {
            "scheme": _get_str(parser, stash_section, "scheme", fallback="http"),
            "host": _get_str(parser, stash_section, "host", fallback="localhost"),
            "port": _get_int(parser, stash_section, "port", fallback=9999),
            "apikey": _get_str(parser, stash_section, "apikey", fallback="") or "",
        }

    # ------------------------------------------------------------------
    # Assemble the schema dict — Pydantic validates on construction
    # ------------------------------------------------------------------
    raw: dict = {
        "targeted_creator": {
            "usernames": username_raw,
            "use_following": use_following,
            "use_following_with_pagination": use_following_with_pagination,
        },
        "my_account": {
            "authorization_token": authorization_token,
            "user_agent": user_agent,
            "check_key": check_key,
            "username": username if username else None,
            "password": password if password else None,
        },
        "options": {
            "download_directory": download_directory,
            "download_mode": download_mode,
            "show_downloads": show_downloads,
            "show_skipped_downloads": show_skipped_downloads,
            "download_media_previews": download_media_previews,
            "open_folder_when_finished": open_folder_when_finished,
            "separate_messages": separate_messages,
            "separate_previews": separate_previews,
            "separate_timeline": separate_timeline,
            "use_duplicate_threshold": use_duplicate_threshold,
            "use_pagination_duplication": use_pagination_duplication,
            "use_folder_suffix": use_folder_suffix,
            "interactive": interactive,
            "prompt_on_exit": prompt_on_exit,
            "debug": debug,
            "trace": trace,
            "timeline_retries": timeline_retries,
            "timeline_delay_seconds": timeline_delay_seconds,
            "api_max_retries": api_max_retries,
            "rate_limiting_enabled": rate_limiting_enabled,
            "rate_limiting_adaptive": rate_limiting_adaptive,
            "rate_limiting_requests_per_minute": rate_limiting_requests_per_minute,
            "rate_limiting_burst_size": rate_limiting_burst_size,
            "rate_limiting_retry_after_seconds": rate_limiting_retry_after_seconds,
            "rate_limiting_backoff_factor": rate_limiting_backoff_factor,
            "rate_limiting_max_backoff_seconds": rate_limiting_max_backoff_seconds,
            "temp_folder": temp_folder,
        },
        "postgres": {
            "pg_host": pg_host,
            "pg_port": pg_port,
            "pg_database": pg_database,
            "pg_user": pg_user,
            "pg_password": pg_password_raw,
            "pg_sslmode": pg_sslmode,
            "pg_sslcert": pg_sslcert,
            "pg_sslkey": pg_sslkey,
            "pg_sslrootcert": pg_sslrootcert,
            "pg_pool_size": pg_pool_size,
            "pg_max_overflow": pg_max_overflow,
            "pg_pool_timeout": pg_pool_timeout,
        },
        "cache": {
            "device_id": cache_device_id,
            "device_id_timestamp": cache_device_id_timestamp,
        },
        "logging": {
            "sqlalchemy": log_sqlalchemy,
            "stash_console": log_stash_console,
            "stash_file": log_stash_file,
            "textio": log_textio,
            "json": log_json,
        },
        "stash_context": stash_context,
        "logic": {
            "check_key_pattern": check_key_pattern,
            "main_js_pattern": main_js_pattern,
        },
    }

    return ConfigSchema.model_validate(raw)


def _schema_to_comparable(schema: ConfigSchema) -> dict:
    """Flatten a ConfigSchema into a comparable plain-dict form.

    SecretStr fields are unwrapped so that dict equality works correctly.
    Used only for the migration parity check.
    """
    result: dict = {}
    for section_name in (
        "targeted_creator",
        "my_account",
        "options",
        "postgres",
        "cache",
        "logging",
        "monitoring",
        "logic",
    ):
        section = getattr(schema, section_name)
        section_dict: dict = {}
        for field_name in section.model_fields:
            raw = getattr(section, field_name)
            if isinstance(raw, SecretStr):
                section_dict[field_name] = raw.get_secret_value()
            else:
                # Copy enums as their string value for stable comparison
                section_dict[field_name] = copy.copy(raw)
        result[section_name] = section_dict
    # stash_context is optional — only include when present
    if schema.stash_context is not None:
        sc_dict: dict = {}
        for field_name in schema.stash_context.model_fields:
            raw = getattr(schema.stash_context, field_name)
            sc_dict[field_name] = copy.copy(raw)
        result["stash_context"] = sc_dict
    else:
        result["stash_context"] = None
    return result


def _assert_parity(
    schema_from_ini: ConfigSchema,
    schema_reloaded: ConfigSchema,
    yaml_path: Path,
) -> None:
    """Raise ValueError with a diff message if the two schemas diverge.

    On failure, ``yaml_path`` is removed so the user is not left with a
    partially-correct YAML file.
    """
    a = _schema_to_comparable(schema_from_ini)
    b = _schema_to_comparable(schema_reloaded)

    diffs: list[str] = []
    for section_name in a:
        section_a = a.get(section_name)
        section_b = b.get(section_name)
        # Optional sections (e.g. stash_context) may be None on both sides
        if section_a is None and section_b is None:
            continue
        if section_a is None or section_b is None:
            diffs.append(f"  [{section_name}]: ini={section_a!r} vs yaml={section_b!r}")
            continue
        for key in set(section_a) | set(section_b):
            val_a = section_a.get(key)
            val_b = section_b.get(key)
            if val_a != val_b:
                diffs.append(
                    f"  [{section_name}] {key}: ini={val_a!r} vs yaml={val_b!r}"
                )

    if diffs:
        yaml_path.unlink(missing_ok=True)
        diff_text = "\n".join(diffs)
        raise ValueError(
            f"Migration parity check failed — {len(diffs)} field(s) diverged after "
            f"YAML round-trip.  config.ini has NOT been renamed.\n{diff_text}"
        )
