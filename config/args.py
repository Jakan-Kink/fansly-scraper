"""Argument Parsing and Configuration Mapping"""

import argparse
from functools import partial
from pathlib import Path

from config.logging import set_debug_enabled, textio_logger
from errors import ConfigError
from helpers.common import get_post_id_from_request, is_valid_post_id

from .config import parse_items_from_line, sanitize_creator_names, save_config_or_raise
from .fanslyconfig import FanslyConfig
from .metadatahandling import MetadataHandling
from .modes import DownloadMode


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fansly Downloader NG scrapes media content from one or more Fansly creators. "
        "Settings will be taken from config.ini or internal defaults and "
        "can be overriden with the following parameters.\n"
        "Using the command-line will not overwrite config.ini.",
    )

    # region Essential Options

    parser.add_argument(
        "-uf",
        "--use-following",
        action="store_true",
        default=False,
        help="Process following list instead of targeted creators",
        required=False,
    )

    parser.add_argument(
        "-ufp",
        "--use-following-with-pagination",
        action="store_true",
        default=False,
        help="Process following list with pagination duplication enabled",
        required=False,
    )

    parser.add_argument(
        "-r",
        "--reverse-order",
        action="store_true",
        default=False,
        help="Process creators in reverse order (applies to following list and targeted creators)",
        required=False,
    )

    parser.add_argument(
        "-u",
        "--user",
        required=False,
        default=None,
        metavar="USER",
        dest="users",
        help="A list of one or more Fansly creators you want to download "
        "content from.\n"
        "This overrides TargetedCreator > username in config.ini.",
        nargs="+",
    )
    parser.add_argument(
        "-dir",
        "--directory",
        required=False,
        default=None,
        dest="download_directory",
        help="The base directory to store all creators' content in. "
        "A subdirectory for each creator will be created automatically. "
        "If you do not specify --no-folder-suffix, "
        "each creator's folder will be suffixed with "
        "_fansly"
        ". "
        "Please remember to quote paths including spaces.",
    )
    parser.add_argument(
        "-t",
        "--token",
        required=False,
        default=None,
        metavar="AUTHORIZATION_TOKEN",
        dest="token",
        help="The Fansly authorization token obtained from a browser session.",
    )
    parser.add_argument(
        "-ua",
        "--user-agent",
        required=False,
        default=None,
        dest="user_agent",
        help="The browser user agent string to use when communicating with "
        "Fansly servers. This should ideally be set to the user agent "
        "of the browser you use to view Fansly pages and where the "
        "authorization token was obtained from.",
    )
    parser.add_argument(
        "-ck",
        "--check-key",
        required=False,
        default=None,
        dest="check_key",
        help="Fansly's _checkKey in the main.js on https://fansly.com. "
        "Essential for digital signature and preventing bans.",
    )
    # parser.add_argument(
    #     '-sid', '--session-id',
    #     required=False,
    #     default=None,
    #     dest='session_id',
    #     help="Fansly's session ID.",
    # )

    # endregion Essentials

    # region Download modes

    download_modes = parser.add_mutually_exclusive_group(required=False)

    download_modes.add_argument(
        "--normal",
        required=False,
        default=False,
        action="store_true",
        dest="download_mode_normal",
        help='Use "Normal" download mode. This will download messages and timeline media.',
    )
    download_modes.add_argument(
        "--messages",
        required=False,
        default=False,
        action="store_true",
        dest="download_mode_messages",
        help='Use "Messages" download mode. This will download messages only.',
    )
    download_modes.add_argument(
        "--timeline",
        required=False,
        default=False,
        action="store_true",
        dest="download_mode_timeline",
        help='Use "Timeline" download mode. This will download timeline content only.',
    )
    download_modes.add_argument(
        "--collection",
        required=False,
        default=False,
        action="store_true",
        dest="download_mode_collection",
        help='Use "Collection" download mode. This will ony download a collection.',
    )
    download_modes.add_argument(
        "--single",
        required=False,
        default=None,
        metavar="REQUESTED_POST",
        dest="download_mode_single",
        help='Use "Single" download mode. This will download a single post '
        "by link or ID from an arbitrary creator. "
        "A post ID must be at least 10 characters and consist of digits only."
        "Example - https://fansly.com/post/1283998432982 -> ID is: 1283998432982",
    )

    # endregion Download Modes

    # region Other Options

    parser.add_argument(
        "-ni",
        "--non-interactive",
        required=False,
        default=False,
        action="store_true",
        dest="non_interactive",
        help="Do not ask for input during warnings and errors that need "
        "your attention but can be automatically continued. "
        "Setting this will download all media of all users without any "
        "intervention.",
    )
    parser.add_argument(
        "-npox",
        "--no-prompt-on-exit",
        required=False,
        default=False,
        action="store_true",
        dest="no_prompt_on_exit",
        help="Do not ask to press <ENTER> at the very end of the program. "
        "Set this for a fully automated/headless experience.",
    )
    parser.add_argument(
        "-nfs",
        "--no-folder-suffix",
        required=False,
        default=False,
        action="store_true",
        dest="no_folder_suffix",
        help='Do not add "_fansly" to the download folder of a creator.',
    )
    parser.add_argument(
        "-np",
        "--no-previews",
        required=False,
        default=False,
        action="store_true",
        dest="no_media_previews",
        help="Do not download media previews (which may contain spam).",
    )
    parser.add_argument(
        "-hd",
        "--hide-downloads",
        required=False,
        default=False,
        action="store_true",
        dest="hide_downloads",
        help="Do not show download information.",
    )
    parser.add_argument(
        "-hsd",
        "--hide-skipped-downloads",
        required=False,
        default=False,
        action="store_true",
        dest="hide_skipped_downloads",
        help="Do not show download information for skipped files.",
    )
    parser.add_argument(
        "-nof",
        "--no-open-folder",
        required=False,
        default=False,
        action="store_true",
        dest="no_open_folder",
        help="Do not open the download folder on creator completion.",
    )
    parser.add_argument(
        "-nsm",
        "--no-separate-messages",
        required=False,
        default=False,
        action="store_true",
        dest="no_separate_messages",
        help="Do not separate messages into their own folder.",
    )
    parser.add_argument(
        "-nst",
        "--no-separate-timeline",
        required=False,
        default=False,
        action="store_true",
        dest="no_separate_timeline",
        help="Do not separate timeline content into it's own folder.",
    )
    parser.add_argument(
        "-smd",
        "--separate-metadata",
        required=False,
        default=False,
        action="store_true",
        dest="separate_metadata",
        help="Do not separate metadata into it's own folder.",
    )
    parser.add_argument(
        "-sp",
        "--separate-previews",
        required=False,
        default=False,
        action="store_true",
        dest="separate_previews",
        help="Separate preview media (which may contain spam) into their own folder.",
    )
    parser.add_argument(
        "-udt",
        "--use-duplicate-threshold",
        required=False,
        default=False,
        action="store_true",
        dest="use_duplicate_threshold",
        help="Use an internal de-deduplication threshold to not download "
        "already downloaded media again.",
    )
    parser.add_argument(
        "-upd",
        "--use-pagination-duplication",
        required=False,
        default=False,
        action="store_true",
        dest="use_pagination_duplication",
        help="Check each page for duplicates during pagination.",
    )
    parser.add_argument(
        "-mh",
        "--metadata-handling",
        required=False,
        default=None,
        type=str,
        dest="metadata_handling",
        help="How to handle media EXIF metadata. "
        "Supported strategies: Advanced (Default), Simple",
    )
    parser.add_argument(
        "-tr",
        "--timeline-retries",
        required=False,
        default=None,
        type=int,
        dest="timeline_retries",
        help="Number of retries on empty timelines. Defaults to 1. "
        "Part of anti-rate-limiting measures - try bumping up to eg. 2 "
        "if nothing gets downloaded. Also see the explanation of "
        "--timeline-delay-seconds.",
    )
    parser.add_argument(
        "-td",
        "--timeline-delay-seconds",
        required=False,
        default=None,
        type=int,
        dest="timeline_delay_seconds",
        help="Number of seconds to wait before retrying empty timelines. "
        "Defaults to 60. "
        "Part of anti-rate-limiting measures - 1 retry/60 seconds works "
        "all the time but also unnecessarily delays at the proper end of "
        "a creator's timeline - since reaching the end and being "
        "rate-limited is indistinguishable as of now. "
        "You may try to lower this or set to 0 in order to speed things "
        "up - but if nothing gets downloaded the Fansly server firewalls "
        "rate-limited you. "
        "You can calculate yourself how long a download session "
        "(without download time and extra retries) will last at minimum: "
        "NUMBER_OF_CREATORS * TIMELINE_RETRIES * TIMELINE_DELAY_SECONDS",
    )
    parser.add_argument(
        "-ar",
        "--api-max-retries",
        required=False,
        default=None,
        type=int,
        dest="api_max_retries",
        help="Maximum number of retries for API requests that fail with 429 (rate limit). "
        "Defaults to 10. "
        "Higher values allow exponential backoff to reach maximum backoff time "
        "(30s → 60s → 120s → 240s → 300s max) before giving up. "
        "Lower values may cause downloads to fail during sustained rate limiting.",
    )

    parser.add_argument(
        "--db-sync-commits",
        required=False,
        default=None,
        type=int,
        dest="db_sync_commits",
        help="[DEPRECATED - SQLite only] Number of commits before syncing database to remote location. "
        "Only applies to databases larger than --db-sync-min-size. "
        "PostgreSQL manages transactions natively.",
    )
    parser.add_argument(
        "--db-sync-seconds",
        required=False,
        default=None,
        type=int,
        dest="db_sync_seconds",
        help="[DEPRECATED - SQLite only] Number of seconds between database syncs to remote location. "
        "Only applies to databases larger than --db-sync-min-size. "
        "PostgreSQL manages transactions natively.",
    )
    parser.add_argument(
        "--db-sync-min-size",
        required=False,
        default=None,
        type=int,
        dest="db_sync_min_size",
        help="[DEPRECATED - SQLite only] Minimum database size in MB to enable background syncing. "
        "Smaller databases are synced immediately. "
        "PostgreSQL manages transactions natively.",
    )
    parser.add_argument(
        "--metadata-db-file",
        required=False,
        default=None,
        type=str,
        dest="metadata_db_file",
        help="[DEPRECATED - SQLite only] Custom path for the metadata database file. "
        "Use PostgreSQL connection parameters (--pg-host, --pg-database, etc.) instead.",
    )
    # PostgreSQL arguments
    parser.add_argument(
        "--pg-host",
        required=False,
        default=None,
        type=str,
        dest="pg_host",
        help="PostgreSQL host (default: localhost)",
    )
    parser.add_argument(
        "--pg-port",
        required=False,
        default=None,
        type=int,
        dest="pg_port",
        help="PostgreSQL port (default: 5432)",
    )
    parser.add_argument(
        "--pg-database",
        required=False,
        default=None,
        type=str,
        dest="pg_database",
        help="PostgreSQL database name (default: fansly_metadata)",
    )
    parser.add_argument(
        "--pg-user",
        required=False,
        default=None,
        type=str,
        dest="pg_user",
        help="PostgreSQL username (default: fansly_user)",
    )
    parser.add_argument(
        "--pg-password",
        required=False,
        default=None,
        type=str,
        dest="pg_password",
        help="PostgreSQL password (prefer FANSLY_PG_PASSWORD environment variable)",
    )
    parser.add_argument(
        "--temp-folder",
        required=False,
        default=None,
        type=str,
        dest="temp_folder",
        help="Custom path for temporary files. "
        "If not specified, uses system default temp folder.",
    )
    parser.add_argument(
        "--stash-only",
        required=False,
        default=False,
        action="store_true",
        dest="stash_only",
        help="Only process Stash metadata, skip downloading media.",
    )

    # endregion Other Options
    parser.add_argument(
        "--stash-scheme",
        required=False,
        default=None,
        dest="stash_scheme",
        help="Scheme for StashContext (e.g., http or https).",
    )
    parser.add_argument(
        "--stash-host",
        required=False,
        default=None,
        dest="stash_host",
        help="Host for StashContext (e.g., localhost).",
    )
    parser.add_argument(
        "--stash-port",
        required=False,
        default=None,
        type=int,
        dest="stash_port",
        help="Port for StashContext (e.g., 9999).",
    )
    parser.add_argument(
        "--stash-apikey",
        required=False,
        default=None,
        dest="stash_apikey",
        help="API key for StashContext.",
    )

    # region Developer/troubleshooting arguments

    parser.add_argument(
        "--debug",
        required=False,
        default=False,
        action="store_true",
        help="Print debugging output. Only for developers or troubleshooting.",
    )
    parser.add_argument(
        "--updated-to",
        required=False,
        default=None,
        help="This is for internal use of the self-updating functionality only.",
    )

    # endregion Dev/Tshoot

    return parser.parse_args()


