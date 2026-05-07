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


# ---------------------------------------------------------------------------
# has_option-gated helpers — populate ``dest`` only when the INI carries the
# key. The Pydantic schema's defaults handle every absent key, and crucially
# the resulting ``model_fields_set`` reflects only what the operator actually
# wrote in their ini. That keeps ``model_dump(exclude_unset=True)`` honest
# across save round-trips post-migration: subsequent ``_save_config()`` calls
# don't re-pin defaulted-away keys back into the YAML.
# ---------------------------------------------------------------------------


def _maybe_str(
    parser: configparser.ConfigParser,
    section: str,
    key: str,
    dest: dict,
    *,
    dest_key: str | None = None,
) -> bool:
    """Populate ``dest[dest_key or key]`` from INI when present. Return whether set."""
    if parser.has_section(section) and parser.has_option(section, key):
        dest[dest_key or key] = parser.get(section, key)
        return True
    return False


def _maybe_bool(
    parser: configparser.ConfigParser,
    section: str,
    key: str,
    dest: dict,
    *,
    dest_key: str | None = None,
) -> bool:
    """Populate ``dest[dest_key or key]`` as bool when present. Return whether set."""
    if parser.has_section(section) and parser.has_option(section, key):
        dest[dest_key or key] = parser.getboolean(section, key)
        return True
    return False


def _maybe_int(
    parser: configparser.ConfigParser,
    section: str,
    key: str,
    dest: dict,
    *,
    dest_key: str | None = None,
) -> bool:
    """Populate ``dest[dest_key or key]`` as int when present. Return whether set."""
    if parser.has_section(section) and parser.has_option(section, key):
        dest[dest_key or key] = parser.getint(section, key)
        return True
    return False


def _maybe_float(
    parser: configparser.ConfigParser,
    section: str,
    key: str,
    dest: dict,
    *,
    dest_key: str | None = None,
) -> bool:
    """Populate ``dest[dest_key or key]`` as float when present. Return whether set."""
    if parser.has_section(section) and parser.has_option(section, key):
        dest[dest_key or key] = parser.getfloat(section, key)
        return True
    return False


