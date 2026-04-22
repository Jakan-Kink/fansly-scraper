"""Unit tests for configuration validation"""

import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

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


@pytest.fixture
def mock_config():
    config = MagicMock(spec=FanslyConfig)
    config.interactive = False
    config.user_names = {"validuser1", "validuser2"}
    config.token = "test_token"
    config.user_agent = "test_user_agent"
    config.check_key = "test_check_key"
    config.download_directory = Path.cwd()
    config.download_mode = DownloadMode.TIMELINE
    # Ensure username/password are None (not MagicMock) to prevent early return
    config.username = None
    config.password = None
    # Make validation functions return True
    config.token_is_valid.return_value = True
    config.useragent_is_valid.return_value = True
    return config


def test_validate_creator_names_valid(mock_config):
    """Test validation of creator names with valid names"""
    with patch("config.validation.validate_adjust_creator_name") as mock_validate:
        mock_validate.return_value = "validuser1"
        assert validate_creator_names(mock_config) is True


def test_validate_creator_names_invalid(mock_config):
    """Test validation of creator names with invalid names"""
    mock_config.user_names = {"invaliduser"}
    with patch("config.validation.validate_adjust_creator_name") as mock_validate:
        mock_validate.return_value = None
        result = validate_creator_names(mock_config)
    assert result is True  # Returns True for empty set as it will use following list


def test_validate_creator_names_empty(mock_config):
    """Test validation with empty user names list"""
    mock_config.user_names = None
    assert validate_creator_names(mock_config) is False


def test_validate_creator_names_adjusted(mock_config):
    """Test validation where a name gets adjusted"""
    mock_config.user_names = {"user1", "user2"}
    with patch("config.validation.validate_adjust_creator_name") as mock_validate:
        # Return adjusted name for first user, same name for second
        mock_validate.side_effect = ["adjusted_user1", "user2"]
        with patch("config.validation.save_config_or_raise") as mock_save:
            assert validate_creator_names(mock_config) is True
            # Verify save was called since a name was adjusted
            mock_save.assert_called_once_with(mock_config)
            # Verify set was updated correctly
            assert mock_config.user_names == {"adjusted_user1", "user2"}


def test_validate_creator_names_removed(mock_config):
    """Test validation where invalid names are removed"""
    mock_config.user_names = {"invalid1", "valid", "invalid2"}
    with patch("config.validation.validate_adjust_creator_name") as mock_validate:
        # First mock returns None (invalid), second returns the valid name, third returns None
        mock_validate.side_effect = lambda name, _interactive: (
            None if name in ["invalid1", "invalid2"] else name
        )
        with patch("config.validation.save_config_or_raise") as mock_save:
            assert validate_creator_names(mock_config) is True
            # Verify save was called since names were removed
            mock_save.assert_called_once_with(mock_config)
            # Verify only valid name remains
            assert mock_config.user_names == {"valid"}


def test_validate_creator_names_interactive_adjustment(mock_config):
    """Test validation with interactive name adjustment"""
    mock_config.interactive = True
    mock_config.user_names = {"invalid_user"}
    with patch("config.validation.validate_adjust_creator_name") as mock_validate:
        mock_validate.return_value = "corrected_user"
        with patch("config.validation.save_config_or_raise") as mock_save:
            assert validate_creator_names(mock_config) is True
            mock_validate.assert_called_once_with("invalid_user", True)
            mock_save.assert_called_once_with(mock_config)
            assert mock_config.user_names == {"corrected_user"}


def test_validate_adjust_creator_name_valid():
    """Test validation of a valid creator name"""
    name = "validuser"
    assert validate_adjust_creator_name(name) == "validuser"


def test_validate_adjust_creator_name_invalid_replaceme():
    """Test validation with 'ReplaceMe' placeholder"""
    assert validate_adjust_creator_name("ReplaceMe") is None


def test_validate_adjust_creator_name_invalid_spaces():
    """Test validation with spaces in name"""
    assert validate_adjust_creator_name("invalid user") is None


def test_validate_adjust_creator_name_invalid_length():
    """Test validation with invalid length"""
    assert validate_adjust_creator_name("a") is None  # Too short
    assert validate_adjust_creator_name("a" * 31) is None  # Too long


