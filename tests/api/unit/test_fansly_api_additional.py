"""Additional unit tests for FanslyApi class to improve coverage"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from api.fansly import FanslyApi


class TestFanslyApiAdditional:
    """Additional tests for FanslyApi class to increase coverage."""

    def test_get_account_media_response(self, fansly_api, mock_http_session):
        """Test get_account_media returns the response from get_with_ngsw"""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.reason_phrase = "OK"
        mock_http_session.get.return_value = mock_response

        result = fansly_api.get_account_media("media123")

        # Verify the result is the response object
        assert result is mock_response

        # Verify the correct URL was used
        args = mock_http_session.get.call_args
        assert "account/media" in args[1]["url"]
        assert args[1]["params"]["ids"] == "media123"

    def test_account_media_validation_flow(self, fansly_api, mock_http_session):
        """Test validation flow for get_account_media when used with get_json_response_contents"""
        # This test shows how validation actually works in the typical API usage flow
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.reason_phrase = "OK"
        mock_response.json.return_value = {"success": "false"}
        mock_http_session.get.return_value = mock_response

        # First get the API response
        response = fansly_api.get_account_media("media123")

        # Then validate it - this would be done by consumers of the API
        with pytest.raises(RuntimeError, match="Invalid or failed JSON response"):
            fansly_api.get_json_response_contents(response)

    def test_get_json_response_contents_error(self, fansly_api):
        """Test get_json_response_contents with invalid JSON response"""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"success": "false"}

        with pytest.raises(RuntimeError, match="Invalid or failed JSON response"):
            fansly_api.get_json_response_contents(mock_response)

    def test_get_wall_posts_with_params(self, fansly_api, mock_http_session):
        """Test get_wall_posts with custom cursor"""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.reason_phrase = "OK"
        mock_http_session.get.return_value = mock_response

        fansly_api.get_wall_posts("creator123", "wall456", "cursor789")

        args = mock_http_session.get.call_args
        params = args[1]["params"]
        assert params["before"] == "cursor789"
        assert params["after"] == "0"
        assert params["wallId"] == "wall456"

    def test_get_wall_posts_default_cursor(self, fansly_api, mock_http_session):
        """Test get_wall_posts with default cursor"""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.reason_phrase = "OK"
        mock_http_session.get.return_value = mock_response

        fansly_api.get_wall_posts("creator123", "wall456")

        args = mock_http_session.get.call_args
        params = args[1]["params"]
        assert params["before"] == "0"  # Default cursor
        assert params["wallId"] == "wall456"

    def test_get_client_account_info_with_alternate_token(
        self, fansly_api, mock_http_session
    ):
        """Test get_client_account_info with alternate token"""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.reason_phrase = "OK"
        mock_http_session.get.return_value = mock_response

        fansly_api.get_client_account_info(alternate_token="alt_token")

        args = mock_http_session.get.call_args
        assert "alt_token" in args[1]["headers"]["authorization"]

    @pytest.mark.asyncio
    async def test_get_active_session_async_error(self, fansly_api):
        """Test get_active_session_async handles WebSocket errors"""
        # Create a mock websocket instance with proper async methods
        mock_ws_instance = AsyncMock()
        mock_ws_instance.__aenter__.return_value = mock_ws_instance

        # Return error response from WebSocket
        mock_ws_instance.recv.return_value = '{"t":0,"d":"Error message"}'

        # Mock the websocket connection
        with patch("websockets.client.connect", return_value=mock_ws_instance):
            with pytest.raises(RuntimeError, match="WebSocket error"):
                await fansly_api.get_active_session_async()

    def test_get_with_ngsw_additional_parameters(self, fansly_api, mock_http_session):
        """Test get_with_ngsw handles additional parameters"""
        test_url = "https://api.test.com/endpoint?existing=param"
        test_params = {"test": "value", "another": "param"}

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.reason_phrase = "OK"
        mock_http_session.get.return_value = mock_response

        fansly_api.get_with_ngsw(url=test_url, params=test_params)

        args = mock_http_session.get.call_args
        params = args[1]["params"]

        # Should include existing URL params, ngsw params, and additional params
        assert params["existing"] == "param"  # From URL
        assert params["ngsw-bypass"] == "true"  # From ngsw_params
        assert params["test"] == "value"  # From additional params
        assert params["another"] == "param"  # From additional params

    def test_get_with_ngsw_with_cookies(self, fansly_api, mock_http_session):
        """Test get_with_ngsw handles cookies"""
        test_url = "https://api.test.com/endpoint"
        test_cookies = {"cookie1": "value1", "cookie2": "value2"}

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.reason_phrase = "OK"
        mock_http_session.get.return_value = mock_response

        fansly_api.get_with_ngsw(url=test_url, cookies=test_cookies)

        args = mock_http_session.get.call_args
        assert args[1]["cookies"] == test_cookies

    def test_get_with_ngsw_stream_mode(self, fansly_api, mock_http_session):
        """Test get_with_ngsw with stream mode"""
        test_url = "https://api.test.com/endpoint"

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.reason_phrase = "OK"
        mock_http_session.build_request = MagicMock()
        mock_http_session.send.return_value = mock_response

        fansly_api.get_with_ngsw(url=test_url, stream=True)

        # For stream mode, it uses build_request and send instead of get
        mock_http_session.build_request.assert_called_once()
        mock_http_session.send.assert_called_once()

    def test_update_client_timestamp_no_attribute(self, fansly_api):
        """Test update_client_timestamp when attribute doesn't exist"""
        # Remove client_timestamp attribute
        delattr(fansly_api, "client_timestamp")

        # Should not raise an error
        fansly_api.update_client_timestamp()

    def test_imul32_overflow(self, fansly_api):
        """Test imul32 handles 32-bit overflow"""
        # Test with values that will overflow 32 bits
        result = fansly_api.imul32(0x7FFFFFFF, 2)  # Max 32-bit signed int * 2

        # Should handle overflow and wrap around
        assert result != 0x7FFFFFFF * 2
        assert result == -2  # Overflow result

    def test_rshift32_with_positive_number(self, fansly_api):
        """Test rshift32 with positive number"""
        result = fansly_api.rshift32(32, 2)
        assert result == 8  # 32 >> 2 = 8

    def test_rshift32_with_negative_number(self, fansly_api):
        """Test rshift32 with negative number"""
        result = fansly_api.rshift32(-32, 2)
        # For negative numbers, it adds int_max_value before shifting
        assert result != -8  # Not regular right shift
        # Should be ((-32 + 2^32) >> 2) instead

    def test_cyrb53_with_different_seeds(self, fansly_api):
        """Test cyrb53 hash function with different seeds"""
        input_str = "test_input"

        # Same input with different seeds should produce different results
        hash1 = fansly_api.cyrb53(input_str, seed=0)
        hash2 = fansly_api.cyrb53(input_str, seed=1)
        hash3 = fansly_api.cyrb53(input_str, seed=42)

        assert hash1 != hash2
        assert hash1 != hash3
        assert hash2 != hash3

    @pytest.mark.asyncio
    async def test_get_active_session(self, fansly_api):
        """Test get_active_session calls get_active_session_async"""
        with patch.object(
            fansly_api,
            "get_active_session_async",
            new=AsyncMock(return_value="test_session"),
        ) as mock_async:
            result = await fansly_api.get_active_session()
            mock_async.assert_called_once()
            assert result == "test_session"

    def test_cors_options_request_includes_headers(self, fansly_api):
        """Test cors_options_request includes required headers"""
        test_url = "https://api.test.com/endpoint"

        fansly_api.cors_options_request(test_url)

        call_args = fansly_api.http_session.options.call_args
        headers = call_args[1]["headers"]

        assert "Origin" in headers
        assert "Access-Control-Request-Method" in headers
        assert "Access-Control-Request-Headers" in headers

        # Verify it contains required Fansly headers
        assert (
            "authorization,fansly-client-check,fansly-client-id,fansly-client-ts,fansly-session-id"
            in headers["Access-Control-Request-Headers"]
        )

    def test_init_without_device_info(self):
        """Test initialization without device ID and timestamp parameters."""
        # Mock the update_device_id method to avoid real API calls
        with patch.object(FanslyApi, "update_device_id") as mock_update_device_id:
            # Call the constructor without device_id and device_id_timestamp
            api = FanslyApi(
                token="test_token",
                user_agent="test_user_agent",
                check_key="test_check_key",
            )

            # Verify the default timestamp is used (January 1st, 1990 at midnight)
            expected_timestamp = int(datetime(1990, 1, 1, 0, 0, tzinfo=UTC).timestamp())
            assert api.device_id_timestamp == expected_timestamp

            # Verify that update_device_id was called to fetch a new device ID
            mock_update_device_id.assert_called_once()

    def test_init_without_device_id_but_with_timestamp(self):
        """Test initialization with only timestamp but no device ID."""
        custom_timestamp = 123456789

        # Mock the update_device_id method to avoid real API calls
        with patch.object(FanslyApi, "update_device_id") as mock_update_device_id:
            # Call the constructor with device_id_timestamp but without device_id
            api = FanslyApi(
                token="test_token",
                user_agent="test_user_agent",
                check_key="test_check_key",
                device_id_timestamp=custom_timestamp,
            )

            # The API should still use the default timestamp since both parameters
            # need to be provided to skip the update_device_id call
            expected_timestamp = int(datetime(1990, 1, 1, 0, 0, tzinfo=UTC).timestamp())
            assert api.device_id_timestamp == expected_timestamp

            # Verify that update_device_id was called
            mock_update_device_id.assert_called_once()

    def test_init_with_device_id_but_without_timestamp(self):
        """Test initialization with only device ID but no timestamp."""
        custom_device_id = "custom_device_id"

        # Mock the update_device_id method to avoid real API calls
        with patch.object(FanslyApi, "update_device_id") as mock_update_device_id:
            # Call the constructor with device_id but without device_id_timestamp
            api = FanslyApi(
                token="test_token",
                user_agent="test_user_agent",
                check_key="test_check_key",
                device_id=custom_device_id,
            )

            # The API should still use the default timestamp since both parameters
            # need to be provided to skip the update_device_id call
            expected_timestamp = int(datetime(1990, 1, 1, 0, 0, tzinfo=UTC).timestamp())
            assert api.device_id_timestamp == expected_timestamp

            # Verify that update_device_id was called
            mock_update_device_id.assert_called_once()
