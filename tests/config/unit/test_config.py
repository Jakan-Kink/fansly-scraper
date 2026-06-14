from configparser import NoOptionError
from pathlib import Path

import pytest

from config.config import (
    _handle_config_error,
    load_config,
    parse_items_from_line,
    sanitize_creator_names,
    save_config_or_raise,
    username_has_valid_chars,
    username_has_valid_length,
)
from config.fanslyconfig import FanslyConfig
from config.schema import ConfigSchema
from errors import ConfigError


def test_user_names_str_none(fresh_config):
    assert fresh_config.user_names is None
    assert fresh_config.user_names_str() == "ReplaceMe"


def test_user_names_str_empty_set(fresh_config):
    fresh_config.user_names = set()
    assert fresh_config.user_names_str() == ""


def test_user_names_str_with_names(fresh_config):
    fresh_config.user_names = {"alice", "bob", "charlie"}
    assert fresh_config.user_names_str() == "alice, bob, charlie"


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


def test_load_config_creates_file_if_not_exists(config_dir, fresh_config):
    """When no config file exists, load_config creates config.yaml with defaults."""
    yaml_path = config_dir / "config.yaml"
    ini_path = config_dir / "config.ini"
    assert not yaml_path.exists()
    assert not ini_path.exists()
    load_config(fresh_config)
    # New system creates config.yaml, not config.ini
    assert yaml_path.exists()


def test_load_config_temp_folder_handling(config_dir, fresh_config):
    config_path = config_dir / "config.ini"

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

    load_config(fresh_config)
    assert fresh_config.temp_folder == Path("/custom/temp/path")


def test_load_config_download_directory_handling(config_dir):
    # Create fresh config to avoid pollution
    config = FanslyConfig(program_version="0.13.0")
    config_path = config_dir / "config.ini"

    # Create config with download_directory
    with config_path.open("w") as f:
        f.write(
            """[Options]
download_directory = /custom/download/path
"""
        )

    load_config(config)
    assert config.download_directory == Path("/custom/download/path")


def test_load_config_default_download_directory(config_dir):
    # Create fresh config to avoid pollution
    config = FanslyConfig(program_version="0.13.0")
    config_path = config_dir / "config.ini"

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


def test_save_config_or_raise(config_dir, fresh_config):
    config_path = config_dir / "config.ini"
    fresh_config.config_path = config_path
    fresh_config.user_names = {"testuser"}
    fresh_config.token = "test_token"
    fresh_config.user_agent = "test_agent"
    fresh_config.check_key = "test_key"

    # Should save successfully
    assert save_config_or_raise(fresh_config) is True

    # Verify file exists and contains expected values
    assert config_path.exists()
    with config_path.open() as f:
        content = f.read()
        assert "testuser" in content
        assert "test_token" in content
        assert "test_agent" in content
        assert "test_key" in content


def test_save_config_or_raise_no_path(fresh_config):
    fresh_config.config_path = None
    with pytest.raises(ConfigError):
        save_config_or_raise(fresh_config)


def test_load_config_invalid_config(config_dir, fresh_config):
    config_path = config_dir / "config.ini"

    # Create invalid config with invalid value
    with config_path.open("w") as f:
        f.write(
            """[Options]
download_mode = InvalidMode
"""
        )

    with pytest.raises(ConfigError) as exc_info:
        load_config(fresh_config)
    err_msg = str(exc_info.value)
    # New error format (ValidationError via load_yaml) OR the legacy
    # configparser path — either way, the failing field + value must
    # appear in the surfaced error.
    assert "download_mode" in err_msg
    assert "InvalidMode" in err_msg.lower() or "invalidmode" in err_msg.lower()


def test_token_validation(fresh_config):
    # Test valid token
    fresh_config.token = "a" * 51  # Token longer than 50 chars
    assert fresh_config.token_is_valid() is True

    # Test invalid tokens
    fresh_config.token = None
    assert fresh_config.token_is_valid() is False

    fresh_config.token = "a" * 49  # Too short
    assert fresh_config.token_is_valid() is False

    fresh_config.token = "ReplaceMe" + "a" * 50
    assert fresh_config.token_is_valid() is False


def test_useragent_validation(fresh_config):
    # Test valid user agent
    fresh_config.user_agent = "a" * 41  # User agent longer than 40 chars
    assert fresh_config.useragent_is_valid() is True

    # Test invalid user agents
    fresh_config.user_agent = None
    assert fresh_config.useragent_is_valid() is False

    fresh_config.user_agent = "a" * 39  # Too short
    assert fresh_config.useragent_is_valid() is False

    fresh_config.user_agent = "ReplaceMe" + "a" * 40
    assert fresh_config.useragent_is_valid() is False


def test_load_config_with_cache_section(config_dir, fresh_config):
    config_path = config_dir / "config.ini"

    # Create config with Cache section
    with config_path.open("w") as f:
        f.write(
            """[Cache]
device_id = test_device_id
device_id_timestamp = 123456789
"""
        )

    load_config(fresh_config)
    assert fresh_config.cached_device_id == "test_device_id"
    assert fresh_config.cached_device_id_timestamp == 123456789


