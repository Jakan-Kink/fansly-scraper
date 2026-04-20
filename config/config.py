"""Configuration File Manipulation"""

import configparser
import os
from configparser import ConfigParser
from pathlib import Path

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


def copy_old_config_values() -> None:
    """Copies configuration values from an old configuration file to
    a new one.

    Only sections/values existing in the new configuration will be adjusted.

    The hardcoded file names are from `old_config.ini` to `config.ini`.
    """
    current_directory = Path.cwd()
    old_config_path = current_directory / "old_config.ini"
    new_config_path = current_directory / "config.ini"

    if old_config_path.is_file() and new_config_path.is_file():
        old_config = ConfigParser(interpolation=None)
        old_config.read(old_config_path)

        new_config = ConfigParser(interpolation=None)
        new_config.read(new_config_path)

        # iterate over each section in the old config
        for section in old_config.sections():
            # check if the section exists in the new config
            if new_config.has_section(section):
                # iterate over each option in the section
                for option in old_config.options(section):
                    # check if the option exists in the new config
                    if new_config.has_option(section, option):
                        # get the value from the old config and set it in the new config
                        value = old_config.get(section, option)

                        # skip overwriting the version value
                        if section == "Other" and option == "version":
                            continue

                        new_config.set(section, option, value)

        # save the updated new config
        with Path(new_config_path).open("w") as config_file:
            new_config.write(config_file)


def _populate_config_from_schema(config: FanslyConfig, schema: ConfigSchema) -> None:
    """Copy all values from a loaded ``ConfigSchema`` onto ``FanslyConfig`` attributes.

    This is the bridge layer: ``FanslyConfig`` is a runtime-mutable facade;
    ``ConfigSchema`` is the disk format.  Fields in the schema that carry
    ``SecretStr`` values are unwrapped to plain ``str`` here so that the
    existing call-sites that read ``config.token`` etc. continue to work
    unchanged.
    """
    # --- TargetedCreator ---
    # Only override if not already set via command-line
    if config.user_names is None:
        raw_names = schema.targeted_creator.usernames
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
    config.metadata_handling = opts.metadata_handling

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
    config.interactive = opts.interactive
    config.prompt_on_exit = opts.prompt_on_exit
    config.debug = opts.debug
    config.trace = opts.trace

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

    # DB sync settings (optional ints)
    config.db_sync_commits = opts.db_sync_commits
    config.db_sync_seconds = opts.db_sync_seconds
    config.db_sync_min_size = opts.db_sync_min_size

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
    config.log_levels = {
        "sqlalchemy": log.sqlalchemy,
        "stash_console": log.stash_console,
        "stash_file": log.stash_file,
        "textio": log.textio,
        "websocket": log.websocket,
        # `log.json` would return the BaseModel.json() bound method —
        # the Python attribute is json_level, YAML key is still "json".
        "json": log.json_level,
    }

    # Validate log levels
    valid_levels = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
    for logger_name, level in config.log_levels.items():
        if level not in valid_levels:
            textio_logger.opt(depth=1).log(
                "WARNING",
                f"Invalid log level '{level}' for logger '{logger_name}', using 'INFO'",
            )
            config.log_levels[logger_name] = "INFO"

    # --- Monitoring ---
    # session_baseline: only populate if not already set via CLI
    if config.monitoring_session_baseline is None:
        config.monitoring_session_baseline = schema.monitoring.session_baseline
    # daemon_mode: only populate from schema if not already enabled via CLI
    if not config.daemon_mode:
        config.daemon_mode = schema.monitoring.daemon_mode
    config.unrecoverable_error_timeout_seconds = (
        schema.monitoring.unrecoverable_error_timeout_seconds
    )
    config.monitoring_dashboard_enabled = schema.monitoring.dashboard_enabled

    # --- StashContext (optional) ---
    if schema.stash_context is not None:
        config.stash_context_conn = {
            "scheme": schema.stash_context.scheme,
            "host": schema.stash_context.host,
            "port": schema.stash_context.port,
            "apikey": schema.stash_context.apikey,
        }


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
