"""Unit tests for the account module."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import respx
from sqlalchemy.ext.asyncio import AsyncSession

from config.modes import DownloadMode
from download.account import (
    _extract_account_data,
    _get_account_response,
    _make_rate_limited_request,
    _update_state_from_account,
    _validate_download_mode,
    get_creator_account_info,
    get_following_accounts,
)
from download.downloadstate import DownloadState
from errors import ApiAccountInfoError, ApiAuthenticationError, ApiError


# Removed create_mock_response - all tests now use respx for edge mocking


@pytest.fixture
def mock_config_with_api(mock_config, fansly_api):
    """Create a mock config with a properly configured API instance."""
    mock_config.get_api = MagicMock(return_value=fansly_api)
    mock_config._api = fansly_api
    mock_config.token = "test_token"

    # Mock database for tests that need it
    mock_db = MagicMock()
    mock_config._database = mock_db

    return mock_config


class TestValidateDownloadMode:
    """Tests for the _validate_download_mode function."""

    def test_valid_mode_with_creator(self, mock_config):
        """Test with a creator name and valid mode."""
        mock_config.download_mode = DownloadMode.TIMELINE
        state = DownloadState()
        state.creator_name = "testuser"

        # Should not raise an exception
        _validate_download_mode(mock_config, state)

    def test_invalid_mode_with_creator(self, mock_config):
        """Test with a creator name and invalid mode."""
        mock_config.download_mode = (
            DownloadMode.COLLECTION
        )  # Not in valid modes for creator
        state = DownloadState()
        state.creator_name = "testuser"

        # Should not raise an exception (mode check skipped)
        _validate_download_mode(mock_config, state)

    def test_no_mode_set(self, mock_config):
        """Test when download mode is not set."""
        mock_config.download_mode = DownloadMode.NOTSET
        state = DownloadState()

        # Should raise RuntimeError
        with pytest.raises(RuntimeError) as excinfo:
            _validate_download_mode(mock_config, state)

        assert "config download mode not set" in str(excinfo.value)

    def test_client_account_any_mode(self, mock_config):
        """Test with client account (no creator) and any mode."""
        mock_config.download_mode = DownloadMode.COLLECTION  # Any mode should be fine
        state = DownloadState()
        state.creator_name = None  # Client account

        # Should not raise an exception
        _validate_download_mode(mock_config, state)


class TestGetAccountResponse:
    """Tests for the _get_account_response function."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_get_client_account_info(self, mock_config_with_api, fansly_api):
        """Test getting client account info."""
        state = DownloadState()
        state.creator_name = None  # Client account

        # Mock CORS preflight OPTIONS request
        respx.options("https://apiv3.fansly.com/api/v1/account/me").mock(
            return_value=httpx.Response(200)
        )

        # Mock HTTP response at the edge (Fansly API endpoint)
        respx.get("https://apiv3.fansly.com/api/v1/account/me").mock(
            return_value=httpx.Response(
                200,
                json={"response": {"account": {"id": "client123"}}},
            )
        )

        # Call real code path - will hit mocked HTTP endpoint
        response = await _get_account_response(mock_config_with_api, state)

        # Verify response
        assert response.status_code == 200
        assert response.json() == {"response": {"account": {"id": "client123"}}}

    @pytest.mark.asyncio
    @respx.mock
    async def test_get_creator_account_info(self, mock_config_with_api, fansly_api):
        """Test getting creator account info."""
        state = DownloadState()
        state.creator_name = "testcreator"  # Creator account

        # Mock CORS preflight OPTIONS request
        respx.options(
            "https://apiv3.fansly.com/api/v1/account?usernames=testcreator"
        ).mock(return_value=httpx.Response(200))

        # Mock HTTP response at the edge (Fansly API endpoint) - includes ngsw-bypass param
        respx.get(
            "https://apiv3.fansly.com/api/v1/account?usernames=testcreator&ngsw-bypass=true"
        ).mock(
            return_value=httpx.Response(
                200,
                json={"response": [{"id": "creator123"}]},
            )
        )

        # Call real code path - will hit mocked HTTP endpoint
        response = await _get_account_response(mock_config_with_api, state)

        # Verify response
        assert response.status_code == 200
        assert response.json() == {"response": [{"id": "creator123"}]}

    @pytest.mark.asyncio
    @respx.mock
    async def test_get_account_error_response(self, mock_config_with_api):
        """Test handling error response."""
        state = DownloadState()
        state.creator_name = "testcreator"

        # Mock CORS preflight OPTIONS request
        respx.options(
            "https://apiv3.fansly.com/api/v1/account?usernames=testcreator"
        ).mock(return_value=httpx.Response(200))

        # Mock HTTP response at the edge with error status - includes ngsw-bypass param
        respx.get(
            "https://apiv3.fansly.com/api/v1/account?usernames=testcreator&ngsw-bypass=true"
        ).mock(return_value=httpx.Response(400, text="Bad Request"))

        # Should raise ApiError when HTTP returns 400 (wrapped HTTPStatusError)
        with pytest.raises(ApiError) as excinfo:
            await _get_account_response(mock_config_with_api, state)

        assert "Error getting account info from fansly API" in str(excinfo.value)

    @pytest.mark.asyncio
    async def test_get_account_request_exception(self, mock_config_with_api):
        """Test handling request exception."""
        state = DownloadState()
        state.creator_name = "testcreator"

        # Mock _make_rate_limited_request to raise httpx.HTTPError (not requests.RequestException)
        mock_exception = httpx.HTTPError("Connection error")
        with patch(
            "download.account._make_rate_limited_request",
            AsyncMock(side_effect=mock_exception),
        ) as mock_rate_limited:
            # Should raise ApiError
            with pytest.raises(ApiError) as excinfo:
                await _get_account_response(mock_config_with_api, state)

            assert "Error getting account info from fansly API" in str(excinfo.value)
            assert "Connection error" in str(excinfo.value)
            mock_rate_limited.assert_called_once()


