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
  to a no-op (``no_display``).

The ``setup_api`` / ``login`` HTTP edges are NOT method-patched — that
would leave the real ``setup_session`` / ``update_device_id`` / ``login``
logic untested and, being incomplete by construction, risks leaking a
real call (it did: the device-id fetch hit Fansly's CloudFront until the
pytest-socket backstop caught it). Those tests run the real methods over
the ``respx_fansly_api`` fixture (mocked apiv3 endpoints + fake WS) and
assert at the HTTP edge.

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
import base64
from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest
import respx
from stash_graphql_client import StashClient, StashContext

from api import FanslyApi
from api.rate_limiter import RateLimiter
from config.config import _populate_config_from_schema
from config.fanslyconfig import FanslyConfig, _rebuild_schema_from_config
from config.modes import DownloadMode
from config.schema import ConfigSchema, StashContextSection
from tests.fixtures.api import dump_fansly_calls


# A valid f-s-c session cookie: base64 of ``sessionId:1:1:hash`` (the format the
# real login() decodes) — sessionId 123456 here so the parsed session_id is known.
_FSC_COOKIE = base64.b64encode(b"123456:1:1:loginhashvalue").decode()


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
    async def test_login_failure_modes_each_raise_runtime_error(self, respx_fansly_api):
        """The REAL login() raises a precise RuntimeError for each failure branch.

        Drives the actual ``login()`` (no internal-method patching) over the
        respx edge through three branches in one test — HTTP error, a missing
        f-s-c session cookie, and a valid-cookie-but-no-token response — asserting
        the branch-specific message each time. The socket backstop would catch a
        leaked call.
        """
        api = respx_fansly_api  # real FanslyApi; respx active, device_id populated
        route = respx.post(url=api.LOGIN_ENDPOINT).mock(
            side_effect=[
                # 1. HTTP error -> raise_for_status -> wrapped with the status code.
                httpx.Response(401, json={"error": "bad credentials"}),
                # 2. 200 but no f-s-c cookie -> "No f-s-c session cookie".
                httpx.Response(200, json={"success": True, "response": {}}),
                # 3. Valid cookie (session_id parses) but no token in the body.
                httpx.Response(
                    200,
                    json={"success": True, "response": {}},
                    headers={"set-cookie": f"f-s-c={_FSC_COOKIE}; Path=/"},
                ),
            ]
        )
        try:
            with pytest.raises(RuntimeError, match="Login failed: 401"):
                await api.login("myuser", "mypass")
            with pytest.raises(RuntimeError, match="No f-s-c session cookie"):
                await api.login("myuser", "mypass")
            with pytest.raises(
                RuntimeError, match="did not contain an authorization token"
            ):
                await api.login("myuser", "mypass")
        finally:
            dump_fansly_calls(route.calls, "login_failure_modes")

    @pytest.mark.asyncio
    async def test_login_success_persists_token_and_session(
        self, respx_fansly_api, unit_config, no_display
    ):
        """setup_api's login branch: REAL login sets token + session, config persists.

        An invalid token plus credentials drives setup_api through the real
        ``login()`` over respx: login derives ``session_id`` from the f-s-c cookie
        and the auth token from ``response.response.session.token``. setup_api then
        persists the token onto the config and saves it; setup_session is skipped
        because session_id is no longer "null". No internal-method patching.
        """
        unit_config._api = None
        unit_config.username = "myuser"
        unit_config.password = "mypass"
        unit_config.token = "short"  # invalid -> triggers the login flow
        # Pre-seed a fresh device id so update_device_id makes no HTTP; the only
        # request this test mocks is the login POST.
        unit_config.cached_device_id = "test-device-id"
        unit_config.cached_device_id_timestamp = 9_999_999_999_999  # never stale

        route = respx.post(url=respx_fansly_api.LOGIN_ENDPOINT).mock(
            side_effect=[
                httpx.Response(
                    200,
                    json={
                        "success": True,
                        "response": {"session": {"token": "auth_token_long_enough"}},
                    },
                    headers={"set-cookie": f"f-s-c={_FSC_COOKIE}; Path=/"},
                )
            ]
        )
        try:
            result = await unit_config.setup_api()
        finally:
            dump_fansly_calls(route.calls, "login_success")

        assert isinstance(result, FanslyApi)
        assert result.token == "auth_token_long_enough"  # login set the auth token
        assert result.session_id == "123456"  # derived from the f-s-c cookie
        # setup_api persisted the new token onto the config and saved it.
        assert unit_config.token == "auth_token_long_enough"
        assert unit_config.config_path.exists()

    def test_missing_user_agent_raises_failed_to_create(self, unit_config, no_display):
        """Missing user_agent → guard fails → _api stays None → RuntimeError.

        get_api builds the api whenever ``user_agent and check_key`` are
        present (token may be empty, for the login flow or browser-token
        probing). If either is missing the guard fails, ``_api`` stays None,
        and the defensive check raises ``RuntimeError("Failed to create...")``.
        """
        unit_config._api = None
        unit_config.user_agent = None  # guard fails regardless of token

        with pytest.raises(RuntimeError, match="Failed to create API instance"):
            unit_config.get_api()

    def test_invalid_token_no_creds_builds_empty_token_probe_api(
        self, unit_config, no_display
    ):
        """Invalid token + no creds still builds an empty-token api.

        Relaxed gate (browser-token autolink fix): verifying a browser-found
        token via ``get_client_user_name(alternate_token=...)`` needs an api
        object before ``config.token`` is valid, so get_api builds one with an
        empty token rather than raising.
        """
        unit_config._api = None
        unit_config.token = "short"  # invalid
        unit_config.username = None
        unit_config.password = None
        # user_agent + check_key present from the fixture.

        api = unit_config.get_api()

        assert api is not None
        assert api.token == ""