def test_token_scrambling(fresh_config):
    # Test unscrambling a scrambled token
    scrambled = "abcdefghijklmnopqrstuvwxyzfNs"  # 26 chars + "fNs"
    fresh_config.token = scrambled
    unscrambled = fresh_config.get_unscrambled_token()
    assert len(unscrambled) == 26  # Original length without "fNs"
    assert unscrambled != scrambled
    assert scrambled.endswith("fNs")

    # Test unscrambling an unscrambled token
    normal_token = "normal_token_without_scrambling"
    fresh_config.token = normal_token
    assert fresh_config.get_unscrambled_token() == normal_token

    # Test None token
    fresh_config.token = None
    assert fresh_config.get_unscrambled_token() is None


def test_config_section_handling(config_dir, fresh_config):
    """Migration from legacy ini populates schema sections and [Other] is dropped."""
    config_path = config_dir / "config.ini"

    # Create config with all sections (including legacy [Other] with version)
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

    load_config(fresh_config)

    # [Other] is not carried into the YAML schema — it is silently dropped
    assert fresh_config._schema is not None
    schema_dict = fresh_config._schema.model_dump()
    assert "other" not in schema_dict

    # Verify key section values were migrated correctly
    assert fresh_config._schema.targeted_creator.usernames == ["testuser"]
    assert fresh_config._schema.my_account.user_agent == "test_agent"
    assert fresh_config._schema.cache.device_id == "test_device"
    assert fresh_config._schema.cache.device_id_timestamp == 123456789
    assert fresh_config._schema.logic.check_key_pattern == "test_pattern"


def test_config_path_edge_cases(config_dir):
    """Paths with spaces and special characters survive a YAML round-trip."""
    config_yaml_path = config_dir / "config.yaml"

    # Test paths with spaces and special chars
    test_paths = {
        "space path": "/path with spaces/file",
        "unicode path": "/path/with/unicode/file",
        "mixed slashes": r"C:\Windows/style/mixed\slashes",
    }

    for path in test_paths.values():
        # Build a schema with the custom download_directory and write config.yaml
        schema = ConfigSchema()
        schema.options.download_directory = path
        schema.dump_yaml(config_yaml_path)

        # Load fresh config from the yaml
        reloaded_config = FanslyConfig(program_version="0.13.0")
        load_config(reloaded_config)
        assert reloaded_config.download_directory == Path(path)


def test_config_error_cases(config_dir):
    config_path = config_dir / "config.ini"

    # Test invalid section reference
    config = FanslyConfig(program_version="0.13.0")
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
    config = FanslyConfig(program_version="0.13.0")
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


# -- SSL path handling in _handle_postgresql_options --


def test_load_config_with_ssl_paths(config_dir, fresh_config):
    """SSL cert/key/rootcert paths are parsed when present (lines 326, 330, 334)."""
    config_path = config_dir / "config.ini"

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

    load_config(fresh_config)
    assert fresh_config.pg_sslcert == Path("/path/to/client-cert.pem")
    assert fresh_config.pg_sslkey == Path("/path/to/client-key.pem")
    assert fresh_config.pg_sslrootcert == Path("/path/to/ca.pem")
    assert fresh_config.pg_sslmode == "verify-full"


# -- StashContext section handling --


def test_load_config_with_stash_section(config_dir, fresh_config):
    """StashContext section is parsed into stash_context_conn dict (line 400)."""
    config_path = config_dir / "config.ini"

    with config_path.open("w") as f:
        f.write(
            """[StashContext]
scheme = https
host = stash.local
port = 9998
apikey = my-api-key
"""
        )

    load_config(fresh_config)
    assert fresh_config.stash_context_conn is not None
    assert fresh_config.stash_context_conn["scheme"] == "https"
    assert fresh_config.stash_context_conn["host"] == "stash.local"
    assert fresh_config.stash_context_conn["port"] == 9998
    assert fresh_config.stash_context_conn["apikey"] == "my-api-key"


# -- Invalid log level warning in _handle_logging_section --


def test_load_config_with_invalid_log_level(config_dir, fresh_config):
    """Invalid log level triggers warning and falls back to INFO (lines 434-440)."""
    config_path = config_dir / "config.ini"

    with config_path.open("w") as f:
        f.write(
            """[Logging]
sqlalchemy = GARBAGE
textio = INFO
"""
        )

    load_config(fresh_config)
    assert fresh_config.log_levels["sqlalchemy"] == "INFO"
    assert fresh_config.log_levels["textio"] == "INFO"


# -- Renamed option handling in load_config --