class TestExtractAccountData:
    """Tests for the _extract_account_data function."""

    def test_extract_client_account_data(self, mock_config_with_api, fansly_api):
        """Test extracting client account data."""
        # Mock response with client account data
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "response": {
                "account": {
                    "id": "client123",
                    "username": "clientuser",
                    "displayName": "Client User",
                }
            }
        }

        # Configure the API to return the proper response data structure
        fansly_api.get_json_response_contents = MagicMock(
            return_value={
                "account": {
                    "id": "client123",
                    "username": "clientuser",
                    "displayName": "Client User",
                }
            }
        )

        # Extract data
        account_data = _extract_account_data(mock_response, mock_config_with_api)

        # Verify data
        assert account_data["id"] == "client123"
        assert account_data["username"] == "clientuser"
        assert account_data["displayName"] == "Client User"

    def test_extract_creator_account_data(self, mock_config_with_api, fansly_api):
        """Test extracting creator account data."""
        # Mock response with creator account data
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "response": [
                {
                    "id": "creator123",
                    "username": "creatoruser",
                    "displayName": "Creator User",
                    "following": True,
                    "subscribed": True,
                    "timelineStats": {
                        "imageCount": 100,
                        "videoCount": 50,
                    },
                }
            ]
        }

        # Configure the API to return the proper response data structure (a list)
        fansly_api.get_json_response_contents = MagicMock(
            return_value=[
                {
                    "id": "creator123",
                    "username": "creatoruser",
                    "displayName": "Creator User",
                    "following": True,
                    "subscribed": True,
                    "timelineStats": {
                        "imageCount": 100,
                        "videoCount": 50,
                    },
                }
            ]
        )

        # Extract data
        account_data = _extract_account_data(mock_response, mock_config_with_api)

        # Verify data
        assert account_data["id"] == "creator123"
        assert account_data["username"] == "creatoruser"
        assert account_data["displayName"] == "Creator User"
        assert account_data["following"] is True
        assert account_data["subscribed"] is True
        assert account_data["timelineStats"]["imageCount"] == 100
        assert account_data["timelineStats"]["videoCount"] == 50

    def test_extract_unauthorized_error(self, mock_config_with_api, fansly_api):
        """Test handling of unauthorized error."""
        # Create proper httpx.Response for 401 error
        mock_response = httpx.Response(
            status_code=401, json={"error": "Unauthorized"}, text="Unauthorized"
        )

        mock_config_with_api.token = "invalid_token"

        # Mock get_json_response_contents to raise KeyError (simulating missing response key)
        # This triggers the 401-specific error handling path
        fansly_api.get_json_response_contents = MagicMock(
            side_effect=KeyError("response")
        )

        # Should raise ApiAuthenticationError
        with pytest.raises(ApiAuthenticationError) as excinfo:
            _extract_account_data(mock_response, mock_config_with_api)

        assert "API returned unauthorized" in str(excinfo.value)
        assert "invalid_token" in str(excinfo.value)

    def test_extract_missing_creator_error(self, mock_config_with_api, fansly_api):
        """Test handling of missing creator error."""
        # Mock response with empty list (creator not found)
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.json.return_value = {"response": []}
        mock_response.text = '{"response": []}'

        # Configure mock to return empty list
        fansly_api.get_json_response_contents = MagicMock(return_value=[])

        # Should raise ApiAccountInfoError
        with pytest.raises(ApiAccountInfoError) as excinfo:
            _extract_account_data(mock_response, mock_config_with_api)

        assert "Bad response from fansly API" in str(excinfo.value)
        assert "misspelled the creator name" in str(excinfo.value)

    def test_extract_malformed_response(self, mock_config_with_api, fansly_api):
        """Test handling of malformed response - returns non-standard data structure."""
        # Mock response with malformed data that isn't a list or dict with 'account'
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.reason_phrase = "OK"
        mock_response.json.return_value = {"invalid": "data"}
        mock_response.text = '{"invalid": "data"}'

        # Configure mock to return malformed data - this doesn't trigger an exception,
        # just returns the data as-is since it doesn't match expected patterns
        fansly_api.get_json_response_contents = MagicMock(
            return_value={"invalid": "data"}
        )

        # This doesn't raise an error - it just returns the data
        result = _extract_account_data(mock_response, mock_config_with_api)

        # The function returns the malformed data as-is when it doesn't match expected structure
        assert result == {"invalid": "data"}


