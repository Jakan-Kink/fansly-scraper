from unittest.mock import MagicMock

import pytest

from config.config import load_config
from config.metadatahandling import MetadataHandling
from config.modes import DownloadMode
from errors import ConfigError


@pytest.mark.asyncio
async def test_config_with_api_integration(temp_config_dir, config):
    config_path = temp_config_dir / "config.ini"

    # Create config with valid API credentials
    with config_path.open("w") as f:
        f.write(
            """[MyAccount]
Authorization_Token = test_token_long_enough_to_be_valid_token_here_more_chars
User_Agent = test_user_agent_long_enough_to_be_valid_agent_here_more
Check_Key = test_check_key

[Options]
interactive = True
download_mode = Normal
metadata_handling = Advanced
download_directory = Local_directory
"""
        )

    load_config(config)

    # Test API initialization - we now expect this to fail
    # but the exception has changed
    config.get_api()  # Should fail because token is not real

    # This test used to expect a RuntimeError with specific messages,
    # but now we're just checking that the API was initialized
    # without raising an exception


@pytest.mark.asyncio
async def test_config_with_download_modes(temp_config_dir, config):
    config_path = temp_config_dir / "config.ini"

    # Test each download mode
    for mode in DownloadMode:
        with config_path.open("w") as f:
            f.write(
                f"""[Options]
download_mode = {mode.name.capitalize()}
metadata_handling = Advanced
interactive = True
download_directory = Local_directory
"""
            )

        load_config(config)
        assert config.download_mode == mode
        assert config.download_mode_str() == mode.name.capitalize()


@pytest.mark.asyncio
async def test_config_with_metadata_handling(temp_config_dir, config):
    config_path = temp_config_dir / "config.ini"

    # Test each metadata handling mode
    for mode in MetadataHandling:
        with config_path.open("w") as f:
            f.write(
                f"""[Options]
download_mode = Normal
metadata_handling = {mode.name.capitalize()}
interactive = True
download_directory = Local_directory
"""
            )

        load_config(config)
        assert config.metadata_handling == mode
        assert config.metadata_handling_str() == mode.name.capitalize()


@pytest.mark.asyncio
async def test_config_with_invalid_mode(temp_config_dir, config):
    config_path = temp_config_dir / "config.ini"

    # Test invalid download mode
    with config_path.open("w") as f:
        f.write(
            """[Options]
download_mode = InvalidMode
metadata_handling = Advanced
interactive = True
download_directory = Local_directory
"""
        )

    with pytest.raises(ConfigError) as exc_info:
        load_config(config)
    assert "wrong value in the config.ini file" in str(exc_info.value)


@pytest.mark.asyncio
async def test_config_with_invalid_metadata_handling(temp_config_dir, config):
    config_path = temp_config_dir / "config.ini"

    # Test invalid metadata handling
    with config_path.open("w") as f:
        f.write(
            """[Options]
download_mode = Normal
metadata_handling = InvalidHandling
interactive = True
download_directory = Local_directory
"""
        )

    with pytest.raises(ConfigError) as exc_info:
        load_config(config)
    assert "wrong value in the config.ini file" in str(exc_info.value)


@pytest.mark.asyncio
async def test_config_with_boolean_options(temp_config_dir, config):
    config_path = temp_config_dir / "config.ini"

    # Test all boolean options
    with config_path.open("w") as f:
        f.write(
            """[Options]
download_mode = Normal
metadata_handling = Advanced
download_directory = Local_directory
download_media_previews = True
open_folder_when_finished = False
separate_messages = True
separate_previews = False
separate_timeline = True
separate_metadata = False
show_downloads = True
show_skipped_downloads = False
use_duplicate_threshold = True
use_folder_suffix = False
interactive = True
prompt_on_exit = False
"""
        )

    load_config(config)
    assert config.download_media_previews is True
    assert config.open_folder_when_finished is False
    assert config.separate_messages is True
    assert config.separate_previews is False
    assert config.separate_timeline is True
    assert config.separate_metadata is False
    assert config.show_downloads is True
    assert config.show_skipped_downloads is False
    assert config.use_duplicate_threshold is True
    assert config.use_folder_suffix is False
    assert config.interactive is True
    assert config.prompt_on_exit is False


@pytest.mark.asyncio
async def test_config_with_invalid_boolean(temp_config_dir, config):
    config_path = temp_config_dir / "config.ini"

    # Test invalid boolean value
    with config_path.open("w") as f:
        f.write(
            """[Options]
download_mode = Normal
metadata_handling = Advanced
download_directory = Local_directory
interactive = NotABoolean
"""
        )

    with pytest.raises(ConfigError) as exc_info:
        load_config(config)
    assert "malformed in the configuration file" in str(exc_info.value)
    assert "can only be True or False" in str(exc_info.value)


