"""Unit tests for the account module."""

import json
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest
import requests
from sqlalchemy.ext.asyncio import AsyncSession

from config.fanslyconfig import FanslyConfig
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


class TestValidateDownloadMode:
    """Tests for the _validate_download_mode function."""

    def test_valid_mode_with_creator(self):
        """Test with a creator name and valid mode."""
        config = MagicMock(spec=FanslyConfig)
        config.download_mode = DownloadMode.TIMELINE
        state = DownloadState()
        state.creator_name = "testuser"

        # Should not raise an exception
        _validate_download_mode(config, state)

    def test_invalid_mode_with_creator(self):
        """Test with a creator name and invalid mode."""
        config = MagicMock(spec=FanslyConfig)
        config.download_mode = DownloadMode.COLLECTION  # Not in valid modes for creator
        state = DownloadState()
        state.creator_name = "testuser"

        # Should not raise an exception (mode check skipped)
        _validate_download_mode(config, state)

    def test_no_mode_set(self):
        """Test when download mode is not set."""
        config = MagicMock(spec=FanslyConfig)
        config.download_mode = DownloadMode.NOTSET
        state = DownloadState()

        # Should raise RuntimeError
        with pytest.raises(RuntimeError) as excinfo:
            _validate_download_mode(config, state)

        assert "config download mode not set" in str(excinfo.value)

    def test_client_account_any_mode(self):
        """Test with client account (no creator) and any mode."""
        config = MagicMock(spec=FanslyConfig)
        config.download_mode = DownloadMode.COLLECTION  # Any mode should be fine
        state = DownloadState()
        state.creator_name = None  # Client account

        # Should not raise an exception
        _validate_download_mode(config, state)


class TestGetAccountResponse:
    """Tests for the _get_account_response function."""

    @pytest.mark.asyncio
    async def test_get_client_account_info(self):
        """Test getting client account info."""
        config = MagicMock(spec=FanslyConfig)
        state = DownloadState()
        state.creator_name = None  # Client account

        # Mock API response
        mock_response = MagicMock(spec=requests.Response)
        mock_response.status_code = 200
        mock_response.json.return_value = {"response": {"account": {"id": "client123"}}}

        # Mock _make_rate_limited_request
        with patch(
            "download.account._make_rate_limited_request",
            AsyncMock(return_value=mock_response),
        ) as mock_rate_limited:
            response = await _get_account_response(config, state)

            # Verify response and API call
            assert response == mock_response
            mock_rate_limited.assert_called_once_with(
                config.get_api().get_client_account_info, rate_limit_delay=30.0
            )

    @pytest.mark.asyncio
    async def test_get_creator_account_info(self):
        """Test getting creator account info."""
        config = MagicMock(spec=FanslyConfig)
        state = DownloadState()
        state.creator_name = "testcreator"  # Creator account

        # Mock API response
        mock_response = MagicMock(spec=requests.Response)
        mock_response.status_code = 200
        mock_response.json.return_value = {"response": [{"id": "creator123"}]}

        # Mock _make_rate_limited_request
        with patch(
            "download.account._make_rate_limited_request",
            AsyncMock(return_value=mock_response),
        ) as mock_rate_limited:
            response = await _get_account_response(config, state)

            # Verify response and API call
            assert response == mock_response
            mock_rate_limited.assert_called_once_with(
                config.get_api().get_creator_account_info,
                state.creator_name,
                rate_limit_delay=30.0,
            )

    @pytest.mark.asyncio
    async def test_get_account_error_response(self):
        """Test handling error response."""
        config = MagicMock(spec=FanslyConfig)
        state = DownloadState()
        state.creator_name = "testcreator"

        # Mock API response with error
        mock_response = MagicMock(spec=requests.Response)
        mock_response.status_code = 400
        mock_response.text = "Bad Request"

        # Mock _make_rate_limited_request
        with patch(
            "download.account._make_rate_limited_request",
            AsyncMock(return_value=mock_response),
        ) as mock_rate_limited:
            # Should raise ApiAccountInfoError
            with pytest.raises(ApiAccountInfoError) as excinfo:
                await _get_account_response(config, state)

            assert "API returned status code 400" in str(excinfo.value)
            mock_rate_limited.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_account_request_exception(self):
        """Test handling request exception."""
        config = MagicMock(spec=FanslyConfig)
        state = DownloadState()
        state.creator_name = "testcreator"

        # Mock _make_rate_limited_request to raise exception
        mock_exception = requests.exceptions.RequestException("Connection error")
        with patch(
            "download.account._make_rate_limited_request",
            AsyncMock(side_effect=mock_exception),
        ) as mock_rate_limited:
            # Should raise ApiError
            with pytest.raises(ApiError) as excinfo:
                await _get_account_response(config, state)

            assert "Error getting account info from fansly API" in str(excinfo.value)
            assert "Connection error" in str(excinfo.value)
            mock_rate_limited.assert_called_once()


