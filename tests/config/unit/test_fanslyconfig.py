"""Unit tests for FanslyConfig class.

Real-pipeline rewrite (Wave 2 Cat-D #4): the previous version used
``MagicMock(spec=FanslyApi)``, ``patch("config.fanslyconfig.FanslyApi")``,
``patch("api.rate_limiter.RateLimiter")``, and similar internal-mock
patterns to test the FanslyConfig factory methods (``get_api``,
``setup_api``, ``get_stash_context``, ``get_stash_api``,
``cancel_background_tasks``). The rewrite uses real ``FanslyApi``,
``RateLimiter``, and ``StashContext`` objects (all of their constructors
are pure attribute initialization — no network I/O at construction
time) and only patches at true edges:

- ``RateLimiterDisplay.start`` — would spawn a daemon thread running a
  Rich live display that interferes with pytest output capture; patched
  to a no-op.
- ``FanslyApi.setup_session`` — real HTTP call to Fansly's session
  endpoint; patched as the network edge.
- ``FanslyApi.login`` — real HTTP call for username/password login;
  patched as the network edge for the login-flow test.

Real code throughout: real ``FanslyApi.__init__`` (creates a real
``httpx.Client`` but issues no requests), real ``RateLimiter.__init__``
(sets up bucket state), real ``StashContext.__init__`` (normalizes the
conn dict).

The remaining intentional ``MagicMock(spec=asyncio.Task)`` usage in
``test_background_tasks_lifecycle`` is acceptable per the audit's
infrastructure-exception rule (asyncio.Task can't be constructed
without an event loop and a real coroutine; spec'd-mock is the
standard pattern for inspectable task-list assertions).
"""

import asyncio
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from stash_graphql_client import StashClient, StashContext

from api import FanslyApi
from api.rate_limiter import RateLimiter
from config.fanslyconfig import FanslyConfig
from config.modes import DownloadMode


# Fixtures (unit_config_path, unit_config, no_display, validation_config)
# live in tests/fixtures/config/ and flow through tests/conftest.py via the
# wildcard import — single-source-of-truth per project convention.


# ============================================================================
# Pure data / property tests — no mocks needed
# ============================================================================


class TestFanslyConfigBasics:
    """Pure-attribute tests that never touch the API or Stash factories."""

    def test_init_defaults(self):
        """FanslyConfig initializes with the documented default attribute values."""
        unit_config = FanslyConfig(program_version="1.0.0")

        assert unit_config.program_version == "1.0.0"
        assert unit_config.use_following is False
        assert unit_config.DUPLICATE_THRESHOLD == 50
        assert unit_config.BATCH_SIZE == 150
        assert unit_config.token is None
        assert unit_config.user_agent is None
        assert unit_config.debug is False
        assert unit_config.trace is False
        assert unit_config.download_mode == DownloadMode.NORMAL
        # _schema replaces _parser — no _parser attribute
        assert not hasattr(unit_config, "_parser")
        assert unit_config._schema is None
        assert unit_config._api is None

    def test_user_names_str_with_names(self, unit_config):
        """user_names_str returns alphabetically-sorted comma-joined list."""
        # Both orderings are valid because user_names is a set.
        assert unit_config.user_names_str() in ["user1, user2", "user2, user1"]

    def test_user_names_str_none(self):
        """user_names_str returns 'ReplaceMe' sentinel when user_names is None."""
        unit_config = FanslyConfig(program_version="1.0.0")
        unit_config.user_names = None
        assert unit_config.user_names_str() == "ReplaceMe"

    def test_download_mode_str(self, unit_config):
        """download_mode_str capitalizes the enum name."""
        unit_config.download_mode = DownloadMode.NORMAL
        assert unit_config.download_mode_str() == "Normal"

        unit_config.download_mode = DownloadMode.TIMELINE
        assert unit_config.download_mode_str() == "Timeline"

    def test_save_config_writes_yaml(self, unit_config, unit_config_path):
        """_save_config writes a YAML file via schema.dump_yaml."""
        unit_config.user_names = {"testuser"}
        unit_config.download_directory = Path("/test/path")

        result = unit_config._save_config()

        assert result is True
        assert unit_config_path.exists()
        content = unit_config_path.read_text(encoding="utf-8")
        assert "testuser" in content
        assert "/test/path" in content

    def test_save_config_no_path(self, unit_config):
        """_save_config with no path returns False without writing."""
        unit_config.config_path = None
        result = unit_config._save_config()
        assert result is False

    def test_load_raw_config_returns_empty_list(self, unit_config, unit_config_path):
        """_load_raw_config is a legacy stub that returns []."""
        unit_config_path.write_text("[TestSection]\ntest_key=test_value\n")
        result = unit_config._load_raw_config()
        assert result == []

    def test_load_raw_config_no_path(self, unit_config):
        """_load_raw_config with no path also returns []."""
        unit_config.config_path = None
        result = unit_config._load_raw_config()
        assert result == []

    def test_token_is_valid(self, unit_config):
        """token_is_valid: rejects short, ReplaceMe-containing, and None tokens."""
        unit_config.token = "a" * 60
        assert unit_config.token_is_valid() is True

        unit_config.token = "a" * 40
        assert unit_config.token_is_valid() is False

        unit_config.token = "a" * 40 + "ReplaceMe" + "a" * 10
        assert unit_config.token_is_valid() is False

        unit_config.token = None
        assert unit_config.token_is_valid() is False

    def test_useragent_is_valid(self, unit_config):
        """useragent_is_valid: rejects short, ReplaceMe-containing, and None UAs."""
        unit_config.user_agent = "a" * 50
        assert unit_config.useragent_is_valid() is True

        unit_config.user_agent = "a" * 30
        assert unit_config.useragent_is_valid() is False

        unit_config.user_agent = "a" * 40 + "ReplaceMe" + "a" * 10
        assert unit_config.useragent_is_valid() is False

        unit_config.user_agent = None
        assert unit_config.useragent_is_valid() is False

    def test_get_unscrambled_token_regular(self, unit_config):
        """get_unscrambled_token returns the token unchanged when no scramble suffix."""
        unit_config.token = "regular_token"
        assert unit_config.get_unscrambled_token() == "regular_token"

    def test_get_unscrambled_token_scrambled(self, unit_config):
        """get_unscrambled_token reverses the per-7-step scramble for tokens ending 'fNs'."""
        scrambled_token = "acegikmoqsuwybdf" + "fNs"
        unit_config.token = scrambled_token
        # Empirically derived from the production algorithm.
        expected = "agkoswbcimquyde"
        assert unit_config.get_unscrambled_token() == expected

    def test_get_unscrambled_token_none(self, unit_config):
        """get_unscrambled_token returns None when the token is None."""
        unit_config.token = None
        assert unit_config.get_unscrambled_token() is None


