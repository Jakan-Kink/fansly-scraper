"""Unit tests for configuration validation"""

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
        mock_validate.side_effect = lambda name, interactive: (
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

    with pytest.raises(
        ConfigError, match="Reached.*authorization token.*still invalid"
    ):
        validate_adjust_token(mock_config)


def test_validate_adjust_user_agent_valid(mock_config):
    """Test user agent validation with valid agent"""
    mock_config.useragent_is_valid.return_value = True
    validate_adjust_user_agent(mock_config)
    mock_config.useragent_is_valid.assert_called_once()


@patch("requests.get")
def test_validate_adjust_user_agent_invalid(mock_get, mock_config):
    """Test user agent validation with invalid agent"""
    mock_config.useragent_is_valid.return_value = False
    mock_get.return_value.status_code = 200
    mock_get.return_value.json.return_value = ["test-agent"]

    validate_adjust_user_agent(mock_config)
    assert mock_config.user_agent is not None


def test_validate_adjust_check_key_guessed(mock_config):
    """Test check key validation with successful guess"""
    mock_config.user_agent = "test-agent"
    mock_config.main_js_pattern = "pattern"
    mock_config.check_key_pattern = "pattern"

    with patch("config.validation.guess_check_key") as mock_guess:
        mock_guess.return_value = "guessed_key"
        validate_adjust_check_key(mock_config)
        assert mock_config.check_key == "guessed_key"


def test_validate_adjust_check_key_interactive_change(mock_config, monkeypatch):
    """Test check key validation with interactive user input to change the key"""
    mock_config.interactive = True
    mock_config.user_agent = None
    inputs = iter(
        ["n", "new_key", "y"]
    )  # First no to confirm current, then new key, then yes to confirm
    monkeypatch.setattr("builtins.input", lambda _: next(inputs))
    validate_adjust_check_key(mock_config)
    assert mock_config.check_key == "new_key"


def test_validate_adjust_download_directory_local(mock_config):
    """Test download directory validation with local directory"""
    mock_config.download_directory = Path("local_dir")
    validate_adjust_download_directory(mock_config)
    assert mock_config.download_directory == Path.cwd()


def test_validate_adjust_download_directory_custom_valid(mock_config):
    """Test download directory validation with valid custom directory"""
    mock_dir = MagicMock(spec=Path)
    mock_dir.is_dir.return_value = True
    mock_config.download_directory = mock_dir
    validate_adjust_download_directory(mock_config)
    assert mock_config.download_directory == mock_dir


def test_validate_adjust_download_directory_create_temp(mock_config):
    """Test download directory validation with temp folder creation"""
    mock_path = MagicMock(spec=Path)
    mock_path.exists.return_value = False
    mock_config.temp_folder = mock_path
    validate_adjust_download_directory(mock_config)
    mock_path.mkdir.assert_called_once_with(parents=True, exist_ok=True)


def test_validate_adjust_download_directory_temp_error(mock_config):
    """Test download directory validation with temp folder creation error"""
    mock_path = MagicMock(spec=Path)
    mock_path.exists.return_value = False
    mock_path.mkdir.side_effect = PermissionError("Access denied")
    mock_config.temp_folder = mock_path
    validate_adjust_download_directory(mock_config)
    assert mock_config.temp_folder is None  # Should fall back to system default


def test_validate_adjust_download_directory_invalid(mock_config):
    """Test download directory validation with invalid directory"""
    mock_path = MagicMock(spec=Path)
    mock_path.is_dir.return_value = False
    mock_config.download_directory = mock_path
    mock_ask_dir = MagicMock(spec=Path)
    with patch("config.validation.ask_correct_dir", return_value=mock_ask_dir):
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
    inputs = iter(["y", "INVALID"])  # Yes to change, then invalid mode
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
    with patch("config.validation.validate_creator_names") as mock_validate:
        mock_validate.return_value = True
        validate_adjust_config(mock_config, download_mode_set=False)
        mock_validate.assert_called_once()


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