class TestExtractAccountData:
    """Tests for the _extract_account_data function."""

    def test_extract_client_account_data(self):
        """Test extracting client account data."""
        # Mock response with client account data
        mock_response = MagicMock(spec=requests.Response)
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

        config = MagicMock(spec=FanslyConfig)

        # Extract data
        account_data = _extract_account_data(mock_response, config)

        # Verify data
        assert account_data["id"] == "client123"
        assert account_data["username"] == "clientuser"
        assert account_data["displayName"] == "Client User"

    def test_extract_creator_account_data(self):
        """Test extracting creator account data."""
        # Mock response with creator account data
        mock_response = MagicMock(spec=requests.Response)
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

        config = MagicMock(spec=FanslyConfig)

        # Extract data
        account_data = _extract_account_data(mock_response, config)

        # Verify data
        assert account_data["id"] == "creator123"
        assert account_data["username"] == "creatoruser"
        assert account_data["displayName"] == "Creator User"
        assert account_data["following"] is True
        assert account_data["subscribed"] is True
        assert account_data["timelineStats"]["imageCount"] == 100
        assert account_data["timelineStats"]["videoCount"] == 50

    def test_extract_unauthorized_error(self):
        """Test handling of unauthorized error."""
        # Mock response with 401 error
        mock_response = MagicMock(spec=requests.Response)
        mock_response.status_code = 401
        mock_response.json.return_value = {"error": "Unauthorized"}
        mock_response.text = "Unauthorized"

        config = MagicMock(spec=FanslyConfig)
        config.token = "invalid_token"

        # Should raise ApiAuthenticationError
        with pytest.raises(ApiAuthenticationError) as excinfo:
            _extract_account_data(mock_response, config)

        assert "API returned unauthorized" in str(excinfo.value)
        assert "invalid_token" in str(excinfo.value)

    def test_extract_missing_creator_error(self):
        """Test handling of missing creator error."""
        # Mock response with empty list (creator not found)
        mock_response = MagicMock(spec=requests.Response)
        mock_response.status_code = 200
        mock_response.json.return_value = {"response": []}
        mock_response.text = '{"response": []}'

        config = MagicMock(spec=FanslyConfig)

        # Should raise ApiAccountInfoError
        with pytest.raises(ApiAccountInfoError) as excinfo:
            _extract_account_data(mock_response, config)

        assert "Bad response from fansly API" in str(excinfo.value)
        assert "misspelled the creator name" in str(excinfo.value)

    def test_extract_malformed_response(self):
        """Test handling of malformed response."""
        # Mock response with malformed data
        mock_response = MagicMock(spec=requests.Response)
        mock_response.status_code = 200
        mock_response.json.return_value = {"invalid": "data"}
        mock_response.text = '{"invalid": "data"}'

        config = MagicMock(spec=FanslyConfig)

        # Should raise ApiError
        with pytest.raises(ApiError) as excinfo:
            _extract_account_data(mock_response, config)

        assert "Bad response from fansly API" in str(excinfo.value)