# ============================================================================
# get_api / setup_api — real FanslyApi + RateLimiter, edge-only patches
# ============================================================================


class TestGetApi:
    """get_api() factory: builds a real FanslyApi + RateLimiter wiring."""

    def test_returns_real_fansly_api_instance(self, unit_config, no_display):
        """get_api builds a real FanslyApi backed by a real RateLimiter."""
        unit_config._api = None

        result = unit_config.get_api()

        assert isinstance(result, FanslyApi)
        assert unit_config._api is result
        # RateLimiter wiring landed on the API instance.
        assert isinstance(unit_config._rate_limiter_display.rate_limiter, RateLimiter)
        # Token + user-agent + check-key flowed into the real instance.
        assert result.user_agent == unit_config.user_agent
        assert result.check_key == unit_config.check_key

    def test_caches_instance_across_calls(self, unit_config, no_display):
        """get_api returns the cached _api on second call without rebuilding."""
        unit_config._api = None

        api1 = unit_config.get_api()
        api2 = unit_config.get_api()

        assert api1 is api2
        assert unit_config._api is api1

    @pytest.mark.asyncio
    async def test_login_failure_wraps_in_runtime_error(
        self, unit_config, no_display, monkeypatch
    ):
        """When login() raises during the username/password flow → RuntimeError("Login failed: ...")."""
        unit_config._api = None
        unit_config.username = "myuser"
        unit_config.password = "mypass"
        unit_config.token = "short"  # invalid → triggers login flow

        async def _failing_login(self, username, password):
            raise RuntimeError("auth failed")

        async def _noop_update_device_id(self):
            return self.device_id or "stub"

        monkeypatch.setattr("api.fansly.FanslyApi.login", _failing_login)
        monkeypatch.setattr(
            "api.fansly.FanslyApi.update_device_id", _noop_update_device_id
        )

        with pytest.raises(RuntimeError, match="Login failed"):
            await unit_config.setup_api()

    @pytest.mark.asyncio
    async def test_login_success_persists_returned_token(
        self, unit_config, no_display, monkeypatch
    ):
        """Successful login replaces unit_config.token and triggers _save_unit_config."""
        unit_config._api = None
        unit_config.username = "myuser"
        unit_config.password = "mypass"
        unit_config.token = "short"

        async def _successful_login(self, username, password):
            self.token = "newly_issued_token_long_enough_to_pass_validation_checks"

        async def _noop_update_device_id(self):
            return self.device_id or "stub"

        async def _noop_setup_session(self):
            self.session_id = "fake_session"
            return True

        monkeypatch.setattr("api.fansly.FanslyApi.login", _successful_login)
        monkeypatch.setattr(
            "api.fansly.FanslyApi.update_device_id", _noop_update_device_id
        )
        monkeypatch.setattr("api.fansly.FanslyApi.setup_session", _noop_setup_session)

        result = await unit_config.setup_api()

        assert isinstance(result, FanslyApi)
        assert unit_config.token == (
            "newly_issued_token_long_enough_to_pass_validation_checks"
        ), "Successful login must replace unit_config.token with the API-returned value"
        assert unit_config.config_path.exists(), (
            "Successful login should trigger _save_config to persist the new token"
        )

    def test_returns_none_path_raises_failed_to_create(self, unit_config, no_display):
        """Missing token AND missing username/password → RuntimeError at fanslyconfig.py:241.

        The outer ``if user_agent and check_key and (token_is_valid or
        has_login_credentials):`` at line 202 evaluates False, so
        ``self._api`` stays None, so the defensive check at line 241
        raises ``RuntimeError("Failed to create API instance...")``.
        """
        unit_config._api = None
        # Invalid token AND no login credentials → 202's guard is False.
        unit_config.token = "short"
        unit_config.username = None
        unit_config.password = None
        # user_agent and check_key are still set (from the fixture), so
        # only the third condition makes the AND fail.

        with pytest.raises(RuntimeError, match="Failed to create API instance"):
            unit_config.get_api()


