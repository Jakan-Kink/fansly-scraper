import os
from configparser import ConfigParser
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from config.config import (
    load_config,
    parse_items_from_line,
    sanitize_creator_names,
    save_config_or_raise,
    username_has_valid_chars,
    username_has_valid_length,
)
from config.fanslyconfig import FanslyConfig
from errors import ConfigError


@pytest.fixture
def config():
    return FanslyConfig(program_version="0.10.0")


@pytest.fixture
def temp_config_dir():
    with TemporaryDirectory() as temp_dir:
        original_cwd = os.getcwd()
        os.chdir(temp_dir)
        yield Path(temp_dir)
        os.chdir(original_cwd)


def test_user_names_str_none(config):
    assert config.user_names is None
    assert config.user_names_str() == "ReplaceMe"


def test_user_names_str_empty_set(config):
    config.user_names = set()
    assert config.user_names_str() == ""


def test_user_names_str_with_names(config):
    config.user_names = {"alice", "bob", "charlie"}
    assert config.user_names_str() == "alice, bob, charlie"


def test_parse_items_from_line_comma_separated():
    line = "alice,bob,charlie"
    assert parse_items_from_line(line) == ["alice", "bob", "charlie"]


def test_parse_items_from_line_space_separated():
    line = "alice bob charlie"
    assert parse_items_from_line(line) == ["alice", "bob", "charlie"]


def test_sanitize_creator_names():
    names = ["  @Alice  ", "BOB", " charlie ", "", "  "]
    expected = {"alice", "bob", "charlie"}
    assert sanitize_creator_names(names) == expected


def test_load_config_creates_file_if_not_exists(temp_config_dir, config):
    config_path = temp_config_dir / "config.ini"
    assert not config_path.exists()
    load_config(config)
    assert config_path.exists()


def test_load_config_temp_folder_handling(temp_config_dir, config):
    config_path = temp_config_dir / "config.ini"

    # Create config with temp_folder
    with config_path.open("w") as f:
        f.write(
            """[Options]
download_mode = Normal
metadata_handling = Advanced
interactive = True
download_directory = Local_directory
temp_folder = /custom/temp/path
"""
        )

    load_config(config)
    assert config.temp_folder == Path("/custom/temp/path")


def test_load_config_download_directory_handling(temp_config_dir, config):
    config_path = temp_config_dir / "config.ini"

    # Create config with download_directory
    with config_path.open("w") as f:
        f.write(
            """[Options]
download_directory = /custom/download/path
"""
        )

    load_config(config)
    assert config.download_directory == Path("/custom/download/path")


def test_load_config_default_download_directory(temp_config_dir, config):
    config_path = temp_config_dir / "config.ini"

    # Create minimal config without download_directory
    with config_path.open("w") as f:
        f.write(
            """[Options]
"""
        )

    load_config(config)
    assert config.download_directory == Path("Local_directory")


def test_username_validation():
    # Test valid usernames
    assert username_has_valid_length("user123")
    assert username_has_valid_chars("user123")
    assert username_has_valid_length("user_name")
    assert username_has_valid_chars("user_name")
    assert username_has_valid_length("a" * 30)  # Max length
    assert username_has_valid_chars("user-name_123")

    # Test invalid usernames
    assert not username_has_valid_length(None)
    assert not username_has_valid_chars(None)
    assert not username_has_valid_length("abc")  # Too short
    assert not username_has_valid_length("a" * 31)  # Too long
    assert not username_has_valid_chars("user@name")  # Invalid char @
    assert not username_has_valid_chars("user name")  # Space not allowed
    assert not username_has_valid_chars("user#name")  # Invalid char #


def test_save_config_or_raise(temp_config_dir, config):
    config_path = temp_config_dir / "config.ini"
    config.config_path = config_path
    config.user_names = {"testuser"}
    config.token = "test_token"
    config.user_agent = "test_agent"
    config.check_key = "test_key"

    # Should save successfully
    assert save_config_or_raise(config) is True

    # Verify file exists and contains expected values
    assert config_path.exists()
    with config_path.open() as f:
        content = f.read()
        assert "testuser" in content
        assert "test_token" in content
        assert "test_agent" in content
        assert "test_key" in content


def test_save_config_or_raise_no_path(config):
    config.config_path = None
    with pytest.raises(ConfigError):
        save_config_or_raise(config)