def check_attributes(
    args: argparse.Namespace,
    config: FanslyConfig,
    arg_attribute: str,
    config_attribute: str,
) -> None:
    """A helper method to validate the presence of attributes (properties)
    in `argparse.Namespace` and `FanslyConfig` objects for mapping
    arguments. This is to locate code changes and typos.

    :param args: The arguments parsed.
    :type args: argparse.Namespace
    :param config: The Fansly Downloader NG configuration.
    :type config: FanslyConfig
    :param arg_attribute: The argument destination variable name.
    :type arg_attribute: str
    :param config_attribute: The configuration attribute/property name.
    :type config_attribute: str

    :raise RuntimeError: Raised when an attribute does not exist.

    if args.stash_scheme or args.stash_host or args.stash_port or args.stash_apikey:
        config.stash_context_conn = {
            "scheme": args.stash_scheme or config.stash_context_conn.get("scheme", "http"),
            "host": args.stash_host or config.stash_context_conn.get("host", "localhost"),
            "port": args.stash_port or config.stash_context_conn.get("port", 9999),
            "apikey": args.stash_apikey or config.stash_context_conn.get("apikey", ""),
        }
    """
    if hasattr(args, arg_attribute) and hasattr(config, config_attribute):
        return

    raise RuntimeError(
        "Internal argument configuration error - please contact the developer."
        f"(args.{arg_attribute} == {hasattr(args, arg_attribute)}, "
        f"config.{config_attribute} == {hasattr(config, config_attribute)})"
    )


