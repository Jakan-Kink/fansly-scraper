import configparser
import os
from configparser import ConfigParser
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pytest
from loguru import logger

from config.config import (
    _handle_config_error,
    copy_old_config_values,
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
    return FanslyConfig(program_version="0.11.0")


@pytest.fixture
def temp_config_dir():
    with TemporaryDirectory() as temp_dir:
        original_cwd = Path.cwd()
        os.chdir(temp_dir)
        # Create logs directory in both places
        logs_dir = Path(temp_dir) / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        cwd_logs = Path.cwd() / "logs"
        cwd_logs.mkdir(parents=True, exist_ok=True)
        yield Path(temp_dir)
        # Clean up
        logger.remove()  # Close all handlers
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


def test_load_config_download_directory_handling(temp_config_dir):
    # Create fresh config to avoid pollution
    config = FanslyConfig(program_version="0.11.0")
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


def test_load_config_default_download_directory(temp_config_dir):
    # Create fresh config to avoid pollution
    config = FanslyConfig(program_version="0.11.0")
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
    original_cwd = Path.cwd()
    os.chdir(temp_config_dir)
    try:
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
    original_cwd = Path.cwd()
    os.chdir(temp_config_dir)
    try:
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

    for path in test_paths.values():
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
    config = FanslyConfig(program_version="0.11.0")
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
    config = FanslyConfig(program_version="0.11.0")
    with config_path.open("w") as f:
        f.write(
            """[Options]
download_mode = Normal
metadata_handling = Advanced
interactive = True
download_directory = Local_directory
temp_folder =
"""
        )

    load_config(config)
    assert config.temp_folder is None


# -- copy_old_config_values: section/option mismatch branches --


def test_copy_old_config_skips_section_not_in_new(temp_config_dir):
    """Old config has a section the new config doesn't → skip it (line 111)."""
    old_path = temp_config_dir / "old_config.ini"
    new_path = temp_config_dir / "config.ini"

    with old_path.open("w") as f:
        f.write(
            """[MyAccount]
Authorization_Token = old_tok

[ExtraSection]
some_key = some_value
"""
        )
    with new_path.open("w") as f:
        f.write(
            """[MyAccount]
Authorization_Token = new_tok
"""
        )

    copy_old_config_values()

    result = ConfigParser(interpolation=None)
    result.read(new_path)
    assert result.get("MyAccount", "Authorization_Token") == "old_tok"
    assert not result.has_section("ExtraSection")


def test_copy_old_config_skips_option_not_in_new(temp_config_dir):
    """Old config has an option the new config doesn't → skip it (line 115)."""
    old_path = temp_config_dir / "old_config.ini"
    new_path = temp_config_dir / "config.ini"

    with old_path.open("w") as f:
        f.write(
            """[MyAccount]
Authorization_Token = old_tok
Extra_Option = extra_value
"""
        )
    with new_path.open("w") as f:
        f.write(
            """[MyAccount]
Authorization_Token = new_tok
"""
        )

    copy_old_config_values()

    result = ConfigParser(interpolation=None)
    result.read(new_path)
    assert result.get("MyAccount", "Authorization_Token") == "old_tok"
    assert not result.has_option("MyAccount", "Extra_Option")


def test_copy_old_config_skips_version(temp_config_dir):
    """version key in [Other] section is never overwritten (line 121)."""
    old_path = temp_config_dir / "old_config.ini"
    new_path = temp_config_dir / "config.ini"

    with old_path.open("w") as f:
        f.write(
            """[Other]
version = 0.9.0
"""
        )
    with new_path.open("w") as f:
        f.write(
            """[Other]
version = 1.0.0
"""
        )

    copy_old_config_values()

    result = ConfigParser(interpolation=None)
    result.read(new_path)
    assert result.get("Other", "version") == "1.0.0"


# -- SSL path handling in _handle_postgresql_options --


def test_load_config_with_ssl_paths(temp_config_dir, config):
    """SSL cert/key/rootcert paths are parsed when present (lines 326, 330, 334)."""
    config_path = temp_config_dir / "config.ini"

    with config_path.open("w") as f:
        f.write(
            """[Options]
download_mode = Normal
metadata_handling = Advanced
pg_sslmode = verify-full
pg_sslcert = /path/to/client-cert.pem
pg_sslkey = /path/to/client-key.pem
pg_sslrootcert = /path/to/ca.pem
"""
        )

    load_config(config)
    assert config.pg_sslcert == Path("/path/to/client-cert.pem")
    assert config.pg_sslkey == Path("/path/to/client-key.pem")
    assert config.pg_sslrootcert == Path("/path/to/ca.pem")
    assert config.pg_sslmode == "verify-full"


# -- StashContext section handling --


def test_load_config_with_stash_section(temp_config_dir, config):
    """StashContext section is parsed into stash_context_conn dict (line 400)."""
    config_path = temp_config_dir / "config.ini"

    with config_path.open("w") as f:
        f.write(
            """[StashContext]
scheme = https
host = stash.local
port = 9998
apikey = my-api-key
"""
        )

    load_config(config)
    assert config.stash_context_conn is not None
    assert config.stash_context_conn["scheme"] == "https"
    assert config.stash_context_conn["host"] == "stash.local"
    assert config.stash_context_conn["port"] == 9998
    assert config.stash_context_conn["apikey"] == "my-api-key"


# -- Invalid log level warning in _handle_logging_section --


def test_load_config_with_invalid_log_level(temp_config_dir, config):
    """Invalid log level triggers warning and falls back to INFO (lines 434-440)."""
    config_path = temp_config_dir / "config.ini"

    with config_path.open("w") as f:
        f.write(
            """[Logging]
sqlalchemy = GARBAGE
textio = INFO
"""
        )

    load_config(config)
    assert config.log_levels["sqlalchemy"] == "INFO"
    assert config.log_levels["textio"] == "INFO"


# -- _handle_config_error branches --


class TestHandleConfigError:
    """Cover all branches in _handle_config_error (lines 448-475)."""

    def test_no_option_error(self, config):
        e = configparser.NoOptionError("missing_opt", "SomeSection")
        with pytest.raises(ConfigError, match=r"config\.ini file is invalid"):
            _handle_config_error(e, config)

    def test_value_error_boolean(self, config):
        """ValueError with 'a boolean' substring → boolean-specific message (line 453)."""
        config.interactive = False
        e = ValueError("Not a boolean: notbool")
        with pytest.raises(ConfigError, match="malformed in the configuration file"):
            _handle_config_error(e, config)

    def test_value_error_generic(self, config):
        """Plain ValueError without 'boolean' → generic value error (line 460)."""
        config.interactive = False
        e = ValueError("'INVALID' is not a valid DownloadMode")
        with pytest.raises(ConfigError, match=r"wrong value in the config\.ini file"):
            _handle_config_error(e, config)

    def test_key_error(self, config):
        """KeyError → missing/malformed message (line 466)."""
        config.interactive = False
        e = KeyError("some_key")
        with pytest.raises(ConfigError, match="missing or malformed"):
            _handle_config_error(e, config)

    def test_name_error(self, config):
        """NameError → missing/malformed message (line 466)."""
        config.interactive = False
        e = NameError("undefined_name")
        with pytest.raises(ConfigError, match="missing or malformed"):
            _handle_config_error(e, config)

    def test_generic_exception(self, config):
        """Unrecognized exception type → generic message (line 473)."""
        config.interactive = False
        e = RuntimeError("something went wrong")
        with pytest.raises(ConfigError, match="An error occurred"):
            _handle_config_error(e, config)

    def test_value_error_boolean_interactive_opens_url(self, config, monkeypatch):
        """Interactive + boolean ValueError opens wiki URL (line 455)."""
        config.interactive = True
        monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
        e = ValueError("Not a boolean: notbool")
        with (
            patch("config.config.open_url") as mock_open,
            pytest.raises(ConfigError, match="malformed in the configuration file"),
        ):
            _handle_config_error(e, config)
        mock_open.assert_called_once()

    def test_value_error_generic_interactive_opens_url(self, config, monkeypatch):
        """Interactive + generic ValueError opens wiki URL (line 461)."""
        config.interactive = True
        monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
        e = ValueError("'BAD' is not valid")
        with (
            patch("config.config.open_url") as mock_open,
            pytest.raises(ConfigError, match=r"wrong value in the config\.ini file"),
        ):
            _handle_config_error(e, config)
        mock_open.assert_called_once()

    def test_key_error_interactive_opens_url(self, config, monkeypatch):
        """Interactive + KeyError opens wiki URL (line 468)."""
        config.interactive = True
        monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
        e = KeyError("missing_key")
        with (
            patch("config.config.open_url") as mock_open,
            pytest.raises(ConfigError, match="missing or malformed"),
        ):
            _handle_config_error(e, config)
        mock_open.assert_called_once()


# -- Renamed option handling in load_config --


def test_load_config_renamed_options(temp_config_dir, config):
    """Old option names (utilise_duplicate_threshold, use_suffix) are migrated."""
    config_path = temp_config_dir / "config.ini"

    with config_path.open("w") as f:
        f.write(
            """[Options]
download_mode = Normal
metadata_handling = Advanced
utilise_duplicate_threshold = True
use_suffix = False
"""
        )

    load_config(config)
    assert config.use_duplicate_threshold is True
    assert config.use_folder_suffix is False
    # Old options should be removed
    assert not config._parser.has_option("Options", "utilise_duplicate_threshold")
    assert not config._parser.has_option("Options", "use_suffix")


# -- Rate limiting config options --


def test_load_config_rate_limiting_options(temp_config_dir, config):
    """Rate limiting settings are parsed from config.ini."""
    config_path = temp_config_dir / "config.ini"

    with config_path.open("w") as f:
        f.write(
            """[Options]
download_mode = Normal
metadata_handling = Advanced
rate_limiting_enabled = False
rate_limiting_adaptive = False
rate_limiting_requests_per_minute = 30
rate_limiting_burst_size = 5
rate_limiting_retry_after_seconds = 15
rate_limiting_max_backoff_seconds = 120
rate_limiting_backoff_factor = 2.0
"""
        )

    load_config(config)
    assert config.rate_limiting_enabled is False
    assert config.rate_limiting_adaptive is False
    assert config.rate_limiting_requests_per_minute == 30
    assert config.rate_limiting_burst_size == 5
    assert config.rate_limiting_retry_after_seconds == 15
    assert config.rate_limiting_max_backoff_seconds == 120
    assert config.rate_limiting_backoff_factor == 2.0


# -- Outdated check key replacement --


def test_load_config_replaces_outdated_check_keys(temp_config_dir, config):
    """Known outdated check keys are replaced with the current default."""
    config_path = temp_config_dir / "config.ini"
    outdated_key = "negwij-zyZnek-wavje1"

    with config_path.open("w") as f:
        f.write(
            f"""[MyAccount]
Authorization_Token = test_token
User_Agent = test_agent
Check_Key = {outdated_key}
"""
        )

    load_config(config)
    assert config.check_key != outdated_key
    assert config.check_key == "oybZy8-fySzis-bubayf"


# -- Deprecated option cleanup --


def test_load_config_removes_deprecated_include_meta(temp_config_dir, config):
    """include_meta_database option is removed from config (line 380)."""
    config_path = temp_config_dir / "config.ini"

    with config_path.open("w") as f:
        f.write(
            """[Options]
download_mode = Normal
metadata_handling = Advanced
include_meta_database = True
"""
        )

    load_config(config)
    assert not config._parser.has_option("Options", "include_meta_database")