def test_load_config_renamed_options(config_dir, fresh_config):
    """Old option names (utilise_duplicate_threshold, use_suffix) are migrated."""
    config_path = config_dir / "config.ini"

    with config_path.open("w") as f:
        f.write(
            """[Options]
download_mode = Normal
metadata_handling = Advanced
utilise_duplicate_threshold = True
use_suffix = False
"""
        )

    load_config(fresh_config)
    # Legacy INI keys map onto their current schema fields.
    assert fresh_config.use_duplicate_threshold is True
    assert fresh_config.use_folder_suffix is False


# -- Rate limiting config options --


def test_load_config_rate_limiting_options(config_dir, fresh_config):
    """Rate limiting settings are parsed from config.ini."""
    config_path = config_dir / "config.ini"

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

    load_config(fresh_config)
    assert fresh_config.rate_limiting_enabled is False
    assert fresh_config.rate_limiting_adaptive is False
    assert fresh_config.rate_limiting_requests_per_minute == 30
    assert fresh_config.rate_limiting_burst_size == 5
    assert fresh_config.rate_limiting_retry_after_seconds == 15
    assert fresh_config.rate_limiting_max_backoff_seconds == 120
    assert fresh_config.rate_limiting_backoff_factor == 2.0


# -- Outdated check key replacement --


def test_load_config_replaces_outdated_check_keys(config_dir, fresh_config):
    """Known outdated check keys are replaced with the current default."""
    config_path = config_dir / "config.ini"
    outdated_key = "negwij-zyZnek-wavje1"

    with config_path.open("w") as f:
        f.write(
            f"""[MyAccount]
Authorization_Token = test_token
User_Agent = test_agent
Check_Key = {outdated_key}
"""
        )

    load_config(fresh_config)
    assert fresh_config.check_key != outdated_key
    assert fresh_config.check_key == "oybZy8-fySzis-bubayf"


# Retired-field silent-drop coverage lives in tests/config/unit/test_schema.py
# (test_retired_field_*_silently_dropped) — the ConfigParser-based "remove
# from _parser" check disappeared with the Pydantic migration.


# ---------------------------------------------------------------------------
# _handle_config_error — config/config.py:297-324
# ---------------------------------------------------------------------------


class TestHandleConfigError:
    """Lines 297-324: error → ConfigError translation matrix.

    Each branch transforms a specific exception type into a ConfigError
    with a tailored message. Tests pass each input shape and verify the
    resulting ConfigError text contains the expected discriminating phrase.
    """

    def test_no_option_error_yields_config_yaml_invalid(self):
        """Lines 308-311: configparser.NoOptionError → 'config.yaml is invalid'."""
        exc = NoOptionError("missing_key", "Options")
        with pytest.raises(ConfigError) as info:
            _handle_config_error(exc)
        assert "config.yaml is invalid" in str(info.value)

    def test_value_error_with_pydantic_problem_count_passes_through(self):
        """Lines 312-316: ValueError with 'N problem(s) in ' prefix → 'Configuration file needs editing'."""
        # Pydantic's _format_validation_error emits "3 problem(s) in download settings: ..."
        exc = ValueError("3 problem(s) in download settings: bad value")
        with pytest.raises(ConfigError) as info:
            _handle_config_error(exc)
        assert "Configuration file needs editing" in str(info.value)
        assert "3 problem(s)" in str(info.value)

    def test_value_error_with_boolean_marker_extracts_field_name(self):
        """Lines 317-320: ValueError containing 'a boolean' → '<field> is malformed... must be true or false'."""
        exc = ValueError("could not parse field as a boolean: download_media_previews")
        with pytest.raises(ConfigError) as info:
            _handle_config_error(exc)
        msg = str(info.value)
        assert "download_media_previews" in msg
        assert "must be true or false" in msg

    def test_generic_value_error_yields_invalid_value(self):
        """Line 321: generic ValueError → 'Invalid value in config.yaml'."""
        exc = ValueError("some random validation issue")
        with pytest.raises(ConfigError) as info:
            _handle_config_error(exc)
        assert "Invalid value in config.yaml" in str(info.value)

    def test_key_error_yields_missing_or_malformed(self):
        """Lines 322-323: KeyError → "'<key>' is missing or malformed"."""
        exc = KeyError("download_directory")
        with pytest.raises(ConfigError) as info:
            _handle_config_error(exc)
        msg = str(info.value)
        assert "missing or malformed" in msg
        assert "download_directory" in msg

    def test_name_error_treated_same_as_key_error(self):
        """Lines 322-323: NameError is in the same isinstance check as KeyError."""
        exc = NameError("undefined_name")
        with pytest.raises(ConfigError) as info:
            _handle_config_error(exc)
        assert "missing or malformed" in str(info.value)

    def test_generic_exception_yields_fallback(self):
        """Line 324: anything else → 'An error occurred while reading config.yaml'."""
        exc = RuntimeError("filesystem went sideways")
        with pytest.raises(ConfigError) as info:
            _handle_config_error(exc)
        assert "An error occurred while reading config.yaml" in str(info.value)
        assert "filesystem went sideways" in str(info.value)