def test_load_config_invalid_config(temp_config_dir, config):
    config_path = temp_config_dir / "config.ini"

    # Create invalid config with invalid value
    with config_path.open("w") as f:
        f.write(
            """[Options]
download_mode = InvalidMode
"""
        )

    with pytest.raises(ConfigError) as exc_info:
        load_config(config)
    assert "wrong value in the config.ini file" in str(exc_info.value)


def test_token_validation(config):
    # Test valid token
    config.token = "a" * 51  # Token longer than 50 chars
    assert config.token_is_valid() is True

    # Test invalid tokens
    config.token = None
    assert config.token_is_valid() is False

    config.token = "a" * 49  # Too short
    assert config.token_is_valid() is False

    config.token = "ReplaceMe" + "a" * 50
    assert config.token_is_valid() is False


def test_useragent_validation(config):
    # Test valid user agent
    config.user_agent = "a" * 41  # User agent longer than 40 chars
    assert config.useragent_is_valid() is True

    # Test invalid user agents
    config.user_agent = None
    assert config.useragent_is_valid() is False

    config.user_agent = "a" * 39  # Too short
    assert config.useragent_is_valid() is False

    config.user_agent = "ReplaceMe" + "a" * 40
    assert config.useragent_is_valid() is False


def test_load_config_with_db_sync_settings(temp_config_dir, config):
    config_path = temp_config_dir / "config.ini"

    # Create config with DB sync settings
    with config_path.open("w") as f:
        f.write(
            """[Options]
download_mode = Normal
metadata_handling = Advanced
interactive = True
download_directory = Local_directory
db_sync_commits = 500
db_sync_seconds = 30
db_sync_min_size = 100
"""
        )

    load_config(config)
    assert config.db_sync_commits == 500
    assert config.db_sync_seconds == 30
    assert config.db_sync_min_size == 100


def test_load_config_with_metadata_db_file(temp_config_dir, config):
    config_path = temp_config_dir / "config.ini"
    db_path = "/custom/path/metadata.db"

    # Create config with metadata_db_file
    with config_path.open("w") as f:
        f.write(
            f"""[Options]
download_mode = Normal
metadata_handling = Advanced
interactive = True
download_directory = Local_directory
metadata_db_file = {db_path}
"""
        )

    load_config(config)
    assert config.metadata_db_file == Path(db_path)


def test_default_metadata_db_location(temp_config_dir, config):
    # Test default location with download_directory set
    config.download_directory = Path("/test/download/dir")
    config.metadata_db_file = None  # Reset to trigger default location
    config.__post_init__()  # Need to call this to update metadata_db_file
    assert config.metadata_db_file == Path("/test/download/dir/metadata_db.sqlite3")

    # Test default location without download_directory
    config.download_directory = None
    config.metadata_db_file = None  # Reset to trigger default location
    config.__post_init__()  # Need to call this to update metadata_db_file
    assert (
        config.metadata_db_file == Path.cwd() / "metadata_db.sqlite3"
    )  # We're in temp_config_dir


def test_load_config_with_cache_section(temp_config_dir, config):
    config_path = temp_config_dir / "config.ini"

    # Create config with Cache section
    with config_path.open("w") as f:
        f.write(
            """[Cache]
device_id = test_device_id
device_id_timestamp = 123456789
"""
        )

    load_config(config)
    assert config.cached_device_id == "test_device_id"
    assert config.cached_device_id_timestamp == 123456789


def test_load_config_with_logic_section(temp_config_dir, config):
    config_path = temp_config_dir / "config.ini"

    # Create config with Logic section
    with config_path.open("w") as f:
        f.write(
            """[Options]
download_mode = Normal
metadata_handling = Advanced
interactive = True
download_directory = Local_directory

[Logic]
check_key_pattern = test_pattern
main_js_pattern = test_js_pattern
"""
        )

    load_config(config)
    assert config.check_key_pattern == "test_pattern"
    assert config.main_js_pattern == "test_js_pattern"


