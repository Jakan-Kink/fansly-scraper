"""Configuration Class for Shared State"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import SecretStr
from stash_graphql_client import StashClient, StashContext

from api import FanslyApi
from config.metadatahandling import MetadataHandling
from config.modes import DownloadMode
from config.schema import (
    CacheSection,
    ConfigSchema,
    LoggingSection,
    MyAccountSection,
    OptionsSection,
    PostgresSection,
    StashContextSection,
    TargetedCreatorSection,
)


if TYPE_CHECKING:
    from metadata import Database


@dataclass
class FanslyConfig:
    # region Fields

    # region File-Independent Fields

    # Mandatory property
    # This should be set to __version__ in the main script.
    program_version: str

    # Private fields - initialized as None but set through methods
    # The getters will ensure they're not None when accessed
    _api: FanslyApi | None = None
    _database: Database | None = None
    _stash: Any = None  # StashContext | None

    # Command line flags
    use_following: bool = False
    reverse_order: bool = False

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
    _schema: ConfigSchema | None = field(default=None)
    _background_tasks: list[asyncio.Task] = field(default_factory=list)

    # endregion File-Independent

    # region config.ini Fields

    # TargetedCreator > username
    user_names: set[str] | None = None

    # MyAccount
    token: str | None = None
    user_agent: str | None = None
    check_key: str | None = None
    username: str | None = None  # For automatic login
    password: str | None = None  # For automatic login
    # session_id: str = 'null'

    # Options
    # "Normal" | "Timeline" | "Messages" | "Single" | "Collection"
    download_mode: DownloadMode = DownloadMode.NORMAL
    download_directory: Path | None = None
    download_media_previews: bool = True
    # "Advanced" | "Simple"
    metadata_handling: MetadataHandling = MetadataHandling.ADVANCED
    open_folder_when_finished: bool = True
    separate_messages: bool = True
    separate_previews: bool = False
    separate_timeline: bool = True
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
    # Number of retries to get a timeline
    timeline_retries: int = 1
    # Anti-rate-limiting delay in seconds
    timeline_delay_seconds: int = 60
    # Maximum number of retries for API requests that fail with 429 (rate limit)
    # Allows exponential backoff progression: 30s → 60s → 120s → 240s → 300s (max)
    api_max_retries: int = 10

    # Rate limiting configuration
    rate_limiting_enabled: bool = True
    rate_limiting_adaptive: bool = True
    rate_limiting_requests_per_minute: int = 60  # 1 request per second
    rate_limiting_burst_size: int = 10  # Allow bursts of 10 requests
    rate_limiting_retry_after_seconds: int = 30  # Base backoff duration
    rate_limiting_backoff_factor: float = 1.5  # Exponential backoff multiplier
    rate_limiting_max_backoff_seconds: int = 300  # Cap backoff at 5 minutes
    # Database sync settings (SQLite only - deprecated for PostgreSQL)
    db_sync_commits: int | None = None  # Sync after this many commits (default: 1000)
    db_sync_seconds: int | None = None  # Sync after this many seconds (default: 60)
    db_sync_min_size: int | None = (
        None  # Only use background sync for DBs larger than this MB (default: 50)
    )

    # PostgreSQL configuration
    pg_host: str = "localhost"
    pg_port: int = 5432
    pg_database: str = "fansly_metadata"
    pg_user: str = "fansly_user"
    pg_password: str | None = None  # Prefer using FANSLY_PG_PASSWORD env var

    # PostgreSQL SSL/TLS settings
    pg_sslmode: str = "prefer"
    pg_sslcert: Path | None = None
    pg_sslkey: Path | None = None
    pg_sslrootcert: Path | None = None

    # PostgreSQL connection pool settings
    pg_pool_size: int = 5
    pg_max_overflow: int = 10
    pg_pool_timeout: int = 30

    # Temporary folder for downloads
    temp_folder: Path | None = None  # When None, use system default temp folder

    # Cache
    cached_device_id: str | None = None
    cached_device_id_timestamp: int | None = None

    # Monitoring
    # Per-run baseline datetime: when set, the daemon uses this instead of each
    # creator's stored MonitorState.lastCheckedAt.  Set via --monitor-since or
    # --full-pass CLI flags; loaded from schema.monitoring.session_baseline.
    monitoring_session_baseline: datetime | None = None
    # When True, enter the post-batch monitoring daemon after the normal
    # batch download completes.  Set via --daemon / -d CLI flag.
    daemon_mode: bool = False
    # Seconds of total failure before the daemon exits with DAEMON_UNRECOVERABLE.
    # Loaded from schema.monitoring.unrecoverable_error_timeout_seconds.
    unrecoverable_error_timeout_seconds: int = 3600
    # Live Rich dashboard while the daemon runs. Default on; disable when
    # piping through log-capture tools that mangle ANSI escape sequences.
    # Loaded from schema.monitoring.dashboard_enabled.
    monitoring_dashboard_enabled: bool = True

    # StashContext
    # Widened to dict[str, Any] so port:int coexists with the string-valued keys.
    # StashContext accepts a port:int, so we don't need to stringify it.
    stash_context_conn: dict[str, Any] | None = None

    # Logging
    log_levels: dict[str, str] = field(
        default_factory=lambda: {
            "sqlalchemy": "INFO",
            "stash_console": "INFO",
            "stash_file": "INFO",
            "textio": "INFO",
            "websocket": "INFO",
            "json": "INFO",
        }
    )
    # endregion config.ini

    # endregion Fields

    # region Methods

    def get_api(self) -> FanslyApi:
        """Get the API instance without session setup.

        If username/password are configured, creates API with empty token for login.
        Otherwise requires a valid token to be configured.
        """
        if self._api is None:
            token = self.get_unscrambled_token()
            user_agent = self.user_agent

            # Allow empty token if username/password are provided (for login flow)
            has_login_credentials = self.username and self.password

            if (
                user_agent
                and self.check_key
                and (self.token_is_valid() or has_login_credentials)
            ):
                # Initialize rate limiter with visual display
                from api.rate_limiter import RateLimiter
                from api.rate_limiter_display import RateLimiterDisplay

                rate_limiter = RateLimiter(self)
                self._rate_limiter_display = RateLimiterDisplay(rate_limiter)
                self._rate_limiter_display.start()

                # Use empty string if token is invalid (for login flow)
                # Otherwise use the valid unscrambled token
                api_token = token if self.token_is_valid() else ""

                self._api = FanslyApi(
                    token=api_token,
                    user_agent=user_agent,
                    check_key=self.check_key,
                    device_id=self.cached_device_id,
                    device_id_timestamp=self.cached_device_id_timestamp,
                    on_device_updated=self._save_config,
                    rate_limiter=rate_limiter,
                    config=self,
                )

                # If we have login credentials but no valid token, perform login
                if has_login_credentials and not self.token_is_valid():
                    try:
                        self._api.login(self.username, self.password)  # type: ignore
                        # Update config with the new token
                        self.token = self._api.token
                        self._save_config()
                    except Exception as e:
                        raise RuntimeError(f"Login failed: {e}")

        if self._api is None:
            raise RuntimeError("Failed to create API instance - check configuration")

        return self._api

    async def setup_api(self) -> FanslyApi:
        """Set up the API instance including async session setup."""
        api = self.get_api()

        # Check if api is None before trying to access its attributes
        if api is None:
            raise RuntimeError("Token or user agent error creating Fansly API object.")

        if api.session_id == "null":
            await api.setup_session()

            # Explicit save - on init of FanslyApi() self._api was None
            self._save_config()

        return api

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

        return ", ".join(sorted(self.user_names, key=str.lower))

    def download_mode_str(self) -> str:
        """Gets the string representation of `download_mode`."""
        return str(self.download_mode).capitalize()

    def metadata_handling_str(self) -> str:
        """Gets the string representation of `metadata_handling`."""
        return str(self.metadata_handling).capitalize()

    def _load_raw_config(self) -> list[str]:
        """Legacy stub — config loading is now handled by ``load_or_migrate``."""
        return []

    def _save_config(self) -> bool:
        """Save current config attributes to disk via the schema's ``dump_yaml``."""
        if self.config_path is None:
            return False

        # Update cache from current API state if available
        if self._api is not None:
            self.cached_device_id = self._api.device_id
            self.cached_device_id_timestamp = self._api.device_id_timestamp

        updated_schema = _rebuild_schema_from_config(self)
        self._schema = updated_schema
        updated_schema.dump_yaml(self.config_path)
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
            scramble_suffix = "fNs"
            token = self.token

            if token[-3:] == scramble_suffix:
                scrambled_token = token.rstrip(scramble_suffix)

                unscrambled_chars = [""] * len(scrambled_token)
                step_size = 7
                scrambled_index = 0

                for offset in range(step_size):
                    for result_position in range(
                        offset, len(unscrambled_chars), step_size
                    ):
                        unscrambled_chars[result_position] = scrambled_token[
                            scrambled_index
                        ]
                        scrambled_index += 1

                return "".join(unscrambled_chars)

            return self.token

        return self.token

    # endregion

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
        except RuntimeError as e:
            raise RuntimeError(f"Failed to initialize Stash API: {e}")
        else:
            return stash_context.client

    def get_background_tasks(self) -> list[asyncio.Task]:
        return self._background_tasks

    def cancel_background_tasks(self) -> None:
        for task in self._background_tasks:
            if not task.done():
                task.cancel()
        self._background_tasks.clear()


