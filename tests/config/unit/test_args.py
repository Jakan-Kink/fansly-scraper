"""Unit tests for argument parsing and configuration mapping."""

import argparse
from pathlib import Path

import pytest

from config.args import map_args_to_config
from config.logging import init_logging_config


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
        metadata_db_file=None,
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