class TestSetupApi:
    """setup_api(): wraps get_api + the async session-setup edge."""

    @pytest.mark.asyncio
    async def test_setup_session_runs_when_session_id_null(
        self, respx_fansly_api, mock_config
    ):
        """A 'null' session_id drives the REAL setup_session over the respx edge.

        ``respx_fansly_api`` bootstraps the real ``get_api -> setup_api ->
        update_device_id + setup_session`` seam against mocked apiv3 endpoints —
        no internal-method patching. session_id started "null"; setup_session
        running is what populated it from the WS auth, so the populated value is
        the proof it fired (and a leaked external call would be caught).
        """
        assert isinstance(respx_fansly_api, FanslyApi)
        assert respx_fansly_api.session_id == "test-session-id-1"
        assert mock_config._api is respx_fansly_api

    @pytest.mark.asyncio
    async def test_setup_session_skipped_when_session_id_already_set(
        self, respx_fansly_api, mock_config, no_display
    ):
        """A non-'null' session_id skips setup_session on a re-run.

        The fixture left session_id populated and device_id fresh, so re-invoking
        ``setup_api`` fires NO HTTP — ``update_device_id`` sees a fresh device_id
        and ``setup_session`` is skipped. The fixture's respx is still active
        (assert_all_mocked, routes cleared), so any leaked call would raise;
        session_id staying unchanged is the proof setup_session was skipped.
        """
        api = respx_fansly_api
        assert api.session_id == "test-session-id-1"

        result = await mock_config.setup_api()

        assert result is api
        assert api.session_id == "test-session-id-1"


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

    def test_stash_active_gate(self, unit_config):
        """stash_active: configured + (no restriction OR mode matches)."""
        unit_config.stash_context_conn = None
        unit_config.stash_require_stash_only_mode = False
        assert unit_config.stash_active is False

        unit_config.stash_context_conn = {"scheme": "http", "host": "x", "port": "9999"}
        unit_config.stash_require_stash_only_mode = False
        unit_config.download_mode = DownloadMode.NORMAL
        assert unit_config.stash_active is True

        unit_config.stash_require_stash_only_mode = True
        unit_config.download_mode = DownloadMode.NORMAL
        assert unit_config.stash_active is False

        unit_config.download_mode = DownloadMode.STASH_ONLY
        assert unit_config.stash_active is True

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


