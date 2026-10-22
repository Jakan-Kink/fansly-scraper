"""Unit tests for configuration validation against a real ``FanslyConfig``.

Edges stood in for:
- HTTP: respx (Fansly API, website main.js scrape)
- Browser storage: real LevelDB/SQLite stores under a temporary ``HOME``
  (``linux_browser_home``); ``platform.system`` and ``psutil`` processes
- Prompts: the prompt_toolkit session (``tty`` + ``fake_prompt_session``)
- ``webbrowser.open`` (``opened_urls``); ``importlib.util.find_spec`` only
  to simulate plyvel being absent

Everything else runs real code:
- ``FanslyConfig`` is real — set ``token = "a" * 60`` for a valid-length
  token; set ``user_agent = "Mozilla/5.0 " + "A" * 60`` for a valid UA.
- ``save_config_or_raise`` runs against a real ``tmp_path / config.yaml``.
- ``validate_adjust_creator_name`` runs its real char/length/space checks.
- ``textio_logger`` output is captured via ``caplog``.

The ``validation_config`` fixture is the replacement for ``mock_config``:
it returns a fresh real ``FanslyConfig`` with a writable config_path, so
save-triggering branches exercise the real YAML write without mocks.
"""

import importlib.util
import logging
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
import respx

from api.fansly import FanslyApi
from config.modes import DownloadMode
from config.validation import (
    validate_adjust_check_key,
    validate_adjust_config,
    validate_adjust_creator_name,
    validate_adjust_download_directory,
    validate_adjust_download_mode,
    validate_adjust_token,
    validate_adjust_user_agent,
    validate_creator_names,
    validate_log_levels,
)
from errors import ConfigError
from tests.fixtures.api.api_fixtures import dump_fansly_calls
from tests.fixtures.config import (
    FakeBrowserProcess,
    firefox_session_row,
    hold_exclusive_lock,
    leveldb_session_value,
    write_firefox_store,
    write_leveldb_store,
)


# Website-host URLs scraped by guess_check_key for main.js — not apiv3
# endpoints, so no FanslyApi.*_ENDPOINT constant maps to them.
_FANSLY_HOMEPAGE = "https://fansly.com/"  # CCH:api  # website root for main.js scrape, not an apiv3 endpoint
_FANSLY_MAIN_JS = "https://fansly.com/main.abc123.js"  # CCH:api  # scraped website main.js asset, not an apiv3 endpoint

# Chrome Local Storage under a Linux ``HOME`` (see ``linux_browser_home``).
CHROME_LOCAL_STORAGE = Path(".config/google-chrome/Default/Local Storage/leveldb")

needs_plyvel = pytest.mark.skipif(
    importlib.util.find_spec("plyvel") is None, reason="plyvel-ci not installed"
)


# validation_config fixture lives in tests/fixtures/config/ and flows through
# tests/conftest.py via the wildcard import — single-source-of-truth per
# project convention.


# -- validate_creator_names -------------------------------------------------


async def test_validate_creator_names_valid_names_pass_through(validation_config):
    """Validation accepts a set of real valid names unchanged.

    Runs the real ``validate_adjust_creator_name`` for each name — every
    name passes the length/space/chars checks so the set is returned
    unchanged. No internal patches.
    """
    validation_config.user_names = {"alice", "bobuser"}
    assert await validate_creator_names(validation_config) is True
    assert validation_config.user_names == {"alice", "bobuser"}


async def test_validate_creator_names_returns_false_when_user_names_is_none(
    validation_config,
):
    """Validation returns False immediately when user_names is None (line 36)."""
    validation_config.user_names = None
    assert await validate_creator_names(validation_config) is False


async def test_validate_creator_names_removes_invalid_and_saves(
    validation_config, caplog
):
    """Names failing the real validator are removed and config is saved.

    Mixes valid + invalid names (too short, spaces, bad chars) and asserts
    only the valid one survives. The real ``save_config_or_raise`` runs
    and writes a YAML file to the ``tmp_path`` config_path — proof that
    the side-effectful "list changed → save" branch fires end-to-end.
    """

    caplog.set_level(logging.WARNING)
    validation_config.user_names = {"a", "valid", "bad chars!"}

    assert await validate_creator_names(validation_config) is True
    # Invalid names filtered out; only "valid" (5 chars, OK) remains.
    assert validation_config.user_names == {"valid"}
    # Real YAML save happened.
    assert validation_config.config_path.exists(), (
        "list_changed path must call save_config_or_raise, which writes YAML"
    )
    # Real logger captured the removal warnings.
    warning_messages = [
        r.getMessage() for r in caplog.records if r.levelname == "WARNING"
    ]
    assert any("Invalid creator name" in m for m in warning_messages), (
        f"Expected removal warning, got: {warning_messages}"
    )