def _handle_debug_settings(args: argparse.Namespace, config: FanslyConfig) -> None:
    """Handle debug settings and logging."""
    config.debug = args.debug
    set_debug_enabled(args.debug)

    if args.debug:
        textio_logger.opt(depth=1).log("DEBUG", f"Args: {args}")
        print()


def _handle_user_settings(args: argparse.Namespace, config: FanslyConfig) -> bool:
    """Handle user settings and return if config was overridden."""
    config_overridden = False

    # Handle use_following_with_pagination (combined option)
    if args.use_following_with_pagination:
        config.use_following = True
        config.use_pagination_duplication = True
        config_overridden = True
        # If this combined option is used, we don't need to check the individual options
        return config_overridden

    # Check for conflicting arguments
    if args.use_following and args.users is not None:
        raise ConfigError(
            "Cannot use both --use-following and --user options at the same time. "
            "Please use either --use-following to process your following list, "
            "or --user to specify target creators."
        )

    # Handle use_following
    if args.use_following:
        config.use_following = True
        config_overridden = True

    if args.users is None:
        return config_overridden

    users_line = " ".join(args.users)
    config.user_names = sanitize_creator_names(parse_items_from_line(users_line))
    config_overridden = True

    if config.debug:
        textio_logger.opt(depth=1).log(
            "DEBUG", f"Value of `args.users` is: {args.users}"
        )
        textio_logger.opt(depth=1).log(
            "DEBUG", f"`args.users` is None == {args.users is None}"
        )
        textio_logger.opt(depth=1).log(
            "DEBUG", f"`config.username` is: {config.user_names}"
        )
        print()

    return config_overridden