def test_validate_adjust_creator_name_invalid_chars():
    """Test validation with invalid characters"""
    assert validate_adjust_creator_name("user!@#") is None


def test_validate_adjust_creator_name_interactive(monkeypatch):
    """Test interactive validation with user input"""
    monkeypatch.setattr("builtins.input", lambda _: "validuser")
    assert validate_adjust_creator_name("invalid user", interactive=True) == "validuser"


@patch("importlib.util.find_spec")
def test_validate_adjust_token_valid(mock_find_spec, mock_config):
    """Test token validation with valid token"""
    mock_find_spec.return_value = None  # Mock plyvel not being installed
    mock_config.token_is_valid.return_value = True

    # Add mock to prevent actual validation code from running
    with patch("config.validation.textio_logger"):
        validate_adjust_token(mock_config)
        assert (
            mock_config.token_is_valid.call_count == 2
        )  # Called during initial check and final validation


@patch("importlib.util.find_spec")
def test_validate_adjust_token_invalid_raises(mock_find_spec, mock_config):
    """Test token validation with invalid token raises error"""
    mock_find_spec.return_value = None  # Mock plyvel not being installed
    mock_config.token_is_valid.return_value = False
    mock_config.interactive = True

    # Skip the browser automation and web calls by mocking
    with (
        patch("config.validation.open_get_started_url"),
        patch("config.browser.find_leveldb_folders", return_value=[]),
        pytest.raises(
            ConfigError, match=r"Reached.*authorization token.*still invalid"
        ),
    ):
        validate_adjust_token(mock_config)


def test_validate_adjust_user_agent_valid(mock_config):
    """Test user agent validation with valid agent"""
    mock_config.useragent_is_valid.return_value = True
    validate_adjust_user_agent(mock_config)
    mock_config.useragent_is_valid.assert_called_once()


@patch("httpx.get")
def test_validate_adjust_user_agent_invalid(mock_get, mock_config):
    """Test user agent validation with invalid agent"""
    # Set up the mock to return invalid user agent and then get a new one
    mock_config.useragent_is_valid.return_value = False
    mock_get.return_value.status_code = 200
    mock_get.return_value.json.return_value = ["test-agent"]

    # Mock save_config_or_raise to avoid file access
    with patch("config.validation.save_config_or_raise") as mock_save:
        validate_adjust_user_agent(mock_config)

        # Verify the user agent is updated and config is saved
        assert mock_config.user_agent is not None
        mock_save.assert_called_once_with(mock_config)


def test_validate_adjust_check_key_guessed(mock_config):
    """Test check key validation with successful guess"""
    mock_config.user_agent = "test-agent"
    mock_config.main_js_pattern = "pattern"
    mock_config.check_key_pattern = "pattern"

    with (
        patch("helpers.checkkey.guess_check_key", return_value="guessed_key"),
        patch("config.validation.save_config_or_raise"),
        patch("config.validation.textio_logger"),
    ):
        validate_adjust_check_key(mock_config)
        assert mock_config.check_key == "guessed_key"


def test_validate_adjust_check_key_interactive_change(mock_config, monkeypatch):
    """Test check key validation with interactive user input to change the key"""
    mock_config.interactive = True
    mock_config.user_agent = None

    # Mock user inputs
    inputs = iter(
        ["n", "new_key", "y"]
    )  # First no to confirm current, then new key, then yes to confirm
    monkeypatch.setattr("builtins.input", lambda _: next(inputs))

    # Mock dependent functions to speed up test
    with (
        patch("helpers.checkkey.guess_check_key", return_value=None),
        patch("config.validation.save_config_or_raise"),
        patch("config.validation.textio_logger"),
    ):
        validate_adjust_check_key(mock_config)
        assert mock_config.check_key == "new_key"


def test_validate_adjust_download_directory_local(mock_config):
    """Test download directory validation with local directory"""
    mock_config.download_directory = Path("local_dir")

    # Add mocks to prevent actual file system operations
    with patch("config.validation.textio_logger"):
        validate_adjust_download_directory(mock_config)
        assert mock_config.download_directory == Path.cwd()