async def test_validate_creator_names_all_removed_returns_true_and_falls_back(
    validation_config, caplog
):
    """All-names-removed path returns True ("will process following list").

    Covers the len==0 branch on line 66-68: after every name is removed,
    validator still returns True and logs the "will process following
    list" info message.
    """

    caplog.set_level(logging.INFO)
    validation_config.user_names = {"a", "b"}  # all too short

    assert await validate_creator_names(validation_config) is True
    assert len(validation_config.user_names) == 0
    info_messages = [r.getMessage() for r in caplog.records if r.levelname == "INFO"]
    assert any("will process following list" in m for m in info_messages), (
        f"Expected following-list log, got INFO: {info_messages}"
    )


async def test_validate_creator_names_interactive_adjustment(
    validation_config, tty, fake_prompt_session
):
    """Interactive mode: user fixes an invalid name → config updates and saves.

    The real ``validate_adjust_creator_name`` prompts through the real
    ``aprompt_text``; the scripted answer is accepted and the save fires.
    """
    validation_config.interactive = True
    validation_config.user_names = {"bad user"}  # space → invalid
    fake_prompt_session.append("correctuser")

    assert await validate_creator_names(validation_config) is True
    assert fake_prompt_session == []
    assert validation_config.user_names == {"correctuser"}
    assert validation_config.config_path.exists()


# -- validate_adjust_creator_name (pure function, no fixture) ---------------


async def test_validate_adjust_creator_name_valid():
    """Real validator accepts a well-formed name."""
    assert await validate_adjust_creator_name("validuser") == "validuser"


async def test_validate_adjust_creator_name_replaceme_placeholder():
    """ReplaceMe placeholder is rejected (non-interactive → None)."""
    assert await validate_adjust_creator_name("ReplaceMe") is None


async def test_validate_adjust_creator_name_rejects_spaces():
    assert await validate_adjust_creator_name("invalid user") is None


@pytest.mark.parametrize(
    "name", ["a", "abc", "a" * 31], ids=["one_char", "three_chars", "thirty_one"]
)
async def test_validate_adjust_creator_name_rejects_bad_length(name):
    assert await validate_adjust_creator_name(name) is None


async def test_validate_adjust_creator_name_rejects_bad_chars():
    assert await validate_adjust_creator_name("user!@#") is None


async def test_validate_adjust_creator_name_interactive_retries_until_valid(
    tty, fake_prompt_session
):
    """Interactive mode re-prompts on invalid input until a valid one is entered."""
    fake_prompt_session.extend(["still bad", "validuser"])

    assert (
        await validate_adjust_creator_name("invalid user", interactive=True)
        == "validuser"
    )
    assert fake_prompt_session == []


# -- validate_adjust_token --------------------------------------------------


@pytest.mark.asyncio
async def test_validate_adjust_token_skips_when_username_password_set(
    validation_config, caplog
):
    """When credentials are configured, token validation is skipped (lines 139-143).

    Uses real ``FanslyConfig.token_is_valid()`` — we're proving it's
    never called by setting token to a known-invalid value and asserting
    no ConfigError escapes. (Previously this test asserted on a mock
    call-count, which was proxy-evidence.)
    """

    caplog.set_level(logging.INFO)
    validation_config.username = "someone"
    validation_config.password = "secret"
    validation_config.token = "short"  # would be invalid, but should be ignored

    await validate_adjust_token(validation_config)  # Must not raise.

    info_messages = [r.getMessage() for r in caplog.records if r.levelname == "INFO"]
    assert any("Username and password configured" in m for m in info_messages), (
        f"Expected credentials-skip log, got: {info_messages}"
    )


@pytest.mark.asyncio
@patch("importlib.util.find_spec", side_effect=ImportError("no plyvel"))
async def test_validate_adjust_token_plyvel_import_error_raises_config_error(
    _find_spec,  # noqa: PT019 — @patch decorator, not a fixture
    validation_config,
):
    """ImportError during plyvel check + invalid token → logs info + raises."""
    validation_config.token = "short"  # invalid (<50 chars)

    with pytest.raises(ConfigError, match=r"authorization token.*still invalid"):
        await validate_adjust_token(validation_config)