def test_copy_old_config_values(temp_config_dir):
    old_config_path = temp_config_dir / "old_config.ini"
    new_config_path = temp_config_dir / "config.ini"

    # Create old config with some values
    with old_config_path.open("w") as f:
        f.write(
            """[MyAccount]
Authorization_Token = old_token
User_Agent = old_agent
Check_Key = old_key

[Options]
download_mode = Normal
metadata_handling = Advanced
interactive = True
download_directory = /old/path
temp_folder = /old/temp
db_sync_commits = 100
"""
        )

    # Create new config with different values
    with new_config_path.open("w") as f:
        f.write(
            """[MyAccount]
Authorization_Token = new_token
User_Agent = new_agent
Check_Key = new_key

[Options]
download_mode = Normal
metadata_handling = Advanced
interactive = True
download_directory = /new/path
temp_folder = /new/temp
db_sync_commits = 200
"""
        )

    # Change to temp_dir so copy_old_config_values can find the files
    original_cwd = os.getcwd()
    os.chdir(temp_config_dir)
    try:
        from config.config import copy_old_config_values

        copy_old_config_values()

        # Read the new config and verify values were copied
        config = ConfigParser(interpolation=None)
        config.read(new_config_path)

        assert config.get("MyAccount", "Authorization_Token") == "old_token"
        assert config.get("MyAccount", "User_Agent") == "old_agent"
        assert config.get("MyAccount", "Check_Key") == "old_key"
        assert config.get("Options", "download_directory") == "/old/path"
        assert config.get("Options", "temp_folder") == "/old/temp"
        assert config.get("Options", "db_sync_commits") == "100"
    finally:
        os.chdir(original_cwd)


def test_copy_old_config_no_files(temp_config_dir):
    # Change to temp_dir where no config files exist
    original_cwd = os.getcwd()
    os.chdir(temp_config_dir)
    try:
        from config.config import copy_old_config_values

        copy_old_config_values()  # Should do nothing and not raise
    finally:
        os.chdir(original_cwd)


def test_token_scrambling(config):
    # Test unscrambling a scrambled token
    scrambled = "abcdefghijklmnopqrstuvwxyzfNs"  # 26 chars + "fNs"
    config.token = scrambled
    unscrambled = config.get_unscrambled_token()
    assert len(unscrambled) == 26  # Original length without "fNs"
    assert unscrambled != scrambled
    assert scrambled.endswith("fNs")

    # Test unscrambling an unscrambled token
    normal_token = "normal_token_without_scrambling"
    config.token = normal_token
    assert config.get_unscrambled_token() == normal_token

    # Test None token
    config.token = None
    assert config.get_unscrambled_token() is None


def test_config_section_handling(temp_config_dir, config):
    config_path = temp_config_dir / "config.ini"

    # Create config with all sections
    with config_path.open("w") as f:
        f.write(
            """[TargetedCreator]
Username = testuser

[MyAccount]
Authorization_Token = test_token
User_Agent = test_agent
Check_Key = test_key

[Options]
download_mode = Normal
metadata_handling = Advanced
interactive = True
download_directory = Local_directory

[Cache]
device_id = test_device
device_id_timestamp = 123456789

[Logic]
check_key_pattern = test_pattern
main_js_pattern = test_js_pattern

[Other]
version = 1.0.0
"""
        )

    load_config(config)

    # Verify Other section is removed
    assert not config._parser.has_section("Other")
    assert not config._parser.has_option("Other", "version")

    # Verify required sections exist
    assert config._parser.has_section("TargetedCreator")
    assert config._parser.has_section("MyAccount")
    assert config._parser.has_section("Options")
    assert config._parser.has_section("Cache")
    assert config._parser.has_section("Logic")


def test_config_path_edge_cases(temp_config_dir, config):
    config_path = temp_config_dir / "config.ini"

    # Test paths with spaces and special chars
    test_paths = {
        "space path": "/path with spaces/file",
        "unicode path": "/path/with/unicode/🐍/file",
        "quotes path": '/path/with/"quotes"/file',
        "mixed slashes": r"C:\Windows/style/mixed\slashes",
    }

    for name, path in test_paths.items():
        with config_path.open("w") as f:
            f.write(
                f"""[Options]
download_mode = Normal
metadata_handling = Advanced
interactive = True
download_directory = {path}
"""
            )

        load_config(config)
        assert config.download_directory == Path(path)


def test_config_error_cases(temp_config_dir):
    config_path = temp_config_dir / "config.ini"

    # Test invalid section reference
    config = FanslyConfig(program_version="0.10.0")
    with config_path.open("w") as f:
        f.write(
            """[Options]
download_mode = Normal
metadata_handling = Advanced
interactive = True
download_directory = Local_directory

[NonexistentSection]
key = value
"""
        )

    load_config(config)  # Should ignore nonexistent section

    # Test empty values with fresh config
    config = FanslyConfig(program_version="0.10.0")
    with config_path.open("w") as f:
        f.write(
            """[Options]
download_mode = Normal
metadata_handling = Advanced
interactive = True
download_directory = Local_directory
temp_folder =
metadata_db_file =
"""
        )

    load_config(config)
    assert config.temp_folder is None
    assert config.metadata_db_file is not None  # Should use default
