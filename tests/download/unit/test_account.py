"""Unit tests for the account module."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import respx

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
from metadata import Account, TimelineStats, Wall
from tests.fixtures.utils import snowflake_id


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
            side_effect=[httpx.Response(200)]
        )

        # Mock HTTP response at the edge (Fansly API endpoint)
        respx.get("https://apiv3.fansly.com/api/v1/account/me").mock(
            side_effect=[
                httpx.Response(
                    200,
                    json={"response": {"account": {"id": "client123"}}},
                )
            ]
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
        ).mock(side_effect=[httpx.Response(200)])

        # Mock HTTP response at the edge (Fansly API endpoint) - includes ngsw-bypass param
        respx.get(
            "https://apiv3.fansly.com/api/v1/account?usernames=testcreator&ngsw-bypass=true"
        ).mock(
            side_effect=[
                httpx.Response(
                    200,
                    json={"response": [{"id": "creator123"}]},
                )
            ]
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
        ).mock(side_effect=[httpx.Response(200)])

        # Mock HTTP response at the edge with error status - includes ngsw-bypass param
        respx.get(
            "https://apiv3.fansly.com/api/v1/account?usernames=testcreator&ngsw-bypass=true"
        ).mock(side_effect=[httpx.Response(400, text="Bad Request")])

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
        account_id = snowflake_id()
        wall_id_1 = snowflake_id()
        wall_id_2 = snowflake_id()

        account = Account(
            id=account_id,
            username="clientuser",
            walls=[
                Wall(id=wall_id_1, accountId=account_id),
                Wall(id=wall_id_2, accountId=account_id),
            ],
        )

        state = DownloadState()
        state.creator_name = None  # Client account

        _update_state_from_account(mock_config, state, account)

        assert state.creator_id == account_id
        assert state.walls == {wall_id_1, wall_id_2}
        # These should not be set for client
        assert state.following is False
        assert state.subscribed is False
        assert state.total_timeline_pictures == 0
        assert state.total_timeline_videos == 0

    def test_update_creator_state(self, mock_config):
        """Test updating state for creator account."""
        account_id = snowflake_id()
        wall_id_1 = snowflake_id()
        wall_id_2 = snowflake_id()

        account = Account(
            id=account_id,
            username="creatoruser",
            following=True,
            subscribed=True,
            timelineStats=TimelineStats(
                accountId=account_id,
                imageCount=100,
                videoCount=50,
            ),
            walls=[
                Wall(id=wall_id_1, accountId=account_id),
                Wall(id=wall_id_2, accountId=account_id),
            ],
        )

        mock_config.DUPLICATE_THRESHOLD = 10  # Initial value

        state = DownloadState()
        state.creator_name = "creatoruser"  # Creator account

        _update_state_from_account(mock_config, state, account)

        assert state.creator_id == account_id
        assert state.following is True
        assert state.subscribed is True
        assert state.total_timeline_pictures == 100
        assert state.total_timeline_videos == 50
        assert state.walls == {wall_id_1, wall_id_2}

        # Custom duplicate threshold - 20% of timeline content
        assert int(0.2 * (100 + 50)) == mock_config.DUPLICATE_THRESHOLD

    def test_update_creator_missing_timeline_stats(self, mock_config):
        """Test error when timeline stats are missing for creator."""
        account = Account(
            id=snowflake_id(),
            username="creatoruser",
            following=True,
            subscribed=True,
        )

        state = DownloadState()
        state.creator_name = "creatoruser"  # Creator account

        with pytest.raises(ApiAccountInfoError) as excinfo:
            _update_state_from_account(mock_config, state, account)

        assert "Can not get timelineStats for creator" in str(excinfo.value)
        assert "creatoruser" in str(excinfo.value)


class TestMakeRateLimitedRequest:
    """Tests for the _make_rate_limited_request function."""

    @pytest.mark.asyncio
    async def test_successful_request(self):
        """Test successful API request."""
        # Mock request function
        mock_request_func = AsyncMock()
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
        mock_request_func = AsyncMock()

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
        mock_request_func = AsyncMock()

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


class TestGetCreatorAccountInfo:
    """Tests for the get_creator_account_info function.

    Uses respx to mock HTTP at the edge and entity_store for real DB.
    No internal functions are patched — the full code path runs end-to-end.
    """

    @pytest.mark.asyncio
    @respx.mock
    async def test_get_creator_account_info_success(
        self, mock_config_with_api, entity_store
    ):
        """Test successful retrieval of creator account info."""
        creator_id = snowflake_id()

        state = DownloadState()
        state.creator_name = "testcreator"
        mock_config_with_api.download_mode = DownloadMode.TIMELINE

        # Mock HTTP at the edge (OPTIONS preflight + GET)
        respx.options(
            "https://apiv3.fansly.com/api/v1/account?usernames=testcreator"
        ).mock(side_effect=[httpx.Response(200)])

        respx.get(
            "https://apiv3.fansly.com/api/v1/account?usernames=testcreator&ngsw-bypass=true"
        ).mock(
            side_effect=[
                httpx.Response(
                    200,
                    json={
                        "success": "true",
                        "response": [
                            {
                                "id": str(creator_id),
                                "username": "testcreator",
                                "following": True,
                                "subscribed": True,
                                "timelineStats": {
                                    "accountId": str(creator_id),
                                    "imageCount": 100,
                                    "videoCount": 50,
                                },
                            }
                        ],
                    },
                )
            ]
        )

        await get_creator_account_info(mock_config_with_api, state)

        # Verify real _update_state_from_account ran
        assert state.creator_id == creator_id
        assert state.following is True
        assert state.subscribed is True
        assert state.total_timeline_pictures == 100
        assert state.total_timeline_videos == 50

        # Account persisted to real store
        account = await entity_store.get(Account, creator_id)
        assert account is not None
        assert account.username == "testcreator"

    @pytest.mark.asyncio
    @respx.mock
    async def test_get_creator_account_info_timeline_duplication(
        self, mock_config_with_api, entity_store
    ):
        """Test timeline duplication detection via store cache."""
        creator_id = snowflake_id()
        fetched_at = datetime.fromtimestamp(1633046400, UTC)

        mock_config_with_api.use_duplicate_threshold = True
        mock_config_with_api.download_mode = DownloadMode.TIMELINE

        state = DownloadState()
        state.creator_name = "testcreator"

        # Pre-seed store: Account must exist before TimelineStats (FK constraint)
        await entity_store.save(Account(id=creator_id, username="testcreator"))
        await entity_store.save(
            TimelineStats(accountId=creator_id, fetchedAt=fetched_at)
        )

        # Mock HTTP — API returns same fetchedAt as pre-seeded DB value
        respx.options(
            "https://apiv3.fansly.com/api/v1/account?usernames=testcreator"
        ).mock(side_effect=[httpx.Response(200)])

        respx.get(
            "https://apiv3.fansly.com/api/v1/account?usernames=testcreator&ngsw-bypass=true"
        ).mock(
            side_effect=[
                httpx.Response(
                    200,
                    json={
                        "success": "true",
                        "response": [
                            {
                                "id": str(creator_id),
                                "username": "testcreator",
                                "following": True,
                                "subscribed": True,
                                "timelineStats": {
                                    "accountId": str(creator_id),
                                    "imageCount": 100,
                                    "videoCount": 50,
                                    "fetchedAt": 1633046400000,
                                },
                            }
                        ],
                    },
                )
            ]
        )

        await get_creator_account_info(mock_config_with_api, state)

        assert state.fetched_timeline_duplication is True


@patch("download.account._make_rate_limited_request")
class TestGetFollowingAccounts:
    """Tests for the get_following_accounts function."""

    @pytest.mark.asyncio
    async def test_get_following_accounts_success(
        self, mock_make_request, mock_config_with_api, entity_store
    ):
        """Test successful retrieval of following accounts."""
        creator1_id = snowflake_id()
        creator2_id = snowflake_id()

        state = DownloadState()
        state.creator_id = snowflake_id()

        request = httpx.Request("GET", "https://example.com")

        # Mock following list response
        following_list_response = httpx.Response(
            status_code=200,
            json={
                "success": "true",
                "response": [
                    {"accountId": str(creator1_id)},
                    {"accountId": str(creator2_id)},
                ],
            },
            request=request,
        )

        # Mock account details response
        account_details_response = httpx.Response(
            status_code=200,
            json={
                "success": "true",
                "response": [
                    {"id": str(creator1_id), "username": "creator1user"},
                    {"id": str(creator2_id), "username": "creator2user"},
                ],
            },
            request=request,
        )

        mock_make_request.side_effect = [
            following_list_response,
            account_details_response,
        ]

        with patch("asyncio.sleep", AsyncMock()):
            result = await get_following_accounts(mock_config_with_api, state)

        assert result == {"creator1user", "creator2user"}
        assert mock_make_request.call_count == 2

        # Accounts persisted to real store
        account1 = await entity_store.get(Account, creator1_id)
        assert account1 is not None
        assert account1.username == "creator1user"

    @pytest.mark.asyncio
    async def test_get_following_accounts_empty(
        self, mock_make_request, mock_config_with_api
    ):
        """Test handling empty following list."""
        # Setup mocks
        state = DownloadState()
        state.creator_id = 123456

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
        state.creator_id = 123456

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
        state.creator_id = 123456

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
        self, mock_make_request, mock_config_with_api, entity_store
    ):
        """Test following list pagination."""
        state = DownloadState()
        state.creator_id = snowflake_id()

        request = httpx.Request("GET", "https://example.com")

        # Generate stable snowflake IDs for 52 creators
        creator_ids = [snowflake_id() for _ in range(52)]

        # Page 1: 50 items (triggers pagination since count == page_size)
        first_page_accounts = [{"accountId": str(cid)} for cid in creator_ids[:50]]
        first_page_details = [
            {"id": str(cid), "username": f"user_{cid}"} for cid in creator_ids[:50]
        ]

        # Page 2: 2 items (count < page_size → stops)
        second_page_accounts = [{"accountId": str(cid)} for cid in creator_ids[50:]]
        second_page_details = [
            {"id": str(cid), "username": f"user_{cid}"} for cid in creator_ids[50:]
        ]

        mock_make_request.side_effect = [
            httpx.Response(
                200,
                json={"success": "true", "response": first_page_accounts},
                request=request,
            ),
            httpx.Response(
                200,
                json={"success": "true", "response": first_page_details},
                request=request,
            ),
            httpx.Response(
                200,
                json={"success": "true", "response": second_page_accounts},
                request=request,
            ),
            httpx.Response(
                200,
                json={"success": "true", "response": second_page_details},
                request=request,
            ),
        ]

        with patch("asyncio.sleep", AsyncMock()):
            result = await get_following_accounts(mock_config_with_api, state)

        expected_usernames = {f"user_{cid}" for cid in creator_ids}
        assert result == expected_usernames
        assert mock_make_request.call_count == 4

    @pytest.mark.asyncio
    async def test_reverse_order_processes_accounts_in_reverse(
        self, mock_make_request, mock_config_with_api, entity_store, caplog
    ):
        """Lines 448-450: config.reverse_order=True → reverse list + log info."""
        import logging as _logging

        caplog.set_level(_logging.INFO)
        creator_a = snowflake_id()
        creator_b = snowflake_id()
        creator_c = snowflake_id()

        mock_config_with_api.reverse_order = True

        state = DownloadState()
        state.creator_id = snowflake_id()

        request = httpx.Request("GET", "https://example.com")
        following_list_response = httpx.Response(
            status_code=200,
            json={
                "success": "true",
                "response": [
                    {"accountId": str(creator_a)},
                    {"accountId": str(creator_b)},
                    {"accountId": str(creator_c)},
                ],
            },
            request=request,
        )
        account_details_response = httpx.Response(
            status_code=200,
            json={
                "success": "true",
                "response": [
                    {"id": str(creator_a), "username": "alpha"},
                    {"id": str(creator_b), "username": "bravo"},
                    {"id": str(creator_c), "username": "charlie"},
                ],
            },
            request=request,
        )

        mock_make_request.side_effect = [
            following_list_response,
            account_details_response,
        ]

        with patch("asyncio.sleep", AsyncMock()):
            result = await get_following_accounts(mock_config_with_api, state)

        # All three creators surfaced; the order isn't observable in the
        # returned set, but the "Processing accounts in reverse order"
        # info log proves the branch fired.
        assert result == {"alpha", "bravo", "charlie"}
        info_messages = [
            r.getMessage() for r in caplog.records if r.levelname == "INFO"
        ]
        assert any("Processing accounts in reverse order" in m for m in info_messages)

    @pytest.mark.asyncio
    async def test_per_account_exception_logged_loop_continues(
        self, mock_make_request, mock_config_with_api, entity_store, caplog, monkeypatch
    ):
        """Lines 468-471: process_account_data raises mid-loop → log error + continue."""
        import logging as _logging

        caplog.set_level(_logging.ERROR)
        creator_good = snowflake_id()
        creator_bad = snowflake_id()

        state = DownloadState()
        state.creator_id = snowflake_id()

        request = httpx.Request("GET", "https://example.com")
        following_list_response = httpx.Response(
            status_code=200,
            json={
                "success": "true",
                "response": [
                    {"accountId": str(creator_good)},
                    {"accountId": str(creator_bad)},
                ],
            },
            request=request,
        )
        account_details_response = httpx.Response(
            status_code=200,
            json={
                "success": "true",
                "response": [
                    {"id": str(creator_good), "username": "goodguy"},
                    {"id": str(creator_bad), "username": "badguy"},
                ],
            },
            request=request,
        )

        mock_make_request.side_effect = [
            following_list_response,
            account_details_response,
        ]

        # Monkeypatch process_account_data: raise for badguy's data.
        # Match by username (not id) — the id field may go through int/str
        # coercion somewhere upstream. Username is a stable string.
        async def _selective_raise(*, config, state, data):
            if data.get("username") == "badguy":
                raise RuntimeError("simulated account-process failure")

        monkeypatch.setattr("download.account.process_account_data", _selective_raise)

        with patch("asyncio.sleep", AsyncMock()):
            result = await get_following_accounts(mock_config_with_api, state)

        # Loop continued past the failure — good account still in result,
        # bad account excluded (its except-branch's continue prevented
        # usernames.add). This behavior alone proves the except path fired:
        # without it, the RuntimeError would propagate and fail the test.
        assert "goodguy" in result
        assert "badguy" not in result
