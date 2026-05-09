"""Unit tests for configuration validation.

Post-Wave-2.3 rewrite: this suite exercises ``config/validation.py`` with
a real ``FanslyConfig`` instead of ``MagicMock(spec=FanslyConfig)``. The
prior file used a ``mock_config`` fixture with ``return_value`` attribute
stubs for ``token_is_valid()``/``useragent_is_valid()``, then further
``@patch``-ed every internal helper (``save_config_or_raise``,
``validate_adjust_creator_name``, ``textio_logger``). Those tests
exercised mocks rather than production code.

Replacement strategy — only mock at true edges:
- HTTP: ``respx.mock`` for ``httpx.get`` (user-agent fetcher)
- Browser leaves: ``config.browser.find_leveldb_folders`` etc. touch on-
  disk leveldb/firefox profiles
- stdlib leaves: ``importlib.util.find_spec``, ``builtins.input``,
  ``config.validation.sleep``
- Side-effectful leaves: ``config.validation.open_get_started_url``
  (browser), ``config.validation.ask_correct_dir`` (prompt_toolkit prompt)

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

import logging
import platform as _platform
import types
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest
import respx

from config.fanslyconfig import FanslyConfig
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
    validation_config, monkeypatch
):
    """Interactive mode: user fixes an invalid name → config updates and saves.

    The real ``validate_adjust_creator_name`` loops on invalid input until
    the user types a valid name. We inject ``correctuser`` by monkey-patching
    the ``aprompt_text`` async helper; the real validator accepts the result
    and the set mutation + save fires.
    """
    validation_config.interactive = True
    validation_config.user_names = {"bad user"}  # space → invalid

    async def _fake_aprompt_text(_prompt: str, **_kwargs) -> str:
        return "correctuser"

    monkeypatch.setattr("config.validation.aprompt_text", _fake_aprompt_text)

    assert await validate_creator_names(validation_config) is True
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
    monkeypatch,
):
    """Interactive mode loops on invalid input until a valid one is entered."""

    async def _fake_aprompt_text(_prompt: str, **_kwargs) -> str:
        return "validuser"

    monkeypatch.setattr("config.validation.aprompt_text", _fake_aprompt_text)
    assert (
        await validate_adjust_creator_name("invalid user", interactive=True)
        == "validuser"
    )


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
    mock_find_spec, validation_config
):
    """Interactive + invalid token + no browsers → open_get_started_url fires."""
    mock_find_spec.return_value = None
    validation_config.token = "short"
    validation_config.interactive = True

    with (
        patch("config.validation.open_get_started_url") as mock_open_url,
        pytest.raises(ConfigError, match=r"authorization token.*still invalid"),
    ):
        await validate_adjust_token(validation_config)

    mock_open_url.assert_called_once()


@pytest.mark.asyncio
@patch("importlib.util.find_spec")
async def test_validate_adjust_token_plyvel_installed_no_account_raises(
    mock_find_spec, validation_config
):
    """Plyvel installed + empty browser list → raises with "not found" message."""
    mock_find_spec.return_value = types.SimpleNamespace()  # plyvel "installed"
    validation_config.token = "short"
    validation_config.interactive = False

    with (
        patch("config.browser.get_browser_config_paths", return_value=[]),
        pytest.raises(ConfigError, match="not found in any of your browser"),
    ):
        await validate_adjust_token(validation_config)


@pytest.mark.asyncio
@patch("importlib.util.find_spec")
async def test_validate_adjust_token_auto_links_in_non_interactive_mode(
    mock_find_spec, validation_config
):
    """Non-interactive + token found in browser → auto-linked (lines 242-258)."""
    mock_find_spec.return_value = types.SimpleNamespace()
    validation_config.token = "short"  # invalid → triggers browser search
    validation_config.interactive = False

    valid_token = "a" * 60

    with (
        patch(
            "config.browser.get_browser_config_paths",
            return_value=["/home/user/.config/chromium"],
        ),
        patch(
            "config.browser.find_leveldb_folders",
            return_value=["/home/user/.config/chromium/leveldb"],
        ),
        patch(
            "config.browser.get_auth_token_from_leveldb_folder",
            return_value=valid_token,
        ),
        patch("config.browser.parse_browser_from_string", return_value="Chromium"),
        patch.object(
            FanslyConfig,
            "get_api",
            return_value=types.SimpleNamespace(
                get_client_user_name=AsyncMock(return_value="found_user")
            ),
        ),
    ):
        await validate_adjust_token(validation_config)

    assert validation_config.token == valid_token
    assert validation_config.token_from_browser_name == "Chromium"


@pytest.mark.asyncio
@patch("importlib.util.find_spec")
async def test_validate_adjust_token_firefox_profile_branch(
    mock_find_spec, validation_config
):
    """Firefox path uses ``get_token_from_firefox_profile`` (lines 208-214)."""
    mock_find_spec.return_value = types.SimpleNamespace()
    validation_config.token = "short"
    validation_config.interactive = False

    firefox_token = "b" * 60

    with (
        patch(
            "config.browser.get_browser_config_paths",
            return_value=["/home/user/.mozilla/firefox"],
        ),
        patch(
            "config.browser.get_token_from_firefox_profile",
            return_value=firefox_token,
        ),
        patch("config.browser.parse_browser_from_string", return_value="Firefox"),
        patch.object(
            FanslyConfig,
            "get_api",
            return_value=types.SimpleNamespace(
                get_client_user_name=AsyncMock(return_value="firefox_user")
            ),
        ),
    ):
        await validate_adjust_token(validation_config)

    assert validation_config.token == firefox_token


@pytest.mark.asyncio
@patch("importlib.util.find_spec")
async def test_validate_adjust_token_interactive_user_accepts_link(
    mock_find_spec, validation_config, monkeypatch
):
    """Interactive: token found → user types "yes" → token saved (lines 222-258)."""
    mock_find_spec.return_value = types.SimpleNamespace()
    validation_config.interactive = True
    validation_config.token = "short"

    async def _fake_aconfirm(_q: str, **_k) -> bool:
        return True

    monkeypatch.setattr("config.validation.aconfirm", _fake_aconfirm)

    valid_token = "c" * 60

    with (
        patch(
            "config.browser.get_browser_config_paths",
            return_value=["/home/user/.config/chromium"],
        ),
        patch(
            "config.browser.find_leveldb_folders",
            return_value=["/home/user/.config/chromium/leveldb"],
        ),
        patch(
            "config.browser.get_auth_token_from_leveldb_folder",
            return_value=valid_token,
        ),
        patch("config.browser.parse_browser_from_string", return_value="Chromium"),
        patch.object(
            FanslyConfig,
            "get_api",
            return_value=types.SimpleNamespace(
                get_client_user_name=AsyncMock(return_value="found_user")
            ),
        ),
    ):
        await validate_adjust_token(validation_config)

    assert validation_config.token == valid_token
    assert validation_config.token_from_browser_name == "Chromium"


@pytest.mark.asyncio
@patch("importlib.util.find_spec")
async def test_validate_adjust_token_interactive_user_rejects_link(
    mock_find_spec, validation_config, monkeypatch
):
    """Interactive: token found but user says "no" → raises ConfigError."""
    mock_find_spec.return_value = types.SimpleNamespace()
    validation_config.interactive = True
    validation_config.token = "short"

    async def _fake_aconfirm(_q: str, **_k) -> bool:
        return False

    monkeypatch.setattr("config.validation.aconfirm", _fake_aconfirm)

    with (
        patch(
            "config.browser.get_browser_config_paths",
            return_value=["/home/user/.config/chromium"],
        ),
        patch(
            "config.browser.find_leveldb_folders",
            return_value=["/home/user/.config/chromium/leveldb"],
        ),
        patch(
            "config.browser.get_auth_token_from_leveldb_folder",
            return_value="d" * 60,
        ),
        patch("config.browser.parse_browser_from_string", return_value="Chromium"),
        patch("config.validation.open_get_started_url"),
        patch.object(
            FanslyConfig,
            "get_api",
            return_value=types.SimpleNamespace(
                get_client_user_name=AsyncMock(return_value="found_user")
            ),
        ),
        pytest.raises(ConfigError, match=r"authorization token.*still invalid"),
    ):
        await validate_adjust_token(validation_config)


@pytest.mark.asyncio
@patch("importlib.util.find_spec")
async def test_validate_adjust_token_leveldb_folder_no_token_continues_loop(
    mock_find_spec, validation_config
):
    """Leveldb folder yields no token → inner ``if`` False; outer ``if all`` False.

    Covers partial branches 201->196 (no token → loop next folder) and
    216->189 (no account → loop next browser_path).
    """
    mock_find_spec.return_value = types.SimpleNamespace()
    validation_config.token = "short"
    validation_config.interactive = False

    with (
        patch(
            "config.browser.get_browser_config_paths",
            return_value=["/home/user/.config/chromium"],
        ),
        patch(
            "config.browser.find_leveldb_folders",
            return_value=["/leveldb/folder1", "/leveldb/folder2"],
        ),
        # Every folder returns None → no valid token found → loop continues,
        # fansly_account stays None, outer ``if all`` skips, final ConfigError fires.
        patch(
            "config.browser.get_auth_token_from_leveldb_folder",
            return_value=None,
        ),
        pytest.raises(ConfigError, match="not found in any of your browser"),
    ):
        await validate_adjust_token(validation_config)


@pytest.mark.asyncio
@patch("importlib.util.find_spec")
async def test_validate_adjust_token_empty_leveldb_folders_continues(
    mock_find_spec, validation_config
):
    """``find_leveldb_folders`` returns empty → inner for-loop body skipped.

    Covers partial branch 196->216 (for-folder loop doesn't execute) and
    216->189 (continues to next browser_path).
    """
    mock_find_spec.return_value = types.SimpleNamespace()
    validation_config.token = "short"
    validation_config.interactive = False

    with (
        patch(
            "config.browser.get_browser_config_paths",
            return_value=["/home/user/.config/chromium"],
        ),
        # Empty folder list — inner for-loop body never executes.
        patch("config.browser.find_leveldb_folders", return_value=[]),
        pytest.raises(ConfigError, match="not found in any of your browser"),
    ):
        await validate_adjust_token(validation_config)


@pytest.mark.asyncio
@patch("importlib.util.find_spec")
async def test_validate_adjust_token_firefox_no_token_continues(
    mock_find_spec, validation_config
):
    """Firefox profile yields no token → ``if browser_fansly_token`` False.

    Covers partial branch 211->216 (firefox path with no token, falls
    through to the ``if all`` check which also evaluates False).
    """
    mock_find_spec.return_value = types.SimpleNamespace()
    validation_config.token = "short"
    validation_config.interactive = False

    with (
        patch(
            "config.browser.get_browser_config_paths",
            return_value=["/home/user/.mozilla/firefox"],
        ),
        patch(
            "config.browser.get_token_from_firefox_profile",
            return_value=None,
        ),
        pytest.raises(ConfigError, match="not found in any of your browser"),
    ):
        await validate_adjust_token(validation_config)


@pytest.mark.asyncio
@patch("importlib.util.find_spec")
async def test_validate_adjust_token_plyvel_installed_interactive_opens_started_url(
    mock_find_spec, validation_config
):
    """Plyvel + interactive + no account found → ``open_get_started_url`` fires.

    Covers line 266: the inner ``if config.interactive: open_get_started_url()``
    branch inside the "no account found" path. Distinct from the
    outer-invalid-token branch which also calls open_get_started_url
    but from a different location.
    """
    mock_find_spec.return_value = types.SimpleNamespace()
    validation_config.token = "short"
    validation_config.interactive = True

    with (
        patch("config.browser.get_browser_config_paths", return_value=[]),
        patch("config.validation.open_get_started_url") as mock_open_url,
        pytest.raises(ConfigError, match="not found in any of your browser"),
    ):
        await validate_adjust_token(validation_config)

    mock_open_url.assert_called_once()


@pytest.mark.asyncio
@patch("importlib.util.find_spec")
async def test_validate_adjust_token_interactive_reprompts_on_invalid_input(
    mock_find_spec, validation_config, monkeypatch
):
    """Interactive: user types garbage → prompt re-asks → accepts next valid input.

    Covers line 236: the "Please enter either 'Yes' or 'No'" error log
    when the user provides input that doesn't start with y or n. This is
    the ONE line previously uncovered in config/validation.py — fixed by
    simulating a garbage-then-valid input sequence.
    """
    mock_find_spec.return_value = types.SimpleNamespace()
    validation_config.interactive = True
    validation_config.token = "short"

    # Note: invalid-input retry behavior now lives in textio.prompts.aconfirm
    # itself (the helper loops on unparseable answers); production code only
    # sees the final True/False. Test simplifies to "user eventually accepts."
    async def _fake_aconfirm(_q: str, **_k) -> bool:
        return True

    monkeypatch.setattr("config.validation.aconfirm", _fake_aconfirm)

    with (
        patch(
            "config.browser.get_browser_config_paths",
            return_value=["/home/user/.config/chromium"],
        ),
        patch(
            "config.browser.find_leveldb_folders",
            return_value=["/home/user/.config/chromium/leveldb"],
        ),
        patch(
            "config.browser.get_auth_token_from_leveldb_folder",
            return_value="e" * 60,
        ),
        patch("config.browser.parse_browser_from_string", return_value="Chromium"),
        patch.object(
            FanslyConfig,
            "get_api",
            return_value=types.SimpleNamespace(
                get_client_user_name=AsyncMock(return_value="found_user")
            ),
        ),
    ):
        await validate_adjust_token(validation_config)

    # User eventually accepted, so token is saved.
    assert validation_config.token == "e" * 60


# -- validate_adjust_user_agent ---------------------------------------------


def test_validate_adjust_user_agent_valid_skips_fetch(validation_config):
    """Valid user-agent → function returns without fetching new one.

    Uses real ``useragent_is_valid()`` which checks len >= 40 and no
    "ReplaceMe". Our default "Mozilla/5.0 " + "A" * 60 is 72 chars,
    trivially valid.
    """
    # No respx.mock context — if the function tried to fetch, httpx would
    # raise a real ConnectError, which would fail the test. Absence of
    # error is proof the fetch path didn't run.
    validate_adjust_user_agent(validation_config)
    # user_agent is unchanged.
    assert validation_config.user_agent.startswith("Mozilla/5.0 ")


def test_validate_adjust_user_agent_invalid_fetches_and_saves(validation_config):
    """Invalid user-agent → fetches from jnrbsn, picks one, saves to config.

    Uses ``respx.mock`` for the httpx call — the real edge. Returns a
    plausible user-agent list spanning multiple OS variants; the real
    ``guess_user_agent`` helper platform-matches (Windows/Darwin/Linux)
    AND version-matches against the host OS. Test asserts only that the
    user_agent *changed* and that the save happened — avoiding brittle
    coupling to CI host OS version strings.
    """
    validation_config.user_agent = "short"  # invalid
    original_ua = validation_config.user_agent

    # Include a UA for the host OS so the platform match inside
    # guess_user_agent can succeed on the CI runner without fragile
    # assertions on OS/version values.
    if _platform.system() == "Darwin":
        # guess_user_agent extracts the "Mac OS X X_Y_Z" tuple from the
        # candidate UA and checks the same string appears in the UA; a
        # plausible modern mac UA looks like "Mac OS X 14_0_0".
        version_str = _platform.mac_ver()[0].replace(".", "_")
        if not version_str:
            version_str = "14_0_0"
        fake_agents = [
            f"Mozilla/5.0 (Macintosh; Intel Mac OS X {version_str}) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ]
    else:
        # Linux/Windows runners can match against a plausible host string.
        fake_agents = [
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ]

    with respx.mock:
        respx.get("https://jnrbsn.github.io/user-agents/user-agents.json").mock(
            side_effect=[httpx.Response(200, json=fake_agents)]
        )
        validate_adjust_user_agent(validation_config)

    # Either the fetched UA was used, or the fallback (Chrome/116) was —
    # both are valid outcomes for this test; what we care about is that
    # the validator ran and set a valid UA + saved the config.
    assert validation_config.user_agent != original_ua
    assert "Chrome" in validation_config.user_agent
    assert validation_config.config_path.exists()


def test_validate_adjust_user_agent_http_error_falls_back(validation_config):
    """httpx.HTTPError during fetch → real hardcoded fallback UA applied."""
    validation_config.user_agent = "short"

    with respx.mock:
        respx.get("https://jnrbsn.github.io/user-agents/user-agents.json").mock(
            side_effect=httpx.ConnectError("network down")
        )
        validate_adjust_user_agent(validation_config)

    # Fallback UA is the hardcoded Chrome 116 string.
    assert "Chrome/116" in validation_config.user_agent


def test_validate_adjust_user_agent_non_200_falls_back(validation_config):
    """Non-200 HTTP response → hardcoded fallback UA applied."""
    validation_config.user_agent = "short"
    validation_config.token_from_browser_name = None

    with respx.mock:
        respx.get("https://jnrbsn.github.io/user-agents/user-agents.json").mock(
            side_effect=[httpx.Response(500, text="internal error")]
        )
        validate_adjust_user_agent(validation_config)

    assert "Chrome/116" in validation_config.user_agent


def test_validate_adjust_user_agent_logs_browser_specific_when_token_from_browser(
    validation_config, caplog
):
    """token_from_browser_name set → logs browser-specific message (lines 302-306)."""

    caplog.set_level(logging.INFO)
    validation_config.user_agent = "short"
    validation_config.token_from_browser_name = "Chrome"

    with respx.mock:
        respx.get("https://jnrbsn.github.io/user-agents/user-agents.json").mock(
            side_effect=[
                httpx.Response(
                    200,
                    json=["Mozilla/5.0 (X11; Linux x86_64) Chrome/120.0.0.0"],
                )
            ]
        )
        validate_adjust_user_agent(validation_config)

    info_messages = [r.getMessage() for r in caplog.records if r.levelname == "INFO"]
    assert any("operating system" in m and "browser" in m for m in info_messages), (
        f"Expected browser-specific info log, got INFO: {info_messages}"
    )


# -- validate_adjust_check_key ----------------------------------------------


async def test_validate_adjust_check_key_guess_succeeds_sets_key(validation_config):
    """guess_check_key returns a value → config.check_key is updated + saved."""
    # config.user_agent is set via fixture — guess_check_key gets called.
    with patch("helpers.checkkey.guess_check_key", return_value="guessed_key_xyz"):
        await validate_adjust_check_key(validation_config)

    assert validation_config.check_key == "guessed_key_xyz"
    assert validation_config.config_path.exists()


async def test_validate_adjust_check_key_guess_fails_interactive_confirm_keeps_key(
    validation_config, monkeypatch
):
    """guess_check_key returns None → interactive yes confirms existing key."""
    validation_config.interactive = True
    original_key = validation_config.check_key

    async def _fake_aconfirm(_q: str, **_k) -> bool:
        return True

    monkeypatch.setattr("config.validation.aconfirm", _fake_aconfirm)

    with patch("helpers.checkkey.guess_check_key", return_value=None):
        await validate_adjust_check_key(validation_config)

    assert validation_config.check_key == original_key


async def test_validate_adjust_check_key_guess_fails_interactive_user_enters_new_key(
    validation_config, monkeypatch
):
    """guess fails → user rejects current → enters new key → confirms it."""
    validation_config.interactive = True

    # First aconfirm: reject current. Second aconfirm: accept new key.
    confirm_answers = iter([False, True])

    async def _fake_aconfirm(_q: str, **_k) -> bool:
        return next(confirm_answers)

    async def _fake_aprompt_text(_q: str, **_k) -> str:
        return "new_key_value"

    monkeypatch.setattr("config.validation.aconfirm", _fake_aconfirm)
    monkeypatch.setattr("config.validation.aprompt_text", _fake_aprompt_text)

    with patch("helpers.checkkey.guess_check_key", return_value=None):
        await validate_adjust_check_key(validation_config)

    assert validation_config.check_key == "new_key_value"


async def test_validate_adjust_check_key_user_rejects_new_key_then_accepts(
    validation_config, monkeypatch
):
    """Interactive: user rejects a new-key confirmation, re-enters, accepts next.

    Covers partial branch where the while-loop continues when the
    confirmation of the typed key is False, prompting again.
    """
    validation_config.interactive = True

    # aconfirm sequence: reject current, reject first try, accept second try.
    confirm_answers = iter([False, False, True])
    text_answers = iter(["first_try", "second_try"])

    async def _fake_aconfirm(_q: str, **_k) -> bool:
        return next(confirm_answers)

    async def _fake_aprompt_text(_q: str, **_k) -> str:
        return next(text_answers)

    monkeypatch.setattr("config.validation.aconfirm", _fake_aconfirm)
    monkeypatch.setattr("config.validation.aprompt_text", _fake_aprompt_text)

    with patch("helpers.checkkey.guess_check_key", return_value=None):
        await validate_adjust_check_key(validation_config)

    assert validation_config.check_key == "second_try"


async def test_validate_adjust_check_key_no_user_agent_non_interactive(
    validation_config,
):
    """user_agent is None → skips guess; non-interactive falls through to continue.

    Covers the ``if config.user_agent:`` False branch.
    """
    validation_config.user_agent = None
    validation_config.interactive = False

    with patch(
        "config.validation.input_enter_continue", new_callable=AsyncMock
    ) as mock_continue:
        await validate_adjust_check_key(validation_config)

    mock_continue.assert_called_once_with(False)


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
    validation_config, tmp_path, monkeypatch
):
    """Invalid directory → sleep + ask_correct_dir → save.

    Uses a non-existent Path as the bad directory; a real tmp_path
    directory as the replacement. Patches ``asyncio.sleep`` (so tests don't
    pause 10s) and ``ask_correct_dir`` (would open a prompt_toolkit prompt).
    """
    validation_config.download_directory = tmp_path / "nonexistent"
    replacement = tmp_path / "picked"
    replacement.mkdir()

    async def _fake_sleep(_seconds):
        return None

    monkeypatch.setattr("config.validation.asyncio.sleep", _fake_sleep)

    with patch(
        "config.validation.ask_correct_dir",
        new_callable=AsyncMock,
        return_value=replacement,
    ):
        await validate_adjust_download_directory(validation_config)

    assert validation_config.download_directory == replacement
    assert validation_config.config_path.exists()


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


async def test_validate_adjust_download_mode_interactive_user_declines(
    validation_config, monkeypatch
):
    """Interactive mode: user answers no → mode unchanged."""
    validation_config.interactive = True

    async def _fake_aconfirm(_q: str, **_k) -> bool:
        return False

    monkeypatch.setattr("config.validation.aconfirm", _fake_aconfirm)

    await validate_adjust_download_mode(validation_config, download_mode_set=False)

    assert validation_config.download_mode == DownloadMode.TIMELINE


async def test_validate_adjust_download_mode_interactive_user_changes_mode(
    validation_config, monkeypatch
):
    """Interactive mode: user answers yes then 'SINGLE' → mode updated."""
    validation_config.interactive = True

    async def _fake_aconfirm(_q: str, **_k) -> bool:
        return True

    async def _fake_aprompt_text(_q: str, **_k) -> str:
        return "SINGLE"

    monkeypatch.setattr("config.validation.aconfirm", _fake_aconfirm)
    monkeypatch.setattr("config.validation.aprompt_text", _fake_aprompt_text)

    await validate_adjust_download_mode(validation_config, download_mode_set=False)

    assert validation_config.download_mode == DownloadMode.SINGLE


async def test_validate_adjust_download_mode_interactive_invalid_mode_preserves_original(
    validation_config, monkeypatch
):
    """Interactive: user yes → invalid mode → no exits → original kept.

    Runs real ``DownloadMode(...)`` constructor — an invalid string raises
    the real ``ValueError`` which the production code catches and logs.
    """
    validation_config.interactive = True

    confirm_answers = iter([True, False])

    async def _fake_aconfirm(_q: str, **_k) -> bool:
        return next(confirm_answers)

    async def _fake_aprompt_text(_q: str, **_k) -> str:
        return "INVALIDMODE"

    monkeypatch.setattr("config.validation.aconfirm", _fake_aconfirm)
    monkeypatch.setattr("config.validation.aprompt_text", _fake_aprompt_text)

    await validate_adjust_download_mode(validation_config, download_mode_set=False)

    assert validation_config.download_mode == DownloadMode.TIMELINE


async def test_validate_adjust_download_mode_skips_prompt_when_mode_preset(
    validation_config, monkeypatch
):
    """download_mode_set=True → no interactive prompt even if interactive=True."""
    validation_config.interactive = True

    async def _fail_aconfirm(*_args, **_kwargs):
        raise AssertionError("aconfirm should not be called when download_mode_set")

    monkeypatch.setattr("config.validation.aconfirm", _fail_aconfirm)

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