def _build_schema_from_parser(parser: configparser.ConfigParser) -> ConfigSchema:
    """Build a ConfigSchema from an already-parsed ConfigParser.

    has_option-gated extraction: only INI keys that are *actually present*
    are forwarded to Pydantic. Absent keys are omitted from the validation
    dict so Pydantic's defaults apply, and crucially the resulting
    ``model_fields_set`` reflects only what the operator wrote in their
    ini. That means ``model_dump(exclude_unset=True)`` in the dump path
    stays honest post-migration: subsequent ``_save_config()`` calls
    don't re-pin defaulted-away keys back into the YAML.

    Legacy spellings (``utilise_duplicate_threshold`` → ``use_duplicate_threshold``,
    ``use_suffix`` → ``use_folder_suffix``, ``Username`` → ``usernames``,
    ``Authorization_Token`` → ``authorization_token``, etc.) are translated
    via ``dest_key=``. ``pg_*`` keys live under ``[Postgres]`` in modern
    INIs but legacy files put them under ``[Options]``; we check both.
    """
    raw: dict = {}

    # [TargetedCreator]
    tc: dict = {}
    _maybe_str(parser, "TargetedCreator", "Username", tc, dest_key="usernames")
    _maybe_bool(parser, "TargetedCreator", "use_following", tc)
    if tc:
        raw["targeted_creator"] = tc

    # [MyAccount]
    acct: dict = {}
    _maybe_str(
        parser, "MyAccount", "Authorization_Token", acct, dest_key="authorization_token"
    )
    _maybe_str(parser, "MyAccount", "User_Agent", acct, dest_key="user_agent")
    _maybe_str(parser, "MyAccount", "Check_Key", acct, dest_key="check_key")
    _maybe_str(parser, "MyAccount", "username", acct)
    _maybe_str(parser, "MyAccount", "password", acct)
    if acct:
        raw["my_account"] = acct

    # [Options]
    opts_section = "Options"
    opts: dict = {}
    _maybe_str(parser, opts_section, "download_directory", opts)
    _maybe_str(parser, opts_section, "download_mode", opts)
    _maybe_bool(parser, opts_section, "show_downloads", opts)
    _maybe_bool(parser, opts_section, "show_skipped_downloads", opts)
    _maybe_bool(parser, opts_section, "download_media_previews", opts)
    _maybe_bool(parser, opts_section, "open_folder_when_finished", opts)
    _maybe_bool(parser, opts_section, "separate_messages", opts)
    _maybe_bool(parser, opts_section, "separate_previews", opts)
    _maybe_bool(parser, opts_section, "separate_timeline", opts)
    # Legacy spelling first; only one of the two should be in the INI.
    if not _maybe_bool(
        parser,
        opts_section,
        "utilise_duplicate_threshold",
        opts,
        dest_key="use_duplicate_threshold",
    ):
        _maybe_bool(parser, opts_section, "use_duplicate_threshold", opts)
    if not _maybe_bool(
        parser, opts_section, "use_suffix", opts, dest_key="use_folder_suffix"
    ):
        _maybe_bool(parser, opts_section, "use_folder_suffix", opts)
    _maybe_bool(parser, opts_section, "interactive", opts)
    _maybe_bool(parser, opts_section, "prompt_on_exit", opts)
    _maybe_int(parser, opts_section, "timeline_retries", opts)
    _maybe_int(parser, opts_section, "timeline_delay_seconds", opts)
    _maybe_str(parser, opts_section, "temp_folder", opts)
    _maybe_bool(parser, opts_section, "use_pagination_duplication", opts)
    _maybe_bool(parser, opts_section, "debug", opts)
    _maybe_bool(parser, opts_section, "trace", opts)
    _maybe_int(parser, opts_section, "api_max_retries", opts)
    _maybe_bool(parser, opts_section, "rate_limiting_enabled", opts)
    _maybe_bool(parser, opts_section, "rate_limiting_adaptive", opts)
    _maybe_int(parser, opts_section, "rate_limiting_requests_per_minute", opts)
    _maybe_int(parser, opts_section, "rate_limiting_burst_size", opts)
    _maybe_int(parser, opts_section, "rate_limiting_retry_after_seconds", opts)
    _maybe_int(parser, opts_section, "rate_limiting_max_backoff_seconds", opts)
    _maybe_float(parser, opts_section, "rate_limiting_backoff_factor", opts)
    if opts:
        raw["options"] = opts

    # [Postgres] — prefer dedicated section; legacy INIs put pg_* under [Options].
    pg_section = "Postgres" if parser.has_section("Postgres") else opts_section
    pg: dict = {}
    _maybe_str(parser, pg_section, "pg_host", pg)
    _maybe_int(parser, pg_section, "pg_port", pg)
    _maybe_str(parser, pg_section, "pg_database", pg)
    _maybe_str(parser, pg_section, "pg_user", pg)
    # FANSLY_PG_PASSWORD env var is intentionally NOT consulted here — runtime
    # override, not a disk value.
    _maybe_str(parser, pg_section, "pg_password", pg)
    _maybe_str(parser, pg_section, "pg_sslmode", pg)
    _maybe_str(parser, pg_section, "pg_sslcert", pg)
    _maybe_str(parser, pg_section, "pg_sslkey", pg)
    _maybe_str(parser, pg_section, "pg_sslrootcert", pg)
    _maybe_int(parser, pg_section, "pg_pool_size", pg)
    _maybe_int(parser, pg_section, "pg_max_overflow", pg)
    _maybe_int(parser, pg_section, "pg_pool_timeout", pg)
    if pg:
        raw["postgres"] = pg

    # [Cache]
    cache: dict = {}
    _maybe_str(parser, "Cache", "device_id", cache)
    _maybe_int(parser, "Cache", "device_id_timestamp", cache)
    if cache:
        raw["cache"] = cache

    # [Logging] — log levels are uppercased post-extraction so legacy lowercase
    # entries still pass schema validation.
    log: dict = {}
    for level_key in ("sqlalchemy", "stash_console", "stash_file", "textio", "json"):
        if _maybe_str(parser, "Logging", level_key, log):
            log[level_key] = log[level_key].upper()
    if log:
        raw["logging"] = log

    # [Logic]
    logic: dict = {}
    _maybe_str(parser, "Logic", "check_key_pattern", logic)
    _maybe_str(parser, "Logic", "main_js_pattern", logic)
    if logic:
        raw["logic"] = logic

    # [StashContext] — section-level optional. Only included if the [StashContext]
    # block exists at all in the INI.
    if parser.has_section("StashContext"):
        sc: dict = {}
        _maybe_str(parser, "StashContext", "scheme", sc)
        _maybe_str(parser, "StashContext", "host", sc)
        _maybe_int(parser, "StashContext", "port", sc)
        _maybe_str(parser, "StashContext", "apikey", sc)
        if sc:
            raw["stash_context"] = sc

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
