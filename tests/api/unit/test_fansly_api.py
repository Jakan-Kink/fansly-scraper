"""Unit tests for FanslyApi class.

Fixture choice:

- ``fansly_api_factory`` — bare ``FanslyApi`` with no bootstrap. For sync
  property/method tests and tests inspecting pre-bootstrap invariants
  (e.g. ``session_id == "null"``) or calling ``setup_session`` directly.
- ``respx_fansly_api`` — fully-bootstrapped ``FanslyApi`` with respx +
  apiv3-host OPTIONS responder active. For async tests exercising HTTP.

Endpoint URLs use the production constants on ``FanslyApi`` (e.g.
``api.ACCOUNT_ME_ENDPOINT``, ``api.FOLLOWING_ENDPOINT.format(user_id)``)
so production URL changes propagate without per-test churn.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import respx

from api.fansly import FanslyApi
from tests.fixtures.api import dump_fansly_calls


_OK_EMPTY = {"success": "true", "response": []}

# Rows: (endpoint, method_name, call_args, call_kwargs, response_json,
# expected_result, expected_params, path_contains).
# ``endpoint`` is a callable taking the api so production URL constants
# (and their .format() fills) resolve at test time.
_GET_ENDPOINT_CASES = [
    pytest.param(
        lambda api: api.ACCOUNT_ME_ENDPOINT,
        "get_client_user_name",
        (),
        {},
        {"success": "true", "response": {"account": {"username": "test_user"}}},
        "test_user",
        {},
        None,
        id="client-user-name",
    ),
    pytest.param(
        lambda api: api.ACCOUNT_BY_USERNAME_ENDPOINT.format(""),
        "get_creator_account_info",
        ("test_creator",),
        {},
        _OK_EMPTY,
        None,
        {"usernames": "test_creator", "ngsw-bypass": "true"},
        None,
        id="creator-account-info-single",
    ),
    pytest.param(
        lambda api: api.ACCOUNT_BY_USERNAME_ENDPOINT.format(""),
        "get_creator_account_info",
        (["creator1", "creator2"],),
        {},
        _OK_EMPTY,
        None,
        {"usernames": "creator1,creator2", "ngsw-bypass": "true"},
        None,
        id="creator-account-info-multiple",
    ),
    pytest.param(
        lambda api: api.ACCOUNT_BY_ID_ENDPOINT.format(""),
        "get_account_info_by_id",
        (123,),
        {},
        _OK_EMPTY,
        None,
        {"ids": "123", "ngsw-bypass": "true"},
        None,
        id="account-info-by-id-single",
    ),
    pytest.param(
        lambda api: api.ACCOUNT_BY_ID_ENDPOINT.format(""),
        "get_account_info_by_id",
        ([123, 456],),
        {},
        _OK_EMPTY,
        None,
        {"ids": "123,456", "ngsw-bypass": "true"},
        None,
        id="account-info-by-id-multiple",
    ),
    pytest.param(
        lambda api: api.ACCOUNT_MEDIA_ORDERS_ENDPOINT,
        "get_media_collections",
        (),
        {},
        _OK_EMPTY,
        None,
        {"limit": "9999", "offset": "0"},
        None,
        id="media-collections",
    ),
    pytest.param(
        lambda api: api.FOLLOWING_ENDPOINT.format("user123"),
        "get_following_list",
        ("user123",),
        {},
        _OK_EMPTY,
        None,
        {"limit": "425", "offset": "0", "before": "0", "after": "0"},
        "user123",
        id="following-list-defaults",
    ),
    pytest.param(
        lambda api: api.FOLLOWING_ENDPOINT.format("user123"),
        "get_following_list",
        ("user123",),
        {"limit": 10, "offset": 5, "before": 1000, "after": 500},
        _OK_EMPTY,
        None,
        {"limit": "10", "offset": "5", "before": "1000", "after": "500"},
        "user123",
        id="following-list-custom-params",
    ),
    pytest.param(
        lambda api: api.ACCOUNT_MEDIA_ENDPOINT.format(""),
        "get_account_media",
        ("media123,media456",),
        {},
        _OK_EMPTY,
        None,
        {"ids": "media123,media456"},
        None,
        id="account-media",
    ),
    pytest.param(
        lambda api: api.POST_ENDPOINT,
        "get_post",
        ("post123",),
        {},
        _OK_EMPTY,
        None,
        {"ids": "post123"},
        None,
        id="post",
    ),
    pytest.param(
        lambda api: api.TIMELINE_NEW_ENDPOINT.format("creator123"),
        "get_timeline",
        ("creator123", "cursor123"),
        {},
        _OK_EMPTY,
        None,
        {"before": "cursor123", "after": "0", "wallId": "", "contentSearch": ""},
        "creator123",
        id="timeline",
    ),
    pytest.param(
        lambda api: api.TIMELINE_NEW_ENDPOINT.format("creator123"),
        "get_wall_posts",
        ("creator123", "wall123", "cursor456"),
        {},
        _OK_EMPTY,
        None,
        {"before": "cursor456", "after": "0", "wallId": "wall123", "contentSearch": ""},
        "creator123",
        id="wall-posts",
    ),
    pytest.param(
        lambda api: api.MESSAGING_GROUPS_ENDPOINT,
        "get_group",
        (),
        {},
        _OK_EMPTY,
        None,
        {},
        None,
        id="group",
    ),
    pytest.param(
        lambda api: api.MESSAGE_ENDPOINT,
        "get_message",
        ({"param1": "value1"},),
        {},
        _OK_EMPTY,
        None,
        {"param1": "value1", "ngsw-bypass": "true"},
        None,
        id="message",
    ),
]


class TestFanslyApi:
    def test_init(self, fansly_api_factory):
        """Test FanslyApi initialization with basic parameters"""
        api = fansly_api_factory()
        assert api.token == "test_token"
        assert api.user_agent == "test_user_agent"
        assert api.check_key == "test_check_key"
        assert api.session_id == "null"
        assert hasattr(api, "device_id")
        assert hasattr(api, "device_id_timestamp")

    def test_init_with_device_info(self):
        """Test FanslyApi initialization with device ID parameters"""
        test_device_id = "test_device_id"
        test_timestamp = int(datetime(2024, 1, 1, tzinfo=UTC).timestamp())
        mock_callback = MagicMock()

        api = FanslyApi(
            token="test_token",  # Test fixture token
            user_agent="test_user_agent",
            check_key="test_check_key",
            device_id=test_device_id,
            device_id_timestamp=test_timestamp,
            on_device_updated=mock_callback,
        )

        assert api.device_id == test_device_id
        assert api.device_id_timestamp == test_timestamp
        assert api.on_device_updated == mock_callback

    def test_get_text_accept(self, fansly_api_factory):
        """Test get_text_accept returns correct accept header"""
        api = fansly_api_factory()
        assert api.get_text_accept() == "application/json, text/plain, */*"

    def test_set_text_accept(self, fansly_api_factory):
        """Test set_text_accept adds accept header correctly"""
        api = fansly_api_factory()
        headers: dict[str, str] = {}
        api.set_text_accept(headers)
        assert headers["Accept"] == api.get_text_accept()

    def test_get_common_headers(self, fansly_api_factory):
        """Test get_common_headers returns correct header structure"""
        api = fansly_api_factory()
        headers = api.get_common_headers()

        assert headers["Accept-Language"] == "en-US,en;q=0.9"
        assert headers["authorization"] == "test_token"
        assert headers["Origin"] == FanslyApi.FANSLY_HOST
        assert headers["Referer"] == f"{FanslyApi.FANSLY_HOST}/"
        assert headers["User-Agent"] == "test_user_agent"

    def test_get_common_headers_alternate_token(self, fansly_api_factory):
        """Test get_common_headers with alternate token"""
        api = fansly_api_factory()
        alt_token = "alternate_token"
        headers = api.get_common_headers(alternate_token=alt_token)
        assert headers["authorization"] == alt_token

    def test_get_common_headers_missing_token(self):
        """Test get_common_headers raises error with missing token"""
        api = FanslyApi(
            token=None,  # type: ignore[arg-type]  # intentionally invalid: tests missing-token error path
            user_agent="test_user_agent",
            check_key="test_check_key",
            device_id="test_device_id",
            device_id_timestamp=int(datetime.now(UTC).timestamp() * 1000),
        )
        with pytest.raises(
            RuntimeError, match="Internal error generating HTTP headers"
        ):
            api.get_common_headers()

    def test_get_ngsw_params(self, fansly_api_factory):
        """Test get_ngsw_params returns correct parameters"""
        api = fansly_api_factory()
        assert api.get_ngsw_params() == {"ngsw-bypass": "true"}

    def test_cyrb53(self, fansly_api_factory):
        """Test cyrb53 hash function"""
        api = fansly_api_factory()
        test_input = "test_string"
        hash1 = api.cyrb53(test_input)
        hash2 = api.cyrb53(test_input)

        assert hash1 == hash2
        assert hash1 != api.cyrb53("different_string")

    def test_cyrb53_with_seed(self, fansly_api_factory):
        """Test cyrb53 hash function with seed"""
        api = fansly_api_factory()
        test_input = "test_string"
        assert api.cyrb53(test_input, seed=1) != api.cyrb53(test_input, seed=2)

    def test_get_timestamp_ms(self, fansly_api_factory):
        """Test get_timestamp_ms returns current timestamp in milliseconds"""
        api = fansly_api_factory()
        timestamp = api.get_timestamp_ms()
        now = int(datetime.now(UTC).timestamp() * 1000)
        assert abs(timestamp - now) < 1000

    def test_get_client_timestamp(self, fansly_api_factory):
        """Test get_client_timestamp returns value within expected range"""
        api = fansly_api_factory()
        timestamp = api.get_client_timestamp()
        now = int(datetime.now(UTC).timestamp() * 1000)
        assert abs(timestamp - now) <= 5000

    def test_update_client_timestamp(self, fansly_api_factory):
        """Test update_client_timestamp updates when newer"""
        api = fansly_api_factory()
        old_timestamp = api.client_timestamp
        api.update_client_timestamp()
        assert api.client_timestamp >= old_timestamp

    def test_to_str16(self, fansly_api_factory):
        """Test to_str16 hex conversion"""
        api = fansly_api_factory()
        assert api.to_str16(255) == "ff"

    def test_int32(self, fansly_api_factory):
        """Test int32 conversion"""
        api = fansly_api_factory()
        assert api.int32(100) == 100
        assert api.int32(2**31 + 1) < 2**31

    @pytest.mark.asyncio
    @respx.mock
    async def test_setup_session(self, fansly_api_factory):
        """Test setup_session success path — direct call without bootstrap fixture.

        Uses fansly_api_factory + manual respx because respx_fansly_api's
        bootstrap already calls setup_session via setup_api — testing the
        method in isolation requires a pre-bootstrap api.
        """
        api = fansly_api_factory()
        options_route = respx.options(api.ACCOUNT_ME_ENDPOINT).mock(
            side_effect=[httpx.Response(200)]
        )
        get_route = respx.get(api.ACCOUNT_ME_ENDPOINT).mock(
            side_effect=[httpx.Response(200, json={"success": "true", "response": {}})]
        )

        mock_ws_client = AsyncMock()
        mock_ws_client.connected = True
        mock_ws_client.session_id = "test_session_id"
        mock_ws_client.start_in_thread = MagicMock()
        mock_ws_client.stop_thread = AsyncMock()

        try:
            with patch(
                "api.fansly.FanslyWebSocket",
                new=lambda **_kwargs: mock_ws_client,
            ):
                result = await api.setup_session()
        finally:
            dump_fansly_calls(options_route.calls, "setup_session-options")
            dump_fansly_calls(get_route.calls, "setup_session-get")

        assert result is True
        assert api.session_id == "test_session_id"

    @pytest.mark.parametrize(
        ("status", "payload", "expected_exc"),
        [
            pytest.param(200, {"success": "true"}, None, id="success"),
            pytest.param(200, {"success": "false"}, RuntimeError, id="failure-flag"),
            pytest.param(404, {}, httpx.HTTPStatusError, id="non-200-status"),
        ],
    )
    def test_validate_json_response(
        self, fansly_api_factory, status, payload, expected_exc
    ):
        """validate_json_response: success → True, success=false → RuntimeError,
        non-200 → HTTPStatusError."""
        api = fansly_api_factory()
        request = httpx.Request("GET", "https://api.test.com")
        mock_response = httpx.Response(status, json=payload, request=request)

        if expected_exc is None:
            assert api.validate_json_response(mock_response) is True
        else:
            with pytest.raises(expected_exc):
                api.validate_json_response(mock_response)

    def test_get_json_response_contents(self, fansly_api_factory):
        """Test get_json_response_contents extracts response field"""
        api = fansly_api_factory()
        request = httpx.Request("GET", "https://api.test.com")
        mock_response = httpx.Response(
            200,
            json={"success": "true", "response": {"data": "test_data"}},
            request=request,
        )

        assert api.get_json_response_contents(mock_response) == {"data": "test_data"}

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        (
            "endpoint",
            "method_name",
            "call_args",
            "call_kwargs",
            "response_json",
            "expected_result",
            "expected_params",
            "path_contains",
        ),
        _GET_ENDPOINT_CASES,
    )
    async def test_get_endpoint_requests(
        self,
        respx_fansly_api,
        request,
        endpoint,
        method_name,
        call_args,
        call_kwargs,
        response_json,
        expected_result,
        expected_params,
        path_contains,
    ):
        """Single-GET endpoint methods: route match, params, and result.

        Mocks HTTP at the edge only. Matches by ``url__startswith`` because
        ``get_with_ngsw`` appends ``ngsw-bypass=true``, and multi-value rows
        assert by parameter name rather than URL value because httpx
        URL-encodes commas in query params (``,`` → ``%2C``).
        """
        route = respx.get(url__startswith=endpoint(respx_fansly_api)).mock(
            side_effect=[httpx.Response(200, json=response_json)]
        )

        try:
            result = await getattr(respx_fansly_api, method_name)(
                *call_args, **call_kwargs
            )
        finally:
            dump_fansly_calls(route.calls, f"get_endpoint-{request.node.callspec.id}")

        assert route.called
        if expected_result is not None:
            assert result == expected_result
        http_request = route.calls.last.request
        if path_contains is not None:
            assert path_contains in http_request.url.path
        for key, value in expected_params.items():
            assert http_request.url.params[key] == value

    @pytest.mark.asyncio
    async def test_get_with_ngsw(self, respx_fansly_api):
        """Test get_with_ngsw builds correct request - mocks HTTP at edge"""
        test_url = f"{respx_fansly_api.BASE_URL}test_endpoint"
        test_params = {"test": "param"}

        get_route = respx.get(test_url).mock(side_effect=[httpx.Response(200)])

        try:
            await respx_fansly_api.get_with_ngsw(
                url=test_url, params=test_params, add_fansly_headers=True
            )
        finally:
            dump_fansly_calls(get_route.calls, "test_get_with_ngsw")

        assert get_route.called
        get_request = get_route.calls.last.request
        assert get_request.url.params["test"] == "param"
        assert get_request.url.params["ngsw-bypass"] == "true"
        assert get_request.headers["Origin"] == FanslyApi.FANSLY_HOST

    @pytest.mark.asyncio
    async def test_update_device_id_within_timeframe(self, fansly_api_factory):
        """Test update_device_id is a no-op if within time window.

        Uses fansly_api_factory because we need pre-bootstrap device_id state
        — respx_fansly_api would have already refreshed it during setup_api.
        """
        api = fansly_api_factory()
        original_device_id = api.device_id
        api.device_id_timestamp = api.get_timestamp_ms()

        updated_id = await api.update_device_id()
        assert updated_id == original_device_id

    @pytest.mark.asyncio
    async def test_update_device_id_expired(self, respx_fansly_api):
        """Test update_device_id refetches when timestamp expired"""
        route = respx.get(url__startswith=respx_fansly_api.DEVICE_ID_ENDPOINT).mock(
            side_effect=[
                httpx.Response(
                    200,
                    json={"success": "true", "response": "new_device_id"},
                )
            ]
        )

        respx_fansly_api.device_id_timestamp = 0

        mock_callback = MagicMock()
        respx_fansly_api.on_device_updated = mock_callback

        try:
            updated_id = await respx_fansly_api.update_device_id()
        finally:
            dump_fansly_calls(route.calls, "test_update_device_id_expired")

        assert updated_id == "new_device_id"
        mock_callback.assert_called_once()

    @pytest.mark.asyncio
    @respx.mock
    async def test_setup_session_error(self, fansly_api_factory):
        """setup_session wraps an HTTP-layer auth failure as RuntimeError.

        Uses fansly_api_factory + manual respx because respx_fansly_api's
        bootstrap requires setup_session to SUCCEED — a 401 here would
        crash fixture setup before the test body runs.
        """
        api = fansly_api_factory()
        options_route = respx.options(api.ACCOUNT_ME_ENDPOINT).mock(
            side_effect=[httpx.Response(200)]
        )
        get_route = respx.get(api.ACCOUNT_ME_ENDPOINT).mock(
            side_effect=[httpx.Response(401)]
        )

        def _explode(**_kwargs):
            raise RuntimeError("WS should not be instantiated on 401")

        try:
            with (
                patch("api.fansly.FanslyWebSocket", new=_explode),
                pytest.raises(RuntimeError, match="Error during session setup"),
            ):
                await api.setup_session()
        finally:
            dump_fansly_calls(options_route.calls, "setup_session_error-options")
            dump_fansly_calls(get_route.calls, "setup_session_error-get")

    def test_get_http_headers_with_session(self, fansly_api_factory):
        """Test get_http_headers includes session ID when available.

        Uses fansly_api_factory so session_id can be set explicitly without
        the bootstrap fixture's auth response overriding it.
        """
        api = fansly_api_factory()
        api.session_id = "test_session"
        headers = api.get_http_headers(url="https://test.com", add_fansly_headers=True)
        assert headers["fansly-session-id"] == "test_session"