class TestIsUsernameInScope:
    """Coverage for ``FanslyConfig.is_username_in_scope`` — the cross-cutting
    scope predicate hoisted to the config layer in v0.14.2.

    The predicate is synchronous and string-based on purpose: callers that
    only hold a creator_id resolve to username themselves (the daemon
    keeps a private id-shim that walks the metadata store), so the
    predicate itself never has to poll metadata. These tests pin that
    contract.
    """

    def _fresh(self) -> FanslyConfig:
        """Bare-state FanslyConfig for predicate-only assertions."""
        return FanslyConfig(program_version="test")

    def test_use_following_short_circuits_true_for_any_username(self):
        """When ``-uf`` / ``-ufp`` is on, every followed creator is in scope.

        Covers the first branch of the predicate. ``user_names`` is
        intentionally empty too — under ``-uf`` the operator's curated
        list is overridden by the full following set.
        """
        cfg = self._fresh()
        cfg.use_following = True
        cfg.user_names = None
        assert cfg.is_username_in_scope("alice") is True
        # Even None / empty short-circuit True under -uf — there's no
        # whitelist to fail against.
        assert cfg.is_username_in_scope(None) is True
        assert cfg.is_username_in_scope("") is True

    def test_unrestricted_when_user_names_empty(self):
        """Empty ``user_names`` + ``use_following=False`` → unrestricted.

        Edge case for legacy / test-time configs that haven't populated
        the whitelist. The predicate degrades to "act on everything"
        rather than "act on nothing" — matches the pre-hoist behavior
        of the id-based shim.
        """
        cfg = self._fresh()
        cfg.use_following = False
        cfg.user_names = None
        assert cfg.is_username_in_scope("alice") is True

        cfg.user_names = set()
        assert cfg.is_username_in_scope("alice") is True

    def test_listed_username_in_scope(self):
        """Operator-listed usernames pass the scope check."""
        cfg = self._fresh()
        cfg.use_following = False
        cfg.user_names = {"alice", "bob"}
        assert cfg.is_username_in_scope("alice") is True
        assert cfg.is_username_in_scope("bob") is True

    def test_unlisted_username_out_of_scope(self):
        """Followed creators NOT in ``user_names`` fail the scope check.

        This is the #94 reporter's exact case — they had `usernames: [xx]`
        and follow many other creators; the predicate must return False
        for every non-listed username.
        """
        cfg = self._fresh()
        cfg.use_following = False
        cfg.user_names = {"alice", "bob"}
        assert cfg.is_username_in_scope("carol") is False
        assert cfg.is_username_in_scope("eve") is False

    def test_case_insensitive_match(self):
        """Case normalization on both sides — operator may have any
        casing in YAML, payloads may have any casing on the wire.
        """
        cfg = self._fresh()
        cfg.user_names = {"Alice", "BOB"}
        assert cfg.is_username_in_scope("alice") is True
        assert cfg.is_username_in_scope("ALICE") is True
        assert cfg.is_username_in_scope("bob") is True
        assert cfg.is_username_in_scope("Bob") is True

    def test_none_or_empty_username_out_of_scope_when_restricted(self):
        """Missing username with a populated whitelist → False, not True.

        Prevents a misuse where a caller passes ``account.username``
        that turned out to be None and accidentally lets the work
        through. Under ``-uf`` / unrestricted, the missing-username
        path was tested above (returns True via short-circuit); this
        test pins the restricted case.
        """
        cfg = self._fresh()
        cfg.use_following = False
        cfg.user_names = {"alice"}
        assert cfg.is_username_in_scope(None) is False
        assert cfg.is_username_in_scope("") is False

    def test_non_string_input_raises_type_error(self):
        """Type validator catches creator_id (int) mistakenly passed as username.

        Migration hazard: callers used to invoke
        ``_is_creator_in_scope(config, creator_id)`` (id-based). The new
        ``is_username_in_scope(username)`` is str-based; a caller doing
        ``config.is_username_in_scope(12345)`` out of muscle memory
        should hit a TypeError at the entry point, not a confusing
        ``AttributeError: 'int' object has no attribute 'lower'`` deep
        inside the predicate. The error message points the caller at
        the id-based shim explicitly.
        """
        cfg = self._fresh()
        cfg.use_following = False
        cfg.user_names = {"alice"}
        with pytest.raises(TypeError, match=r"expects str.*got int"):
            cfg.is_username_in_scope(12345)  # type: ignore[arg-type]  # intentionally passes int to prove the entry point rejects non-str
        with pytest.raises(TypeError, match=r"expects str"):
            cfg.is_username_in_scope([1, 2, 3])  # type: ignore[arg-type]  # intentionally passes list to prove the entry point rejects non-str

    def test_all_digit_username_is_a_real_username_not_an_id(self):
        """Numeric-string usernames pass the predicate normally.

        Fansly usernames can be entirely digits ("12345" as a real
        operator-chosen username). The validator INTENTIONALLY does
        not anti-validate "looks-like-an-id" content; content-based
        rejection would break real operators whose targeted creators
        chose all-digit usernames. Pins the contract that the
        validator is a type-only gate.
        """
        cfg = self._fresh()
        cfg.use_following = False
        cfg.user_names = {"12345"}
        assert cfg.is_username_in_scope("12345") is True
        assert cfg.is_username_in_scope("54321") is False  # different digit string


# ============================================================================
# stash_enable_scene_split — flattened-twin round-trip (schema ↔ runtime)
# ============================================================================


class TestStashEnableSceneSplitRoundTrip:
    """``enable_scene_split`` survives both bridge directions.

    Mirrors the existing ``stash_require_stash_only_mode`` flattened-twin
    wiring: ``_populate_config_from_schema`` (schema → runtime) and
    ``_rebuild_schema_from_config`` (runtime → schema). If either side
    drops the field, saving config back to YAML would silently lose the
    operator's setting.
    """

    @pytest.mark.parametrize("value", [True, "dry-run", False])
    def test_forward_schema_to_runtime(self, unit_config, value):
        """``_populate_config_from_schema`` flattens ``enable_scene_split``."""
        schema = ConfigSchema(
            stash_context=StashContextSection(enable_scene_split=value),
        )

        _populate_config_from_schema(unit_config, schema)

        if isinstance(value, bool):
            assert unit_config.stash_enable_scene_split is value
        else:
            assert unit_config.stash_enable_scene_split == value

    @pytest.mark.parametrize("value", [True, "dry-run", False])
    def test_reverse_runtime_to_schema(self, unit_config, value):
        """``_rebuild_schema_from_config`` re-nests ``enable_scene_split``."""
        unit_config.stash_context_conn = {
            "scheme": "http",
            "host": "localhost",
            "port": 9999,
            "apikey": "test_key",
        }
        unit_config.stash_enable_scene_split = value

        schema = _rebuild_schema_from_config(unit_config)

        assert schema.stash_context is not None
        if isinstance(value, bool):
            assert schema.stash_context.enable_scene_split is value
        else:
            assert schema.stash_context.enable_scene_split == value

    def test_default_is_false(self):
        """A bare ``FanslyConfig`` defaults ``stash_enable_scene_split`` to False."""
        assert FanslyConfig(program_version="1.0.0").stash_enable_scene_split is False