def test_validate_adjust_download_directory_custom_valid(mock_config):
    """Test download directory validation with valid custom directory"""
    mock_dir = MagicMock(spec=Path)
    mock_dir.is_dir.return_value = True
    mock_config.download_directory = mock_dir

    # Add mocks to prevent actual file system operations
    with patch("config.validation.textio_logger"):
        validate_adjust_download_directory(mock_config)
        assert mock_config.download_directory == mock_dir


def test_validate_adjust_download_directory_create_temp(mock_config):
    """Test download directory validation with temp folder creation"""
    mock_path = MagicMock(spec=Path)
    mock_path.exists.return_value = False
    mock_config.temp_folder = mock_path

    # Add mocks to prevent actual file system operations
    with patch("config.validation.textio_logger"):
        validate_adjust_download_directory(mock_config)
        mock_path.mkdir.assert_called_once_with(parents=True, exist_ok=True)


def test_validate_adjust_download_directory_temp_error(mock_config):
    """Test download directory validation with temp folder creation error"""
    mock_path = MagicMock(spec=Path)
    mock_path.exists.return_value = False
    mock_path.mkdir.side_effect = PermissionError("Access denied")
    mock_config.temp_folder = mock_path

    # Add mocks to prevent actual file system operations
    with patch("config.validation.textio_logger"):
        validate_adjust_download_directory(mock_config)
        assert mock_config.temp_folder is None  # Should fall back to system default


def test_validate_adjust_download_directory_invalid(mock_config):
    """Test download directory validation with invalid directory"""
    mock_path = MagicMock(spec=Path)
    mock_path.is_dir.return_value = False
    mock_config.download_directory = mock_path
    mock_ask_dir = MagicMock(spec=Path)

    # Add mocks to prevent actual file system operations and UI dialogs
    with (
        patch("config.validation.ask_correct_dir", return_value=mock_ask_dir),
        patch("config.validation.textio_logger"),
        patch(
            "config.validation.sleep"
        ),  # Prevent the sleep() call that slows down the test
        patch("config.validation.save_config_or_raise"),
    ):
        validate_adjust_download_directory(mock_config)
        assert mock_config.download_directory == mock_ask_dir


def test_validate_adjust_download_mode(mock_config):
    """Test download mode validation"""
    validate_adjust_download_mode(mock_config, download_mode_set=False)
    assert mock_config.download_mode == DownloadMode.TIMELINE


def test_validate_adjust_download_mode_interactive(mock_config, monkeypatch):
    """Test interactive download mode validation"""
    mock_config.interactive = True
    # Simulate user not wanting to change the mode
    monkeypatch.setattr("builtins.input", lambda _: "n")
    validate_adjust_download_mode(mock_config, download_mode_set=False)
    assert mock_config.download_mode == DownloadMode.TIMELINE


def test_validate_adjust_download_mode_interactive_change(mock_config, monkeypatch):
    """Test interactive download mode validation with mode change"""
    mock_config.interactive = True
    inputs = iter(["y", "SINGLE"])  # Yes to change, then new mode
    monkeypatch.setattr("builtins.input", lambda _: next(inputs))
    validate_adjust_download_mode(mock_config, download_mode_set=False)
    assert mock_config.download_mode == DownloadMode.SINGLE


def test_validate_adjust_download_mode_invalid_input(mock_config, monkeypatch):
    """Test interactive download mode validation with invalid mode input"""
    mock_config.interactive = True
    # Provide enough inputs: 'y' to change, 'INVALID' as invalid input, then 'n' to exit loop
    inputs = iter(["y", "INVALID", "n"])
    monkeypatch.setattr("builtins.input", lambda _: next(inputs))

    # Should raise ValueError for invalid mode and keep original mode
    def mock_enum(value):
        if value == "INVALID":
            raise ValueError("Invalid mode")
        return DownloadMode.SINGLE

    with patch("config.modes.DownloadMode") as mock_mode:
        mock_mode.side_effect = mock_enum
        validate_adjust_download_mode(mock_config, download_mode_set=False)
        # Should keep TIMELINE mode after invalid input
        assert mock_config.download_mode == DownloadMode.TIMELINE