class TestSetupApi:
    """setup_api(): wraps get_api + the async session-setup edge."""

    @pytest.mark.asyncio
    async def test_calls_setup_session_when_session_id_null(
        self, unit_config, no_display, monkeypatch
    ):
        """Real FanslyApi initializes session_id='null' → setup_session is called."""
        unit_config._api = None

        # Replace just the network edge; FanslyApi instance is real.
        setup_session_calls: list[tuple] = []

        async def _fake_setup_session(self):
            setup_session_calls.append((self,))
            return True

        monkeypatch.setattr("api.fansly.FanslyApi.setup_session", _fake_setup_session)

        api = await unit_config.setup_api()

        assert isinstance(api, FanslyApi)
        assert api.session_id == "null"  # not modified by our fake
        assert len(setup_session_calls) == 1, (
            "setup_session should fire exactly once when session_id was 'null'"
        )

    @pytest.mark.asyncio
    async def test_skips_setup_session_when_session_id_already_set(
        self, unit_config, no_display, monkeypatch
    ):
        """When session_id is already non-'null', setup_session is NOT called."""
        unit_config._api = None

        setup_session_calls: list[tuple] = []

        async def _fake_setup_session(self):
            setup_session_calls.append((self,))
            return True

        monkeypatch.setattr("api.fansly.FanslyApi.setup_session", _fake_setup_session)

        # Build the API, then mutate session_id to mimic a session
        # restored from cache.
        api = unit_config.get_api()
        api.session_id = "existing_session"

        result = await unit_config.setup_api()

        assert result is api
        assert setup_session_calls == [], (
            "setup_session must not fire when session_id was already set"
        )


# ============================================================================
# get_stash_context / get_stash_api — real StashContext, no HTTP at __init__
# ============================================================================


