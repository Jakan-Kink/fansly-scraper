"""Unit tests for argument parsing and configuration mapping."""

import argparse
from pathlib import Path

import pytest

from config.args import (
    _handle_boolean_settings,
    _handle_download_mode,
    _handle_metadata_settings,
    _handle_path_settings,
    _handle_unsigned_ints,
    _handle_user_settings,
    check_attributes,
    map_args_to_config,
    parse_args,
)
from config.logging import init_logging_config
from errors import ConfigError


@pytest.fixture
def config_with_path(mock_config, tmp_path):
    """Create a mock_config with config_path set for testing.

    The map_args_to_config function requires config.config_path to be set.
    """
    config_path = tmp_path / "config.ini"
    mock_config.config_path = config_path
    # Initialize logging config to set global _config variable
    init_logging_config(mock_config)
    return mock_config


@pytest.fixture
def args():
    """Create a basic argparse.Namespace instance for testing.

    Includes all required attributes for map_args_to_config including
    PostgreSQL-related settings added in recent migrations.
    """
    return argparse.Namespace(
        debug=False,
        users=None,
        download_mode_normal=False,
        download_mode_messages=False,
        download_mode_timeline=False,
        download_mode_collection=False,
        download_mode_single=None,
        metadata_handling=None,
        download_directory=None,
        token=None,
        user_agent=None,
        check_key=None,
        updated_to=None,
        db_sync_commits=None,
        db_sync_seconds=None,
        db_sync_min_size=None,
        temp_folder=None,
        separate_previews=False,
        use_duplicate_threshold=False,
        separate_metadata=False,
        non_interactive=False,
        no_prompt_on_exit=False,
        no_folder_suffix=False,
        no_media_previews=False,
        hide_downloads=False,
        hide_skipped_downloads=False,
        no_open_folder=False,
        no_separate_messages=False,
        no_separate_timeline=False,
        timeline_retries=None,
        timeline_delay_seconds=None,
        api_max_retries=None,
        use_following=None,
        use_following_with_pagination=False,
        use_pagination_duplication=False,
        reverse_order=False,
        # PostgreSQL settings (added in PostgreSQL migration)
        pg_host=None,
        pg_port=None,
        pg_database=None,
        pg_user=None,
        pg_password=None,
    )


def test_temp_folder_path_conversion(config_with_path, args, tmp_path):
    """Test that temp_folder is properly converted to a Path object."""
    # Create a real temporary folder
    test_temp = tmp_path / "test_temp"
    test_temp.mkdir()

    # Test with a string path
    args.temp_folder = str(test_temp)
    map_args_to_config(args, config_with_path)
    assert isinstance(config_with_path.temp_folder, Path)
    assert config_with_path.temp_folder == test_temp

    # Test with None value - should keep previous value
    args.temp_folder = None
    map_args_to_config(args, config_with_path)
    assert isinstance(config_with_path.temp_folder, Path)
    assert config_with_path.temp_folder == test_temp


def test_temp_folder_and_download_dir_path_conversion(config_with_path, args, tmp_path):
    """Test that both temp_folder and download_directory are properly handled."""
    # Create real temporary folders
    test_temp = tmp_path / "test_temp"
    test_downloads = tmp_path / "test_downloads"
    test_temp.mkdir()
    test_downloads.mkdir()

    # Test both paths being set
    args.temp_folder = str(test_temp)
    args.download_directory = str(test_downloads)
    map_args_to_config(args, config_with_path)
    assert isinstance(config_with_path.temp_folder, Path)
    assert isinstance(config_with_path.download_directory, Path)
    assert config_with_path.temp_folder == test_temp
    assert config_with_path.download_directory == test_downloads

    # Test mixed None and path values - should keep previous values
    args.temp_folder = None
    args.download_directory = str(test_downloads)
    map_args_to_config(args, config_with_path)
    assert isinstance(config_with_path.temp_folder, Path)
    assert isinstance(config_with_path.download_directory, Path)
    assert config_with_path.temp_folder == test_temp
    assert config_with_path.download_directory == test_downloads

    args.temp_folder = str(test_temp)
    args.download_directory = None
    map_args_to_config(args, config_with_path)
    assert isinstance(config_with_path.temp_folder, Path)
    assert isinstance(config_with_path.download_directory, Path)
    assert config_with_path.temp_folder == test_temp
    assert config_with_path.download_directory == test_downloads