def _handle_download_mode(
    args: argparse.Namespace, config: FanslyConfig
) -> tuple[bool, bool]:
    """Handle download mode settings and return (config_overridden, download_mode_set)."""
    config_overridden = False
    download_mode_set = False

    # Map of argument flags to download modes
    mode_map = {
        "stash_only": DownloadMode.STASH_ONLY,
        "download_mode_normal": DownloadMode.NORMAL,
        "download_mode_messages": DownloadMode.MESSAGES,
        "download_mode_timeline": DownloadMode.TIMELINE,
        "download_mode_collection": DownloadMode.COLLECTION,
    }

    # Check each mode flag
    for arg_name, mode in mode_map.items():
        if getattr(args, arg_name, False):
            config.download_mode = mode
            return True, True

    # Handle single mode separately due to additional validation
    if args.download_mode_single is not None:
        post_id = get_post_id_from_request(args.download_mode_single)
        if not is_valid_post_id(post_id):
            raise ConfigError(
                f"Argument error - '{post_id}' is not a valid post ID. "
                "For an ID at least 10 characters/only digits are required."
            )
        config.download_mode = DownloadMode.SINGLE
        config.post_id = post_id
        return True, True

    return config_overridden, download_mode_set


def _handle_metadata_settings(args: argparse.Namespace, config: FanslyConfig) -> bool:
    """Handle metadata settings and return if config was overridden."""
    if args.metadata_handling is None:
        return False

    handling = args.metadata_handling.strip().lower()
    try:
        config.metadata_handling = MetadataHandling(handling)
    except ValueError:
        raise ConfigError(
            f"Argument error - '{handling}' is not a valid metadata handling strategy."
        )
    else:
        return True