@pytest.mark.asyncio
@patch("importlib.util.find_spec", return_value=None)
async def test_validate_adjust_token_no_plyvel_invalid_token_raises(
    _find_spec,  # noqa: PT019 — @patch decorator, not a fixture
    validation_config,
):
    """Plyvel not installed + invalid token → ConfigError (lines 275-282)."""
    validation_config.token = "short"
    validation_config.interactive = False

    with pytest.raises(ConfigError, match=r"authorization token.*still invalid"):
        await validate_adjust_token(validation_config)


@pytest.mark.asyncio
@patch("importlib.util.find_spec")
async def test_validate_adjust_token_interactive_invalid_token_opens_url_and_raises(
    mock_find_spec, validation_config, opened_urls
):
    """Interactive + invalid token + no plyvel → the Getting-Started page opens."""
    mock_find_spec.return_value = None
    validation_config.token = "short"
    validation_config.interactive = True

    with pytest.raises(ConfigError, match=r"authorization token.*still invalid"):
        await validate_adjust_token(validation_config)

    assert len(opened_urls) == 1
    assert "Getting-Started" in opened_urls[0]


@needs_plyvel
@pytest.mark.asyncio
async def test_validate_adjust_token_plyvel_installed_no_browsers_raises(
    validation_config, monkeypatch
):
    """An OS with no known browser paths → raises with the "not found" message."""
    monkeypatch.setattr("platform.system", lambda: "FreeBSD")
    validation_config.token = "short"
    validation_config.interactive = False

    with pytest.raises(ConfigError, match="not found in any of your browser"):
        await validate_adjust_token(validation_config)


@needs_plyvel
@pytest.mark.asyncio
async def test_validate_adjust_token_auto_links_in_non_interactive_mode(
    respx_fansly_api, validation_config, linux_browser_home
):
    """Non-interactive + token in a real Chrome LevelDB store → auto-linked.

    Real ``get_browser_config_paths`` / ``find_leveldb_folders`` / plyvel read;
    ``get_client_user_name(browser_token)`` hits the account endpoint (respx)
    to confirm the browser-found token before linking.
    """
    validation_config.token = "short"  # invalid → triggers browser search
    validation_config.interactive = False

    valid_token = "a" * 60
    write_leveldb_store(
        linux_browser_home / CHROME_LOCAL_STORAGE, leveldb_session_value(valid_token)
    )

    # respx_fansly_api cleared its one-shot bootstrap routes; re-mount
    # device/id + account/me so validation_config's own probe api can verify
    # the browser-found token.
    respx.get(url__startswith=f"{FanslyApi.BASE_URL}device/id?").mock(
        side_effect=[
            httpx.Response(200, json={"success": True, "response": "test-device-id"})
        ]
    )
    account_route = respx.get(url__startswith=f"{FanslyApi.BASE_URL}account/me?").mock(
        side_effect=[
            httpx.Response(
                200,
                json={
                    "success": True,
                    "response": {"account": {"username": "found_user"}},
                },
            )
        ]
    )

    try:
        await validate_adjust_token(validation_config)
    finally:
        dump_fansly_calls(account_route.calls, "auto_links_non_interactive")

    assert account_route.call_count == 1
    assert validation_config.token == valid_token
    assert validation_config.token_from_browser_name == "Chrome"


@needs_plyvel
@pytest.mark.asyncio
async def test_validate_adjust_token_firefox_profile_branch(
    respx_fansly_api,
    validation_config,
    linux_browser_home,
    sqlite_connect_spy,
):
    """Firefox path reads a real ``~/.mozilla/firefox`` profile store and links it.

    Real ``get_browser_config_paths`` / ``os.walk`` / sqlite read; only the
    account lookup is mocked (respx).
    """
    validation_config.token = "short"
    validation_config.interactive = False

    firefox_token = "b" * 60
    write_firefox_store(
        linux_browser_home
        / ".mozilla"
        / "firefox"
        / "abc.default"
        / "storage"
        / "default"
        / "webappsstore.sqlite",
        firefox_session_row(firefox_token),
    )

    respx.get(url__startswith=f"{FanslyApi.BASE_URL}device/id?").mock(
        side_effect=[
            httpx.Response(200, json={"success": True, "response": "test-device-id"})
        ]
    )
    account_route = respx.get(url__startswith=f"{FanslyApi.BASE_URL}account/me?").mock(
        side_effect=[
            httpx.Response(
                200,
                json={
                    "success": True,
                    "response": {"account": {"username": "firefox_user"}},
                },
            )
        ]
    )

    try:
        await validate_adjust_token(validation_config)
    finally:
        dump_fansly_calls(account_route.calls, "firefox_profile")

    assert account_route.call_count == 1
    assert validation_config.token == firefox_token
    assert validation_config.token_from_browser_name == "Firefox"
    assert sqlite_connect_spy.all_closed()