class TestUpdateStateFromAccount:
    """Tests for the _update_state_from_account function."""

    def test_update_client_state(self):
        """Test updating state for client account."""
        # Account data for client
        account_data = {
            "id": "client123",
            "username": "clientuser",
            "walls": [{"id": "wall1"}, {"id": "wall2"}],
        }

        config = MagicMock(spec=FanslyConfig)
        state = DownloadState()
        state.creator_name = None  # Client account

        # Update state
        _update_state_from_account(config, state, account_data)

        # Verify state updates
        assert state.creator_id == "client123"
        assert state.walls == {"wall1", "wall2"}
        # These should not be set for client
        assert state.following is False
        assert state.subscribed is False
        assert state.total_timeline_pictures == 0
        assert state.total_timeline_videos == 0

    def test_update_creator_state(self):
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

        config = MagicMock(spec=FanslyConfig)
        config.DUPLICATE_THRESHOLD = 10  # Initial value

        state = DownloadState()
        state.creator_name = "creatoruser"  # Creator account

        # Update state
        _update_state_from_account(config, state, account_data)

        # Verify state updates
        assert state.creator_id == "creator123"
        assert state.following is True
        assert state.subscribed is True
        assert state.total_timeline_pictures == 100
        assert state.total_timeline_videos == 50
        assert state.walls == {"wall1", "wall2"}

        # Custom duplicate threshold - 20% of timeline content
        assert config.DUPLICATE_THRESHOLD == int(0.2 * (100 + 50))

    def test_update_creator_missing_timeline_stats(self):
        """Test error when timeline stats are missing for creator."""
        # Account data without timelineStats
        account_data = {
            "id": "creator123",
            "username": "creatoruser",
            "following": True,
            "subscribed": True,
            # No timelineStats
        }

        config = MagicMock(spec=FanslyConfig)
        state = DownloadState()
        state.creator_name = "creatoruser"  # Creator account

        # Should raise ApiAccountInfoError
        with pytest.raises(ApiAccountInfoError) as excinfo:
            _update_state_from_account(config, state, account_data)

        assert "Can not get timelineStats for creator" in str(excinfo.value)
        assert "creatoruser" in str(excinfo.value)


class TestMakeRateLimitedRequest:
    """Tests for the _make_rate_limited_request function."""

    @pytest.mark.asyncio
    async def test_successful_request(self):
        """Test successful API request."""
        # Mock request function
        mock_request_func = MagicMock()
        mock_response = MagicMock(spec=requests.Response)
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

        # First response is rate limited, second is success
        error_response = MagicMock(spec=requests.Response)
        error_response.status_code = 429
        error_response.raise_for_status.side_effect = requests.exceptions.HTTPError(
            "Rate limited"
        )

        success_response = MagicMock(spec=requests.Response)
        success_response.status_code = 200

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

        # Response with 404 error
        error_response = MagicMock(spec=requests.Response)
        error_response.status_code = 404
        error_exception = requests.exceptions.HTTPError("Not Found")
        error_response.raise_for_status.side_effect = error_exception

        mock_request_func.return_value = error_response

        # Call the function - should re-raise the error
        with patch("asyncio.sleep", AsyncMock()):
            with pytest.raises(requests.exceptions.HTTPError) as excinfo:
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
    ):
        """Test successful retrieval of creator account info."""
        # Setup mocks
        config = MagicMock(spec=FanslyConfig)
        state = DownloadState()
        state.creator_name = "testcreator"

        # Mock response
        mock_response = MagicMock(spec=requests.Response)
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
        await get_creator_account_info(config, state)

        # Verify function calls
        mock_validate_mode.assert_called_once_with(config, state)
        mock_get_response.assert_called_once_with(config, state)
        mock_extract_data.assert_called_once_with(mock_response, config)
        mock_update_state.assert_called_once_with(config, state, mock_account_data)
        assert mock_json_output.call_count == 2  # Two json_output calls

    @pytest.mark.asyncio
    async def test_get_creator_account_info_with_database(
        self,
        mock_json_output,
        mock_update_state,
        mock_extract_data,
        mock_get_response,
        mock_validate_mode,
    ):
        """Test account info retrieval with database session and timeline duplication check."""
        # Setup mocks
        config = MagicMock(spec=FanslyConfig)
        config.use_duplicate_threshold = True

        state = DownloadState()
        state.creator_id = "creator123"

        session = MagicMock(spec=AsyncSession)

        # Mock response
        mock_response = MagicMock(spec=requests.Response)
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
        from datetime import datetime, timezone

        mock_timelinestats.fetchedAt = datetime.fromtimestamp(
            1633046400, timezone.utc
        )  # Datetime object
        mock_query_result.scalar_one_or_none = AsyncMock(
            return_value=mock_timelinestats
        )
        session.execute = AsyncMock(return_value=mock_query_result)

        # Call function
        await get_creator_account_info(config, state, session=session)

        # Verify function calls
        mock_validate_mode.assert_called_once_with(config, state)
        mock_get_response.assert_called_once_with(config, state)
        mock_extract_data.assert_called_once_with(mock_response, config)
        mock_update_state.assert_called_once_with(config, state, mock_account_data)

        # Verify timeline duplication check
        session.execute.assert_called_once()
        assert state.fetchedTimelineDuplication is True


