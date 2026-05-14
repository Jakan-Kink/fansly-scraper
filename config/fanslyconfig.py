"""Configuration Class for Shared State"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, SecretStr
from stash_graphql_client import StashClient, StashContext

from api import FanslyApi
from api.rate_limiter import RateLimiter
from api.rate_limiter_display import RateLimiterDisplay
from config.modes import DownloadMode
from config.schema import (
    CacheSection,
    ConfigSchema,
    LoggingSection,
    StashContextSection,
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
    _rate_limiter_display: Any = None  # RateLimiterDisplay | None

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

    # Objects
    _schema: ConfigSchema | None = field(default=None)
    _background_tasks: list[asyncio.Task] = field(default_factory=list)

    # Names of attributes whose runtime value originated from a CLI flag (or
    # any other ephemeral source) and must NOT round-trip into config.yaml on
    # save. ``_rebuild_schema_from_config`` falls back to the YAML-loaded
    # value (held in ``_schema``) for any name in this set. Currently used
    # for mode flags (``--stash-only``, ``--normal``, ``--single``, etc.) so
    # invoking ``--stash-only`` once doesn't make stash-only the new YAML
    # default. Naming chosen to reflect *persistence semantics* rather than
    # source — a future programmatic override path can also opt in.
    _ephemeral_overrides: set[str] = field(default_factory=set)

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
    open_folder_when_finished: bool = True
    separate_messages: bool = True
    separate_previews: bool = False
    separate_timeline: bool = True
    show_downloads: bool = True
    show_skipped_downloads: bool = True
    use_duplicate_threshold: bool = False
    use_pagination_duplication: bool = False  # Check each page for duplicates
    use_folder_suffix: bool = True
    # When False, ignore the creator_content_unchanged check in
    # download/timeline.py and download/wall.py and force a full scan
    # regardless of whether TimelineStats counts + wall structure match
    # the DB. Hidden from YAML at default — see schema.OptionsSection.
    respect_timeline_stats: bool = True
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
    # Three-tier simulator state durations + per-resource poll intervals.
    # Loaded from schema.monitoring.* — see config_options.md for semantics.
    monitoring_active_duration_minutes: int = 60
    monitoring_idle_duration_minutes: int = 120
    monitoring_hidden_duration_minutes: int = 300
    monitoring_timeline_poll_active_seconds: int = 180
    monitoring_timeline_poll_idle_seconds: int = 600
    monitoring_story_poll_active_seconds: int = 30
    monitoring_story_poll_idle_seconds: int = 300
    # Minutes between "WS alive" heartbeat log lines. Lets operators confirm
    # the daemon is running during long hidden phases with no other activity.
    # Loaded from schema.monitoring.heartbeat_interval_minutes.
    monitoring_heartbeat_interval_minutes: int = 15
    # Opt-in flag for livestream recording (silent until a followed creator
    # goes live). Loaded from schema.monitoring.livestream_recording_enabled.
    monitoring_livestream_recording_enabled: bool = False
    # Seconds between followingstreams/online polls.
    # Loaded from schema.monitoring.livestream_poll_interval_seconds.
    monitoring_livestream_poll_interval_seconds: int = 30
    # Seconds between IVS variant manifest re-fetches during live capture.
    # IVS TARGETDURATION is 6 s; capped at 15 s (max ~2.5 segments per fetch).
    # Loaded from schema.monitoring.livestream_manifest_poll_interval_seconds.
    monitoring_livestream_manifest_poll_interval_seconds: int = 3

    # StashContext
    # Widened to dict[str, Any] so port:int coexists with the string-valued keys.
    # StashContext accepts a port:int, so we don't need to stringify it.
    stash_context_conn: dict[str, Any] | None = None
    stash_mapped_path: Path | None = None
    stash_override_dldir_w_mapped: bool = False
    stash_require_stash_only_mode: bool = False

    # Logging
    # ``log_levels`` is the legacy flat ``{logger_name: level_string}``
    # view, kept for ``get_log_level()`` consumers. ``logging`` is the
    # full nested LoggingSection — used by ``setup_handlers()`` to read
    # per-handler rotation knobs (max_size, backup_count, when, utc,
    # compression, keep_uncompressed) with fall-through to
    # ``logging.global_.default_*``.
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
    logging: LoggingSection | None = None
    # endregion config.ini

    # endregion Fields

    # region Methods

    def get_api(self) -> FanslyApi:
        """Return the cached API instance, constructing it on first call. No I/O."""
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
                # Initialize rate limiter with visual display.
                # The display thread is NOT started here — get_api() must stay
                # I/O-free. setup_api() starts it after bootstrap completes.
                rate_limiter = RateLimiter(self)
                self._rate_limiter_display = RateLimiterDisplay(rate_limiter)

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

        if self._api is None:
            raise RuntimeError("Failed to create API instance - check configuration")

        return self._api

    async def setup_api(self) -> FanslyApi:
        """Bootstrap device_id, login if needed, and set up the WebSocket session."""
        api = self.get_api()
        if self._rate_limiter_display is not None:
            self._rate_limiter_display.start()
        await api.update_device_id()

        has_login_credentials = self.username and self.password
        if has_login_credentials and not self.token_is_valid():
            try:
                await api.login(self.username, self.password)  # type: ignore
                self.token = api.token
                self._save_config()
            except Exception as e:
                raise RuntimeError(f"Login failed: {e}") from e

        if api.session_id == "null":
            await api.setup_session()
            self._save_config()

        return api

    def is_username_in_scope(self, username: str | None) -> bool:
        """Return True when *username* is in scope for this invocation.

        Cross-cutting scope predicate shared by every code path that
        decides "is this creator something the user asked us to act
        on?" — daemon WS dispatch (after resolving id→username),
        livestream watcher (api payload carries the username
        directly), polling loops, future entry points. Lives on
        FanslyConfig because the answer is a function of config
        (``user_names`` + ``use_following``) alone — no subsystem
        state, no metadata store, no async.

        Synchronous + string-based on purpose. Callers that only hold a
        creator_id resolve to username first (via their own cached
        account lookup, or by reading the username off whatever
        payload spawned the work). Keeping the predicate id-free
        means scope checks never have to poll the metadata store.

        Resolution:
          - ``use_following=True`` (``-uf`` / ``-ufp``) → True for any
            username. The whole following-list is in scope.
          - Empty ``user_names`` with ``use_following=False`` → True
            (unrestricted edge case; legacy or test-time configs).
          - Populated ``user_names`` → True iff *username*
            (case-insensitive) is in ``user_names``. None or empty
            *username* returns False.

        Args:
            username: Fansly account username (case-insensitive
                compared). ``None`` and empty strings return False
                (under restricted scope). Must be ``str`` or ``None``
                — passing an ``int`` (e.g., a stray creator_id from a
                caller migrating off the id-based shim) raises
                ``TypeError`` to surface the misuse at the entry
                point rather than fail confusingly inside the
                predicate. Content-based validation is deliberately
                NOT performed: Fansly allows all-digit usernames
                ("12345" is a legitimate username) so we cannot
                reject "looks-like-an-id" inputs without breaking
                real operators.

        Returns:
            True when the username is in scope; False otherwise.

        Raises:
            TypeError: When *username* is neither ``str`` nor ``None``.
        """
        if username is not None and not isinstance(username, str):
            raise TypeError(
                "is_username_in_scope() expects str | None; got "
                f"{type(username).__name__}. If you have a creator_id "
                "(int), resolve to username first (e.g., via "
                "daemon.runner._is_creator_in_scope which does the "
                "Account-store lookup) — the canonical predicate is "
                "str-based on purpose."
            )
        if self.use_following:
            return True
        if not self.user_names:
            return True  # unrestricted edge case
        if not username:
            return False  # unknown / empty username — skip
        return username.lower() in {n.lower() for n in self.user_names}

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

    @property
    def stash_active(self) -> bool:
        """Whether Stash integration should engage for the current run.

        True iff a Stash connection is configured AND, if
        ``stash_require_stash_only_mode`` is set, the current download mode
        is ``STASH_ONLY``. Lets users with a separate-host Stash setup
        keep credentials in config without engaging Stash on every run.
        """
        if self.stash_context_conn is None:
            return False
        if self.stash_require_stash_only_mode:
            return self.download_mode == DownloadMode.STASH_ONLY
        return True

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
    stash_section: StashContextSection | None = None
    if config.stash_context_conn is not None:
        conn = config.stash_context_conn
        stash_section = StashContextSection(
            scheme=conn.get("scheme", "http"),
            host=conn.get("host", "localhost"),
            port=int(conn.get("port", 9999)),
            apikey=conn.get("apikey", ""),
            mapped_path=str(config.stash_mapped_path)
            if config.stash_mapped_path is not None
            else None,
            override_dldir_w_mapped=config.stash_override_dldir_w_mapped,
            require_stash_only_mode=config.stash_require_stash_only_mode,
        )

    # Re-use the existing schema if available so we don't lose monitoring/logic
    base = config._schema if config._schema is not None else ConfigSchema()

    # In-place mutation pattern (replaces the prior wholesale section
    # reconstruction). Pydantic v2 adds a field to ``model_fields_set`` on
    # ANY assignment, so we only assign when the runtime value differs
    # from the schema's current value AND the field is not in
    # ``_ephemeral_overrides`` (CLI flags don't pin themselves into YAML).
    # This is what lets ``model_dump(exclude_unset=True)`` in the dump
    # path stay honest across save round-trips.
    def _maybe_set(section: BaseModel, name: str, value: Any) -> None:
        if name in config._ephemeral_overrides:
            return
        current = getattr(section, name, None)
        if current != value:
            setattr(section, name, value)

    # targeted_creator
    if "user_names" not in config._ephemeral_overrides:
        target_usernames: list[str] | None = (
            sorted(config.user_names) if config.user_names else None
        )
        _maybe_set(base.targeted_creator, "usernames", target_usernames)
    _maybe_set(base.targeted_creator, "use_following", config.use_following)
    # use_following_with_pagination intentionally NOT mutated — it was removed
    # from the schema; it's a CLI-only macro toggling other flags at runtime.

    # my_account
    _maybe_set(base.my_account, "authorization_token", SecretStr(config.token or ""))
    _maybe_set(base.my_account, "user_agent", config.user_agent or "ReplaceMe")
    _maybe_set(base.my_account, "check_key", config.check_key or "qybZy9-fyszis-bybxyf")
    _maybe_set(base.my_account, "username", config.username)
    _maybe_set(
        base.my_account,
        "password",
        SecretStr(config.password) if config.password else None,
    )

    # options
    _maybe_set(
        base.options,
        "download_directory",
        str(config.download_directory or "Local_directory"),
    )
    _maybe_set(base.options, "download_mode", config.download_mode)
    _maybe_set(base.options, "show_downloads", config.show_downloads)
    _maybe_set(base.options, "show_skipped_downloads", config.show_skipped_downloads)
    _maybe_set(base.options, "download_media_previews", config.download_media_previews)
    _maybe_set(
        base.options, "open_folder_when_finished", config.open_folder_when_finished
    )
    _maybe_set(base.options, "separate_messages", config.separate_messages)
    _maybe_set(base.options, "separate_previews", config.separate_previews)
    _maybe_set(base.options, "separate_timeline", config.separate_timeline)
    _maybe_set(base.options, "use_duplicate_threshold", config.use_duplicate_threshold)
    _maybe_set(
        base.options, "use_pagination_duplication", config.use_pagination_duplication
    )
    _maybe_set(base.options, "use_folder_suffix", config.use_folder_suffix)
    _maybe_set(base.options, "respect_timeline_stats", config.respect_timeline_stats)
    _maybe_set(base.options, "interactive", config.interactive)
    _maybe_set(base.options, "prompt_on_exit", config.prompt_on_exit)
    _maybe_set(base.options, "timeline_retries", config.timeline_retries)
    _maybe_set(base.options, "timeline_delay_seconds", config.timeline_delay_seconds)
    _maybe_set(base.options, "api_max_retries", config.api_max_retries)
    _maybe_set(base.options, "rate_limiting_enabled", config.rate_limiting_enabled)
    _maybe_set(base.options, "rate_limiting_adaptive", config.rate_limiting_adaptive)
    _maybe_set(
        base.options,
        "rate_limiting_requests_per_minute",
        config.rate_limiting_requests_per_minute,
    )
    _maybe_set(
        base.options, "rate_limiting_burst_size", config.rate_limiting_burst_size
    )
    _maybe_set(
        base.options,
        "rate_limiting_retry_after_seconds",
        config.rate_limiting_retry_after_seconds,
    )
    _maybe_set(
        base.options,
        "rate_limiting_backoff_factor",
        config.rate_limiting_backoff_factor,
    )
    _maybe_set(
        base.options,
        "rate_limiting_max_backoff_seconds",
        config.rate_limiting_max_backoff_seconds,
    )
    _maybe_set(
        base.options,
        "temp_folder",
        str(config.temp_folder) if config.temp_folder is not None else None,
    )

    # postgres
    pg_password_secret: SecretStr | None = None
    if config.pg_password:
        pg_password_secret = SecretStr(config.pg_password)
    _maybe_set(base.postgres, "pg_host", config.pg_host)
    _maybe_set(base.postgres, "pg_port", config.pg_port)
    _maybe_set(base.postgres, "pg_database", config.pg_database)
    _maybe_set(base.postgres, "pg_user", config.pg_user)
    # FANSLY_PG_PASSWORD env var takes precedence at load time. Persist
    # only when explicitly configured; the env-var path is per-process.
    if "pg_password" not in config._ephemeral_overrides:
        _maybe_set(base.postgres, "pg_password", pg_password_secret)
    _maybe_set(base.postgres, "pg_sslmode", config.pg_sslmode)
    _maybe_set(
        base.postgres,
        "pg_sslcert",
        str(config.pg_sslcert) if config.pg_sslcert is not None else None,
    )
    _maybe_set(
        base.postgres,
        "pg_sslkey",
        str(config.pg_sslkey) if config.pg_sslkey is not None else None,
    )
    _maybe_set(
        base.postgres,
        "pg_sslrootcert",
        str(config.pg_sslrootcert) if config.pg_sslrootcert is not None else None,
    )
    _maybe_set(base.postgres, "pg_pool_size", config.pg_pool_size)
    _maybe_set(base.postgres, "pg_max_overflow", config.pg_max_overflow)
    _maybe_set(base.postgres, "pg_pool_timeout", config.pg_pool_timeout)

    # cache (auto-instantiated via default_factory; mutate in place)
    if base.cache is None:
        base.cache = CacheSection()
    _maybe_set(base.cache, "device_id", config.cached_device_id)
    _maybe_set(base.cache, "device_id_timestamp", config.cached_device_id_timestamp)

    # logging — sync flat ``log_levels`` dict onto the nested per-handler
    # ``level`` fields. Legacy ``textio`` key drove both the rich console
    # and the main file handler; keep that pairing on save so the YAML
    # round-trip stays stable for operators who never touched the new
    # split.
    log = config.log_levels
    _maybe_set(base.logging.db, "level", log.get("sqlalchemy", "INFO"))
    _maybe_set(base.logging.stash_console, "level", log.get("stash_console", "INFO"))
    _maybe_set(base.logging.stash_file, "level", log.get("stash_file", "INFO"))
    _maybe_set(base.logging.main_log, "level", log.get("textio", "INFO"))
    _maybe_set(
        base.logging.rich_handler,
        "level",
        log.get("rich_handler", log.get("textio", "INFO")),
    )
    _maybe_set(base.logging.websocket, "level", log.get("websocket", "INFO"))
    _maybe_set(base.logging.json_, "level", log.get("json", "INFO"))

    # stash_context: replace wholesale (Optional[Section]; None when unconfigured)
    base.stash_context = stash_section

    return base