@needs_plyvel
@pytest.mark.asyncio
async def test_validate_adjust_token_interactive_user_accepts_link(
    respx_fansly_api, validation_config, linux_browser_home, tty, fake_prompt_session
):
    """Interactive: token found → user types "y" → token saved (lines 222-258)."""
    validation_config.interactive = True
    validation_config.token = "short"
    fake_prompt_session.append("y")

    valid_token = "c" * 60
    write_leveldb_store(
        linux_browser_home / CHROME_LOCAL_STORAGE, leveldb_session_value(valid_token)
    )

    respx.get(url__startswith=f"{FanslyApi.BASE_URL}device/id?").mock(
        side_effect=[
            httpx.Response(200, json={"success": True, "response": "test-device-id"})
        ]
    )
    account_route = respx.get(url__startswith=f"{FanslyApi.BASE_URL}account/me?").mock(
        side_effect=[
            httpx.Response(
                200,
                json={
                    "success": True,
                    "response": {"account": {"username": "found_user"}},
                },
            )
        ]
    )

    try:
        await validate_adjust_token(validation_config)
    finally:
        dump_fansly_calls(account_route.calls, "interactive_accepts")

    assert fake_prompt_session == []
    assert validation_config.token == valid_token
    assert validation_config.token_from_browser_name == "Chrome"


@needs_plyvel
@pytest.mark.asyncio
async def test_validate_adjust_token_interactive_user_rejects_link(
    respx_fansly_api,
    validation_config,
    linux_browser_home,
    tty,
    fake_prompt_session,
    opened_urls,
):
    """Interactive: token found but user says "n" → Getting-Started opens, ConfigError."""
    validation_config.interactive = True
    validation_config.token = "short"
    fake_prompt_session.append("n")
    write_leveldb_store(
        linux_browser_home / CHROME_LOCAL_STORAGE, leveldb_session_value("d" * 60)
    )

    respx.get(url__startswith=f"{FanslyApi.BASE_URL}device/id?").mock(
        side_effect=[
            httpx.Response(200, json={"success": True, "response": "test-device-id"})
        ]
    )
    account_route = respx.get(url__startswith=f"{FanslyApi.BASE_URL}account/me?").mock(
        side_effect=[
            httpx.Response(
                200,
                json={
                    "success": True,
                    "response": {"account": {"username": "found_user"}},
                },
            )
        ]
    )

    try:
        with pytest.raises(ConfigError, match=r"authorization token.*still invalid"):
            await validate_adjust_token(validation_config)
    finally:
        dump_fansly_calls(account_route.calls, "interactive_rejects")

    assert fake_prompt_session == []
    assert len(opened_urls) == 1
    assert validation_config.token == "short"


@needs_plyvel
@pytest.mark.asyncio
async def test_validate_adjust_token_leveldb_stores_without_token_raise(
    validation_config, linux_browser_home
):
    """Real LevelDB stores without a session → every loop continues → "not found".

    Chrome holds two token-less stores (no token → next folder); Brave's profile
    exists with no LevelDB at all (empty folder loop) → next browser path.
    """
    validation_config.token = "short"
    validation_config.interactive = False
    write_leveldb_store(linux_browser_home / CHROME_LOCAL_STORAGE, None)
    write_leveldb_store(
        linux_browser_home
        / ".config"
        / "google-chrome"
        / "Default"
        / "Session Storage"
        / "leveldb",
        None,
    )
    (
        linux_browser_home / ".config" / "BraveSoftware" / "Brave-Browser" / "Default"
    ).mkdir(parents=True)

    with pytest.raises(ConfigError, match="not found in any of your browser"):
        await validate_adjust_token(validation_config)


@needs_plyvel
@pytest.mark.asyncio
async def test_validate_adjust_token_firefox_locked_non_interactive_no_token(
    validation_config,
    linux_browser_home,
    sqlite_connect_spy,
    browser_processes,
):
    """A Firefox store locked by a running browser yields no token in non-interactive mode.

    ``config.interactive`` must reach the Firefox reader: no prompt (no TTY
    here, so a prompt would raise ``RuntimeError``), no browser kill, and the
    "not found" ``ConfigError`` fires.
    """
    validation_config.token = "short"
    validation_config.interactive = False

    db_path = write_firefox_store(
        linux_browser_home
        / ".mozilla"
        / "firefox"
        / "abc.default"
        / "storage"
        / "default"
        / "webappsstore.sqlite",
        firefox_session_row("c" * 60),
    )
    firefox = FakeBrowserProcess("firefox")
    browser_processes.append(firefox)

    with (
        hold_exclusive_lock(db_path),
        pytest.raises(ConfigError, match="not found in any of your browser"),
    ):
        await validate_adjust_token(validation_config)

    assert not firefox.killed
    assert not firefox.terminated
    assert sqlite_connect_spy.all_closed()