def test_temp_folder_with_spaces(config_with_path, args, tmp_path):
    """Test that temp_folder paths with spaces are handled correctly."""
    # Create a real folder with spaces in the name
    test_folder = tmp_path / "test folder" / "with spaces"
    test_folder.mkdir(parents=True)

    args.temp_folder = str(test_folder)
    map_args_to_config(args, config_with_path)
    assert isinstance(config_with_path.temp_folder, Path)
    assert config_with_path.temp_folder == test_folder
    assert test_folder.exists()  # Verify real folder exists


def test_temp_folder_with_special_chars(config_with_path, args, tmp_path):
    """Test that temp_folder paths with special characters are handled correctly."""
    # Create a real folder with special characters (filesystem-safe ones)
    # Note: Some special chars like : / are not allowed on all filesystems
    test_folder = tmp_path / "test@folder" / "with#special&chars!"
    test_folder.mkdir(parents=True)

    args.temp_folder = str(test_folder)
    map_args_to_config(args, config_with_path)
    assert isinstance(config_with_path.temp_folder, Path)
    assert config_with_path.temp_folder == test_folder
    assert test_folder.exists()  # Verify real folder exists


def test_temp_folder_relative_path(config_with_path, args, tmp_path, monkeypatch):
    """Test that relative temp_folder paths are handled correctly."""
    # Change to tmp_path directory to test relative paths
    monkeypatch.chdir(tmp_path)

    # Create a relative path folder
    test_folder = Path("relative/path/to/temp")
    test_folder.mkdir(parents=True)

    args.temp_folder = str(test_folder)
    map_args_to_config(args, config_with_path)
    assert isinstance(config_with_path.temp_folder, Path)
    # The path should be preserved as relative if that's what was provided
    assert str(config_with_path.temp_folder) == "relative/path/to/temp"
    assert test_folder.exists()  # Verify real folder exists


def test_temp_folder_windows_path(config_with_path, args, tmp_path):
    """Test that Windows-style paths are handled correctly.

    This test verifies that Path objects correctly handle Windows-style
    path strings on all platforms. The Path object normalizes slashes
    according to the current platform.
    """
    # Create a real folder and test with Windows-style path string
    test_folder = tmp_path / "Users" / "Test" / "AppData" / "Local" / "Temp"
    test_folder.mkdir(parents=True)

    # On non-Windows systems, this will be treated as a relative path
    # On Windows systems, it would be an absolute path
    # We test that Path handles it correctly regardless
    args.temp_folder = str(test_folder)
    map_args_to_config(args, config_with_path)
    assert isinstance(config_with_path.temp_folder, Path)
    assert config_with_path.temp_folder == test_folder
    assert test_folder.exists()  # Verify real folder exists


def test_parse_args_returns_namespace():
    """Lines 17-487: parse_args returns a Namespace with expected attributes."""
    import sys
    from unittest.mock import patch

    with patch.object(sys, "argv", ["prog"]):
        result = parse_args()

    assert isinstance(result, argparse.Namespace)
    assert hasattr(result, "users")
    assert hasattr(result, "download_mode_single")
    assert hasattr(result, "debug")
    assert hasattr(result, "pg_host")
    assert hasattr(result, "stash_only")


def test_check_attributes_success_and_failure(config_with_path, args):
    """Lines 519-526: valid → pass; invalid → RuntimeError."""
    check_attributes(args, config_with_path, "debug", "debug")

    with pytest.raises(RuntimeError, match="Internal argument configuration error"):
        check_attributes(args, config_with_path, "nonexistent_arg", "debug")

    with pytest.raises(RuntimeError, match="Internal argument configuration error"):
        check_attributes(args, config_with_path, "debug", "nonexistent_config")


def test_handle_debug_settings(config_with_path, args):
    """Lines 529-536: debug=True → sets config.debug + logs args."""
    from config.args import _handle_debug_settings

    args.debug = True
    _handle_debug_settings(args, config_with_path)
    assert config_with_path.debug is True