class TestUpdateStateFromAccount:
    """Tests for the _update_state_from_account function."""

    def test_update_client_state(self, mock_config):
        """Test updating state for client account."""
        # Account data for client
        account_data = {
            "id": "client123",
            "username": "clientuser",
            "walls": [{"id": "wall1"}, {"id": "wall2"}],
        }

        state = DownloadState()
        state.creator_name = None  # Client account

        # Update state
        _update_state_from_account(mock_config, state, account_data)

        # Verify state updates
        assert state.creator_id == "client123"
        assert state.walls == {"wall1", "wall2"}
        # These should not be set for client
        assert state.following is False
        assert state.subscribed is False
        assert state.total_timeline_pictures == 0
        assert state.total_timeline_videos == 0

    def test_update_creator_state(self, mock_config):
        """Test updating state for creator account."""
        # Account data for creator
        account_data = {
            "id": "creator123",
            "username": "creatoruser",
            "following": True,
            "subscribed": True,
            "timelineStats": {
                "imageCount": 100,
                "videoCount": 50,
            },
            "walls": [{"id": "wall1"}, {"id": "wall2"}],
        }

        mock_config.DUPLICATE_THRESHOLD = 10  # Initial value

        state = DownloadState()
        state.creator_name = "creatoruser"  # Creator account

        # Update state
        _update_state_from_account(mock_config, state, account_data)

        # Verify state updates
        assert state.creator_id == "creator123"
        assert state.following is True
        assert state.subscribed is True
        assert state.total_timeline_pictures == 100
        assert state.total_timeline_videos == 50
        assert state.walls == {"wall1", "wall2"}

        # Custom duplicate threshold - 20% of timeline content
        assert int(0.2 * (100 + 50)) == mock_config.DUPLICATE_THRESHOLD

    def test_update_creator_missing_timeline_stats(self, mock_config):
        """Test error when timeline stats are missing for creator."""
        # Account data without timelineStats
        account_data = {
            "id": "creator123",
            "username": "creatoruser",
            "following": True,
            "subscribed": True,
            # No timelineStats
        }

        state = DownloadState()
        state.creator_name = "creatoruser"  # Creator account

        # Should raise ApiAccountInfoError
        with pytest.raises(ApiAccountInfoError) as excinfo:
            _update_state_from_account(mock_config, state, account_data)

        assert "Can not get timelineStats for creator" in str(excinfo.value)
        assert "creatoruser" in str(excinfo.value)