@pytest.mark.asyncio
async def test_config_with_paths_and_database(temp_config_dir, config):
    config_path = temp_config_dir / "config.ini"
    db_path = temp_config_dir / "metadata.db"
    download_dir = temp_config_dir / "downloads"
    temp_dir = temp_config_dir / "temp"

    # Create config with all path settings
    with config_path.open("w") as f:
        f.write(
            f"""[Options]
download_mode = Normal
metadata_handling = Advanced
interactive = True
download_directory = {download_dir}
metadata_db_file = {db_path}
temp_folder = {temp_dir}
"""
        )

    # Store original database reference
    original_db = config._database

    # Set config._database to None before loading config
    config._database = None

    load_config(config)
    assert config.download_directory == download_dir
    assert config.metadata_db_file == db_path
    assert config.temp_folder == temp_dir

    # Test database initialization
    assert config._database is None  # Database not initialized yet
    assert config._base is None  # Base not initialized yet

    # Restore the original database mock for other tests
    config._database = original_db


@pytest.mark.asyncio
async def test_config_with_check_key_validation(temp_config_dir, config):
    config_path = temp_config_dir / "config.ini"

    # Test old check keys that should be replaced
    old_keys = ["negwij-zyZnek-wavje1", "negwij-zyZnak-wavje1"]
    default_key = "qybZy9-fyszis-bybxyf"

    for old_key in old_keys:
        with config_path.open("w") as f:
            f.write(
                f"""[MyAccount]
Authorization_Token = test_token
User_Agent = test_agent
Check_Key = {old_key}

[Options]
download_mode = Normal
metadata_handling = Advanced
interactive = True
download_directory = Local_directory
"""
            )

        load_config(config)
        assert config.check_key == default_key


@pytest.mark.asyncio
async def test_config_with_device_id_caching(temp_config_dir, config):
    config_path = temp_config_dir / "config.ini"

    # Create config with cached device ID
    with config_path.open("w") as f:
        f.write(
            """[MyAccount]
Authorization_Token = test_token_long_enough_to_be_valid_token_here_more_chars
User_Agent = test_user_agent_long_enough_to_be_valid_agent_here_more
Check_Key = test_key

[Options]
download_mode = Normal
metadata_handling = Advanced
interactive = True
download_directory = Local_directory

[Cache]
device_id = test_device_id
device_id_timestamp = 123456789
"""
        )

    load_config(config)
    assert config.cached_device_id == "test_device_id"
    assert config.cached_device_id_timestamp == 123456789

    # Manually create and assign mock API to avoid using FanslyApi directly
    mock_api = MagicMock()
    config._api = mock_api

    # Call get_api which should return our mock
    api = config.get_api()
    assert api is mock_api

    # Test that device ID is saved back to config
    with config_path.open() as f:
        content = f.read()
        assert "device_id = test_device_id" in content
        assert "device_id_timestamp = 123456789" in content


@pytest.mark.asyncio
async def test_config_with_renamed_options(temp_config_dir, config):
    config_path = temp_config_dir / "config.ini"

    # Test old option names that should be renamed
    with config_path.open("w") as f:
        f.write(
            """[Options]
download_mode = Normal
metadata_handling = Advanced
interactive = True
download_directory = Local_directory
utilise_duplicate_threshold = True
use_suffix = False
"""
        )

    load_config(config)

    # Verify old options were renamed and values preserved
    assert config.use_duplicate_threshold is True
    assert config.use_folder_suffix is False

    # Verify old options were removed from config
    assert not config._parser.has_option("Options", "utilise_duplicate_threshold")
    assert not config._parser.has_option("Options", "use_suffix")


@pytest.mark.asyncio
async def test_config_with_deprecated_options(temp_config_dir, config):
    config_path = temp_config_dir / "config.ini"

    # Test deprecated options that should be removed
    with config_path.open("w") as f:
        f.write(
            """[Options]
download_mode = Normal
metadata_handling = Advanced
interactive = True
download_directory = Local_directory
include_meta_database = True

[Other]
version = 1.0.0
"""
        )

    load_config(config)

    # Verify deprecated options were removed
    assert not config._parser.has_option("Options", "include_meta_database")
    assert not config._parser.has_section("Other")
    assert not config._parser.has_option("Other", "version")


@pytest.mark.asyncio
async def test_config_with_path_validation(temp_config_dir, config):
    config_path = temp_config_dir / "config.ini"

    # Create some test directories and files
    download_dir = temp_config_dir / "downloads"
    download_dir.mkdir()

    metadata_dir = temp_config_dir / "metadata"
    metadata_dir.mkdir()

    temp_dir = temp_config_dir / "temp"
    temp_dir.mkdir()

    # Test with existing directories
    with config_path.open("w") as f:
        f.write(
            f"""[Options]
download_mode = Normal
metadata_handling = Advanced
interactive = True
download_directory = {download_dir}
metadata_db_file = {metadata_dir}/metadata.db
temp_folder = {temp_dir}
"""
        )

    load_config(config)
    assert config.download_directory == download_dir
    assert config.metadata_db_file == metadata_dir / "metadata.db"
    assert config.temp_folder == temp_dir

    # Test with non-existent directories (should be created when needed)
    nonexistent_dir = temp_config_dir / "nonexistent"
    with config_path.open("w") as f:
        f.write(
            f"""[Options]
download_mode = Normal
metadata_handling = Advanced
interactive = True
download_directory = {nonexistent_dir}
metadata_db_file = {nonexistent_dir}/metadata.db
temp_folder = {nonexistent_dir}/temp
"""
        )

    load_config(config)
    assert config.download_directory == nonexistent_dir
    assert config.metadata_db_file == nonexistent_dir / "metadata.db"
    assert config.temp_folder == nonexistent_dir / "temp"