@needs_plyvel
@pytest.mark.asyncio
async def test_validate_adjust_token_plyvel_installed_interactive_opens_started_url(
    validation_config, monkeypatch, opened_urls
):
    """Plyvel + interactive + no account found → the Getting-Started page opens.

    Covers the inner ``if config.interactive: open_get_started_url()`` branch
    inside the "no account found" path, distinct from the outer invalid-token one.
    """
    monkeypatch.setattr("platform.system", lambda: "FreeBSD")
    validation_config.token = "short"
    validation_config.interactive = True

    with pytest.raises(ConfigError, match="not found in any of your browser"):
        await validate_adjust_token(validation_config)

    assert len(opened_urls) == 1
    assert "Getting-Started" in opened_urls[0]


@needs_plyvel
@pytest.mark.asyncio
async def test_validate_adjust_token_interactive_reprompts_on_invalid_input(
    respx_fansly_api,
    validation_config,
    linux_browser_home,
    tty,
    fake_prompt_session,
    caplog,
):
    """Interactive: user types garbage → the real prompt re-asks → "y" links the token."""
    validation_config.interactive = True
    validation_config.token = "short"
    fake_prompt_session.extend(["maybe", "y"])
    write_leveldb_store(
        linux_browser_home / CHROME_LOCAL_STORAGE, leveldb_session_value("e" * 60)
    )

    respx.get(url__startswith=f"{FanslyApi.BASE_URL}device/id?").mock(
        side_effect=[
            httpx.Response(200, json={"success": True, "response": "test-device-id"})
        ]
    )
    account_route = respx.get(url__startswith=f"{FanslyApi.BASE_URL}account/me?").mock(
        side_effect=[
            httpx.Response(
                200,
                json={
                    "success": True,
                    "response": {"account": {"username": "found_user"}},
                },
            )
        ]
    )

    try:
        await validate_adjust_token(validation_config)
    finally:
        dump_fansly_calls(account_route.calls, "interactive_reprompts")

    assert fake_prompt_session == []
    errors = [r.getMessage() for r in caplog.records if r.levelname == "ERROR"]
    assert "Please enter 'y' or 'n'." in errors
    assert validation_config.token == "e" * 60


# -- validate_adjust_user_agent ---------------------------------------------


def test_validate_adjust_user_agent_valid_skips_generation(validation_config):
    """Valid user-agent → function returns early without generating a new one.

    Uses real ``useragent_is_valid()`` (len >= 40 and no "ReplaceMe"); the
    default "Mozilla/5.0 " + "A" * 60 is 72 chars, trivially valid.
    """
    original_ua = validation_config.user_agent
    validate_adjust_user_agent(validation_config)
    assert validation_config.user_agent == original_ua


def test_validate_adjust_user_agent_invalid_generates_and_saves(validation_config):
    """Invalid user-agent → ua_generator produces a real one, persisted to YAML.

    No network: the validator generates the UA offline with ua_generator, so
    the test asserts on the result (changed, valid, real Mozilla shape) and
    that the real ``save_config_or_raise`` wrote the file.
    """
    validation_config.user_agent = "short"  # invalid (< 40 chars)

    validate_adjust_user_agent(validation_config)

    assert validation_config.user_agent != "short"
    assert validation_config.useragent_is_valid()
    assert validation_config.user_agent.startswith("Mozilla/5.0 ")
    assert validation_config.config_path.exists()


def test_validate_adjust_user_agent_logs_browser_specific_when_token_from_browser(
    validation_config, caplog
):
    """token_from_browser_name set → logs the browser-specific guess message."""
    caplog.set_level(logging.INFO)
    validation_config.user_agent = "short"
    validation_config.token_from_browser_name = "Chrome"

    validate_adjust_user_agent(validation_config)

    info_messages = [r.getMessage() for r in caplog.records if r.levelname == "INFO"]
    assert any("operating system" in m and "browser" in m for m in info_messages), (
        f"Expected browser-specific info log, got INFO: {info_messages}"
    )


