"""Unit tests for FanslyApi class"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import respx

from api.fansly import FanslyApi


class TestFanslyApi:
    def test_init(self, fansly_api):
        """Test FanslyApi initialization with basic parameters"""
        assert fansly_api.token == "test_token"
        assert fansly_api.user_agent == "test_user_agent"
        assert fansly_api.check_key == "test_check_key"
        assert fansly_api.session_id == "null"
        assert hasattr(fansly_api, "device_id")
        assert hasattr(fansly_api, "device_id_timestamp")

    def test_init_with_device_info(self):
        """Test FanslyApi initialization with device ID parameters"""
        test_device_id = "test_device_id"
        test_timestamp = int(datetime(2024, 1, 1, tzinfo=UTC).timestamp())
        mock_callback = MagicMock()

        api = FanslyApi(
            token="test_token",  # noqa: S106 # Test fixture token
            user_agent="test_user_agent",
            check_key="test_check_key",
            device_id=test_device_id,
            device_id_timestamp=test_timestamp,
            on_device_updated=mock_callback,
        )

        assert api.device_id == test_device_id
        assert api.device_id_timestamp == test_timestamp
        assert api.on_device_updated == mock_callback

    def test_get_text_accept(self, fansly_api):
        """Test get_text_accept returns correct accept header"""
        assert fansly_api.get_text_accept() == "application/json, text/plain, */*"

    def test_set_text_accept(self, fansly_api):
        """Test set_text_accept adds accept header correctly"""
        headers = {}
        fansly_api.set_text_accept(headers)
        assert headers["Accept"] == fansly_api.get_text_accept()

    def test_get_common_headers(self, fansly_api):
        """Test get_common_headers returns correct header structure"""
        headers = fansly_api.get_common_headers()

        assert headers["Accept-Language"] == "en-US,en;q=0.9"
        assert headers["authorization"] == "test_token"
        assert headers["Origin"] == "https://fansly.com"
        assert headers["Referer"] == "https://fansly.com/"
        assert headers["User-Agent"] == "test_user_agent"

    def test_get_common_headers_alternate_token(self, fansly_api):
        """Test get_common_headers with alternate token"""
        alt_token = "alternate_token"
        headers = fansly_api.get_common_headers(alternate_token=alt_token)
        assert headers["authorization"] == alt_token

    def test_get_common_headers_missing_token(self):
        """Test get_common_headers raises error with missing token"""
        api = FanslyApi(
            token=None,
            user_agent="test_user_agent",
            check_key="test_check_key",
            device_id="test_device_id",  # Provide device ID to avoid request
            device_id_timestamp=int(
                datetime.now(UTC).timestamp() * 1000
            ),  # Current timestamp
        )
        with pytest.raises(
            RuntimeError, match="Internal error generating HTTP headers"
        ):
            api.get_common_headers()

    def test_get_ngsw_params(self, fansly_api):
        """Test get_ngsw_params returns correct parameters"""
        params = fansly_api.get_ngsw_params()
        assert params == {"ngsw-bypass": "true"}

    def test_cyrb53(self, fansly_api):
        """Test cyrb53 hash function"""
        # Test with known input/output
        test_input = "test_string"
        hash1 = fansly_api.cyrb53(test_input)
        hash2 = fansly_api.cyrb53(test_input)

        # Same input should produce same hash
        assert hash1 == hash2

        # Different inputs should produce different hashes
        different_hash = fansly_api.cyrb53("different_string")
        assert hash1 != different_hash

    def test_cyrb53_with_seed(self, fansly_api):
        """Test cyrb53 hash function with seed"""
        test_input = "test_string"
        hash1 = fansly_api.cyrb53(test_input, seed=1)
        hash2 = fansly_api.cyrb53(test_input, seed=2)

        # Same input with different seeds should produce different hashes
        assert hash1 != hash2

    def test_get_timestamp_ms(self, fansly_api):
        """Test get_timestamp_ms returns current timestamp in milliseconds"""
        timestamp = fansly_api.get_timestamp_ms()
        now = int(datetime.now(UTC).timestamp() * 1000)
        # Allow 1 second difference due to execution time
        assert abs(timestamp - now) < 1000

    def test_get_client_timestamp(self, fansly_api):
        """Test get_client_timestamp returns value within expected range"""
        timestamp = fansly_api.get_client_timestamp()
        now = int(datetime.now(UTC).timestamp() * 1000)
        # Should be current time +/- 5000ms
        assert abs(timestamp - now) <= 5000

    def test_update_client_timestamp(self, fansly_api):
        """Test update_client_timestamp updates when newer"""
        old_timestamp = fansly_api.client_timestamp
        fansly_api.update_client_timestamp()
        assert fansly_api.client_timestamp >= old_timestamp

    def test_to_str16(self, fansly_api):
        """Test to_str16 hex conversion"""
        test_num = 255
        result = fansly_api.to_str16(test_num)
        assert result == "ff"

    def test_int32(self, fansly_api):
        """Test int32 conversion"""
        # Test within 32-bit range
        assert fansly_api.int32(100) == 100
        # Test overflow
        assert fansly_api.int32(2**31 + 1) < 2**31

    @pytest.mark.asyncio
    @respx.mock
    async def test_setup_session(self, fansly_api):
        """Test setup_session success path - mocks HTTP at edge"""
        # Mock the account info HTTP endpoint at edge (OPTIONS + GET)
        respx.options("https://apiv3.fansly.com/api/v1/account/me").mock(
            return_value=httpx.Response(200)
        )
        respx.get("https://apiv3.fansly.com/api/v1/account/me").mock(
            return_value=httpx.Response(200, json={"success": "true", "response": {}})
        )

        # Create a mock websocket instance with proper async methods
        mock_ws_instance = AsyncMock()
        mock_ws_instance.__aenter__.return_value = mock_ws_instance
        mock_ws_instance.__aexit__.return_value = AsyncMock()
        # Mock recv to return the session ID in the expected format
        mock_ws_instance.recv = AsyncMock(
            return_value='{"t":1,"d":"{\\"session\\":{\\"id\\":\\"test_session_id\\"}}"}'
        )

        # Mock the websocket connection (this is OK - websockets are external boundary)
        with patch("websockets.client.connect", return_value=mock_ws_instance):
            result = await fansly_api.setup_session()
            assert result is True
            assert fansly_api.session_id == "test_session_id"

    def test_validate_json_response_success(self, fansly_api):
        """Test validate_json_response with successful response"""
        # Use real httpx.Response instead of MagicMock
        mock_response = httpx.Response(200, json={"success": "true"})

        assert fansly_api.validate_json_response(mock_response) is True

    def test_validate_json_response_failure(self, fansly_api):
        """Test validate_json_response with failed response"""
        # Use real httpx.Response instead of MagicMock
        mock_response = httpx.Response(200, json={"success": "false"})

        with pytest.raises(RuntimeError, match="Invalid or failed JSON response"):
            fansly_api.validate_json_response(mock_response)

    def test_get_json_response_contents(self, fansly_api):
        """Test get_json_response_contents extracts response field"""
        # Use real httpx.Response instead of MagicMock
        mock_response = httpx.Response(
            200,
            json={
                "success": "true",
                "response": {"data": "test_data"},
            },
        )

        result = fansly_api.get_json_response_contents(mock_response)
        assert result == {"data": "test_data"}

    @respx.mock
    def test_get_client_user_name(self, fansly_api):
        """Test get_client_user_name success path - mocks Fansly API at edge"""
        # Mock the actual Fansly API endpoint (OPTIONS + GET)
        respx.options("https://apiv3.fansly.com/api/v1/account/me").mock(
            return_value=httpx.Response(200)
        )
        respx.get("https://apiv3.fansly.com/api/v1/account/me").mock(
            return_value=httpx.Response(
                200,
                json={
                    "success": "true",
                    "response": {"account": {"username": "test_user"}},
                },
            )
        )

        assert fansly_api.get_client_user_name() == "test_user"

    @respx.mock
    def test_get_with_ngsw(self, fansly_api):
        """Test get_with_ngsw builds correct request - mocks HTTP at edge"""
        test_url = "https://api.test.com/endpoint"
        test_params = {"test": "param"}

        # Mock OPTIONS and GET requests at edge
        options_route = respx.options(test_url).mock(return_value=httpx.Response(200))
        get_route = respx.get(test_url).mock(return_value=httpx.Response(200))

        fansly_api.get_with_ngsw(
            url=test_url, params=test_params, add_fansly_headers=True
        )

        # Verify OPTIONS request was made
        assert options_route.called

        # Verify GET request was made with correct parameters
        assert get_route.called
        get_request = get_route.calls.last.request
        assert get_request.url.params["test"] == "param"
        assert get_request.url.params["ngsw-bypass"] == "true"
        assert get_request.headers["Origin"] == "https://fansly.com"

    @respx.mock
    def test_get_creator_account_info_single(self, fansly_api):
        """Test get_creator_account_info with single username - mocks HTTP at edge"""
        # Mock the Fansly API endpoint for account info (OPTIONS + GET)
        respx.options("https://apiv3.fansly.com/api/v1/account").mock(
            return_value=httpx.Response(200)
        )
        route = respx.get("https://apiv3.fansly.com/api/v1/account").mock(
            return_value=httpx.Response(200, json={"success": "true", "response": []})
        )

        fansly_api.get_creator_account_info("test_creator")

        assert route.called
        request = route.calls.last.request
        assert request.url.params["usernames"] == "test_creator"
        assert request.url.params["ngsw-bypass"] == "true"

    @respx.mock
    def test_get_creator_account_info_multiple(self, fansly_api):
        """Test get_creator_account_info with multiple usernames - mocks HTTP at edge"""
        # Mock the Fansly API endpoint for account info (OPTIONS + GET)
        respx.options("https://apiv3.fansly.com/api/v1/account").mock(
            return_value=httpx.Response(200)
        )
        route = respx.get("https://apiv3.fansly.com/api/v1/account").mock(
            return_value=httpx.Response(200, json={"success": "true", "response": []})
        )

        fansly_api.get_creator_account_info(["creator1", "creator2"])

        assert route.called
        request = route.calls.last.request
        assert request.url.params["usernames"] == "creator1,creator2"
        assert request.url.params["ngsw-bypass"] == "true"

    @respx.mock
    def test_get_account_info_by_id_single(self, fansly_api):
        """Test get_account_info_by_id with single ID - mocks HTTP at edge"""
        # Mock the Fansly API endpoint for account info by ID (OPTIONS + GET)
        respx.options("https://apiv3.fansly.com/api/v1/account").mock(
            return_value=httpx.Response(200)
        )
        route = respx.get("https://apiv3.fansly.com/api/v1/account").mock(
            return_value=httpx.Response(200, json={"success": "true", "response": []})
        )

        fansly_api.get_account_info_by_id(123)

        assert route.called
        request = route.calls.last.request
        assert request.url.params["ids"] == "123"
        assert request.url.params["ngsw-bypass"] == "true"

    @respx.mock
    def test_get_account_info_by_id_multiple(self, fansly_api):
        """Test get_account_info_by_id with multiple IDs - mocks HTTP at edge"""
        # Mock the Fansly API endpoint for account info by ID (OPTIONS + GET)
        respx.options("https://apiv3.fansly.com/api/v1/account").mock(
            return_value=httpx.Response(200)
        )
        route = respx.get("https://apiv3.fansly.com/api/v1/account").mock(
            return_value=httpx.Response(200, json={"success": "true", "response": []})
        )

        fansly_api.get_account_info_by_id([123, 456])

        assert route.called
        request = route.calls.last.request
        assert request.url.params["ids"] == "123,456"
        assert request.url.params["ngsw-bypass"] == "true"

    @respx.mock
    def test_get_media_collections(self, fansly_api):
        """Test get_media_collections request - mocks HTTP at edge"""
        # Mock the Fansly API endpoint for media collections (OPTIONS + GET)
        respx.options(
            url__regex=r"https://apiv3\.fansly\.com/api/v1/account/media/orders/.*"
        ).mock(return_value=httpx.Response(200))
        route = respx.get(
            url__regex=r"https://apiv3\.fansly\.com/api/v1/account/media/orders/.*"
        ).mock(
            return_value=httpx.Response(200, json={"success": "true", "response": []})
        )

        fansly_api.get_media_collections()

        assert route.called
        request = route.calls.last.request
        assert request.url.params["limit"] == "9999"
        assert request.url.params["offset"] == "0"

    @respx.mock
    def test_get_following_list(self, fansly_api):
        """Test get_following_list with default parameters - mocks HTTP at edge"""
        # Mock the Fansly API endpoint for following list (OPTIONS + GET)
        respx.options(
            url__regex=r"https://apiv3\.fansly\.com/api/v1/account/.*/following"
        ).mock(return_value=httpx.Response(200))
        route = respx.get(
            url__regex=r"https://apiv3\.fansly\.com/api/v1/account/.*/following"
        ).mock(
            return_value=httpx.Response(200, json={"success": "true", "response": []})
        )

        fansly_api.get_following_list("user123")

        assert route.called
        request = route.calls.last.request
        assert request.url.params["limit"] == "425"
        assert request.url.params["offset"] == "0"
        assert request.url.params["before"] == "0"
        assert request.url.params["after"] == "0"

    @respx.mock
    def test_get_following_list_with_params(self, fansly_api):
        """Test get_following_list with custom parameters - mocks HTTP at edge"""
        # Mock the Fansly API endpoint for following list (OPTIONS + GET)
        respx.options(
            url__regex=r"https://apiv3\.fansly\.com/api/v1/account/.*/following"
        ).mock(return_value=httpx.Response(200))
        route = respx.get(
            url__regex=r"https://apiv3\.fansly\.com/api/v1/account/.*/following"
        ).mock(
            return_value=httpx.Response(200, json={"success": "true", "response": []})
        )

        fansly_api.get_following_list(
            "user123", limit=10, offset=5, before=1000, after=500
        )

        assert route.called
        request = route.calls.last.request
        assert request.url.params["limit"] == "10"
        assert request.url.params["offset"] == "5"
        assert request.url.params["before"] == "1000"
        assert request.url.params["after"] == "500"

    @respx.mock
    def test_get_account_media(self, fansly_api):
        """Test get_account_media request - mocks HTTP at edge"""
        # Mock the Fansly API endpoint for account media (OPTIONS + GET)
        respx.options("https://apiv3.fansly.com/api/v1/account/media").mock(
            return_value=httpx.Response(200)
        )
        route = respx.get("https://apiv3.fansly.com/api/v1/account/media").mock(
            return_value=httpx.Response(200, json={"success": "true", "response": []})
        )

        fansly_api.get_account_media("media123,media456")

        assert route.called
        request = route.calls.last.request
        # The media IDs should be part of the parameters, not the URL
        assert request.url.params["ids"] == "media123,media456"

    @respx.mock
    def test_get_post(self, fansly_api):
        """Test get_post request - mocks HTTP at edge"""
        # Mock the Fansly API endpoint for post (OPTIONS + GET)
        respx.options("https://apiv3.fansly.com/api/v1/post").mock(
            return_value=httpx.Response(200)
        )
        route = respx.get("https://apiv3.fansly.com/api/v1/post").mock(
            return_value=httpx.Response(200, json={"success": "true", "response": []})
        )

        fansly_api.get_post("post123")

        assert route.called
        request = route.calls.last.request
        assert request.url.params["ids"] == "post123"

    @respx.mock
    def test_get_timeline(self, fansly_api):
        """Test get_timeline request - mocks HTTP at edge"""
        # Mock the Fansly API endpoint for timeline (OPTIONS + GET)
        respx.options(
            url__regex=r"https://apiv3\.fansly\.com/api/v1/timelinenew/.*"
        ).mock(return_value=httpx.Response(200))
        route = respx.get(
            url__regex=r"https://apiv3\.fansly\.com/api/v1/timelinenew/.*"
        ).mock(
            return_value=httpx.Response(200, json={"success": "true", "response": []})
        )

        fansly_api.get_timeline("creator123", "cursor123")

        assert route.called
        request = route.calls.last.request
        assert request.url.params["before"] == "cursor123"
        assert request.url.params["after"] == "0"
        assert request.url.params["wallId"] == ""
        assert request.url.params["contentSearch"] == ""

    @respx.mock
    def test_get_wall_posts(self, fansly_api):
        """Test get_wall_posts request - mocks HTTP at edge"""
        # Mock the Fansly API endpoint for wall posts (OPTIONS + GET)
        respx.options(
            url__regex=r"https://apiv3\.fansly\.com/api/v1/timelinenew/.*"
        ).mock(return_value=httpx.Response(200))
        route = respx.get(
            url__regex=r"https://apiv3\.fansly\.com/api/v1/timelinenew/.*"
        ).mock(
            return_value=httpx.Response(200, json={"success": "true", "response": []})
        )

        fansly_api.get_wall_posts("creator123", "wall123", "cursor456")

        assert route.called
        request = route.calls.last.request
        assert request.url.params["before"] == "cursor456"
        assert request.url.params["after"] == "0"
        assert request.url.params["wallId"] == "wall123"
        assert request.url.params["contentSearch"] == ""

    @respx.mock
    def test_get_group(self, fansly_api):
        """Test get_group request - mocks HTTP at edge"""
        # Mock the Fansly API endpoint for messaging groups (OPTIONS + GET)
        respx.options("https://apiv3.fansly.com/api/v1/messaging/groups").mock(
            return_value=httpx.Response(200)
        )
        route = respx.get("https://apiv3.fansly.com/api/v1/messaging/groups").mock(
            return_value=httpx.Response(200, json={"success": "true", "response": []})
        )

        fansly_api.get_group()

        assert route.called

    @respx.mock
    def test_get_message(self, fansly_api):
        """Test get_message request - mocks HTTP at edge"""
        # Mock the Fansly API endpoint for messages (OPTIONS + GET)
        respx.options("https://apiv3.fansly.com/api/v1/message").mock(
            return_value=httpx.Response(200)
        )
        route = respx.get("https://apiv3.fansly.com/api/v1/message").mock(
            return_value=httpx.Response(200, json={"success": "true", "response": []})
        )

        test_params = {"param1": "value1"}
        fansly_api.get_message(test_params)

        assert route.called
        request = route.calls.last.request
        assert request.url.params["param1"] == "value1"
        assert request.url.params["ngsw-bypass"] == "true"

    @respx.mock
    def test_get_device_id(self, fansly_api):
        """Test get_device_id request - mocks HTTP at edge"""
        # Mock the Fansly API endpoint for device ID (OPTIONS + GET)
        respx.options(url__regex=r"https://apiv3\.fansly\.com/api/v1/device/.*").mock(
            return_value=httpx.Response(200)
        )
        respx.get(url__regex=r"https://apiv3\.fansly\.com/api/v1/device/.*").mock(
            return_value=httpx.Response(
                200,
                json={
                    "success": "true",
                    "response": "test_device_id",
                },
            )
        )

        result = fansly_api.get_device_id()
        assert result == "test_device_id"

    def test_update_device_id_within_timeframe(self, fansly_api):
        """Test update_device_id doesn't update if within time window"""
        original_device_id = fansly_api.device_id
        current_ts = fansly_api.get_timestamp_ms()
        fansly_api.device_id_timestamp = current_ts

        updated_id = fansly_api.update_device_id()
        assert updated_id == original_device_id

    @respx.mock
    def test_update_device_id_expired(self, fansly_api):
        """Test update_device_id updates when timestamp expired - mocks HTTP at edge"""
        # Mock the Fansly API endpoint for device ID (OPTIONS + GET)
        respx.options(url__regex=r"https://apiv3\.fansly\.com/api/v1/device/.*").mock(
            return_value=httpx.Response(200)
        )
        respx.get(url__regex=r"https://apiv3\.fansly\.com/api/v1/device/.*").mock(
            return_value=httpx.Response(
                200,
                json={
                    "success": "true",
                    "response": "new_device_id",
                },
            )
        )

        # Set old timestamp
        fansly_api.device_id_timestamp = 0

        # Mock callback
        mock_callback = MagicMock()
        fansly_api.on_device_updated = mock_callback

        updated_id = fansly_api.update_device_id()
        assert updated_id == "new_device_id"
        mock_callback.assert_called_once()

    @pytest.mark.asyncio
    @respx.mock
    async def test_setup_session_error(self, fansly_api):
        """Test setup_session handles errors - mocks HTTP at edge"""
        # Mock HTTP response failure at edge (OPTIONS + GET)
        respx.options("https://apiv3.fansly.com/api/v1/account/me").mock(
            return_value=httpx.Response(200)
        )
        respx.get("https://apiv3.fansly.com/api/v1/account/me").mock(
            return_value=httpx.Response(401)
        )

        # Mock websocket to raise exception (this is OK - websockets are external boundary)
        with patch("websockets.client.connect") as mock_ws:
            mock_ws.side_effect = Exception("Connection failed")

            with pytest.raises(RuntimeError, match="Error during session setup"):
                await fansly_api.setup_session()

    def test_get_http_headers_with_session(self, fansly_api):
        """Test get_http_headers includes session ID when available"""
        fansly_api.session_id = "test_session"
        headers = fansly_api.get_http_headers(
            url="https://test.com", add_fansly_headers=True
        )
        assert headers["fansly-session-id"] == "test_session"

    def test_validate_json_response_non_200(self, fansly_api):
        """Test validate_json_response with non-200 status"""
        # Use real httpx.Response instead of MagicMock
        mock_response = httpx.Response(404, json={})

        with pytest.raises(RuntimeError, match="Web request failed: 404"):
            fansly_api.validate_json_response(mock_response)