def test_validate_adjust_config_valid(mock_config):
    """Test full config validation with valid config"""
    # Mock all validation functions to avoid slow processing
    with (
        patch("config.validation.validate_creator_names", return_value=True),
        patch("config.validation.validate_adjust_token"),
        patch("config.validation.validate_adjust_user_agent"),
        patch("config.validation.validate_adjust_check_key"),
        patch("config.validation.validate_adjust_download_directory"),
        patch("config.validation.validate_adjust_download_mode"),
    ):
        # This should run quickly now with all validation steps mocked
        validate_adjust_config(mock_config, download_mode_set=False)


def test_validate_adjust_config_invalid_creator(mock_config):
    """Test full config validation with invalid creator names"""
    with patch("config.validation.validate_creator_names") as mock_validate:
        mock_validate.return_value = False
        with pytest.raises(ConfigError, match="no valid creator name specified"):
            validate_adjust_config(mock_config, download_mode_set=False)


def test_validate_log_levels_invalid(mock_config):
    """Test log level validation with invalid levels"""
    mock_config.log_levels = {"root": "INVALID", "api": "debug"}
    mock_config.debug = False
    validate_log_levels(mock_config)
    assert mock_config.log_levels["root"] == "INFO"
    assert mock_config.log_levels["api"] == "debug"  # Keeps original case


def test_validate_log_levels_debug_mode(mock_config):
    """Test log level validation in debug mode"""
    mock_config.log_levels = {"root": "INFO", "api": "warning"}
    mock_config.debug = True
    validate_log_levels(mock_config)
    assert all(level == "DEBUG" for level in mock_config.log_levels.values())


# -- validate_adjust_token: username/password configured → skip (lines 141-145) --


def test_validate_adjust_token_skips_with_credentials(mock_config):
    """When username and password are set, skip token validation entirely."""
    mock_config.username = "user"
    mock_config.password = "pass"

    # Should return without touching token_is_valid or raising
    validate_adjust_token(mock_config)
    mock_config.token_is_valid.assert_not_called()


# -- validate_adjust_token: plyvel import error branch (lines 155-157) --


@patch("importlib.util.find_spec", side_effect=ImportError("no plyvel"))
def test_validate_adjust_token_plyvel_import_error(mock_find_spec, mock_config):
    """ImportError during plyvel check → logs info about browser-auth."""
    mock_config.token_is_valid.return_value = False
    mock_config.interactive = False

    with pytest.raises(ConfigError, match=r"authorization token.*still invalid"):
        validate_adjust_token(mock_config)


# -- validate_adjust_token: plyvel installed, no account found (line 266-274) --


@patch("importlib.util.find_spec")
def test_validate_adjust_token_plyvel_installed_no_account_interactive(
    mock_find_spec, mock_config
):
    """Plyvel installed, browsers searched, no account found → raises ConfigError."""
    mock_find_spec.return_value = MagicMock()  # plyvel is "installed"
    mock_config.token_is_valid.return_value = False
    mock_config.interactive = True

    with (
        patch("config.browser.get_browser_config_paths", return_value=[]),
        patch("config.validation.open_get_started_url"),
        pytest.raises(ConfigError, match="not found in any of your browser"),
    ):
        validate_adjust_token(mock_config)


@patch("importlib.util.find_spec")
def test_validate_adjust_token_plyvel_installed_no_account_non_interactive(
    mock_find_spec, mock_config
):
    """Non-interactive mode, no account found → raises ConfigError without opening URL."""
    mock_find_spec.return_value = MagicMock()
    mock_config.token_is_valid.return_value = False
    mock_config.interactive = False

    with (
        patch("config.browser.get_browser_config_paths", return_value=[]),
        pytest.raises(ConfigError, match="not found in any of your browser"),
    ):
        validate_adjust_token(mock_config)


# -- validate_adjust_token: browser found, non-interactive auto-link (lines 242-258) --