class TestMakeRateLimitedRequest:
    """Tests for the _make_rate_limited_request function."""

    @pytest.mark.asyncio
    async def test_successful_request(self):
        """Test successful API request."""
        # Mock request function
        mock_request_func = MagicMock()
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_request_func.return_value = mock_response

        # Call the function
        result = await _make_rate_limited_request(
            request_func=mock_request_func,
            arg1="value1",
            arg2="value2",
            rate_limit_delay=1.0,
        )

        # Verify results
        assert result == mock_response
        mock_request_func.assert_called_once_with(arg1="value1", arg2="value2")

    @pytest.mark.asyncio
    async def test_rate_limited_request(self):
        """Test handling rate limit (429) response."""
        # Mock request function
        mock_request_func = MagicMock()

        # Create httpx.Request for error construction
        request = httpx.Request("GET", "https://example.com")

        # First response is rate limited
        error_response = httpx.Response(
            status_code=429, json={"error": "Rate limited"}, request=request
        )

        # Create httpx.HTTPStatusError for rate limit
        error_exception = httpx.HTTPStatusError(
            "Rate limited", request=request, response=error_response
        )
        error_response.raise_for_status = MagicMock(side_effect=error_exception)

        # Second response is success
        success_response = httpx.Response(
            status_code=200, json={"response": "success"}, request=request
        )

        mock_request_func.side_effect = [error_response, success_response]

        # Mock sleep to speed up test
        with patch("asyncio.sleep", AsyncMock()) as mock_sleep:
            # Call the function
            result = await _make_rate_limited_request(
                request_func=mock_request_func, rate_limit_delay=1.0
            )

            # Verify results
            assert result == success_response
            assert mock_request_func.call_count == 2
            mock_sleep.assert_called_with(1.0)  # Called for rate limit delay

    @pytest.mark.asyncio
    async def test_non_rate_limit_error(self):
        """Test handling non-rate-limit HTTP error."""
        # Mock request function
        mock_request_func = MagicMock()

        # Create httpx.Request for error construction
        request = httpx.Request("GET", "https://example.com")

        # Response with 404 error
        error_response = httpx.Response(
            status_code=404, json={"error": "Not Found"}, request=request
        )

        # Create httpx.HTTPStatusError for 404
        error_exception = httpx.HTTPStatusError(
            "Not Found", request=request, response=error_response
        )
        error_response.raise_for_status = MagicMock(side_effect=error_exception)

        mock_request_func.return_value = error_response

        # Call the function - should re-raise the error
        with patch("asyncio.sleep", AsyncMock()):
            with pytest.raises(httpx.HTTPStatusError) as excinfo:
                await _make_rate_limited_request(
                    request_func=mock_request_func, rate_limit_delay=1.0
                )

            assert "Not Found" in str(excinfo.value)
            mock_request_func.assert_called_once()


