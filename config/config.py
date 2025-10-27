"""Configuration File Manipulation"""

import configparser
from configparser import ConfigParser
from pathlib import Path

from config.fanslyconfig import FanslyConfig
from config.metadatahandling import MetadataHandling
from config.modes import DownloadMode
from errors import ConfigError
from helpers.browser import open_url
from textio import print_config, print_info, print_warning


def save_config_or_raise(config: FanslyConfig) -> bool:
    """Tries to save the configuration to `config.ini` or
    raises a `ConfigError` otherwise.

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
    names: list[str] = []

    if "," in line:
        names = line.split(",")

    else:
        names = line.split()

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


def _ensure_section_exists(parser: configparser.ConfigParser, section: str) -> None:
    """Ensure a section exists in the config parser."""
    if not parser.has_section(section):
        parser.add_section(section)


def _handle_creator_section(config: FanslyConfig, replace_me_str: str) -> None:
    """Handle TargetedCreator section configuration."""
    creator_section = "TargetedCreator"
    _ensure_section_exists(config._parser, creator_section)

    # Check for command-line override - already set?
    if config.user_names is None:
        user_names = config._parser.get(
            creator_section, "Username", fallback=replace_me_str
        )
        config.user_names = sanitize_creator_names(parse_items_from_line(user_names))

    # Handle use_following option
    config.use_following = config._parser.getboolean(
        creator_section, "use_following", fallback=False
    )


def _handle_account_section(config: FanslyConfig, replace_me_str: str) -> None:
    """Handle MyAccount section configuration."""
    account_section = "MyAccount"
    _ensure_section_exists(config._parser, account_section)

    config.token = config._parser.get(
        account_section, "Authorization_Token", fallback=replace_me_str
    )
    config.user_agent = config._parser.get(
        account_section, "User_Agent", fallback=replace_me_str
    )

    # Default check key (current as of 2025-10-25)
    # This is the evaluated result of: ["fySzis","oybZy8"].reverse().join("-")+"-bubayf"
    default_check_key = "oybZy8-fySzis-bubayf"

    config.check_key = config._parser.get(
        account_section, "Check_Key", fallback=default_check_key
    )

    # Replace known outdated check keys with current default
    if config.check_key in [
        "negwij-zyZnek-wavje1",
        "negwij-zyZnak-wavje1",
        "qybZy9-fyszis-bybxyf",  # Old default
    ]:
        config.check_key = default_check_key

    # Load login credentials (optional - for automatic login)
    config.username = config._parser.get(account_section, "username", fallback=None)
    config.password = config._parser.get(account_section, "password", fallback=None)


def _handle_other_section(config: FanslyConfig) -> None:
    """Handle Other section configuration."""
    other_section = "Other"

    # Remove obsolete version option
    if config._parser.has_option(other_section, "version"):
        config._parser.remove_option(other_section, "version")

    # Remove empty section
    if (
        config._parser.has_section(other_section)
        and len(config._parser[other_section]) == 0
    ):
        config._parser.remove_section(other_section)


def _handle_boolean_options(config: FanslyConfig, section: str) -> None:
    """Handle boolean options in a section."""
    boolean_options = {
        "download_media_previews": True,
        "open_folder_when_finished": True,
        "separate_messages": True,
        "separate_previews": False,
        "separate_timeline": True,
        "separate_metadata": False,
        "show_downloads": True,
        "show_skipped_downloads": True,
        "interactive": True,
        "prompt_on_exit": True,
        "use_pagination_duplication": False,  # Check each page for duplicates
        "debug": False,  # Debug mode
        "trace": False,  # Very detailed logging
        "rate_limiting_enabled": True,  # Enable rate limiting
        "rate_limiting_adaptive": True,  # Enable adaptive rate limiting
    }

    for option, default in boolean_options.items():
        setattr(
            config, option, config._parser.getboolean(section, option, fallback=default)
        )


def _handle_renamed_options(config: FanslyConfig, section: str) -> None:
    """Handle renamed options in a section."""
    # Handle use_duplicate_threshold (renamed from utilise_duplicate_threshold)
    if config._parser.has_option(section, "utilise_duplicate_threshold"):
        config.use_duplicate_threshold = config._parser.getboolean(
            section, "utilise_duplicate_threshold", fallback=False
        )
        config._parser.remove_option(section, "utilise_duplicate_threshold")
    else:
        config.use_duplicate_threshold = config._parser.getboolean(
            section, "use_duplicate_threshold", fallback=False
        )

    # Handle use_folder_suffix (renamed from use_suffix)
    if config._parser.has_option(section, "use_suffix"):
        config.use_folder_suffix = config._parser.getboolean(
            section, "use_suffix", fallback=True
        )
        config._parser.remove_option(section, "use_suffix")
    else:
        config.use_folder_suffix = config._parser.getboolean(
            section, "use_folder_suffix", fallback=True
        )


def _handle_path_options(config: FanslyConfig, section: str) -> None:
    """Handle path-type options in a section."""
    # Handle download directory
    config.download_directory = Path(
        config._parser.get(section, "download_directory", fallback="Local_directory")
    )

    # Handle metadata database file
    metadata_db_path = config._parser.get(section, "metadata_db_file", fallback=None)
    if metadata_db_path:
        config.metadata_db_file = Path(metadata_db_path)

    # Handle temp folder
    temp_folder_path = config._parser.get(section, "temp_folder", fallback=None)
    if temp_folder_path:
        config.temp_folder = Path(temp_folder_path)


def _handle_numeric_options(config: FanslyConfig, section: str) -> None:
    """Handle numeric options in a section."""
    # Handle timeline settings
    config.timeline_retries = config._parser.getint(
        section, "timeline_retries", fallback=1
    )
    config.timeline_delay_seconds = config._parser.getint(
        section, "timeline_delay_seconds", fallback=60
    )

    # Handle API retry settings
    config.api_max_retries = config._parser.getint(
        section, "api_max_retries", fallback=10
    )

    # Handle database sync settings (SQLite only - deprecated)
    db_sync_options = ["db_sync_commits", "db_sync_seconds", "db_sync_min_size"]
    for option in db_sync_options:
        if config._parser.has_option(section, option):
            setattr(config, option, config._parser.getint(section, option))

    # Handle rate limiting settings with proper fallback defaults
    config.rate_limiting_requests_per_minute = config._parser.getint(
        section, "rate_limiting_requests_per_minute", fallback=60
    )
    config.rate_limiting_burst_size = config._parser.getint(
        section, "rate_limiting_burst_size", fallback=10
    )
    config.rate_limiting_retry_after_seconds = config._parser.getint(
        section, "rate_limiting_retry_after_seconds", fallback=30
    )
    config.rate_limiting_max_backoff_seconds = config._parser.getint(
        section, "rate_limiting_max_backoff_seconds", fallback=300
    )
    config.rate_limiting_backoff_factor = config._parser.getfloat(
        section, "rate_limiting_backoff_factor", fallback=1.5
    )


def _handle_postgresql_options(config: FanslyConfig, section: str) -> None:
    """Handle PostgreSQL options in a section."""
    # Connection settings
    config.pg_host = config._parser.get(section, "pg_host", fallback="localhost")
    config.pg_port = config._parser.getint(section, "pg_port", fallback=5432)
    config.pg_database = config._parser.get(
        section, "pg_database", fallback="fansly_metadata"
    )
    config.pg_user = config._parser.get(section, "pg_user", fallback="fansly_user")

    # Password from environment variable (preferred) or config file
    config.pg_password = os.getenv("FANSLY_PG_PASSWORD") or config._parser.get(
        section, "pg_password", fallback=None
    )

    # SSL/TLS settings
    config.pg_sslmode = config._parser.get(section, "pg_sslmode", fallback="prefer")

    pg_sslcert_path = config._parser.get(section, "pg_sslcert", fallback=None)
    if pg_sslcert_path:
        config.pg_sslcert = Path(pg_sslcert_path)

    pg_sslkey_path = config._parser.get(section, "pg_sslkey", fallback=None)
    if pg_sslkey_path:
        config.pg_sslkey = Path(pg_sslkey_path)

    pg_sslrootcert_path = config._parser.get(section, "pg_sslrootcert", fallback=None)
    if pg_sslrootcert_path:
        config.pg_sslrootcert = Path(pg_sslrootcert_path)

    # Connection pool settings
    config.pg_pool_size = config._parser.getint(section, "pg_pool_size", fallback=5)
    config.pg_max_overflow = config._parser.getint(
        section, "pg_max_overflow", fallback=10
    )
    config.pg_pool_timeout = config._parser.getint(
        section, "pg_pool_timeout", fallback=30
    )


def _handle_options_section(config: FanslyConfig) -> None:
    """Handle Options section configuration."""
    options_section = "Options"
    _ensure_section_exists(config._parser, options_section)

    # Handle path options
    _handle_path_options(config, options_section)

    # Handle download mode
    download_mode = config._parser.get(
        options_section, "download_mode", fallback="Normal"
    )
    config.download_mode = DownloadMode(download_mode.upper())

    # Handle metadata handling
    metadata_handling = config._parser.get(
        options_section, "metadata_handling", fallback="Advanced"
    )
    config.metadata_handling = MetadataHandling(metadata_handling.upper())

    # Handle boolean options
    _handle_boolean_options(config, options_section)

    # Handle numeric options
    _handle_numeric_options(config, options_section)

    # Handle PostgreSQL options
    _handle_postgresql_options(config, options_section)

    # Handle renamed options
    _handle_renamed_options(config, options_section)

    # Remove deprecated options
    if config._parser.has_option(options_section, "include_meta_database"):
        config._parser.remove_option(options_section, "include_meta_database")


def _handle_cache_section(config: FanslyConfig) -> None:
    """Handle Cache section configuration."""
    cache_section = "Cache"
    _ensure_section_exists(config._parser, cache_section)

    config.cached_device_id = config._parser.get(
        cache_section, "device_id", fallback=None
    )
    config.cached_device_id_timestamp = config._parser.getint(
        cache_section, "device_id_timestamp", fallback=None
    )


def _handle_stash_section(config: FanslyConfig) -> None:
    """Handle StashContext section configuration."""
    stash_section = "StashContext"
    if config._parser.has_section(stash_section):
        config.stash_context_conn = {
            "scheme": config._parser.get(stash_section, "scheme", fallback="http"),
            "host": config._parser.get(stash_section, "host", fallback="localhost"),
            "port": config._parser.getint(stash_section, "port", fallback=9999),
            "apikey": config._parser.get(stash_section, "apikey", fallback=""),
        }


def _handle_logging_section(config: FanslyConfig) -> None:
    """Handle Logging section configuration."""
    logging_section = "Logging"
    _ensure_section_exists(config._parser, logging_section)

    # Load configured levels from file, default to INFO
    config.log_levels = {
        "sqlalchemy": config._parser.get(
            logging_section, "sqlalchemy", fallback="INFO"
        ).upper(),
        "stash_console": config._parser.get(
            logging_section, "stash_console", fallback="INFO"
        ).upper(),
        "stash_file": config._parser.get(
            logging_section, "stash_file", fallback="INFO"
        ).upper(),
        "textio": config._parser.get(
            logging_section, "textio", fallback="INFO"
        ).upper(),
        "json": config._parser.get(logging_section, "json", fallback="INFO").upper(),
    }

    # Validate log levels
    valid_levels = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
    for logger, level in config.log_levels.items():
        if level not in valid_levels:
            print_warning(
                f"Invalid log level '{level}' for logger '{logger}', using 'INFO'"
            )
            config.log_levels[logger] = "INFO"


def _handle_config_error(e: Exception, config: FanslyConfig) -> None:
    """Handle configuration errors with appropriate messages."""
    error_string = str(e)
    wiki_url = "https://github.com/prof79/fansly-downloader-ng/wiki/Explanation-of-provided-programs-&-their-functionality#4-configini"

    if isinstance(e, configparser.NoOptionError):
        raise ConfigError(
            f"Your config.ini file is invalid, please download a fresh version of it from GitHub.\n{error_string}"
        )
    if isinstance(e, ValueError):
        if "a boolean" in error_string:
            if config.interactive and not os.getenv("PYTEST_CURRENT_TEST"):
                open_url(wiki_url)
            raise ConfigError(
                f"'{error_string.rsplit('boolean: ')[1]}' is malformed in the configuration file! This value can only be True or False"
                f"\n{17 * ' '}Read the Wiki > Explanation of provided programs & their functionality > config.ini [1]"
            )
        if config.interactive and not os.getenv("PYTEST_CURRENT_TEST"):
            open_url(wiki_url)
        raise ConfigError(
            f"You have entered a wrong value in the config.ini file -> '{error_string}'"
            f"\n{17 * ' '}Read the Wiki > Explanation of provided programs & their functionality > config.ini [2]"
        )
    if isinstance(e, (KeyError, NameError)):
        if config.interactive and not os.getenv("PYTEST_CURRENT_TEST"):
            open_url(wiki_url)
        raise ConfigError(
            f"'{e}' is missing or malformed in the configuration file!"
            f"\n{17 * ' '}Read the Wiki > Explanation of provided programs & their functionality > config.ini [3]"
        )
    raise ConfigError(
        f"An error occurred while reading the configuration file: {error_string}"
    )


def load_config(config: FanslyConfig) -> None:
    """Loads the program configuration from file.

    :param FanslyConfig config: The configuration object to fill.
    """
    # Initialize logging config first
    from config.logging import init_logging_config, set_debug_enabled

    init_logging_config(config)
    set_debug_enabled(config.debug)

    print_info("Reading config.ini file ...")
    print()

    config.config_path = Path.cwd() / "config.ini"

    if not config.config_path.exists():
        print_warning("Configuration file config.ini not found.")
        print_config("A default configuration file will be generated for you ...")
        with config.config_path.open(mode="w", encoding="utf-8"):
            pass

    config._load_raw_config()

    try:
        # WARNING: Do not use the save config helper until the very end!
        # Since the settings from the config object are synced to the parser
        # on save, all still uninitialized values from the partially loaded
        # config would overwrite the existing configuration!
        replace_me_str = "ReplaceMe"

        # Handle each section
        _handle_creator_section(config, replace_me_str)
        _handle_account_section(config, replace_me_str)
        _handle_other_section(config)
        _handle_options_section(config)
        _handle_cache_section(config)
        _handle_stash_section(config)
        _handle_logging_section(config)

        # Safe to save! :-)
        save_config_or_raise(config)

    except (configparser.NoOptionError, ValueError, KeyError, NameError) as e:
        _handle_config_error(e, config)