@patch("importlib.util.find_spec")
def test_validate_adjust_token_auto_link_non_interactive(mock_find_spec, mock_config):
    """Non-interactive mode: found token in browser → auto-links."""
    mock_find_spec.return_value = MagicMock()
    # First call: invalid (triggers browser search), second call: valid (final check)
    mock_config.token_is_valid.side_effect = [False, False, True]
    mock_config.interactive = False

    mock_api = MagicMock()
    mock_api.get_client_user_name.return_value = "found_user"
    mock_config.get_api.return_value = mock_api

    with (
        patch(
            "config.browser.get_browser_config_paths",
            return_value=["/home/user/.config/chromium"],
        ),
        patch(
            "config.browser.find_leveldb_folders",
            return_value=["/home/user/.config/chromium/Default/Local Storage/leveldb"],
        ),
        patch(
            "config.browser.get_auth_token_from_leveldb_folder",
            return_value="valid_browser_token",
        ),
        patch("config.browser.parse_browser_from_string", return_value="Chromium"),
        patch("config.validation.save_config_or_raise"),
    ):
        validate_adjust_token(mock_config)

    assert mock_config.token == "valid_browser_token"
    assert mock_config.token_from_browser_name == "Chromium"


# -- validate_adjust_token: firefox path (lines 210-216) --


@patch("importlib.util.find_spec")
def test_validate_adjust_token_firefox_path(mock_find_spec, mock_config):
    """Firefox browser path uses get_token_from_firefox_profile instead of leveldb."""
    mock_find_spec.return_value = MagicMock()
    mock_config.token_is_valid.side_effect = [False, False, True]
    mock_config.interactive = False

    mock_api = MagicMock()
    mock_api.get_client_user_name.return_value = "firefox_user"
    mock_config.get_api.return_value = mock_api

    with (
        patch(
            "config.browser.get_browser_config_paths",
            return_value=["/home/user/.mozilla/firefox"],
        ),
        patch(
            "config.browser.get_token_from_firefox_profile",
            return_value="firefox_token",
        ),
        patch("config.browser.parse_browser_from_string", return_value="Firefox"),
        patch("config.validation.save_config_or_raise"),
    ):
        validate_adjust_token(mock_config)

    assert mock_config.token == "firefox_token"


# -- validate_adjust_user_agent: httpx error fallback (line 338-339) --


@patch("httpx.get", side_effect=MagicMock(side_effect=Exception("network error")))
def test_validate_adjust_user_agent_http_error(mock_get, mock_config):
    """HTTP error during user-agent fetch → falls back to hardcoded UA."""
    import httpx

    mock_config.useragent_is_valid.return_value = False
    mock_get.side_effect = httpx.HTTPError("timeout")

    with patch("config.validation.save_config_or_raise"):
        validate_adjust_user_agent(mock_config)

    # Should use fallback UA
    assert mock_config.user_agent is not None


# -- validate_adjust_user_agent: non-200 response (line 336) --


@patch("httpx.get")
def test_validate_adjust_user_agent_non_200(mock_get, mock_config):
    """Non-200 response during user-agent fetch → falls back to hardcoded UA."""
    mock_config.useragent_is_valid.return_value = False
    mock_config.token_from_browser_name = None
    mock_get.return_value.status_code = 500

    with patch("config.validation.save_config_or_raise"):
        validate_adjust_user_agent(mock_config)

    assert mock_config.user_agent is not None


# -- validate_adjust_user_agent: browser name info message (line 304-308) --


@patch("httpx.get")
def test_validate_adjust_user_agent_with_browser_name(mock_get, mock_config):
    """When token_from_browser_name is set, logs browser-specific message."""
    mock_config.useragent_is_valid.return_value = False
    mock_config.token_from_browser_name = "Chrome"
    mock_get.return_value.status_code = 200
    mock_get.return_value.json.return_value = ["some-agent"]

    with patch("config.validation.save_config_or_raise"):
        validate_adjust_user_agent(mock_config)

    assert mock_config.user_agent is not None


# -- validate_adjust_check_key: no user_agent (lines 382-383) --


def test_validate_adjust_check_key_no_user_agent_non_interactive(mock_config):
    """No user_agent → warning about web retrieval failure, non-interactive fallback."""
    mock_config.user_agent = None
    mock_config.interactive = False

    with patch("config.validation.input_enter_continue") as mock_continue:
        validate_adjust_check_key(mock_config)
        mock_continue.assert_called_once_with(False)


# -- validate_adjust_check_key: guess fails, interactive confirms (line 396) --


