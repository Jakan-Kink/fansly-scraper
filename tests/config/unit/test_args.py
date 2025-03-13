"""Unit tests for argument parsing and configuration mapping."""

import argparse
from pathlib import Path

import pytest

from config.args import map_args_to_config
from config.fanslyconfig import FanslyConfig


@pytest.fixture
def config():
    """Create a basic FanslyConfig instance for testing."""
    config = FanslyConfig(program_version="1.0.0")
    config.config_path = Path("/tmp/config_args.ini")
    # Initialize config with default values
    if not config.config_path.exists():
        with open(config.config_path, mode="w", encoding="utf-8") as f:
            f.write(
                """[TargetedCreator]
Username = ReplaceMe

[MyAccount]
Authorization_Token = ReplaceMe
User_Agent = ReplaceMe
Check_Key = qybZy9-fyszis-bybxyf

[Options]
download_directory = Local_directory
download_mode = Normal
metadata_handling = Advanced
download_media_previews = True
open_folder_when_finished = True
separate_messages = True
separate_previews = False
separate_timeline = True
separate_metadata = False
show_downloads = True
show_skipped_downloads = True
use_duplicate_threshold = False
use_folder_suffix = True
interactive = True
prompt_on_exit = True
timeline_retries = 1
timeline_delay_seconds = 60

[Cache]

[Logic]
check_key_pattern = this.checkKey_\\s*=\\s*["']([^"']+)["']
main_js_pattern = \\ssrc\\s*=\\s*"(main\\..*?\\.js)"
"""
            )
    config._load_raw_config()
    return config


@pytest.fixture
def args():
    """Create a basic argparse.Namespace instance for testing."""
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
        use_following=None,
        use_following_with_pagination=False,
        use_pagination_duplication=False,
    )


def test_temp_folder_path_conversion(config, args):
    """Test that temp_folder is properly converted to a Path object."""
    # Test with a string path
    args.temp_folder = "/tmp/test_temp"
    map_args_to_config(args, config)
    assert isinstance(config.temp_folder, Path)
    assert str(config.temp_folder) == "/tmp/test_temp"

    # Test with None value - should keep previous value
    args.temp_folder = None
    map_args_to_config(args, config)
    assert isinstance(config.temp_folder, Path)
    assert str(config.temp_folder) == "/tmp/test_temp"


def test_temp_folder_and_download_dir_path_conversion(config, args):
    """Test that both temp_folder and download_directory are properly handled."""
    # Test both paths being set
    args.temp_folder = "/tmp/test_temp"
    args.download_directory = "/tmp/test_downloads"
    map_args_to_config(args, config)
    assert isinstance(config.temp_folder, Path)
    assert isinstance(config.download_directory, Path)
    assert str(config.temp_folder) == "/tmp/test_temp"
    assert str(config.download_directory) == "/tmp/test_downloads"

    # Test mixed None and path values - should keep previous values
    args.temp_folder = None
    args.download_directory = "/tmp/test_downloads"
    map_args_to_config(args, config)
    assert isinstance(config.temp_folder, Path)
    assert isinstance(config.download_directory, Path)
    assert str(config.temp_folder) == "/tmp/test_temp"
    assert str(config.download_directory) == "/tmp/test_downloads"

    args.temp_folder = "/tmp/test_temp"
    args.download_directory = None
    map_args_to_config(args, config)
    assert isinstance(config.temp_folder, Path)
    assert isinstance(config.download_directory, Path)
    assert str(config.temp_folder) == "/tmp/test_temp"
    assert str(config.download_directory) == "/tmp/test_downloads"


def test_temp_folder_with_spaces(config, args):
    """Test that temp_folder paths with spaces are handled correctly."""
    args.temp_folder = "/tmp/test folder/with spaces"
    map_args_to_config(args, config)
    assert isinstance(config.temp_folder, Path)
    assert str(config.temp_folder) == "/tmp/test folder/with spaces"


def test_temp_folder_with_special_chars(config, args):
    """Test that temp_folder paths with special characters are handled correctly."""
    args.temp_folder = "/tmp/test@folder/with#special&chars!"
    map_args_to_config(args, config)
    assert isinstance(config.temp_folder, Path)
    assert str(config.temp_folder) == "/tmp/test@folder/with#special&chars!"


def test_temp_folder_relative_path(config, args):
    """Test that relative temp_folder paths are handled correctly."""
    args.temp_folder = "relative/path/to/temp"
    map_args_to_config(args, config)
    assert isinstance(config.temp_folder, Path)
    assert str(config.temp_folder) == "relative/path/to/temp"


def test_temp_folder_windows_path(config, args):
    """Test that Windows-style paths are handled correctly."""
    args.temp_folder = "C:\\Users\\Test\\AppData\\Local\\Temp"
    map_args_to_config(args, config)
    assert isinstance(config.temp_folder, Path)
    # Path object will normalize slashes
    assert (
        str(config.temp_folder).replace("/", "\\")
        == "C:\\Users\\Test\\AppData\\Local\\Temp"
    )