@patch("download.account._validate_download_mode")
@patch("download.account._get_account_response")
@patch("download.account._extract_account_data")
@patch("download.account._update_state_from_account")
@patch("download.account.json_output")
class TestGetCreatorAccountInfo:
    """Tests for the get_creator_account_info function."""

    @pytest.mark.asyncio
    async def test_get_creator_account_info_success(
        self,
        mock_json_output,
        mock_update_state,
        mock_extract_data,
        mock_get_response,
        mock_validate_mode,
        mock_config_with_api,
    ):
        """Test successful retrieval of creator account info."""
        # Setup mocks
        state = DownloadState()
        state.creator_name = "testcreator"

        # Mock response
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.json.return_value = {"response": [{"id": "creator123"}]}
        mock_get_response.return_value = mock_response

        # Mock extracted data
        mock_account_data = {
            "id": "creator123",
            "timelineStats": {
                "imageCount": 100,
                "videoCount": 50,
            },
        }
        mock_extract_data.return_value = mock_account_data

        # Call function
        await get_creator_account_info(mock_config_with_api, state)

        # Verify function calls
        mock_validate_mode.assert_called_once_with(mock_config_with_api, state)
        mock_get_response.assert_called_once_with(mock_config_with_api, state)
        mock_extract_data.assert_called_once_with(mock_response, mock_config_with_api)
        mock_update_state.assert_called_once_with(
            mock_config_with_api, state, mock_account_data
        )
        assert mock_json_output.call_count == 2  # Two json_output calls

    @pytest.mark.asyncio
    async def test_get_creator_account_info_with_database(
        self,
        mock_json_output,
        mock_update_state,
        mock_extract_data,
        mock_get_response,
        mock_validate_mode,
        mock_config_with_api,
    ):
        """Test account info retrieval with database session and timeline duplication check."""
        # Setup mocks
        mock_config_with_api.use_duplicate_threshold = True

        state = DownloadState()
        state.creator_id = "creator123"

        session = MagicMock(spec=AsyncSession)

        # Mock response
        mock_response = MagicMock(spec=httpx.Response)
        mock_account_data = {
            "id": "creator123",
            "timelineStats": {
                "imageCount": 100,
                "videoCount": 50,
                "fetchedAt": 1633046400000,  # Milliseconds timestamp
            },
        }
        mock_response.json.return_value = {"response": [mock_account_data]}
        mock_get_response.return_value = mock_response
        mock_extract_data.return_value = mock_account_data

        # Mock database query result - same fetchedAt as API
        mock_query_result = MagicMock()
        mock_timelinestats = MagicMock()

        mock_timelinestats.fetchedAt = datetime.fromtimestamp(
            1633046400, UTC
        )  # Datetime object
        mock_query_result.scalar_one_or_none = AsyncMock(
            return_value=mock_timelinestats
        )
        session.execute = AsyncMock(return_value=mock_query_result)

        # Call function
        await get_creator_account_info(mock_config_with_api, state, session=session)

        # Verify function calls
        mock_validate_mode.assert_called_once_with(mock_config_with_api, state)
        mock_get_response.assert_called_once_with(mock_config_with_api, state)
        mock_extract_data.assert_called_once_with(mock_response, mock_config_with_api)
        mock_update_state.assert_called_once_with(
            mock_config_with_api, state, mock_account_data
        )

        # Verify timeline duplication check
        session.execute.assert_called_once()
        assert state.fetched_timeline_duplication is True