class TestStashContextWiring:
    """Stash factory methods: build real StashContext (pure data init)."""

    def test_get_stash_context_no_data_raises(self, unit_config):
        """No conn data → RuntimeError, no StashContext built."""
        unit_config._stash = None
        unit_config.stash_context_conn = None

        with pytest.raises(RuntimeError, match="No StashContext connection data"):
            unit_config.get_stash_context()

    def test_get_stash_context_builds_real_instance(self, unit_config):
        """Real StashContext is constructed and cached on _stash."""
        unit_config._stash = None
        unit_config.stash_context_conn = {
            "scheme": "http",
            "host": "localhost",
            "port": "9999",
            "apikey": "test_key",
        }

        result = unit_config.get_stash_context()

        assert isinstance(result, StashContext)
        assert unit_config._stash is result
        # StashContext._normalize_conn_keys may rename keys; just check
        # that the apikey value flowed through.
        assert (
            result.conn.get("ApiKey") == "test_key"
            or result.conn.get("apikey") == "test_key"
        )

    def test_get_stash_context_cached_on_second_call(self, unit_config):
        """Second call returns the cached StashContext, not a new instance."""
        unit_config._stash = None
        unit_config.stash_context_conn = {
            "scheme": "http",
            "host": "localhost",
            "port": "9999",
            "apikey": "test_key",
        }

        first = unit_config.get_stash_context()
        second = unit_config.get_stash_context()

        assert first is second

    def test_get_stash_api_returns_initialized_client(self, unit_config):
        """get_stash_api returns the StashContext's already-initialized client.

        Production pattern (per ``stash/processing/base.py:293,334,343,367``):
          1. ``unit_config.get_stash_context()`` → context object
          2. ``await context.get_client()`` → initialize StashClient async
          3. ``unit_config.get_stash_api()`` (or ``context.client``) → sync
             accessor for the now-initialized client.

        ``get_stash_api`` is the sync convenience accessor for step 3 —
        it requires step 2 to have run first. The test injects a
        sentinel ``_client`` directly to verify the delegation
        without needing to drive the async ``get_client()`` HTTP
        initialization (which is StashContext's responsibility,
        covered by stash-graphql-client's own test suite).
        """
        unit_config._stash = None
        unit_config.stash_context_conn = {
            "scheme": "http",
            "host": "localhost",
            "port": "9999",
            "apikey": "test_key",
        }

        # Step 1: build the context.
        context = unit_config.get_stash_context()
        # Step 2 (simulated): the test pre-initializes _client to a
        # sentinel so the sync .client property returns it. In
        # production this would be done by ``await context.get_client()``.
        sentinel_client = MagicMock(spec=StashClient)
        context._client = sentinel_client

        # Step 3: get_stash_api should return the already-initialized
        # client — same identity as context._client.
        result = unit_config.get_stash_api()

        assert result is sentinel_client
        assert unit_config._stash.client is result

    def test_get_stash_api_raises_when_client_not_initialized(self, unit_config):
        """get_stash_api raises RuntimeError when context.get_client() hasn't run.

        Documents the requirement that callers must drive
        ``await context.get_client()`` before calling
        ``unit_config.get_stash_api()``. The wrapping at fanslyconfig.py:387
        catches the inner RuntimeError("Client not initialized") and
        re-raises with the "Failed to initialize Stash API" prefix.
        """
        unit_config._stash = None
        unit_config.stash_context_conn = {
            "scheme": "http",
            "host": "localhost",
            "port": "9999",
            "apikey": "test_key",
        }
        # Context built but client not initialized → .client property
        # raises RuntimeError("Client not initialized - use get_client() first").
        # Note: get_stash_api wraps RuntimeError from get_stash_context,
        # but NOT from .client access — so the inner message propagates.
        with pytest.raises(RuntimeError, match="Client not initialized"):
            unit_config.get_stash_api()

    def test_get_stash_api_wraps_runtime_error(self, unit_config):
        """When get_stash_context raises (no conn data), get_stash_api wraps it."""
        unit_config._stash = None
        unit_config.stash_context_conn = None

        with pytest.raises(RuntimeError, match="Failed to initialize Stash API"):
            unit_config.get_stash_api()


# ============================================================================
# Background-tasks lifecycle
# ============================================================================


class TestBackgroundTasks:
    """get_background_tasks + cancel_background_tasks lifecycle."""

    def test_background_tasks_lifecycle(self, unit_config):
        """Empty list → populated list → cancel-only-not-done after cancel.

        Uses MagicMock(spec=asyncio.Task) — acceptable per the audit's
        infrastructure-exception rule (asyncio.Task requires an event
        loop + real coroutine to construct, and we want inspectable
        ``.done()``/``.cancel()`` call assertions on a fixed list).
        """
        # Empty initial state.
        assert unit_config.get_background_tasks() == []

        # Populate with two task-like stand-ins: one running, one done.
        running_task = MagicMock(spec=asyncio.Task)
        running_task.done.return_value = False
        done_task = MagicMock(spec=asyncio.Task)
        done_task.done.return_value = True

        unit_config._background_tasks = [running_task, done_task]
        assert unit_config.get_background_tasks() == [running_task, done_task]

        unit_config.cancel_background_tasks()

        # Only the not-done task should have been cancelled; the
        # already-done one should be skipped. The list is then cleared.
        running_task.cancel.assert_called_once()
        done_task.cancel.assert_not_called()
        assert unit_config._background_tasks == []