def _handle_path_settings(
    args: argparse.Namespace, config: FanslyConfig, attr_name: str
) -> bool:
    """Handle path-type settings and return if config was overridden."""
    arg_attribute = getattr(args, attr_name)
    if arg_attribute is None:
        return False

    if attr_name == "temp_folder":
        if arg_attribute:  # Only set if not empty string
            setattr(config, attr_name, Path(arg_attribute))
        else:
            setattr(config, attr_name, None)
    elif attr_name == "download_directory":
        setattr(config, attr_name, Path(arg_attribute))
    else:
        setattr(config, attr_name, arg_attribute)

    return True


def _handle_not_none_settings(args: argparse.Namespace, config: FanslyConfig) -> bool:
    """Handle settings that should be set when not None."""
    check_attr = partial(check_attributes, args, config)
    config_overridden = False

    not_none_settings = [
        "download_directory",
        "token",
        "user_agent",
        "check_key",
        "updated_to",
        "db_sync_commits",
        "db_sync_seconds",
        "db_sync_min_size",
        "metadata_db_file",
        "temp_folder",
        # PostgreSQL settings
        "pg_host",
        "pg_port",
        "pg_database",
        "pg_user",
        "pg_password",
    ]

    for attr_name in not_none_settings:
        check_attr(attr_name, attr_name)
        if _handle_path_settings(args, config, attr_name):
            config_overridden = True

    return config_overridden