@pytest.mark.parametrize(
    ("browser_name", "expected_token"),
    [
        ("Chrome", "Mac OS X 10_15_7"),
        ("Microsoft Edge", "Mac OS X 10_15_7"),
        ("Firefox", "Mac OS X 10.15"),
    ],
)
def test_validate_adjust_user_agent_freezes_macos_token(
    validation_config, monkeypatch, browser_name, expected_token
):
    """On macOS the generated UA carries the platform token real browsers
    freeze it at (Chromium/Safari → 10_15_7, Firefox → 10.15), never the
    literal current macOS version.

    Regression guard: ua_generator emits the selected macOS version verbatim
    (e.g. "26_5"), which no real browser produces; validate_adjust_user_agent
    must normalise it.
    """
    monkeypatch.setattr("config.validation.platform.system", lambda: "Darwin")
    validation_config.user_agent = "short"
    validation_config.token_from_browser_name = browser_name

    validate_adjust_user_agent(validation_config)

    assert expected_token in validation_config.user_agent
    assert "Mac OS X 26" not in validation_config.user_agent


@pytest.mark.parametrize(
    ("host_os", "expected_fragment"),
    [("Linux", "Linux"), ("Windows", "Windows NT")],
)
def test_validate_adjust_user_agent_non_macos_host(
    validation_config, monkeypatch, host_os, expected_fragment
):
    """Non-macOS hosts get their own platform token and skip the macOS freeze."""
    monkeypatch.setattr("config.validation.platform.system", lambda: host_os)
    validation_config.user_agent = "short"
    validation_config.token_from_browser_name = "Chrome"

    validate_adjust_user_agent(validation_config)

    assert expected_fragment in validation_config.user_agent
    assert "Mac OS X" not in validation_config.user_agent


# -- validate_adjust_check_key ----------------------------------------------


async def test_validate_adjust_check_key_guess_succeeds_sets_key(
    respx_fansly_api, validation_config
):
    """Real ``guess_check_key`` returns a key → config.check_key updated + saved.

    Drives the real HTTP pipeline: respx serves the Fansly homepage (carrying
    the main.js ``src``) and the main.js asset; the real ``guess_check_key``
    scrapes the URL, downloads the bundle, and runs the real extraction chain.
    The JS-execution leaf (``extract_checkkey_from_js``) is patched to a known
    key — that is the external-bridge boundary already covered in
    ``test_checkkey.py``; everything between ``validate_adjust_check_key`` and
    the JS leaf (HTTP fetch, URL parsing, fall-through) runs for real.

    ``_shutdown_js_bridge`` is patched to skip ~7s of node-bridge thread joins
    (real teardown is exercised in ``test_checkkey.py::TestJsBridgeShutdown``).
    """
    html = '<script src="main.abc123.js"></script>'
    homepage_route = respx.get(_FANSLY_HOMEPAGE).mock(
        side_effect=[httpx.Response(200, text=html)]
    )
    mainjs_route = respx.get(_FANSLY_MAIN_JS).mock(
        side_effect=[httpx.Response(200, text='this.checkKey_ = "x";')]
    )

    try:
        with (
            patch("helpers.checkkey._shutdown_js_bridge"),
            patch(
                "helpers.checkkey.extract_checkkey_from_js",
                return_value="guessed-key-xyz",
            ),
        ):
            await validate_adjust_check_key(validation_config)
    finally:
        dump_fansly_calls(respx.calls, label="check_key_guess_succeeds")

    assert validation_config.check_key == "guessed-key-xyz"
    assert validation_config.config_path.exists()
    assert homepage_route.called
    assert mainjs_route.called


# The real ``guess_check_key`` never returns None — every failure path returns
# the hardcoded default key (helpers/checkkey.py). So the interactive arms of
# ``validate_adjust_check_key`` (``guessed_key is None``) are only reachable
# when ``config.user_agent`` is falsy, which skips the guess entirely. These
# tests drive the real ``aconfirm``/``aprompt_text`` while-loop over a real
# FanslyConfig with no ``guess_check_key`` patch — only the stdin-edge prompts
# are faked.
@pytest.mark.parametrize(
    ("answers", "expected_key_attr"),
    [
        pytest.param(["y"], "original", id="confirm-keeps-existing-key"),
        pytest.param(["n", "new_key_value", "y"], "new_key_value", id="enters-new-key"),
        pytest.param(
            ["n", "first_try", "n", "second_try", "y"],
            "second_try",
            id="rejects-new-key-then-accepts",
        ),
    ],
)
async def test_validate_adjust_check_key_no_guess_interactive_arms(
    validation_config, tty, fake_prompt_session, answers, expected_key_attr
):
    """No user_agent → guess skipped → real interactive key-confirm loop runs.

    Each param scripts the stdin answers for one arm of the real while-loop:
    confirm-keep, enter-new, and reject-then-accept (the new-key confirmation
    is rejected and the prompt repeats). Only the prompt_toolkit session is
    scripted; ``aconfirm``/``aprompt_text``, ``save_config_or_raise`` and the
    loop run for real.
    """
    validation_config.user_agent = None
    validation_config.interactive = True
    original_key = validation_config.check_key
    fake_prompt_session.extend(answers)

    await validate_adjust_check_key(validation_config)

    expected = original_key if expected_key_attr == "original" else expected_key_attr
    assert validation_config.check_key == expected
    assert fake_prompt_session == []