@patch("download.account._make_rate_limited_request")
class TestGetFollowingAccounts:
    """Tests for the get_following_accounts function."""

    @pytest.mark.asyncio
    async def test_get_following_accounts_success(
        self, mock_make_request, mock_config_with_api
    ):
        """Test successful retrieval of following accounts."""
        # Setup mocks
        mock_config_with_api.separate_metadata = False  # Process accounts in main DB

        state = DownloadState()
        state.creator_id = "client123"

        # Create mock session to track calls
        mock_session = AsyncMock(spec=AsyncSession)

        # Create httpx.Request for response construction
        request = httpx.Request("GET", "https://example.com")

        # Mock following list response
        following_list_response = httpx.Response(
            status_code=200,
            json={
                "success": "true",
                "response": [{"accountId": "creator1"}, {"accountId": "creator2"}],
            },
            request=request,
        )

        # Mock account details response
        account_details_response = httpx.Response(
            status_code=200,
            json={
                "success": "true",
                "response": [
                    {"id": "creator1", "username": "creator1user"},
                    {"id": "creator2", "username": "creator2user"},
                ],
            },
            request=request,
        )

        # Configure _make_rate_limited_request to return our mock responses
        mock_make_request.side_effect = [
            following_list_response,  # First call - following list
            account_details_response,  # Second call - account details
        ]

        # Mock database async_session_scope to yield our mock session
        with (
            patch.object(
                mock_config_with_api._database,
                "async_session_scope",
                return_value=AsyncMock(
                    __aenter__=AsyncMock(return_value=mock_session),
                    __aexit__=AsyncMock(),
                ),
            ),
            patch("download.account.process_account_data", AsyncMock()) as mock_process,
        ):
            # Call function
            result = await get_following_accounts(mock_config_with_api, state)

            # Verify result
            assert result == {"creator1user", "creator2user"}

            # Verify API calls
            assert mock_make_request.call_count == 2

            # Verify account processing
            assert mock_process.call_count == 2
            # Should process both accounts
            mock_process.assert_any_call(
                config=mock_config_with_api,
                state=state,
                data={"id": "creator1", "username": "creator1user"},
                session=mock_session,
            )
            mock_process.assert_any_call(
                config=mock_config_with_api,
                state=state,
                data={"id": "creator2", "username": "creator2user"},
                session=mock_session,
            )

    @pytest.mark.asyncio
    async def test_get_following_accounts_empty(
        self, mock_make_request, mock_config_with_api
    ):
        """Test handling empty following list."""
        # Setup mocks
        state = DownloadState()
        state.creator_id = "client123"

        # Create httpx.Request for response construction
        request = httpx.Request("GET", "https://example.com")

        # Mock empty following list response
        following_list_response = httpx.Response(
            status_code=200, json={"success": "true", "response": []}, request=request
        )

        # Configure _make_rate_limited_request to return our mock response
        mock_make_request.return_value = following_list_response

        # Call function
        result = await get_following_accounts(mock_config_with_api, state)

        # Verify result - empty set
        assert result == set()
        assert mock_make_request.call_count == 1

    @pytest.mark.asyncio
    async def test_get_following_accounts_no_client_id(
        self, mock_make_request, mock_config_with_api
    ):
        """Test error when client ID is not set."""
        # Setup mocks
        state = DownloadState()
        state.creator_id = None  # No client ID

        # Call function - should raise RuntimeError
        with pytest.raises(RuntimeError) as excinfo:
            await get_following_accounts(mock_config_with_api, state)

        assert "client ID not set" in str(excinfo.value)
        mock_make_request.assert_not_called()

    @pytest.mark.asyncio
    async def test_get_following_accounts_unauthorized(
        self, mock_make_request, mock_config_with_api
    ):
        """Test handling unauthorized error."""
        # Setup mocks
        mock_config_with_api.token = "invalid_token"
        state = DownloadState()
        state.creator_id = "client123"

        # Create httpx.Request for error construction
        request = httpx.Request("GET", "https://example.com")

        # Mock unauthorized error
        error_response = httpx.Response(
            status_code=401, json={"error": "Unauthorized"}, request=request
        )

        # Make the request raise httpx.HTTPStatusError with this response
        error = httpx.HTTPStatusError(
            "Unauthorized", request=request, response=error_response
        )
        mock_make_request.side_effect = error

        # Call function - should raise ApiAuthenticationError
        with pytest.raises(ApiAuthenticationError) as excinfo:
            await get_following_accounts(mock_config_with_api, state)

        assert "API returned unauthorized while getting following list" in str(
            excinfo.value
        )
        assert "invalid_token" in str(excinfo.value)
        mock_make_request.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_following_accounts_request_error(
        self, mock_make_request, mock_config_with_api
    ):
        """Test handling general request error."""
        # Setup mocks
        state = DownloadState()
        state.creator_id = "client123"

        # Make the request raise httpx.HTTPError (without response attribute)
        error = httpx.HTTPError("Connection error")
        mock_make_request.side_effect = error

        # Call function - should raise ApiError
        with pytest.raises(ApiError) as excinfo:
            await get_following_accounts(mock_config_with_api, state)

        assert "Error getting following list from Fansly API" in str(excinfo.value)
        assert "Connection error" in str(excinfo.value)
        mock_make_request.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_following_accounts_pagination(
        self, mock_make_request, mock_config_with_api
    ):
        """Test following list pagination."""
        # Setup mocks
        mock_config_with_api.separate_metadata = True  # Skip account processing

        state = DownloadState()
        state.creator_id = "client123"

        # Create mock session to track calls
        mock_session = AsyncMock(spec=AsyncSession)

        # Create httpx.Request for response construction
        request = httpx.Request("GET", "https://example.com")

        # Mock following list responses - use enough items to trigger pagination
        # Page size is 50, so first page needs to have exactly 50 items to continue
        # Create 50 accountIds for first page
        first_page_accounts = [{"accountId": f"creator{i}"} for i in range(1, 51)]
        # Create account details for first page
        first_page_details = [
            {"id": f"creator{i}", "username": f"creator{i}user"} for i in range(1, 51)
        ]

        # Second page with fewer items (will stop pagination)
        second_page_accounts = [
            {"accountId": f"creator{i}"} for i in range(51, 53)
        ]  # 2 items
        second_page_details = [
            {"id": f"creator{i}", "username": f"creator{i}user"} for i in range(51, 53)
        ]

        following_list_response1 = httpx.Response(
            status_code=200,
            json={"success": "true", "response": first_page_accounts},
            request=request,
        )

        following_list_response2 = httpx.Response(
            status_code=200,
            json={"success": "true", "response": second_page_accounts},
            request=request,
        )

        # Mock account details responses
        account_details_response1 = httpx.Response(
            status_code=200,
            json={"success": "true", "response": first_page_details},
            request=request,
        )

        account_details_response2 = httpx.Response(
            status_code=200,
            json={"success": "true", "response": second_page_details},
            request=request,
        )

        # Configure _make_rate_limited_request to return our mock responses in sequence
        # Note: pagination stops when count < page_size (50)
        mock_make_request.side_effect = [
            following_list_response1,  # First page of following list (50 items - continues)
            account_details_response1,  # Account details for first page
            following_list_response2,  # Second page of following list (2 items < page_size, stops here)
            account_details_response2,  # Account details for second page
        ]

        # Mock database async_session_scope to yield our mock session
        with (
            patch.object(
                mock_config_with_api._database,
                "async_session_scope",
                return_value=AsyncMock(
                    __aenter__=AsyncMock(return_value=mock_session),
                    __aexit__=AsyncMock(),
                ),
            ),
            patch("asyncio.sleep", AsyncMock()),
        ):
            # Call function
            result = await get_following_accounts(mock_config_with_api, state)

        # Verify result - all usernames from both pages (52 total)
        expected_usernames = {f"creator{i}user" for i in range(1, 53)}
        assert result == expected_usernames
        # Should make 4 calls total
        assert mock_make_request.call_count == 4