@patch("download.account._make_rate_limited_request")
class TestGetFollowingAccounts:
    """Tests for the get_following_accounts function."""

    @pytest.mark.asyncio
    async def test_get_following_accounts_success(self, mock_make_request):
        """Test successful retrieval of following accounts."""
        # Setup mocks
        config = MagicMock(spec=FanslyConfig)
        config.separate_metadata = False  # Process accounts in main DB

        # Mock database session
        mock_session = MagicMock(spec=AsyncSession)
        mock_session.flush = AsyncMock()
        config._database.async_session_scope.return_value.__aenter__.return_value = (
            mock_session
        )

        state = DownloadState()
        state.creator_id = "client123"

        # Mock following list response
        following_list_response = MagicMock(spec=requests.Response)
        following_list_response.json.return_value = {
            "response": [{"accountId": "creator1"}, {"accountId": "creator2"}]
        }

        # Mock account details response
        account_details_response = MagicMock(spec=requests.Response)
        account_details_response.json.return_value = {
            "response": [
                {"id": "creator1", "username": "creator1user"},
                {"id": "creator2", "username": "creator2user"},
            ]
        }

        # Configure _make_rate_limited_request to return our mock responses
        mock_make_request.side_effect = [
            following_list_response,  # First call - following list
            account_details_response,  # Second call - account details
        ]

        # Mock process_account_data
        with patch(
            "download.account.process_account_data", AsyncMock()
        ) as mock_process:
            # Call function
            result = await get_following_accounts(config, state)

            # Verify result
            assert result == {"creator1user", "creator2user"}

            # Verify API calls
            assert mock_make_request.call_count == 2

            # Verify account processing
            assert mock_process.call_count == 2
            # Should process both accounts
            mock_process.assert_any_call(
                config=config,
                state=state,
                data={"id": "creator1", "username": "creator1user"},
                session=mock_session,
            )
            mock_process.assert_any_call(
                config=config,
                state=state,
                data={"id": "creator2", "username": "creator2user"},
                session=mock_session,
            )

    @pytest.mark.asyncio
    async def test_get_following_accounts_empty(self, mock_make_request):
        """Test handling empty following list."""
        # Setup mocks
        config = MagicMock(spec=FanslyConfig)
        state = DownloadState()
        state.creator_id = "client123"

        # Mock empty following list response
        following_list_response = MagicMock(spec=requests.Response)
        following_list_response.json.return_value = {"response": []}

        # Configure _make_rate_limited_request to return our mock response
        mock_make_request.return_value = following_list_response

        # Call function
        result = await get_following_accounts(config, state)

        # Verify result - empty set
        assert result == set()
        assert mock_make_request.call_count == 1

    @pytest.mark.asyncio
    async def test_get_following_accounts_no_client_id(self, mock_make_request):
        """Test error when client ID is not set."""
        # Setup mocks
        config = MagicMock(spec=FanslyConfig)
        state = DownloadState()
        state.creator_id = None  # No client ID

        # Call function - should raise RuntimeError
        with pytest.raises(RuntimeError) as excinfo:
            await get_following_accounts(config, state)

        assert "client ID not set" in str(excinfo.value)
        mock_make_request.assert_not_called()

    @pytest.mark.asyncio
    async def test_get_following_accounts_unauthorized(self, mock_make_request):
        """Test handling unauthorized error."""
        # Setup mocks
        config = MagicMock(spec=FanslyConfig)
        config.token = "invalid_token"
        state = DownloadState()
        state.creator_id = "client123"

        # Mock unauthorized error
        error_response = MagicMock(spec=requests.Response)
        error_response.status_code = 401
        error_response.text = "Unauthorized"

        # Make the request raise a RequestException with this response
        error = requests.exceptions.RequestException("Unauthorized")
        error.response = error_response
        mock_make_request.side_effect = error

        # Call function - should raise ApiAuthenticationError
        with pytest.raises(ApiAuthenticationError) as excinfo:
            await get_following_accounts(config, state)

        assert "API returned unauthorized while getting following list" in str(
            excinfo.value
        )
        assert "invalid_token" in str(excinfo.value)
        mock_make_request.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_following_accounts_request_error(self, mock_make_request):
        """Test handling general request error."""
        # Setup mocks
        config = MagicMock(spec=FanslyConfig)
        state = DownloadState()
        state.creator_id = "client123"

        # Make the request raise a RequestException without a response
        error = requests.exceptions.RequestException("Connection error")
        error.response = None
        mock_make_request.side_effect = error

        # Call function - should raise ApiError
        with pytest.raises(ApiError) as excinfo:
            await get_following_accounts(config, state)

        assert "Error getting following list from Fansly API" in str(excinfo.value)
        assert "Connection error" in str(excinfo.value)
        mock_make_request.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_following_accounts_pagination(self, mock_make_request):
        """Test following list pagination."""
        # Setup mocks
        config = MagicMock(spec=FanslyConfig)
        config.separate_metadata = True  # Skip account processing

        state = DownloadState()
        state.creator_id = "client123"

        # Mock session for async context manager
        mock_session = MagicMock()
        config._database.async_session_scope.return_value.__aenter__.return_value = (
            mock_session
        )

        # Mock following list responses - two pages
        following_list_response1 = MagicMock(spec=requests.Response)
        following_list_response1.json.return_value = {
            "response": [{"accountId": "creator1"}, {"accountId": "creator2"}]
        }

        following_list_response2 = MagicMock(spec=requests.Response)
        following_list_response2.json.return_value = {
            "response": [{"accountId": "creator3"}]
        }

        # Empty response for third page
        following_list_response3 = MagicMock(spec=requests.Response)
        following_list_response3.json.return_value = {"response": []}

        # Mock account details responses
        account_details_response1 = MagicMock(spec=requests.Response)
        account_details_response1.json.return_value = {
            "response": [
                {"id": "creator1", "username": "creator1user"},
                {"id": "creator2", "username": "creator2user"},
            ]
        }

        account_details_response2 = MagicMock(spec=requests.Response)
        account_details_response2.json.return_value = {
            "response": [{"id": "creator3", "username": "creator3user"}]
        }

        # Configure _make_rate_limited_request to return our mock responses in sequence
        mock_make_request.side_effect = [
            following_list_response1,  # First page of following list
            account_details_response1,  # Account details for first page
            following_list_response2,  # Second page of following list
            account_details_response2,  # Account details for second page
            following_list_response3,  # Third page (empty, ends pagination)
        ]

        # Mock asyncio.sleep to speed up test
        with patch("asyncio.sleep", AsyncMock()):
            # Call function
            result = await get_following_accounts(config, state)

            # Verify result - all usernames
            assert result == {"creator1user", "creator2user", "creator3user"}
            assert mock_make_request.call_count == 5
