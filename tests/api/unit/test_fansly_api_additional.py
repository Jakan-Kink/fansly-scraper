"""Additional unit tests for FanslyApi class to improve coverage"""

from unittest.mock import AsyncMock, patch

import httpx
import pytest
import respx

from api.fansly import FanslyApi


class TestFanslyApiAdditional:
    """Additional tests for FanslyApi class to increase coverage."""

    @respx.mock
    def test_get_account_media_response(self, fansly_api):
        """Test get_account_media returns the response from get_with_ngsw"""
        # Mock CORS OPTIONS request
        respx.options(url__regex=r".*account/media.*").mock(
            side_effect=[httpx.Response(200)]
        )

        # Mock HTTP response at the edge
        media_route = respx.get(url__regex=r".*account/media.*").mock(
            side_effect=[httpx.Response(200, json={"success": True, "response": {}})]
        )

        try:
            result = fansly_api.get_account_media("media123")
        finally:
            print("****RESPX Call Debugging****")
            for index, call in enumerate(media_route.calls):
                print(f"Call {index}")
                print(f"--request: {call.request}")
                print(f"--response: {call.response}")

        # Verify the result is a real response object
        assert result.status_code == 200
        assert result.reason_phrase == "OK"

    @respx.mock
    def test_account_media_validation_flow(self, fansly_api):
        """Test validation flow for get_account_media when used with get_json_response_contents"""
        # Mock CORS OPTIONS request
        respx.options(url__regex=r".*account/media.*").mock(
            side_effect=[httpx.Response(200)]
        )

        # Mock invalid API response at the edge
        validation_route = respx.get(url__regex=r".*account/media.*").mock(
            side_effect=[httpx.Response(200, json={"success": "false"})]
        )

        try:
            # First get the API response
            response = fansly_api.get_account_media("media123")

            # Then validate it - this would be done by consumers of the API
            with pytest.raises(RuntimeError, match="Invalid or failed JSON response"):
                fansly_api.get_json_response_contents(response)
        finally:
            print("****RESPX Call Debugging****")
            for index, call in enumerate(validation_route.calls):
                print(f"Call {index}")
                print(f"--request: {call.request}")
                print(f"--response: {call.response}")

    def test_get_json_response_contents_error(self, fansly_api):
        """Test get_json_response_contents with invalid JSON response"""
        # Create a real httpx.Response with invalid data and a request
        request = httpx.Request("GET", "https://api.test.com")
        mock_response = httpx.Response(200, json={"success": "false"}, request=request)

        with pytest.raises(RuntimeError):
            fansly_api.get_json_response_contents(mock_response)

    @respx.mock
    def test_get_wall_posts_with_params(self, fansly_api):
        """Test get_wall_posts with custom cursor"""
        # Mock CORS OPTIONS request
        respx.options(url__regex=r".*timelinenew.*").mock(
            side_effect=[httpx.Response(200)]
        )

        # Capture the request to verify parameters
        route = respx.get(url__regex=r".*timelinenew.*").mock(
            side_effect=[httpx.Response(200, json={"success": True, "response": []})]
        )

        try:
            fansly_api.get_wall_posts("creator123", "wall456", "cursor789")
        finally:
            print("****RESPX Call Debugging****")
            for index, call in enumerate(route.calls):
                print(f"Call {index}")
                print(f"--request: {call.request}")
                print(f"--response: {call.response}")

        # Verify the request was made with correct parameters
        assert route.called
        request = route.calls.last.request
        params = dict(request.url.params)
        assert params["before"] == "cursor789"
        assert params["after"] == "0"
        assert params["wallId"] == "wall456"

    @respx.mock
    def test_get_wall_posts_default_cursor(self, fansly_api):
        """Test get_wall_posts with default cursor"""
        # Mock CORS OPTIONS request
        respx.options(url__regex=r".*timelinenew.*").mock(
            side_effect=[httpx.Response(200)]
        )

        # Capture the request to verify parameters
        route = respx.get(url__regex=r".*timelinenew.*").mock(
            side_effect=[httpx.Response(200, json={"success": True, "response": []})]
        )

        try:
            fansly_api.get_wall_posts("creator123", "wall456")
        finally:
            print("****RESPX Call Debugging****")
            for index, call in enumerate(route.calls):
                print(f"Call {index}")
                print(f"--request: {call.request}")
                print(f"--response: {call.response}")

        # Verify the request was made with correct parameters
        assert route.called
        request = route.calls.last.request
        params = dict(request.url.params)
        assert params["before"] == "0"  # Default cursor
        assert params["wallId"] == "wall456"

    @respx.mock
    def test_get_client_account_info_with_alternate_token(self, fansly_api):
        """Test get_client_account_info with alternate token"""
        # Mock CORS OPTIONS request
        respx.options(url__regex=r".*account/me.*").mock(
            side_effect=[httpx.Response(200)]
        )

        # Capture the request to verify headers
        route = respx.get(url__regex=r".*account/me.*").mock(
            side_effect=[httpx.Response(200, json={"success": True, "response": {}})]
        )

        try:
            fansly_api.get_client_account_info(alternate_token="alt_token")  # noqa: S106 # Test fixture token
        finally:
            print("****RESPX Call Debugging****")
            for index, call in enumerate(route.calls):
                print(f"Call {index}")
                print(f"--request: {call.request}")
                print(f"--response: {call.response}")

        # Verify the request was made with alternate token in headers
        assert route.called
        request = route.calls.last.request
        assert "alt_token" in request.headers["authorization"]

    @pytest.mark.asyncio
    async def test_get_active_session_async_error(self, fansly_api):
        """Test get_active_session_async handles WebSocket errors"""
        # Create a mock websocket instance with proper async methods
        mock_ws_instance = AsyncMock()
        mock_ws_instance.__aenter__.return_value = mock_ws_instance

        # Return error response from WebSocket
        mock_ws_instance.recv.return_value = '{"t":0,"d":"Error message"}'

        # Mock the websocket connection
        with (
            patch("websockets.client.connect", return_value=mock_ws_instance),
            pytest.raises(
                RuntimeError,
                match=r"WebSocket (authentication failed|session setup failed)",
            ),
        ):
            await fansly_api.get_active_session_async()

    @respx.mock
    def test_get_with_ngsw_additional_parameters(self, fansly_api):
        """Test get_with_ngsw handles additional parameters"""
        test_url = "https://api.test.com/endpoint?existing=param"
        test_params = {"test": "value", "another": "param"}

        # Mock CORS OPTIONS request
        respx.options(url__regex=r"https://api\.test\.com/endpoint.*").mock(
            side_effect=[httpx.Response(200)]
        )

        # Capture the request to verify all parameters
        route = respx.get(url__regex=r"https://api\.test\.com/endpoint.*").mock(
            side_effect=[httpx.Response(200, json={})]
        )

        try:
            fansly_api.get_with_ngsw(url=test_url, params=test_params)
        finally:
            print("****RESPX Call Debugging****")
            for index, call in enumerate(route.calls):
                print(f"Call {index}")
                print(f"--request: {call.request}")
                print(f"--response: {call.response}")

        # Verify the request was made with correct parameters
        assert route.called
        request = route.calls.last.request
        params = dict(request.url.params)

        # Should include existing URL params, ngsw params, and additional params
        assert params["existing"] == "param"  # From URL
        assert params["ngsw-bypass"] == "true"  # From ngsw_params
        assert params["test"] == "value"  # From additional params
        assert params["another"] == "param"  # From additional params

    @respx.mock
    def test_get_with_ngsw_with_cookies(self, fansly_api):
        """Test get_with_ngsw handles cookies"""
        test_url = "https://api.test.com/endpoint"
        test_cookies = {"cookie1": "value1", "cookie2": "value2"}

        # Mock CORS OPTIONS request
        respx.options(url__regex=r"https://api\.test\.com/endpoint.*").mock(
            side_effect=[httpx.Response(200)]
        )

        # Capture the request to verify cookies
        route = respx.get(url__regex=r"https://api\.test\.com/endpoint.*").mock(
            side_effect=[httpx.Response(200, json={})]
        )

        try:
            fansly_api.get_with_ngsw(url=test_url, cookies=test_cookies)
        finally:
            print("****RESPX Call Debugging****")
            for index, call in enumerate(route.calls):
                print(f"Call {index}")
                print(f"--request: {call.request}")
                print(f"--response: {call.response}")

        # Verify the request was made with cookies
        assert route.called
        request = route.calls.last.request
        # httpx includes cookies in the request headers
        assert request.headers.get("cookie") is not None

    @respx.mock
    def test_get_with_ngsw_stream_mode(self, fansly_api):
        """Test get_with_ngsw with stream mode"""
        test_url = "https://api.test.com/endpoint"

        # Mock CORS OPTIONS request
        respx.options(url__regex=r"https://api\.test\.com/endpoint.*").mock(
            side_effect=[httpx.Response(200)]
        )

        # Mock HTTP response for stream mode
        route = respx.get(url__regex=r"https://api\.test\.com/endpoint.*").mock(
            side_effect=[httpx.Response(200, content=b"stream data")]
        )

        try:
            response = fansly_api.get_with_ngsw(url=test_url, stream=True)
        finally:
            print("****RESPX Call Debugging****")
            for index, call in enumerate(route.calls):
                print(f"Call {index}")
                print(f"--request: {call.request}")
                print(f"--response: {call.response}")

        # Verify the request was made
        assert route.called
        assert response.status_code == 200

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

    @respx.mock
    def test_cors_options_request_includes_headers(self, fansly_api):
        """Test cors_options_request includes required headers"""
        test_url = "https://api.test.com/endpoint"

        # Mock CORS OPTIONS request
        options_route = respx.options(test_url).mock(side_effect=[httpx.Response(200)])

        try:
            fansly_api.cors_options_request(test_url)
        finally:
            print("****RESPX Call Debugging****")
            for index, call in enumerate(options_route.calls):
                print(f"Call {index}")
                print(f"--request: {call.request}")
                print(f"--response: {call.response}")

        # Verify the request was made
        assert options_route.called
        request = options_route.calls.last.request

        # Verify headers
        assert "origin" in request.headers
        assert "access-control-request-method" in request.headers
        assert "access-control-request-headers" in request.headers

        # Verify it contains required Fansly headers
        assert (
            "authorization,fansly-client-check,fansly-client-id,fansly-client-ts,fansly-session-id"
            in request.headers["access-control-request-headers"]
        )

    @respx.mock
    def test_init_without_device_info(self):
        """Test initialization without device ID and timestamp parameters."""
        # Mock CORS OPTIONS and device ID endpoint at HTTP boundary
        respx.options(url__regex=r"https://apiv3\.fansly\.com/.*").mock(
            side_effect=[httpx.Response(200)]
        )

        device_route = respx.get("https://apiv3.fansly.com/api/v1/device/id").mock(
            side_effect=[
                httpx.Response(
                    200, json={"success": "true", "response": "new_device_id"}
                )
            ]
        )

        try:
            # Call the constructor without device_id and device_id_timestamp
            api = FanslyApi(
                token="test_token",  # noqa: S106 # Test fixture token
                user_agent="test_user_agent",
                check_key="test_check_key",
            )
        finally:
            print("****RESPX Call Debugging****")
            for index, call in enumerate(device_route.calls):
                print(f"Call {index}")
                print(f"--request: {call.request}")
                print(f"--response: {call.response}")

        # Verify the device ID endpoint was called
        assert device_route.called

        # Verify the device ID was fetched
        assert api.device_id == "new_device_id"

    @respx.mock
    def test_init_without_device_id_but_with_timestamp(self):
        """Test initialization with only timestamp but no device ID."""
        custom_timestamp = 123456789

        # Mock CORS OPTIONS and device ID endpoint at HTTP boundary
        respx.options(url__regex=r"https://apiv3\.fansly\.com/.*").mock(
            side_effect=[httpx.Response(200)]
        )

        device_route = respx.get("https://apiv3.fansly.com/api/v1/device/id").mock(
            side_effect=[
                httpx.Response(
                    200, json={"success": "true", "response": "fetched_device_id"}
                )
            ]
        )

        try:
            # Call the constructor with device_id_timestamp but without device_id
            api = FanslyApi(
                token="test_token",  # noqa: S106 # Test fixture token
                user_agent="test_user_agent",
                check_key="test_check_key",
                device_id_timestamp=custom_timestamp,
            )
        finally:
            print("****RESPX Call Debugging****")
            for index, call in enumerate(device_route.calls):
                print(f"Call {index}")
                print(f"--request: {call.request}")
                print(f"--response: {call.response}")

        # Verify the device ID endpoint was called (since device_id wasn't provided)
        assert device_route.called

        # Verify the device ID was fetched
        assert api.device_id == "fetched_device_id"

    @respx.mock
    def test_init_with_device_id_but_without_timestamp(self):
        """Test initialization with only device ID but no timestamp."""
        custom_device_id = "custom_device_id"

        # Mock CORS OPTIONS and device ID endpoint at HTTP boundary
        respx.options(url__regex=r"https://apiv3\.fansly\.com/.*").mock(
            side_effect=[httpx.Response(200)]
        )

        device_route = respx.get("https://apiv3.fansly.com/api/v1/device/id").mock(
            side_effect=[
                httpx.Response(
                    200, json={"success": "true", "response": "updated_device_id"}
                )
            ]
        )

        try:
            # Call the constructor with device_id but without device_id_timestamp
            api = FanslyApi(
                token="test_token",  # noqa: S106 # Test fixture token
                user_agent="test_user_agent",
                check_key="test_check_key",
                device_id=custom_device_id,
            )
        finally:
            print("****RESPX Call Debugging****")
            for index, call in enumerate(device_route.calls):
                print(f"Call {index}")
                print(f"--request: {call.request}")
                print(f"--response: {call.response}")

        # Verify the device ID endpoint was called (since timestamp wasn't provided)
        assert device_route.called

        # Verify the device ID was updated
        assert api.device_id == "updated_device_id"
