"""Additional unit tests for FanslyApi class to improve coverage"""

import types
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import respx

from api.fansly import FanslyApi
from api.rate_limiter import RateLimiter
from config.fanslyconfig import FanslyConfig
from tests.fixtures.api.api_fixtures import dump_fansly_calls


class TestFanslyApiAdditional:
    """Additional tests for FanslyApi class to increase coverage."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_get_account_media_response(self, fansly_api):
        """Test get_account_media returns the response from get_with_ngsw"""
        respx.options(url__startswith=f"{FanslyApi.BASE_URL}account/media").mock(
            side_effect=[httpx.Response(200)]
        )

        media_route = respx.get(
            url__startswith=f"{FanslyApi.BASE_URL}account/media"
        ).mock(
            side_effect=[httpx.Response(200, json={"success": True, "response": {}})]
        )

        try:
            result = await fansly_api.get_account_media("media123")
        finally:
            print("****RESPX Call Debugging****")
            for index, call in enumerate(media_route.calls):
                print(f"Call {index}")
                print(f"--request: {call.request}")
                print(f"--response: {call.response}")

        # Verify the result is a real response object
        assert result.status_code == 200
        assert result.reason_phrase == "OK"

    @pytest.mark.asyncio
    @respx.mock
    async def test_account_media_validation_flow(self, fansly_api):
        """Test validation flow for get_account_media when used with get_json_response_contents"""
        respx.options(url__startswith=f"{FanslyApi.BASE_URL}account/media").mock(
            side_effect=[httpx.Response(200)]
        )

        validation_route = respx.get(
            url__startswith=f"{FanslyApi.BASE_URL}account/media"
        ).mock(side_effect=[httpx.Response(200, json={"success": "false"})])

        try:
            response = await fansly_api.get_account_media("media123")

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

    @pytest.mark.asyncio
    @respx.mock
    async def test_get_wall_posts_with_params(self, fansly_api):
        """Test get_wall_posts with custom cursor"""
        respx.options(url__startswith=f"{FanslyApi.BASE_URL}timelinenew").mock(
            side_effect=[httpx.Response(200)]
        )

        route = respx.get(url__startswith=f"{FanslyApi.BASE_URL}timelinenew").mock(
            side_effect=[httpx.Response(200, json={"success": True, "response": []})]
        )

        try:
            await fansly_api.get_wall_posts("creator123", "wall456", "cursor789")
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

    @pytest.mark.asyncio
    @respx.mock
    async def test_get_wall_posts_default_cursor(self, fansly_api):
        """Test get_wall_posts with default cursor"""
        respx.options(url__startswith=f"{FanslyApi.BASE_URL}timelinenew").mock(
            side_effect=[httpx.Response(200)]
        )

        route = respx.get(url__startswith=f"{FanslyApi.BASE_URL}timelinenew").mock(
            side_effect=[httpx.Response(200, json={"success": True, "response": []})]
        )

        try:
            await fansly_api.get_wall_posts("creator123", "wall456")
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

    @pytest.mark.asyncio
    @respx.mock
    async def test_get_client_account_info_with_alternate_token(self, fansly_api):
        """Test get_client_account_info with alternate token"""
        respx.options(url__startswith=f"{FanslyApi.BASE_URL}account/me").mock(
            side_effect=[httpx.Response(200)]
        )

        route = respx.get(url__startswith=f"{FanslyApi.BASE_URL}account/me").mock(
            side_effect=[httpx.Response(200, json={"success": True, "response": {}})]
        )

        try:
            await fansly_api.get_client_account_info(alternate_token="alt_token")  # noqa: S106 # Test fixture token
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
        """Test get_active_session_async handles WebSocket errors.

        Production resolves the WS class via api.fansly.get_websocket_class
        (subprocess gateway) and calls .start_in_thread(). We mock the class
        so its instance raises on start_in_thread — the except handler then
        wraps the failure as 'WebSocket session setup failed'.
        """
        mock_ws_instance = AsyncMock()
        mock_ws_instance.connected = False
        mock_ws_instance.session_id = None
        mock_ws_instance.start_in_thread = MagicMock(
            side_effect=RuntimeError("Connection failed")
        )
        mock_ws_instance.stop_thread = AsyncMock()

        with (
            patch(
                "api.fansly.get_websocket_class",
                return_value=lambda **_kwargs: mock_ws_instance,
            ),
            pytest.raises(
                RuntimeError,
                match=r"WebSocket (authentication failed|session setup failed)",
            ),
        ):
            await fansly_api.get_active_session()

    @pytest.mark.asyncio
    @respx.mock
    async def test_get_with_ngsw_additional_parameters(self, fansly_api):
        """Test get_with_ngsw handles additional parameters"""
        test_url = "https://api.test.com/endpoint?existing=param"
        test_params = {"test": "value", "another": "param"}

        respx.options(url__regex=r"https://api\.test\.com/endpoint.*").mock(
            side_effect=[httpx.Response(200)]
        )

        route = respx.get(url__regex=r"https://api\.test\.com/endpoint.*").mock(
            side_effect=[httpx.Response(200, json={})]
        )

        try:
            await fansly_api.get_with_ngsw(url=test_url, params=test_params)
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

    @pytest.mark.asyncio
    @respx.mock
    async def test_get_with_ngsw_with_cookies(self, fansly_api):
        """Test get_with_ngsw handles cookies"""
        test_url = "https://api.test.com/endpoint"
        test_cookies = {"cookie1": "value1", "cookie2": "value2"}

        respx.options(url__regex=r"https://api\.test\.com/endpoint.*").mock(
            side_effect=[httpx.Response(200)]
        )

        route = respx.get(url__regex=r"https://api\.test\.com/endpoint.*").mock(
            side_effect=[httpx.Response(200, json={})]
        )

        try:
            await fansly_api.get_with_ngsw(url=test_url, cookies=test_cookies)
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

    @pytest.mark.asyncio
    @respx.mock
    async def test_get_with_ngsw_stream_mode(self, fansly_api):
        """Test get_with_ngsw with stream mode"""
        test_url = "https://api.test.com/endpoint"

        respx.options(url__regex=r"https://api\.test\.com/endpoint.*").mock(
            side_effect=[httpx.Response(200)]
        )

        route = respx.get(url__regex=r"https://api\.test\.com/endpoint.*").mock(
            side_effect=[httpx.Response(200, content=b"stream data")]
        )

        try:
            response = await fansly_api.get_with_ngsw(url=test_url, stream=True)
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
    @respx.mock
    async def test_cors_options_request_includes_headers(self, fansly_api):
        """Test cors_options_request includes required headers"""
        test_url = "https://api.test.com/endpoint"

        options_route = respx.options(test_url).mock(side_effect=[httpx.Response(200)])

        try:
            await fansly_api.cors_options_request(test_url)
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

    @pytest.mark.asyncio
    @respx.mock
    async def test_init_without_device_info(self):
        """No device_id args → device_id stays None; first update_device_id() fetches."""
        respx.options(url__regex=r"https://apiv3\.fansly\.com/.*").mock(
            side_effect=[httpx.Response(200)]
        )

        device_route = respx.get(f"{FanslyApi.BASE_URL}device/id").mock(
            side_effect=[
                httpx.Response(
                    200, json={"success": "true", "response": "new_device_id"}
                )
            ]
        )

        api = FanslyApi(
            token="test_token",  # noqa: S106 # Test fixture token
            user_agent="test_user_agent",
            check_key="test_check_key",
        )
        assert api.device_id is None
        assert not device_route.called

        try:
            await api.update_device_id()
        finally:
            dump_fansly_calls(device_route.calls, "test_update_device_id")

        assert device_route.called
        assert api.device_id == "new_device_id"

    @pytest.mark.asyncio
    @respx.mock
    async def test_init_without_device_id_but_with_timestamp(self):
        """timestamp without device_id → device_id stays None until lazy fetch."""
        custom_timestamp = 123456789

        respx.options(url__regex=r"https://apiv3\.fansly\.com/.*").mock(
            side_effect=[httpx.Response(200)]
        )

        device_route = respx.get(f"{FanslyApi.BASE_URL}device/id").mock(
            side_effect=[
                httpx.Response(
                    200, json={"success": "true", "response": "fetched_device_id"}
                )
            ]
        )

        api = FanslyApi(
            token="test_token",  # noqa: S106 # Test fixture token
            user_agent="test_user_agent",
            check_key="test_check_key",
            device_id_timestamp=custom_timestamp,
        )
        assert api.device_id is None
        assert not device_route.called

        try:
            await api.update_device_id()
        finally:
            dump_fansly_calls(
                device_route.calls,
                "test_init_without_device_id_but_with_timestamp",
            )

        assert device_route.called
        assert api.device_id == "fetched_device_id"

    @pytest.mark.asyncio
    @respx.mock
    async def test_init_with_device_id_but_without_timestamp(self):
        """device_id without timestamp → device_id stays None until lazy fetch."""
        custom_device_id = "custom_device_id"

        respx.options(url__regex=r"https://apiv3\.fansly\.com/.*").mock(
            side_effect=[httpx.Response(200)]
        )

        device_route = respx.get(f"{FanslyApi.BASE_URL}device/id").mock(
            side_effect=[
                httpx.Response(
                    200, json={"success": "true", "response": "updated_device_id"}
                )
            ]
        )

        api = FanslyApi(
            token="test_token",  # noqa: S106 # Test fixture token
            user_agent="test_user_agent",
            check_key="test_check_key",
            device_id=custom_device_id,
        )
        assert api.device_id is None
        assert not device_route.called

        try:
            await api.update_device_id()
        finally:
            dump_fansly_calls(
                device_route.calls,
                "test_init_with_device_id_but_without_timestamp",
            )

        assert device_route.called
        assert api.device_id == "updated_device_id"


class TestValidateJsonResponse:
    """Cover validate_json_response edge cases."""

    def test_non_200_status_raises(self, fansly_api):
        """Non-200 response after raise_for_status raises RuntimeError (line 892).

        204 No Content is valid HTTP but not expected by the Fansly API.
        raise_for_status() only raises for 4xx/5xx, so 204 passes through to
        the explicit != 200 check.
        """
        request = httpx.Request("GET", f"{FanslyApi.BASE_URL}test")
        response = httpx.Response(204, json={"success": "true"}, request=request)
        with pytest.raises(RuntimeError, match="Web request failed"):
            fansly_api.validate_json_response(response)

    @pytest.mark.asyncio
    @respx.mock
    async def test_418_teapot_retried(self, fansly_api):
        """HTTP 418 is in the retry status codes list — verifies retry logic."""
        respx.options(url__startswith=f"{FanslyApi.BASE_URL}test").mock(
            side_effect=[httpx.Response(200)]
        )
        route = respx.get(url__startswith=f"{FanslyApi.BASE_URL}test").mock(
            side_effect=[
                httpx.Response(418),
                httpx.Response(
                    200,
                    json={"success": "true", "response": {"data": "ok"}},
                ),
            ]
        )

        try:
            response = await fansly_api.get_with_ngsw(f"{FanslyApi.BASE_URL}test")
            assert response.status_code == 200
        finally:
            dump_fansly_calls(route.calls)


class TestConvertIdsToInt:
    """Cover convert_ids_to_int edge cases."""

    def test_non_numeric_id_string_unchanged(self):
        """Non-numeric ID string falls back to original value (lines 922-923)."""
        data = {"id": "not_a_number", "name": "test"}
        result = FanslyApi.convert_ids_to_int(data)
        assert result["id"] == "not_a_number"
        assert result["name"] == "test"

    def test_nested_list_of_dicts(self):
        """Nested list of dicts has IDs converted recursively."""
        data = [{"id": "123", "parentId": "456"}]
        result = FanslyApi.convert_ids_to_int(data)
        assert result[0]["id"] == 123
        assert result[0]["parentId"] == 456

    def test_ids_list_field(self):
        """Fields ending with 'Ids' have list items converted."""
        data = {"accountIds": ["111", "222", 333]}
        result = FanslyApi.convert_ids_to_int(data)
        assert result["accountIds"] == [111, 222, 333]


class TestWebSocketHandlers:
    """Cover WebSocket callback handlers (lines 995-1029)."""

    def test_handle_unauthorized_resets_session(self, fansly_api):
        """401 handler resets session_id (lines 995-996)."""
        fansly_api.session_id = "active_session"
        fansly_api._handle_websocket_unauthorized()
        assert fansly_api.session_id == "null"

    def test_handle_rate_limited_with_limiter(self, fansly_api):
        """429 handler triggers rate limiter backoff (lines 1006-1010)."""
        config = FanslyConfig(program_version="1.0.0")
        fansly_api.rate_limiter = RateLimiter(config)

        fansly_api._handle_websocket_rate_limited()
        # Should record the 429 response without raising

    def test_handle_rate_limited_no_limiter(self, fansly_api):
        """429 handler without rate limiter logs warning (lines 1011-1014)."""
        fansly_api.rate_limiter = None
        fansly_api._handle_websocket_rate_limited()
        # Should log warning without raising

    @pytest.mark.asyncio
    async def test_close_websocket_no_client(self, fansly_api):
        """Closing with no websocket client is a no-op (line 1022)."""
        fansly_api._websocket_client = None
        await fansly_api.close_websocket()
        assert fansly_api._websocket_client is None

    @pytest.mark.asyncio
    async def test_close_websocket_with_client(self, fansly_api):
        """Closing with websocket client calls stop_thread and clears ref."""
        stop_called = []

        async def fake_stop_thread():
            stop_called.append(True)

        fansly_api._websocket_client = types.SimpleNamespace(
            stop_thread=fake_stop_thread,
            connected=True,
            session_id="test",
        )
        await fansly_api.close_websocket()
        assert fansly_api._websocket_client is None
        assert len(stop_called) == 1

    @pytest.mark.asyncio
    async def test_close_websocket_stop_raises(self, fansly_api):
        """stop_thread raising exception is handled gracefully."""

        async def failing_stop_thread():
            raise RuntimeError("stop failed")

        fansly_api._websocket_client = types.SimpleNamespace(
            stop_thread=failing_stop_thread,
            connected=False,
        )
        await fansly_api.close_websocket()
        assert fansly_api._websocket_client is None


class TestGetClientUserName:
    """Cover get_client_user_name edge case (line 965)."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_empty_username_returns_none(self, fansly_api):
        """Empty username in API response returns None."""
        respx.options(url__startswith=f"{FanslyApi.BASE_URL}account/me").mock(
            side_effect=[httpx.Response(200)]
        )
        route = respx.get(url__startswith=f"{FanslyApi.BASE_URL}account/me").mock(
            side_effect=[
                httpx.Response(
                    200,
                    json={
                        "success": "true",
                        "response": {
                            "account": {"username": ""},
                        },
                    },
                )
            ]
        )
        try:
            result = await fansly_api.get_client_user_name()
            assert result is None
        finally:
            dump_fansly_calls(route.calls)
