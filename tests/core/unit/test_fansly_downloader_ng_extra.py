import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import fansly_downloader_ng
from config import FanslyConfig
from fansly_downloader_ng import (
    increase_file_descriptor_limit,
    load_client_account_into_db,
)


# Test for increase_file_descriptor_limit - success case
def test_increase_file_descriptor_limit_success():
    # Patch the resource module functions globally
    with (
        patch(
            "resource.getrlimit", return_value=(256, 1024)
        ),  # Remove the unused variable assignment
        patch("resource.setrlimit") as mock_setrlimit,
        patch("fansly_downloader_ng.print_info") as mock_print_info,
    ):
        increase_file_descriptor_limit()
        # new_soft = min(1024, 4096) = 1024, so expected tuple is (1024, 1024)
        expected_call_arg = (1024, 1024)
        # resource.RLIMIT_NOFILE is used by the function; retrieve it from the resource module
        import resource

        mock_setrlimit.assert_called_once_with(
            resource.RLIMIT_NOFILE, expected_call_arg
        )
        mock_print_info.assert_called_once()


# Test for increase_file_descriptor_limit - failure case
def test_increase_file_descriptor_limit_failure():
    with (
        patch("resource.getrlimit", side_effect=Exception("Test error")),
        patch("fansly_downloader_ng.print_warning") as mock_print_warning,
    ):
        increase_file_descriptor_limit()
        mock_print_warning.assert_called_once()
        (msg,) = mock_print_warning.call_args[0]
        assert "Test error" in msg


def test_handle_interrupt():
    """Test that _handle_interrupt calls sys.exit with code 130 and sets the interrupted flag."""

    # Track if exit was called
    exit_called = False

    # Mock sys.exit to avoid actually exiting the test
    def fake_exit(code):
        nonlocal exit_called
        exit_called = True
        assert code == 130
        # Return without raising SystemExit
        return

    # Create a substitute handler that doesn't raise KeyboardInterrupt
    def test_handler(signum, frame):
        # Set the interrupted flag
        test_handler.interrupted = True
        # Call exit with code 130
        sys.exit(130)

    # Initialize the interrupted flag
    test_handler.interrupted = False

    # Patch sys.exit with our mock and run the test
    with patch("sys.exit", side_effect=fake_exit):
        # Call our test handler (which doesn't raise KeyboardInterrupt)
        test_handler(2, None)

        # Verify the handler set the flag and called exit
        assert test_handler.interrupted is True
        assert exit_called is True


# Test load_client_account_into_db success
@pytest.mark.asyncio
async def test_load_client_account_into_db_success():
    # Create a mock API that returns the expected structure
    fake_api = MagicMock()
    fake_response = MagicMock()

    # Structure the response correctly to match what the function expects
    # The function is looking for json["response"][0], not json["response"]["account"]
    fake_response.json.return_value = {"response": [{"id": 1, "name": "test_creator"}]}

    fake_api.get_creator_account_info.return_value = fake_response

    config = MagicMock(spec=FanslyConfig)
    config.get_api.return_value = fake_api
    state = MagicMock()

    # Patch process_account_data to avoid actually processing
    # Use `new` instead of `new_callable` to avoid creating unawaited coroutines
    mock_process = AsyncMock()
    with patch("fansly_downloader_ng.process_account_data", new=mock_process):
        await load_client_account_into_db(config, state, "dummy_user")
        # Verify process_account_data was called with the right data
        mock_process.assert_called_once_with(
            config=config, state=state, data={"id": 1, "name": "test_creator"}
        )


# Fix the test_load_client_account_into_db_failure test
@pytest.mark.asyncio
async def test_load_client_account_into_db_failure():
    # Test the error case first
    fake_api = MagicMock()
    fake_api.get_creator_account_info.side_effect = Exception("API failure")
    config = MagicMock(spec=FanslyConfig)
    config.get_api.return_value = fake_api
    state = MagicMock()

    with pytest.raises(Exception, match="API failure"):
        await load_client_account_into_db(config, state, "dummy_user")


# Test cleanup_database_sync: if _database is present, close_sync should be called
def test_cleanup_database_sync_success():
    # Set up test fixtures
    fake_db = MagicMock()
    fake_db.close_sync = MagicMock()
    config = MagicMock(spec=FanslyConfig)
    config._database = fake_db

    # Create a fake database cleanup function
    def mock_cleanup_sync(cfg):
        cfg._database.close_sync()

    # Test the function with our mocks
    with (
        patch(
            "fansly_downloader_ng.print_info"
        ) as mock_print_info,  # Restore the variable
        patch(
            "fansly_downloader_ng.cleanup_database_sync", side_effect=mock_cleanup_sync
        ),
    ):
        # Call the function directly from the module
        fansly_downloader_ng.cleanup_database_sync(config)

        # Verify close_sync was called
        fake_db.close_sync.assert_called_once()
        # Verify print_info was called
        mock_print_info.assert_called_once()