async def test_validate_adjust_check_key_no_user_agent_non_interactive(
    validation_config, monkeypatch, scaled_async_sleep_recording
):
    """No user_agent → guess skipped; non-interactive waits the real 15s continue."""
    validation_config.user_agent = None
    validation_config.interactive = False
    original_key = validation_config.check_key
    monkeypatch.setattr("textio.textio.asyncio.sleep", scaled_async_sleep_recording)

    await validate_adjust_check_key(validation_config)

    assert scaled_async_sleep_recording.calls == [15]
    assert validation_config.check_key == original_key


# -- validate_adjust_download_directory -------------------------------------


async def test_validate_adjust_download_directory_local_dir_sets_cwd(
    validation_config,
):
    """``local_dir`` sentinel in path → resolved to Path.cwd()."""
    validation_config.download_directory = Path("local_dir")

    await validate_adjust_download_directory(validation_config)

    assert validation_config.download_directory == Path.cwd()


async def test_validate_adjust_download_directory_valid_custom_dir_kept(
    validation_config, tmp_path
):
    """Valid existing directory is kept as-is (no save, no dialog)."""
    custom = tmp_path / "downloads"
    custom.mkdir()
    validation_config.download_directory = custom

    await validate_adjust_download_directory(validation_config)

    assert validation_config.download_directory == custom


async def test_validate_adjust_download_directory_invalid_prompts_for_replacement(
    validation_config,
    tmp_path,
    monkeypatch,
    tty,
    fake_prompt_session,
    scaled_async_sleep_recording,
):
    """Invalid directory → 10s pause → real ask_correct_dir loop → save.

    The first scripted answer is not a directory, so the real prompt loop
    re-asks; the second is accepted and persisted to config.yaml.
    """
    validation_config.download_directory = tmp_path / "nonexistent"
    replacement = tmp_path / "picked"
    replacement.mkdir()
    monkeypatch.setattr("config.validation.asyncio.sleep", scaled_async_sleep_recording)
    fake_prompt_session.extend([str(tmp_path / "still_missing"), str(replacement)])

    await validate_adjust_download_directory(validation_config)

    assert validation_config.download_directory == replacement
    assert validation_config.config_path.exists()
    assert scaled_async_sleep_recording.calls == [10]
    assert fake_prompt_session == []


async def test_validate_adjust_download_directory_creates_missing_temp_folder(
    validation_config, tmp_path
):
    """Non-existent temp_folder → created on disk, kept in config."""
    new_temp = tmp_path / "temp_created"
    validation_config.temp_folder = new_temp
    # Valid download_directory so we don't enter the prompt branch.
    validation_config.download_directory = tmp_path

    await validate_adjust_download_directory(validation_config)

    assert new_temp.exists()
    assert new_temp.is_dir()
    assert validation_config.temp_folder == new_temp