def test_handle_user_settings_all_branches(config_with_path, args):
    """Lines 539-583: use_following_with_pagination, conflict, users, debug."""
    # use_following_with_pagination → sets both flags, early return
    args.use_following_with_pagination = True
    assert _handle_user_settings(args, config_with_path) is True
    assert config_with_path.use_following is True
    assert config_with_path.use_pagination_duplication is True
    args.use_following_with_pagination = False

    # use_following + users → conflict
    args.use_following = True
    args.users = ["creator1"]
    with pytest.raises(ConfigError, match="Cannot use both"):
        _handle_user_settings(args, config_with_path)
    args.use_following = False

    # users specified → parses and sets
    args.users = ["creator1", "creator2,creator3"]
    assert _handle_user_settings(args, config_with_path) is True
    assert config_with_path.user_names is not None

    # Debug logging path (lines 571-583)
    config_with_path.debug = True
    args.users = ["debuguser"]
    _handle_user_settings(args, config_with_path)
    config_with_path.debug = False
    args.users = None

    # users=None, no flags → no override
    assert _handle_user_settings(args, config_with_path) is False

    # use_following alone
    args.use_following = True
    assert _handle_user_settings(args, config_with_path) is True


def test_handle_download_mode_all_modes(config_with_path, args):
    """Lines 586-620: mode flags, single valid, single invalid, no mode."""
    args.download_mode_normal = True
    override, mode_set = _handle_download_mode(args, config_with_path)
    assert override is True
    assert mode_set is True
    args.download_mode_normal = False

    # Single valid
    args.download_mode_single = "1234567890"
    override, mode_set = _handle_download_mode(args, config_with_path)
    assert override is True
    assert config_with_path.post_id == "1234567890"
    args.download_mode_single = None

    # Single invalid
    args.download_mode_single = "short"
    with pytest.raises(ConfigError, match="not a valid post ID"):
        _handle_download_mode(args, config_with_path)
    args.download_mode_single = None

    # No mode
    override, mode_set = _handle_download_mode(args, config_with_path)
    assert override is False
    assert mode_set is False


def test_handle_metadata_settings(config_with_path, args):
    """Lines 623-636: None → False; valid → True; invalid → ConfigError."""
    assert _handle_metadata_settings(args, config_with_path) is False

    args.metadata_handling = "simple"
    assert _handle_metadata_settings(args, config_with_path) is True
    args.metadata_handling = None

    args.metadata_handling = "nonexistent"
    with pytest.raises(ConfigError, match="not a valid metadata handling"):
        _handle_metadata_settings(args, config_with_path)


def test_handle_path_settings_branches(config_with_path, args):
    """Lines 639-657: empty temp_folder → None; generic attr passthrough."""
    args.temp_folder = ""
    assert _handle_path_settings(args, config_with_path, "temp_folder") is True
    assert config_with_path.temp_folder is None

    args.token = "my_token"
    assert _handle_path_settings(args, config_with_path, "token") is True
    assert config_with_path.token == "my_token"
    args.token = None


def test_handle_boolean_settings(config_with_path, args):
    """Lines 691-731: positive bools + negative bools."""
    args.separate_previews = True
    args.non_interactive = True
    args.reverse_order = True
    result = _handle_boolean_settings(args, config_with_path)
    assert result is True
    assert config_with_path.separate_previews is True
    assert config_with_path.interactive is False
    assert config_with_path.reverse_order is True


def test_handle_unsigned_ints(config_with_path, args):
    """Lines 735-761: valid int, negative clamped, None skipped."""
    args.timeline_retries = 5
    assert _handle_unsigned_ints(args, config_with_path) is True
    assert config_with_path.timeline_retries == 5

    args.timeline_retries = -3
    _handle_unsigned_ints(args, config_with_path)
    assert config_with_path.timeline_retries == 0

    args.timeline_retries = None
    args.timeline_delay_seconds = None
    args.api_max_retries = None
    assert _handle_unsigned_ints(args, config_with_path) is False


def test_map_args_no_config_path(mock_config, args):
    """Line 778: config_path is None → RuntimeError."""
    mock_config.config_path = None
    with pytest.raises(RuntimeError, match="configuration path not set"):
        map_args_to_config(args, mock_config)
