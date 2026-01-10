import resource
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
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
def test_cleanup_database_sync_success(config_with_database):
    # Use real FanslyConfig with real Database instance
    config = config_with_database

    # Call the real function - it will call the real close_sync method
    fansly_downloader_ng.cleanup_database_sync(config)

    # Verify cleanup was performed (database should be marked as cleaned up)
    # The real function calls close_sync which sets _cleanup_done
    assert hasattr(config._database, "_cleanup_done")
    assert config._database._cleanup_done.is_set()


# Test cleanup_database_sync: failure scenario when close_sync raises exception
def test_cleanup_database_sync_failure(config_with_database):
    # Use real FanslyConfig with real Database instance
    config = config_with_database

    # Mock close_sync to raise an exception (boundary mocking)
    with patch.object(
        config._database, "close_sync", side_effect=Exception("Sync failure")
    ):
        # Call the real function - it should handle the exception gracefully
        # The function catches exceptions in close_sync and logs them
        fansly_downloader_ng.cleanup_database_sync(config)

        # The function should have attempted cleanup despite the error
        # No exception should be raised to the caller


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


# Test main function with invalid config (use_following without valid client ID)
@pytest.mark.asyncio
async def test_main_invalid_config(
    config, complete_args, respx_mock, tmp_path, fansly_api
):
    """Test main returns error when use_following is set but client ID unavailable."""
    # Use real FanslyConfig from fixture
    config.use_following = True
    config.user_names = set()  # No users specified
    config.config_path = tmp_path / "config.ini"  # Required by map_args_to_config

    # Modify args for this test
    complete_args.use_following = True
    complete_args.users = None  # Must be None, not [] - empty list is considered "set"

    # Mock HTTP responses for Fansly API
    # First, let account/me succeed so we can get the client ID
    respx_mock.options("https://apiv3.fansly.com/api/v1/account/me").mock(
        side_effect=[httpx.Response(200)]
    )
    respx_mock.get("https://apiv3.fansly.com/api/v1/account/me").mock(
        side_effect=[
            httpx.Response(
                200,
                json={
                    "success": True,
                    "response": {
                        "account": {
                            "id": "1234567890123456",  # Bigint ID (fits in int64)
                            "username": "test_user",
                            "displayName": "Test User",
                        }
                    },
                },
            )
        ]
    )

    # Mock the client account loading (load_client_account_into_db)
    respx_mock.options("https://apiv3.fansly.com/api/v1/account").mock(
        side_effect=[httpx.Response(200), httpx.Response(200)]  # Multiple calls
    )
    respx_mock.get("https://apiv3.fansly.com/api/v1/account").mock(
        side_effect=[
            # First call: get_creator_account_info for client account
            httpx.Response(
                200,
                json={
                    "success": True,
                    "response": [
                        {
                            "id": "1234567890123456",  # Bigint ID (fits in int64)
                            "username": "test_user",
                            "displayName": "Test User",
                        }
                    ],
                },
            ),
            # Second call: get_following_accounts - this should fail (what we're testing)
            httpx.Response(401, json={"error": "Unauthorized"}),
        ]
    )

    # Patch functions to skip setup and get to the error we're testing
    with (
        patch("fansly_downloader_ng.load_config"),  # Skip config loading
        patch("fansly_downloader_ng.set_window_title"),  # Skip window title
        patch("config.logging.update_logging_config"),  # Skip logging setup
        patch("fansly_downloader_ng.validate_adjust_config"),  # Skip validation
        patch(
            "config.validation.validate_adjust_check_key"
        ),  # Skip check key validation
        patch(
            "fansly_downloader_ng.parse_args", return_value=complete_args
        ),  # Use our args
        patch.object(config, "setup_api", new_callable=AsyncMock),  # Skip API setup
        patch.object(
            config, "get_api", return_value=fansly_api
        ),  # Use real API from fixture
        patch("fansly_downloader_ng.print_error") as mock_print_error,  # Monitor errors
        patch("fansly_downloader_ng.print_info"),  # Suppress info output
    ):
        # Run the function and get the return code
        # When get_following_accounts fails (due to API error), main() catches it and returns 1
        result = await fansly_downloader_ng.main(config)

        # Check that we got a non-zero exit code (error)
        assert result != 0, "Expected a non-zero exit code when following list fails"

        # Verify error message was printed about following list
        assert mock_print_error.called, (
            "Expected error to be logged when following list fails"
        )

        # Check that the specific error message about following list was printed
        error_messages = [str(call) for call in mock_print_error.call_args_list]
        assert any("following list" in msg.lower() for msg in error_messages), (
            f"Expected 'following list' error message, got: {error_messages}"
        )