def _rebuild_schema_from_config(config: FanslyConfig) -> ConfigSchema:
    """Rebuild a ``ConfigSchema`` from the current ``FanslyConfig`` attribute values.

    Called by ``_save_config()`` before writing to disk. This is the inverse of
    ``_populate_config_from_schema``: runtime mutable attributes → typed schema.
    """
    usernames: list[str] = (
        sorted(config.user_names) if config.user_names else ["ReplaceMe"]
    )

    stash_section: StashContextSection | None = None
    if config.stash_context_conn is not None:
        conn = config.stash_context_conn
        stash_section = StashContextSection(
            scheme=conn.get("scheme", "http"),
            host=conn.get("host", "localhost"),
            port=int(conn.get("port", 9999)),
            apikey=conn.get("apikey", ""),
        )

    # Re-use the existing schema if available so we don't lose monitoring/logic
    base = config._schema if config._schema is not None else ConfigSchema()

    # Rebuild each section from config attributes
    base.targeted_creator = TargetedCreatorSection(
        usernames=usernames,
        use_following=config.use_following,
        use_following_with_pagination=base.targeted_creator.use_following_with_pagination,
    )
    base.my_account = MyAccountSection(
        authorization_token=SecretStr(config.token or ""),
        user_agent=config.user_agent or "ReplaceMe",
        check_key=config.check_key or "qybZy9-fyszis-bybxyf",
        username=config.username,
        password=SecretStr(config.password) if config.password else None,
    )
    base.options = OptionsSection(
        download_directory=str(config.download_directory or "Local_directory"),
        download_mode=config.download_mode,
        metadata_handling=config.metadata_handling,
        show_downloads=config.show_downloads,
        show_skipped_downloads=config.show_skipped_downloads,
        download_media_previews=config.download_media_previews,
        open_folder_when_finished=config.open_folder_when_finished,
        separate_messages=config.separate_messages,
        separate_previews=config.separate_previews,
        separate_timeline=config.separate_timeline,
        use_duplicate_threshold=config.use_duplicate_threshold,
        use_pagination_duplication=config.use_pagination_duplication,
        use_folder_suffix=config.use_folder_suffix,
        interactive=config.interactive,
        prompt_on_exit=config.prompt_on_exit,
        debug=config.debug,
        trace=config.trace,
        timeline_retries=config.timeline_retries,
        timeline_delay_seconds=config.timeline_delay_seconds,
        api_max_retries=config.api_max_retries,
        rate_limiting_enabled=config.rate_limiting_enabled,
        rate_limiting_adaptive=config.rate_limiting_adaptive,
        rate_limiting_requests_per_minute=config.rate_limiting_requests_per_minute,
        rate_limiting_burst_size=config.rate_limiting_burst_size,
        rate_limiting_retry_after_seconds=config.rate_limiting_retry_after_seconds,
        rate_limiting_backoff_factor=config.rate_limiting_backoff_factor,
        rate_limiting_max_backoff_seconds=config.rate_limiting_max_backoff_seconds,
        db_sync_commits=config.db_sync_commits,
        db_sync_seconds=config.db_sync_seconds,
        db_sync_min_size=config.db_sync_min_size,
        temp_folder=str(config.temp_folder) if config.temp_folder is not None else None,
    )

    pg_password_secret: SecretStr | None = None
    if config.pg_password:
        pg_password_secret = SecretStr(config.pg_password)
    base.postgres = PostgresSection(
        pg_host=config.pg_host,
        pg_port=config.pg_port,
        pg_database=config.pg_database,
        pg_user=config.pg_user,
        pg_password=pg_password_secret,
        pg_sslmode=config.pg_sslmode,
        pg_sslcert=str(config.pg_sslcert) if config.pg_sslcert is not None else None,
        pg_sslkey=str(config.pg_sslkey) if config.pg_sslkey is not None else None,
        pg_sslrootcert=(
            str(config.pg_sslrootcert) if config.pg_sslrootcert is not None else None
        ),
        pg_pool_size=config.pg_pool_size,
        pg_max_overflow=config.pg_max_overflow,
        pg_pool_timeout=config.pg_pool_timeout,
    )

    # Cache: read from config attributes (already updated before this call)
    base.cache = CacheSection(
        device_id=config.cached_device_id,
        device_id_timestamp=config.cached_device_id_timestamp,
    )

    log = config.log_levels
    base.logging = LoggingSection(
        sqlalchemy=log.get("sqlalchemy", "INFO"),
        stash_console=log.get("stash_console", "INFO"),
        stash_file=log.get("stash_file", "INFO"),
        textio=log.get("textio", "INFO"),
        websocket=log.get("websocket", "INFO"),
        json=log.get("json", "INFO"),
    )

    base.stash_context = stash_section

    return base
