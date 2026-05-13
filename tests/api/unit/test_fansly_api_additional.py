"""Additional unit tests for FanslyApi class to improve coverage.

Fixture choice:

- ``fansly_api_factory`` — bare ``FanslyApi`` with no bootstrap. For sync
  property/method tests and tests mutating pre-bootstrap state
  (``_websocket_client``, ``rate_limiter``, ``session_id``) or patching
  ``FanslyWebSocket`` for setup-path coverage.
- ``respx_fansly_api`` — fully-bootstrapped ``FanslyApi``. For async
  tests exercising apiv3.fansly.com HTTP endpoints.

Endpoint URLs use production constants on ``FanslyApi`` (e.g.
``api.ACCOUNT_ME_ENDPOINT``, ``api.TIMELINE_NEW_ENDPOINT.format(creator_id)``)
so production URL changes propagate without per-test churn.
"""

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
    async def test_get_account_media_response(self, respx_fansly_api):
        """Test get_account_media returns the response from get_with_ngsw"""
        media_route = respx.get(
            url__startswith=respx_fansly_api.ACCOUNT_MEDIA_ENDPOINT.format("")
        ).mock(
            side_effect=[httpx.Response(200, json={"success": True, "response": {}})]
        )

        try:
            result = await respx_fansly_api.get_account_media("media123")
        finally:
            dump_fansly_calls(media_route.calls, "test_get_account_media_response")

        assert result.status_code == 200
        assert result.reason_phrase == "OK"

    @pytest.mark.asyncio
    async def test_account_media_validation_flow(self, respx_fansly_api):
        """Test validation flow for get_account_media + get_json_response_contents"""
        validation_route = respx.get(
            url__startswith=respx_fansly_api.ACCOUNT_MEDIA_ENDPOINT.format("")
        ).mock(side_effect=[httpx.Response(200, json={"success": "false"})])

        try:
            response = await respx_fansly_api.get_account_media("media123")
            with pytest.raises(RuntimeError, match="Invalid or failed JSON response"):
                respx_fansly_api.get_json_response_contents(response)
        finally:
            dump_fansly_calls(
                validation_route.calls, "test_account_media_validation_flow"
            )

    def test_get_json_response_contents_error(self, fansly_api_factory):
        """Test get_json_response_contents with invalid JSON response"""
        api = fansly_api_factory()
        request = httpx.Request("GET", "https://api.test.com")
        mock_response = httpx.Response(200, json={"success": "false"}, request=request)

        with pytest.raises(RuntimeError):
            api.get_json_response_contents(mock_response)

    @pytest.mark.asyncio
    async def test_get_wall_posts_with_params(self, respx_fansly_api):
        """Test get_wall_posts with custom cursor"""
        route = respx.get(
            url__startswith=respx_fansly_api.TIMELINE_NEW_ENDPOINT.format("creator123")
        ).mock(
            side_effect=[httpx.Response(200, json={"success": True, "response": []})]
        )

        try:
            await respx_fansly_api.get_wall_posts("creator123", "wall456", "cursor789")
        finally:
            dump_fansly_calls(route.calls, "test_get_wall_posts_with_params")

        assert route.called
        request = route.calls.last.request
        assert "creator123" in request.url.path
        params = dict(request.url.params)
        assert params["before"] == "cursor789"
        assert params["after"] == "0"
        assert params["wallId"] == "wall456"

    @pytest.mark.asyncio
    async def test_get_wall_posts_default_cursor(self, respx_fansly_api):
        """Test get_wall_posts with default cursor"""
        route = respx.get(
            url__startswith=respx_fansly_api.TIMELINE_NEW_ENDPOINT.format("creator123")
        ).mock(
            side_effect=[httpx.Response(200, json={"success": True, "response": []})]
        )

        try:
            await respx_fansly_api.get_wall_posts("creator123", "wall456")
        finally:
            dump_fansly_calls(route.calls, "test_get_wall_posts_default_cursor")

        assert route.called
        request = route.calls.last.request
        assert "creator123" in request.url.path
        params = dict(request.url.params)
        assert params["before"] == "0"
        assert params["wallId"] == "wall456"

    @pytest.mark.asyncio
    async def test_get_client_account_info_with_alternate_token(self, respx_fansly_api):
        """Test get_client_account_info with alternate token"""
        route = respx.get(respx_fansly_api.ACCOUNT_ME_ENDPOINT).mock(
            side_effect=[httpx.Response(200, json={"success": True, "response": {}})]
        )

        try:
            await respx_fansly_api.get_client_account_info(alternate_token="alt_token")  # noqa: S106 # Test fixture token
        finally:
            dump_fansly_calls(
                route.calls, "test_get_client_account_info_with_alternate_token"
            )

        assert route.called
        request = route.calls.last.request
        assert "alt_token" in request.headers["authorization"]

    @pytest.mark.asyncio
    async def test_get_active_session_async_error(self, fansly_api_factory):
        """Test get_active_session_async handles WebSocket errors.

        Uses fansly_api_factory because respx_fansly_api's bootstrap calls
        setup_session via setup_api — patching FanslyWebSocket to fail would
        crash fixture setup before the test body runs.
        """
        api = fansly_api_factory()
        mock_ws_instance = AsyncMock()
        mock_ws_instance.connected = False
        mock_ws_instance.session_id = None
        mock_ws_instance.start_in_thread = MagicMock(
            side_effect=RuntimeError("Connection failed")
        )
        mock_ws_instance.stop_thread = AsyncMock()

        with (
            patch(
                "api.fansly.FanslyWebSocket",
                new=lambda **_kwargs: mock_ws_instance,
            ),
            pytest.raises(
                RuntimeError,
                match=r"WebSocket (authentication failed|session setup failed)",
            ),
        ):
            await api.get_active_session()

    @pytest.mark.asyncio
    async def test_get_with_ngsw_additional_parameters(self, respx_fansly_api):
        """Test get_with_ngsw handles additional parameters"""
        test_url = f"{respx_fansly_api.BASE_URL}custom_endpoint?existing=param"
        test_params = {"test": "value", "another": "param"}

        route = respx.get(
            url__startswith=f"{respx_fansly_api.BASE_URL}custom_endpoint"
        ).mock(side_effect=[httpx.Response(200, json={})])

        try:
            await respx_fansly_api.get_with_ngsw(url=test_url, params=test_params)
        finally:
            dump_fansly_calls(route.calls, "test_get_with_ngsw_additional_parameters")

        assert route.called
        request = route.calls.last.request
        params = dict(request.url.params)
        assert params["existing"] == "param"
        assert params["ngsw-bypass"] == "true"
        assert params["test"] == "value"
        assert params["another"] == "param"

    @pytest.mark.asyncio
    async def test_get_with_ngsw_with_cookies(self, respx_fansly_api):
        """Test get_with_ngsw handles cookies"""
        test_url = f"{respx_fansly_api.BASE_URL}custom_endpoint"
        test_cookies = {"cookie1": "value1", "cookie2": "value2"}

        route = respx.get(
            url__startswith=f"{respx_fansly_api.BASE_URL}custom_endpoint"
        ).mock(side_effect=[httpx.Response(200, json={})])

        try:
            await respx_fansly_api.get_with_ngsw(url=test_url, cookies=test_cookies)
        finally:
            dump_fansly_calls(route.calls, "test_get_with_ngsw_with_cookies")

        assert route.called
        request = route.calls.last.request
        assert request.headers.get("cookie") is not None

    @pytest.mark.asyncio
    async def test_get_with_ngsw_stream_mode(self, respx_fansly_api):
        """Test get_with_ngsw with stream mode"""
        test_url = f"{respx_fansly_api.BASE_URL}custom_endpoint"

        route = respx.get(
            url__startswith=f"{respx_fansly_api.BASE_URL}custom_endpoint"
        ).mock(side_effect=[httpx.Response(200, content=b"stream data")])

        try:
            response = await respx_fansly_api.get_with_ngsw(url=test_url, stream=True)
        finally:
            dump_fansly_calls(route.calls, "test_get_with_ngsw_stream_mode")

        assert route.called
        assert response.status_code == 200

    def test_update_client_timestamp_no_attribute(self, fansly_api_factory):
        """Test update_client_timestamp when attribute doesn't exist"""
        api = fansly_api_factory()
        delattr(api, "client_timestamp")
        api.update_client_timestamp()

    def test_imul32_overflow(self, fansly_api_factory):
        """Test imul32 handles 32-bit overflow"""
        api = fansly_api_factory()
        result = api.imul32(0x7FFFFFFF, 2)
        assert result != 0x7FFFFFFF * 2
        assert result == -2

    def test_rshift32_with_positive_number(self, fansly_api_factory):
        """Test rshift32 with positive number"""
        api = fansly_api_factory()
        assert api.rshift32(32, 2) == 8

    def test_rshift32_with_negative_number(self, fansly_api_factory):
        """Test rshift32 with negative number"""
        api = fansly_api_factory()
        assert api.rshift32(-32, 2) != -8

    def test_cyrb53_with_different_seeds(self, fansly_api_factory):
        """Test cyrb53 hash function with different seeds"""
        api = fansly_api_factory()
        input_str = "test_input"

        hash1 = api.cyrb53(input_str, seed=0)
        hash2 = api.cyrb53(input_str, seed=1)
        hash3 = api.cyrb53(input_str, seed=42)

        assert hash1 != hash2
        assert hash1 != hash3
        assert hash2 != hash3

    @pytest.mark.asyncio
    @respx.mock
    async def test_cors_options_request_includes_headers(self, fansly_api_factory):
        """Test cors_options_request includes required headers.

        Uses fansly_api_factory + manual respx because this test asserts on
        the OPTIONS request headers — the fixture's OPTIONS responder is a
        catch-all that wouldn't surface the per-request headers cleanly.
        """
        api = fansly_api_factory()
        test_url = "https://api.test.com/endpoint"

        options_route = respx.options(test_url).mock(side_effect=[httpx.Response(200)])

        try:
            await api.cors_options_request(test_url)
        finally:
            dump_fansly_calls(
                options_route.calls, "test_cors_options_request_includes_headers"
            )

        assert options_route.called
        request = options_route.calls.last.request

        assert "origin" in request.headers
        assert "access-control-request-method" in request.headers
        assert "access-control-request-headers" in request.headers
        assert (
            "authorization,fansly-client-check,fansly-client-id,fansly-client-ts,fansly-session-id"
            in request.headers["access-control-request-headers"]
        )

    @pytest.mark.asyncio
    @respx.mock
    async def test_init_without_device_info(self):
        """No device_id args → device_id stays None; first update_device_id() fetches."""
        api = FanslyApi(
            token="test_token",  # noqa: S106 # Test fixture token
            user_agent="test_user_agent",
            check_key="test_check_key",
        )
        respx.options(url__startswith=api.DEVICE_ID_ENDPOINT).mock(
            side_effect=[httpx.Response(200)]
        )
        device_route = respx.get(api.DEVICE_ID_ENDPOINT).mock(
            side_effect=[
                httpx.Response(
                    200, json={"success": "true", "response": "new_device_id"}
                )
            ]
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

        api = FanslyApi(
            token="test_token",  # noqa: S106 # Test fixture token
            user_agent="test_user_agent",
            check_key="test_check_key",
            device_id_timestamp=custom_timestamp,
        )
        respx.options(url__startswith=api.DEVICE_ID_ENDPOINT).mock(
            side_effect=[httpx.Response(200)]
        )
        device_route = respx.get(api.DEVICE_ID_ENDPOINT).mock(
            side_effect=[
                httpx.Response(
                    200, json={"success": "true", "response": "fetched_device_id"}
                )
            ]
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

        api = FanslyApi(
            token="test_token",  # noqa: S106 # Test fixture token
            user_agent="test_user_agent",
            check_key="test_check_key",
            device_id=custom_device_id,
        )
        respx.options(url__startswith=api.DEVICE_ID_ENDPOINT).mock(
            side_effect=[httpx.Response(200)]
        )
        device_route = respx.get(api.DEVICE_ID_ENDPOINT).mock(
            side_effect=[
                httpx.Response(
                    200, json={"success": "true", "response": "updated_device_id"}
                )
            ]
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

    def test_non_200_status_raises(self, fansly_api_factory):
        """Non-200 response after raise_for_status raises RuntimeError.

        204 No Content is valid HTTP but not expected by the Fansly API.
        raise_for_status() only raises for 4xx/5xx, so 204 passes through to
        the explicit != 200 check.
        """
        api = fansly_api_factory()
        request = httpx.Request("GET", f"{api.BASE_URL}test")
        response = httpx.Response(204, json={"success": "true"}, request=request)
        with pytest.raises(RuntimeError, match="Web request failed"):
            api.validate_json_response(response)

    @pytest.mark.asyncio
    async def test_418_teapot_retried(self, respx_fansly_api):
        """HTTP 418 is in the retry status codes list — verifies retry logic."""
        test_url = f"{respx_fansly_api.BASE_URL}test"
        route = respx.get(url__startswith=test_url).mock(
            side_effect=[
                httpx.Response(418),
                httpx.Response(
                    200,
                    json={"success": "true", "response": {"data": "ok"}},
                ),
            ]
        )

        try:
            response = await respx_fansly_api.get_with_ngsw(test_url)
            assert response.status_code == 200
        finally:
            dump_fansly_calls(route.calls)


class TestConvertIdsToInt:
    """Cover convert_ids_to_int edge cases."""

    def test_non_numeric_id_string_unchanged(self):
        """Non-numeric ID string falls back to original value."""
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
    """Cover WebSocket callback handlers."""

    def test_handle_unauthorized_resets_session(self, fansly_api_factory):
        """401 handler resets session_id."""
        api = fansly_api_factory()
        api.session_id = "active_session"
        api._handle_websocket_unauthorized()
        assert api.session_id == "null"

    def test_handle_rate_limited_with_limiter(self, fansly_api_factory):
        """429 handler triggers rate limiter backoff."""
        api = fansly_api_factory()
        config = FanslyConfig(program_version="1.0.0")
        api.rate_limiter = RateLimiter(config)
        api._handle_websocket_rate_limited()

    def test_handle_rate_limited_no_limiter(self, fansly_api_factory):
        """429 handler without rate limiter logs warning."""
        api = fansly_api_factory()
        api.rate_limiter = None
        api._handle_websocket_rate_limited()

    @pytest.mark.asyncio
    async def test_close_websocket_no_client(self, fansly_api_factory):
        """Closing with no websocket client is a no-op."""
        api = fansly_api_factory()
        api._websocket_client = None
        await api.close_websocket()
        assert api._websocket_client is None

    @pytest.mark.asyncio
    async def test_close_websocket_with_client(self, fansly_api_factory):
        """Closing with websocket client calls stop_thread and clears ref."""
        api = fansly_api_factory()
        stop_called = []

        async def fake_stop_thread():
            stop_called.append(True)

        api._websocket_client = types.SimpleNamespace(
            stop_thread=fake_stop_thread,
            connected=True,
            session_id="test",
        )
        await api.close_websocket()
        assert api._websocket_client is None
        assert len(stop_called) == 1

    @pytest.mark.asyncio
    async def test_close_websocket_stop_raises(self, fansly_api_factory):
        """stop_thread raising exception is handled gracefully."""
        api = fansly_api_factory()

        async def failing_stop_thread():
            raise RuntimeError("stop failed")

        api._websocket_client = types.SimpleNamespace(
            stop_thread=failing_stop_thread,
            connected=False,
        )
        await api.close_websocket()
        assert api._websocket_client is None


class TestGetClientUserName:
    """Cover get_client_user_name edge case."""

    @pytest.mark.asyncio
    async def test_empty_username_returns_none(self, respx_fansly_api):
        """Empty username in API response returns None."""
        route = respx.get(respx_fansly_api.ACCOUNT_ME_ENDPOINT).mock(
            side_effect=[
                httpx.Response(
                    200,
                    json={
                        "success": "true",
                        "response": {"account": {"username": ""}},
                    },
                )
            ]
        )
        try:
            result = await respx_fansly_api.get_client_user_name()
            assert result is None
        finally:
            dump_fansly_calls(route.calls)
