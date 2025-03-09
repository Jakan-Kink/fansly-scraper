"""Configuration Class for Shared State"""

from __future__ import annotations

import asyncio
from configparser import ConfigParser
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from api import FanslyApi
from config.metadatahandling import MetadataHandling
from config.modes import DownloadMode
from pathio import PathConfig

if TYPE_CHECKING:
    from metadata import Base, Database
    from stash import StashClient, StashContext  # noqa: F401


@dataclass
class FanslyConfig(PathConfig):
    # region Fields

    # region File-Independent Fields

    # Mandatory property
    # This should be set to __version__ in the main script.
    program_version: str

    # Command line flags
    use_following: bool = False

    # Define base threshold (used for when modules don't provide vars)
    DUPLICATE_THRESHOLD: int = 50

    # Batch size for batched API access (Fansly API size limit)
    BATCH_SIZE: int = 150

    # Configuration file
    config_path: Path | None = None

    # Misc
    token_from_browser_name: str | None = None
    debug: bool = False
    trace: bool = False  # For very detailed logging
    # If specified on the command-line
    post_id: str | None = None
    # Set on start after self-update
    updated_to: str | None = None

    # Objects
    _parser = ConfigParser(interpolation=None)
    _api: FanslyApi | None = None
    _database: Database | None = None
    _base: Base | None = None
    _stash: Any | None = None  # StashContext | None
    _background_tasks: list[asyncio.Task] = field(default_factory=list)

    # endregion File-Independent

    # region config.ini Fields

    # TargetedCreator > username
    user_names: set[str] | None = None

    # MyAccount
    token: str | None = None
    user_agent: str | None = None
    check_key: str | None = None
    # session_id: str = 'null'

    # Options
    # "Normal" | "Timeline" | "Messages" | "Single" | "Collection"
    download_mode: DownloadMode = DownloadMode.NORMAL
    download_directory: None | Path = None
    download_media_previews: bool = True
    # "Advanced" | "Simple"
    metadata_handling: MetadataHandling = MetadataHandling.ADVANCED
    open_folder_when_finished: bool = True
    separate_messages: bool = True
    separate_previews: bool = False
    separate_timeline: bool = True
    separate_metadata: bool = False
    show_downloads: bool = True
    show_skipped_downloads: bool = True
    use_duplicate_threshold: bool = False
    use_pagination_duplication: bool = False  # Check each page for duplicates
    use_folder_suffix: bool = True
    # Show input prompts or sleep - for automation/scheduling purposes
    interactive: bool = True
    # Should there be a "Press <ENTER>" prompt at the very end of the program?
    # This helps for semi-automated runs (interactive=False) when coming back
    # to the computer and wanting to see what happened in the console window.
    prompt_on_exit: bool = True
    metadata_db_file: Path | None = None

    # Number of retries to get a timeline
    timeline_retries: int = 1
    # Anti-rate-limiting delay in seconds
    timeline_delay_seconds: int = 60
    # Database sync settings
    db_sync_commits: int | None = None  # Sync after this many commits (default: 1000)
    db_sync_seconds: int | None = None  # Sync after this many seconds (default: 60)
    db_sync_min_size: int | None = (
        None  # Only use background sync for DBs larger than this MB (default: 50)
    )

    # Temporary folder for downloads
    temp_folder: Path | None = None  # When None, use system default temp folder

    # Cache
    cached_device_id: str | None = None
    cached_device_id_timestamp: int | None = None

    # Logic
    check_key_pattern: str | None = None
    main_js_pattern: str | None = None

    # StashContext
    stash_context_conn: dict[str, str] | None = None

    # Logging
    log_levels: dict[str, str] = field(
        default_factory=lambda: {
            "sqlalchemy": "INFO",
            "stash_console": "INFO",
            "stash_file": "INFO",
            "textio": "INFO",
            "json": "INFO",
        }
    )
    # endregion config.ini

    # endregion Fields

    # region Methods

    def get_api(self) -> FanslyApi:
        """Get the API instance without session setup."""
        if self._api is None:
            token = self.get_unscrambled_token()
            user_agent = self.user_agent

            if token and user_agent and self.check_key:
                self._api = FanslyApi(
                    token=token,
                    user_agent=user_agent,
                    check_key=self.check_key,
                    device_id=self.cached_device_id,
                    device_id_timestamp=self.cached_device_id_timestamp,
                    on_device_updated=self._save_config,
                )

        return self._api

    async def setup_api(self) -> None:
        """Set up the API instance including async session setup."""
        api = self.get_api()
        if api.session_id == "null":
            await api.setup_session()

            # Explicit save - on init of FanslyApi() self._api was None
            self._save_config()

        if self._api is None:
            raise RuntimeError("Token or user agent error creating Fansly API object.")

        return self._api

    def user_names_str(self) -> str:
        """Returns a nicely formatted and alphabetically sorted list of
        creator names - for console or config file output.

        :return: A single line of all creator names, alphabetically sorted
            and separated by commas eg. "alice, bob, chris, dora".
            Returns "ReplaceMe" if user_names is None.
        :rtype: str
        """
        if self.user_names is None:
            return "ReplaceMe"

        return ", ".join(sorted(self.user_names))

    def download_mode_str(self) -> str:
        """Gets the string representation of `download_mode`."""
        return str(self.download_mode).capitalize()

    def metadata_handling_str(self) -> str:
        """Gets the string representation of `metadata_handling`."""
        return str(self.metadata_handling).capitalize()

    def _sync_settings(self) -> None:
        """Syncs the settings of the config object
        to the config parser/config file.

        This helper is required before saving.
        """
        # Ensure all required sections exist
        for section in ["TargetedCreator", "MyAccount", "Options", "Cache", "Logic"]:
            if not self._parser.has_section(section):
                self._parser.add_section(section)

        self._parser.set("TargetedCreator", "username", self.user_names_str())
        # Only save use_following if it already exists in the config file
        if self._parser.has_option("TargetedCreator", "use_following"):
            self._parser.set(
                "TargetedCreator", "use_following", str(self.use_following)
            )

        self._parser.set(
            "MyAccount",
            "authorization_token",
            str(self.token) if self.token is not None else "",
        )
        self._parser.set(
            "MyAccount",
            "user_agent",
            str(self.user_agent) if self.user_agent is not None else "",
        )
        self._parser.set(
            "MyAccount",
            "check_key",
            str(self.check_key) if self.check_key is not None else "",
        )
        # self._parser.set('MyAccount', 'session_id', self.session_id)

        if self.download_directory is None:
            self._parser.set("Options", "download_directory", "Local_directory")
        else:
            self._parser.set(
                "Options", "download_directory", str(self.download_directory)
            )

        self._parser.set("Options", "download_mode", self.download_mode_str())
        self._parser.set("Options", "metadata_handling", self.metadata_handling_str())

        # Booleans
        self._parser.set("Options", "show_downloads", str(self.show_downloads))
        self._parser.set(
            "Options", "show_skipped_downloads", str(self.show_skipped_downloads)
        )
        self._parser.set(
            "Options", "download_media_previews", str(self.download_media_previews)
        )
        self._parser.set(
            "Options", "open_folder_when_finished", str(self.open_folder_when_finished)
        )
        self._parser.set("Options", "separate_messages", str(self.separate_messages))
        self._parser.set("Options", "separate_previews", str(self.separate_previews))
        self._parser.set("Options", "separate_timeline", str(self.separate_timeline))
        self._parser.set("Options", "separate_metadata", str(self.separate_metadata))
        # Only save metadata_db_file if explicitly configured
        if self.metadata_db_file is not None:
            self._parser.set("Options", "metadata_db_file", str(self.metadata_db_file))
        self._parser.set(
            "Options", "use_duplicate_threshold", str(self.use_duplicate_threshold)
        )
        self._parser.set(
            "Options",
            "use_pagination_duplication",
            str(self.use_pagination_duplication),
        )
        self._parser.set("Options", "use_folder_suffix", str(self.use_folder_suffix))
        self._parser.set("Options", "interactive", str(self.interactive))
        self._parser.set("Options", "prompt_on_exit", str(self.prompt_on_exit))

        # Only save debug/trace if they already exist in the config file
        if self._parser.has_option("Options", "debug"):
            self._parser.set("Options", "debug", str(self.debug))
        if self._parser.has_option("Options", "trace"):
            self._parser.set("Options", "trace", str(self.trace))

        # Unsigned ints
        self._parser.set("Options", "timeline_retries", str(self.timeline_retries))
        self._parser.set(
            "Options", "timeline_delay_seconds", str(self.timeline_delay_seconds)
        )

        # Database sync settings - only save if explicitly set
        if self.db_sync_commits is not None:
            self._parser.set("Options", "db_sync_commits", str(self.db_sync_commits))
        if self.db_sync_seconds is not None:
            self._parser.set("Options", "db_sync_seconds", str(self.db_sync_seconds))
        if self.db_sync_min_size is not None:
            self._parser.set("Options", "db_sync_min_size", str(self.db_sync_min_size))

        # Temp folder
        if self.temp_folder is not None:
            self._parser.set("Options", "temp_folder", str(self.temp_folder))

        # StashContext
        if self._stash is not None:
            conn = self._stash.conn
            if not self._parser.has_section("StashContext"):
                self._parser.add_section("StashContext")
            self._parser.set("StashContext", "scheme", conn["scheme"])
            self._parser.set("StashContext", "host", conn["host"])
            self._parser.set("StashContext", "port", str(conn["port"]))
            self._parser.set("StashContext", "apikey", conn["apikey"])
        # Cache
        if self._api is not None:
            self._parser.set("Cache", "device_id", str(self._api.device_id))
            self._parser.set(
                "Cache", "device_id_timestamp", str(self._api.device_id_timestamp)
            )
            self.cached_device_id = self._api.device_id
            self.cached_device_id_timestamp = self._api.device_id_timestamp

        # Logic
        self._parser.set("Logic", "check_key_pattern", str(self.check_key_pattern))
        self._parser.set("Logic", "main_js_pattern", str(self.main_js_pattern))

        # Logging
        if not self._parser.has_section("Logging"):
            self._parser.add_section("Logging")
        for logger, level in self.log_levels.items():
            self._parser.set("Logging", logger, level)

    def _load_raw_config(self) -> list[str]:
        if self.config_path is None:
            return []

        else:
            return self._parser.read(self.config_path)

    def _save_config(self) -> bool:
        if self.config_path is None:
            return False

        else:
            self._sync_settings()

            with self.config_path.open("w", encoding="utf-8") as f:
                self._parser.write(f)
                return True

    def token_is_valid(self) -> bool:
        if self.token is None:
            return False

        return not any(
            [
                len(self.token) < 50,
                "ReplaceMe" in self.token,
            ]
        )

    def useragent_is_valid(self) -> bool:
        if self.user_agent is None:
            return False

        return not any(
            [
                len(self.user_agent) < 40,
                "ReplaceMe" in self.user_agent,
            ]
        )

    def get_unscrambled_token(self) -> str | None:
        """Gets the unscrambled Fansly authorization token.

        Unscrambles the token if necessary.

        :return: The unscrambled Fansly authorization token.
        :rtype: Optional[str]
        """

        if self.token is not None:
            F, c = "fNs", self.token

            if c[-3:] == F:
                c = c.rstrip(F)

                A, B, C = [""] * len(c), 7, 0

                for D in range(B):
                    for E in range(D, len(A), B):
                        A[E] = c[C]
                        C += 1

                return "".join(A)

            else:
                return self.token

        return self.token

    # endregion
    def __post_init__(self):
        # If no metadata_db_file is set, determine fallback
        if self.metadata_db_file is None:
            self.metadata_db_file = self._get_default_metadata_db_file()

    def _get_default_metadata_db_file(self) -> Path:
        """
        Determine the database file location.
        1. Use explicitly configured path if provided
        2. Otherwise use download directory if available
        3. Finally fall back to current directory
        """
        if self.metadata_db_file:
            return Path(self.metadata_db_file)
        if self.download_directory:
            return self.download_directory / "metadata_db.sqlite3"
        return Path.cwd() / "metadata_db.sqlite3"

    def get_stash_context(self) -> StashContext:
        """Get Stash context.

        Returns:
            StashContext instance

        Raises:
            RuntimeError: If no connection data available
        """
        if self._stash is None:
            if self.stash_context_conn is None:
                raise RuntimeError("No StashContext connection data available.")

            from stash import StashContext

            self._stash = StashContext(conn=self.stash_context_conn)
            self._save_config()

        return self._stash

    def get_stash_api(self) -> StashClient:
        """Get Stash API client.

        Returns:
            StashClient instance

        Raises:
            RuntimeError: If failed to initialize Stash API
        """
        try:
            stash_context = self.get_stash_context()
            return stash_context.client
        except RuntimeError as e:
            raise RuntimeError(f"Failed to initialize Stash API: {e}")

    def get_background_tasks(self) -> list[asyncio.Task]:
        return self._background_tasks

    def cancel_background_tasks(self):
        for task in self._background_tasks:
            if not task.done():
                task.cancel()
        self._background_tasks.clear()