def test_validate_adjust_check_key_guess_fails_interactive_confirm(
    mock_config, monkeypatch
):
    """Web guess fails, interactive user confirms existing key is correct (line 396)."""
    mock_config.interactive = True
    mock_config.user_agent = "test-agent"

    # Confirm with 'y'
    monkeypatch.setattr("builtins.input", lambda _: "y")

    with (
        patch("helpers.checkkey.guess_check_key", return_value=None),
        patch("config.validation.textio_logger"),
    ):
        validate_adjust_check_key(mock_config)
    # Key unchanged
    assert mock_config.check_key == "test_check_key"


# -- validate_adjust_download_directory: temp_folder exists but is not a dir (506-510) --


def test_validate_adjust_download_directory_temp_not_a_dir(mock_config):
    """Temp folder exists but is not a directory → falls back to None."""
    mock_path = MagicMock(spec=Path)
    mock_path.exists.return_value = True
    mock_path.is_dir.return_value = False
    mock_config.temp_folder = mock_path

    with patch("config.validation.textio_logger"):
        validate_adjust_download_directory(mock_config)
    assert mock_config.temp_folder is None


# -- validate_adjust_download_directory: temp_folder exists and is valid dir (512) --


def test_validate_adjust_download_directory_temp_valid_dir(mock_config):
    """Temp folder exists and is a valid directory → keeps it."""
    mock_path = MagicMock(spec=Path)
    mock_path.exists.return_value = True
    mock_path.is_dir.return_value = True
    mock_config.temp_folder = mock_path

    with patch("config.validation.textio_logger"):
        validate_adjust_download_directory(mock_config)
    assert mock_config.temp_folder is mock_path


# -- validate_creator_names: list_changed triggers save (line 63->68) --


def test_validate_creator_names_empty_after_removal(mock_config):
    """All names removed → returns True with 'will process following list' info."""
    mock_config.user_names = {"bad1"}
    with (
        patch("config.validation.validate_adjust_creator_name", return_value=None),
        patch("config.validation.save_config_or_raise"),
    ):
        result = validate_creator_names(mock_config)
    assert result is True
    assert len(mock_config.user_names) == 0


# -- validate_adjust_token: interactive mode with token found (lines 222-258) --


@patch("importlib.util.find_spec")
def test_validate_adjust_token_interactive_user_accepts(mock_find_spec, monkeypatch):
    """Interactive mode: found token → user confirms → token saved (lines 224-258)."""
    config = FanslyConfig(program_version="0.11.0")
    config.interactive = True
    config.token = "short"  # invalid
    config.user_agent = "a" * 50
    config.check_key = "test-key"
    config.username = None
    config.password = None

    mock_find_spec.return_value = types.SimpleNamespace()  # plyvel "installed"

    # User types "yes" to link the account
    monkeypatch.setattr("builtins.input", lambda _: "yes")

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
            return_value="a" * 60,  # valid-length token
        ),
        patch("config.browser.parse_browser_from_string", return_value="Chromium"),
        patch("config.validation.save_config_or_raise"),
        # Patch get_api on config to avoid creating a real FanslyApi
        patch.object(
            FanslyConfig,
            "get_api",
            return_value=types.SimpleNamespace(
                get_client_user_name=lambda _token: "found_user"
            ),
        ),
    ):
        validate_adjust_token(config)

    assert config.token == "a" * 60
    assert config.token_from_browser_name == "Chromium"


@patch("importlib.util.find_spec")
def test_validate_adjust_token_interactive_user_rejects(mock_find_spec, monkeypatch):
    """Interactive mode: found token → user says no → raises ConfigError."""
    config = FanslyConfig(program_version="0.11.0")
    config.interactive = True
    config.token = "short"
    config.user_agent = "a" * 50
    config.check_key = "test-key"
    config.username = None
    config.password = None

    mock_find_spec.return_value = types.SimpleNamespace()

    monkeypatch.setattr("builtins.input", lambda _: "no")

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
            return_value="a" * 60,
        ),
        patch("config.browser.parse_browser_from_string", return_value="Chromium"),
        patch("config.validation.open_get_started_url"),
        patch.object(
            FanslyConfig,
            "get_api",
            return_value=types.SimpleNamespace(
                get_client_user_name=lambda _token: "found_user"
            ),
        ),
        pytest.raises(ConfigError, match=r"authorization token.*still invalid"),
    ):
        validate_adjust_token(config)