async def test_validate_adjust_download_directory_temp_folder_creation_error_falls_back(
    validation_config, tmp_path, monkeypatch
):
    """PermissionError when creating temp_folder → falls back to None.

    Patches ``Path.mkdir`` via an on-the-fly subclass — real code would
    need a truly unwriteable path to reproduce, which is environment-
    dependent. The leaf failure we're exercising is mkdir raising; that
    maps to any OSError subclass.
    """
    bad_temp = tmp_path / "cannot_create"

    original_mkdir = Path.mkdir

    def _raise_on_target(self, *args, **kwargs):
        if self == bad_temp:
            raise PermissionError("simulated access denied")
        return original_mkdir(self, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", _raise_on_target)
    validation_config.temp_folder = bad_temp
    validation_config.download_directory = tmp_path

    await validate_adjust_download_directory(validation_config)

    assert validation_config.temp_folder is None


async def test_validate_adjust_download_directory_temp_folder_not_a_directory(
    validation_config, tmp_path
):
    """temp_folder path exists but is a file, not a directory → falls back."""
    not_a_dir = tmp_path / "i_am_a_file.txt"
    not_a_dir.write_text("hello")
    validation_config.temp_folder = not_a_dir
    validation_config.download_directory = tmp_path

    await validate_adjust_download_directory(validation_config)

    assert validation_config.temp_folder is None


async def test_validate_adjust_download_directory_temp_folder_valid_existing_dir(
    validation_config, tmp_path
):
    """temp_folder already exists as a directory → kept unchanged."""
    existing = tmp_path / "existing_temp"
    existing.mkdir()
    validation_config.temp_folder = existing
    validation_config.download_directory = tmp_path

    await validate_adjust_download_directory(validation_config)

    assert validation_config.temp_folder == existing


# -- validate_adjust_download_mode ------------------------------------------


async def test_validate_adjust_download_mode_non_interactive_no_change(
    validation_config,
):
    """Non-interactive mode: no prompt, mode unchanged."""
    await validate_adjust_download_mode(validation_config, download_mode_set=False)
    assert validation_config.download_mode == DownloadMode.TIMELINE


@pytest.mark.parametrize(
    ("answers", "expected_mode"),
    [
        pytest.param(["n"], DownloadMode.TIMELINE, id="declines"),
        pytest.param(["y", "SINGLE"], DownloadMode.SINGLE, id="changes-mode"),
        pytest.param(
            ["y", "INVALIDMODE", "n"],
            DownloadMode.TIMELINE,
            id="invalid-mode-then-declines-keeps-original",
        ),
    ],
)
async def test_validate_adjust_download_mode_interactive(
    validation_config, tty, fake_prompt_session, answers, expected_mode
):
    """Interactive mode drives the real confirm/mode-entry loop.

    An invalid mode string raises the real ``DownloadMode(...)`` ``ValueError``,
    which production logs before asking again.
    """
    validation_config.interactive = True
    fake_prompt_session.extend(answers)

    await validate_adjust_download_mode(validation_config, download_mode_set=False)

    assert validation_config.download_mode == expected_mode
    assert fake_prompt_session == []


async def test_validate_adjust_download_mode_skips_prompt_when_mode_preset(
    validation_config, no_tty
):
    """download_mode_set=True → no prompt even if interactive (a prompt would raise)."""
    validation_config.interactive = True

    await validate_adjust_download_mode(validation_config, download_mode_set=True)
    assert validation_config.download_mode == DownloadMode.TIMELINE


# -- validate_log_levels ----------------------------------------------------


def test_validate_log_levels_invalid_level_is_corrected(validation_config):
    """Invalid log level is replaced with INFO (the default-non-debug level)."""
    validation_config.log_levels = {"root": "INVALID", "api": "debug"}
    validation_config.debug = False

    validate_log_levels(validation_config)

    assert validation_config.log_levels["root"] == "INFO"
    # "debug" is valid (case-insensitive); the validator only corrects
    # to the default when the uppercased level isn't in VALID_LEVELS.
    assert validation_config.log_levels["api"] == "debug"


def test_validate_log_levels_debug_mode_forces_debug_everywhere(validation_config):
    """debug=True → all log levels forced to DEBUG."""
    validation_config.log_levels = {"root": "INFO", "api": "warning"}
    validation_config.debug = True

    validate_log_levels(validation_config)

    assert all(level == "DEBUG" for level in validation_config.log_levels.values())


# -- validate_adjust_config (orchestrator) ----------------------------------


@pytest.mark.asyncio
async def test_validate_adjust_config_raises_when_creator_names_invalid(
    validation_config,
):
    """Orchestrator raises ConfigError when validate_creator_names returns False.

    Sets user_names to None so the real ``validate_creator_names`` returns
    False — the real orchestrator then raises ConfigError.
    """
    validation_config.user_names = None

    with pytest.raises(ConfigError, match="no valid creator name specified"):
        await validate_adjust_config(validation_config, download_mode_set=False)


@pytest.mark.asyncio
async def test_validate_adjust_config_runs_all_validators_end_to_end(
    validation_config, caplog
):
    """Orchestrator invokes every sub-validator end-to-end with no internal mocks.

    All config fields are pre-set to valid values via the fixture. The
    real orchestrator walks through creator names → token → user agent →
    check key → download directory → download mode. No sub-validator
    should raise; the run completes cleanly.
    """

    caplog.set_level(logging.INFO)

    # Everything's valid → no sub-validator should raise or do meaningful work.
    await validate_adjust_config(validation_config, download_mode_set=True)

    # Smoke: we hit at least one informational log from any sub-validator.
    info_messages = [r.getMessage() for r in caplog.records if r.levelname == "INFO"]
    assert info_messages, (
        "Expected at least one info log from orchestrator sub-validators"
    )