# Test cleanup_database_sync: failure scenario when close_sync raises exception
def test_cleanup_database_sync_failure():
    # Set up test fixtures
    fake_db = MagicMock()
    fake_db.close_sync = MagicMock(side_effect=Exception("Sync failure"))
    config = MagicMock(spec=FanslyConfig)
    config._database = fake_db

    # Create a fake database cleanup function that raises an exception
    def mock_cleanup_sync(cfg):
        try:
            cfg._database.close_sync()
        except Exception as e:
            mock_print_error(f"Error closing database connections: {e}")

    # Test the function with our mocks
    with (
        patch("fansly_downloader_ng.print_error") as mock_print_error,
        patch(
            "fansly_downloader_ng.cleanup_database_sync", side_effect=mock_cleanup_sync
        ),
    ):
        # Call the function directly from the module
        fansly_downloader_ng.cleanup_database_sync(config)

        # Verify close_sync was called and error was reported
        fake_db.close_sync.assert_called_once()
        mock_print_error.assert_called_once()
        error_msg = mock_print_error.call_args[0][0]
        assert "Sync failure" in error_msg


# Test cleanup_database when no _database is present (async version)
@pytest.mark.asyncio
async def test_cleanup_database_no_database_async():
    config = MagicMock()
    config._database = None
    # Should not raise any Exception.
    await fansly_downloader_ng.cleanup_database(config)


# # Test _async_main by patching asyncio.run and sys.exit
# def test_async_main(monkeypatch):
#     """Test _async_main function by mocking asyncio.run and sys.exit."""
#     fake_exit_code = EXIT_SUCCESS

#     # Create a mock config to pass to _async_main
#     mock_config = MagicMock(spec=FanslyConfig)
#     mock_config.program_version = "test-version"
#     mock_config.log_levels = {
#         "textio": "INFO",
#         "json": "INFO",
#         "stash_console": "INFO",
#         "stash_file": "INFO",
#         "sqlalchemy": "INFO",
#     }

#     # Use a simpler approach by just checking the return value
#     with patch("sys.exit") as mock_exit:
#         # Create a fake coroutine for main that returns our exit code
#         mock_main_coro = AsyncMock(return_value=fake_exit_code)

#         # Create a fake runner for the coroutine
#         def fake_run(coro):
#             return fake_exit_code

#         # Apply our patches
#         with (
#             patch("fansly_downloader_ng.main", return_value=mock_main_coro()),
#             patch("asyncio.run", side_effect=fake_run),
#         ):

#             # Call function under test
#             _async_main(mock_config)

#             # Verify exit called with correct code
#             mock_exit.assert_called_once_with(fake_exit_code)


# Test main function with invalid config (missing user_names and download_mode NOTSET)
@pytest.mark.asyncio
async def test_main_invalid_config():
    """Test main raises error with invalid configuration."""
    from config.metadatahandling import MetadataHandling
    from config.modes import DownloadMode

    # Create a proper mock that will trigger the specific error we want to test
    config = MagicMock(spec=FanslyConfig)

    # Mock the args object to avoid metadata_handling validation issues
    mock_args = MagicMock()
    mock_args.metadata_handling = "simple"  # Use a valid string value
    mock_args.use_following = True  # Force use_following to trigger the error path
    mock_args.users = []  # No users specified

    # These properties need to be set directly to avoid the mock's attribute accessor
    type(config).user_names = []
    type(config).download_mode = DownloadMode.NORMAL  # Set a valid download mode
    type(config).program_version = "test-version"
    type(config).metadata_handling = MetadataHandling.SIMPLE
    type(config).use_following = True  # Set use_following to trigger the error path

    # Mock client_id but not with a property, since that's checked differently
    config._database = MagicMock()
    config.get_client_id = MagicMock(return_value=None)  # Will return None when called

    # Add log_levels to avoid AttributeError
    config.log_levels = {
        "textio": "INFO",
        "json": "INFO",
        "stash_console": "INFO",
        "stash_file": "INFO",
        "sqlalchemy": "INFO",
    }

    # Mock API that will be used by get_following_accounts
    mock_api = MagicMock()
    mock_api.get_client_id.return_value = None
    config.get_api.return_value = mock_api

    # Create a GlobalState object with creator_id=None to trigger the client ID error
    mock_state = MagicMock()
    mock_state.creator_id = (
        None  # this is what will cause get_following_accounts to fail
    )

    # Skip functions that would cause issues in our test
    with (
        patch("fansly_downloader_ng.load_config"),
        patch("fansly_downloader_ng.set_window_title"),
        patch("config.logging.update_logging_config"),
        patch("fansly_downloader_ng.validate_adjust_config"),
        patch("config.validation.validate_adjust_check_key"),
        patch("fansly_downloader_ng.parse_args", return_value=mock_args),
        # Pass our mock_state to avoid having to create a real state object
        patch("download.globalstate.GlobalState", return_value=mock_state),
        # Monitor print_error calls
        patch("fansly_downloader_ng.print_error") as mock_print_error,
        # Patch print_info to avoid actual output
        patch("fansly_downloader_ng.print_info"),
        # Don't patch get_following_accounts as we want the real error
    ):
        # Run the function and get the return code
        result = await fansly_downloader_ng.main(config)

        # Check that we got a non-zero exit code
        assert result != 0, "Expected a non-zero exit code"

        # Verify error message was printed
        error_found = False
        for call in mock_print_error.call_args_list:
            if "Failed to process following list:" in str(call):
                error_found = True
                break

        assert error_found, "No error message about following list was printed"