def _handle_boolean_settings(args: argparse.Namespace, config: FanslyConfig) -> bool:
    """Handle boolean settings and return if config was overridden."""
    check_attr = partial(check_attributes, args, config)
    config_overridden = False

    # Handle positive boolean flags
    positive_bools = [
        "separate_previews",
        "use_duplicate_threshold",
        "use_pagination_duplication",
        "separate_metadata",
        "reverse_order",
    ]

    for attr_name in positive_bools:
        check_attr(attr_name, attr_name)
        arg_attribute = getattr(args, attr_name)
        if arg_attribute is True:
            setattr(config, attr_name, arg_attribute)
            config_overridden = True

    # Handle negative boolean flags
    negative_bool_map = [
        ("non_interactive", "interactive"),
        ("no_prompt_on_exit", "prompt_on_exit"),
        ("no_folder_suffix", "use_folder_suffix"),
        ("no_media_previews", "download_media_previews"),
        ("hide_downloads", "show_downloads"),
        ("hide_skipped_downloads", "show_skipped_downloads"),
        ("no_open_folder", "open_folder_when_finished"),
        ("no_separate_messages", "separate_messages"),
        ("no_separate_timeline", "separate_timeline"),
    ]

    for arg_name, config_name in negative_bool_map:
        check_attr(arg_name, config_name)
        arg_attribute = getattr(args, arg_name)
        if arg_attribute is True:
            setattr(config, config_name, not arg_attribute)
            config_overridden = True

    return config_overridden


def _handle_unsigned_ints(args: argparse.Namespace, config: FanslyConfig) -> bool:
    """Handle unsigned integer settings and return if config was overridden."""
    check_attr = partial(check_attributes, args, config)
    config_overridden = False

    unsigned_ints = [
        "timeline_retries",
        "timeline_delay_seconds",
        "api_max_retries",
    ]

    for attr_name in unsigned_ints:
        check_attr(attr_name, attr_name)
        arg_attribute = getattr(args, attr_name)

        if arg_attribute is None:
            continue

        try:
            int_value = max(0, int(arg_attribute))
            config_attribute = getattr(config, attr_name)
            if int_value != int(config_attribute):
                setattr(config, attr_name, int_value)
                config_overridden = True
        except ValueError:
            pass

    return config_overridden


def map_args_to_config(args: argparse.Namespace, config: FanslyConfig) -> bool:
    """Maps command-line arguments to the configuration object of
    the current session.

    :param argparse.Namespace args: The command-line arguments
        retrieved via argparse.
    :param FanslyConfig config: The program configuration to map the
        arguments to.

    :return bool download_mode_set: Used to determine whether the
        download mode has been specified with the command line.
    """
    if config.config_path is None:
        raise RuntimeError(
            "Internal error mapping arguments - configuration path not set. Load the config first."
        )

    config_overridden = False
    download_mode_set = False

    # Handle each group of settings
    _handle_debug_settings(args, config)

    if _handle_user_settings(args, config):
        config_overridden = True

    mode_override, mode_set = _handle_download_mode(args, config)
    if mode_override:
        config_overridden = True
    if mode_set:
        download_mode_set = True

    if _handle_metadata_settings(args, config):
        config_overridden = True

    if _handle_not_none_settings(args, config):
        config_overridden = True

    if _handle_boolean_settings(args, config):
        config_overridden = True

    if _handle_unsigned_ints(args, config):
        config_overridden = True

    if config_overridden:
        textio_logger.opt(depth=1).log(
            "WARNING",
            "You have specified some command-line arguments that override config.ini settings.\n"
            f"{20 * ' '}A separate, temporary config file will be generated for this session\n"
            f"{20 * ' '}to prevent accidental changes to your original configuration.\n",
        )
        # Save original config path before switching to temporary config_args.ini
        # This ensures tokens obtained from login are saved to the original config
        config.original_config_path = config.config_path
        config.config_path = config.config_path.parent / "config_args.ini"
        save_config_or_raise(config)

    return download_mode_set
