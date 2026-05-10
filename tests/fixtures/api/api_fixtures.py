"""API fixtures for testing Fansly API client using respx for edge mocking.

This module provides fixtures for testing the Fansly API client WITHOUT using MagicMock
for internal functions. Instead, we use respx to mock HTTP responses at the edge
(the network boundary).

Key Principles:
- NO MagicMock for internal functions
- Use respx to mock HTTP/HTTPS requests
- Provide real JSON responses that match Fansly API format
- Test with real FanslyApi instances

Usage:
    import respx
    import httpx

    @respx.mock
    async def test_api_call(fansly_api_with_respx):
        # Mock the edge (HTTP response) — ALWAYS side_effect=[] per CLAUDE.md,
        # never return_value (which would defeat retry-budget accounting).
        respx.get("https://apiv3.fansly.com/api/v1/account").mock(
            side_effect=[
                httpx.Response(
                    200,
                    json={"success": True, "response": {"id": "123"}},
                )
            ]
        )

        # Test with real API instance
        result = await fansly_api_with_respx.get_account_info()
        assert result["id"] == "123"
"""

from collections.abc import Generator
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
import respx

from api.fansly import FanslyApi


@pytest.fixture
def fansly_api_with_respx():
    """Create a real FanslyApi instance for use with respx HTTP mocking.

    This fixture provides a real FanslyApi instance with real httpx.Client.
    Use respx to mock the HTTP responses at the edge.

    Returns:
        FanslyApi: Real API instance with real HTTP client

    Example:
        @respx.mock
        async def test_timeline(fansly_api_with_respx):
            # Mock the HTTP response — per-test routes must use side_effect=[].
            respx.get("https://apiv3.fansly.com/api/v1/timeline").mock(
                side_effect=[httpx.Response(200, json={"response": []})]
            )

            # Use real API
            timeline = await fansly_api_with_respx.get_timeline()
            assert timeline == []
    """
    # Lazy import to avoid circular dependency

    api = FanslyApi(
        token="test_token",  # noqa: S106 # Test fixture token
        user_agent="test_user_agent",
        check_key="test_check_key",
        device_id="test_device_id",
        device_id_timestamp=int(datetime.now(UTC).timestamp() * 1000),
    )

    return api


@pytest.fixture
def fansly_api():
    """Create a real FanslyApi instance for testing.

    This is an alias for fansly_api_with_respx for backward compatibility.
    Use with respx to mock HTTP responses.

    Returns:
        FanslyApi: Real API instance
    """
    # Lazy import to avoid circular dependency

    api = FanslyApi(
        token="test_token",  # noqa: S106 # Test fixture token
        user_agent="test_user_agent",
        check_key="test_check_key",
        device_id="test_device_id",
        device_id_timestamp=int(datetime.now(UTC).timestamp() * 1000),
    )

    return api


@pytest.fixture
def fansly_api_factory():
    """Factory fixture for creating FanslyApi instances with custom parameters.

    Returns:
        Callable: Factory function that creates real FanslyApi instances

    Example:
        def test_custom_api(fansly_api_factory):
            api = fansly_api_factory(token="custom_token")
            assert api.token == "custom_token"
    """

    def _create_api(
        token: str = "test_token",  # noqa: S107 # Test fixture default token
        user_agent: str = "test_user_agent",
        check_key: str = "test_check_key",
        device_id: str = "test_device_id",
        device_id_timestamp: int | None = None,
        on_device_updated=None,
    ):
        """Create a FanslyApi instance with specified parameters."""
        if device_id_timestamp is None:
            device_id_timestamp = int(datetime.now(UTC).timestamp() * 1000)

        return FanslyApi(
            token=token,
            user_agent=user_agent,
            check_key=check_key,
            device_id=device_id,
            device_id_timestamp=device_id_timestamp,
            on_device_updated=on_device_updated,
        )

    return _create_api


def create_mock_json_response(
    status_code: int = 200,
    json_data: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    """Create a real httpx.Response for use with respx mocking.

    This creates REAL response objects, not MagicMock objects.

    Args:
        status_code: HTTP status code
        json_data: Dictionary to include as JSON body
        headers: Optional HTTP headers

    Returns:
        httpx.Response: Real httpx Response object

    Example:
        response = create_mock_json_response(
            200,
            {"success": True, "response": {"id": "123"}}
        )

        # Use with respx — per-test routes must use side_effect=[] per CLAUDE.md.
        respx.get("https://api.example.com/test").mock(
            side_effect=[response]
        )
    """
    if json_data is None:
        json_data = {}

    if headers is None:
        headers = {"content-type": "application/json"}

    return httpx.Response(
        status_code=status_code,
        json=json_data,
        headers=headers,
    )


@pytest.fixture
def mock_fansly_account_response():
    """Provide a sample Fansly account API response for testing.

    Returns:
        dict: Sample account response matching Fansly API format

    Example:
        @respx.mock
        def test_account(fansly_api, mock_fansly_account_response):
            respx.get("https://apiv3.fansly.com/api/v1/account/123").mock(
                side_effect=[httpx.Response(200, json=mock_fansly_account_response)]
            )
    """
    return {
        "success": True,
        "response": {
            "id": "123456789",
            "username": "testuser",
            "displayName": "Test User",
            "about": "Test account",
            "location": "Test Location",
            "following": False,
            "subscribed": False,
            "flags": 0,
            "version": 1,
            "createdAt": int(datetime.now(UTC).timestamp() * 1000),
        },
        "aggregationData": {},
    }


@pytest.fixture
def mock_fansly_timeline_response():
    """Provide a sample Fansly timeline API response for testing.

    Returns:
        dict: Sample timeline response matching Fansly API format

    Example:
        @respx.mock
        def test_timeline(fansly_api, mock_fansly_timeline_response):
            respx.get("https://apiv3.fansly.com/api/v1/timeline").mock(
                side_effect=[httpx.Response(200, json=mock_fansly_timeline_response)]
            )
    """
    return {
        "success": True,
        "response": [
            {
                "id": "post_123",
                "accountId": "123456789",
                "content": "Test post content",
                "createdAt": int(datetime.now(UTC).timestamp() * 1000),
                "likeCount": 5,
                "replyCount": 2,
                "attachments": [],
            }
        ],
        "aggregationData": {},
    }


@pytest.fixture
def respx_ivs_cdn() -> Generator[None, None, None]:
    """Activate respx mocking for IVS CDN tests (``*.live-video.net``).

    Use this for tests that exercise IVS-host code paths (master/variant
    playlist fetches, ``.ts`` segment downloads, etc.) — anything that
    hits ``*.live-video.net`` rather than ``apiv3.fansly.com``.

    Yields from inside ``respx.mock`` so the global registry is active
    during the test and torn down on teardown. Tests add their own
    ``respx.get(url=...).mock(side_effect=[...])`` routes for the
    specific IVS URLs under exercise.

    Distinct from ``respx_fansly_api`` because IVS calls do NOT hit a
    Fansly API, do not need the CORS preflight blanket, and do not
    require ``mock_config._api`` wiring. Pick exactly one of these
    fixtures per test — respx maintains a global singleton registry.

    Example::

        async def test_segment_fetch(respx_ivs_cdn, tmp_path):
            url = "https://chan.live-video.net/segment_001.ts"
            route = respx.get(url).mock(
                side_effect=[httpx.Response(200, content=b"TS-DATA")]
            )
            try:
                async with httpx.AsyncClient() as client:
                    ok = await _download_segment(client, url, tmp_path / "s.ts", "[t]")
            finally:
                dump_fansly_calls(route.calls)
            assert ok is True
    """
    with respx.mock:
        yield


@pytest.fixture
def respx_fansly_api(
    mock_config,
    fansly_api,
) -> Generator[None, None, None]:
    """Activate respx mocking with CORS preflight handling for Fansly API tests.

    Wires fansly_api into mock_config, then activates respx.mock with a
    blanket OPTIONS route (FanslyApi.cors_options_request sends an OPTIONS
    preflight before every GET).

    Tests add their own respx.get/post routes for specific endpoints.

    NOTE: Uses ``respx.mock(using="httpcore")`` to intercept at the lowest
    transport level, which works with httpx_retries wrapped transports.

    Example::

        @pytest.mark.asyncio
        async def test_collections(respx_fansly_api, mock_config):
            respx.get("https://apiv3.fansly.com/api/v1/account/media/orders/").mock(
                side_effect=[httpx.Response(200, json={"success": True, "response": {...}})]
            )
            await download_collections(mock_config, state)
    """
    mock_config._api = fansly_api
    with respx.mock:
        # Intentional `return_value` — this is a fixture-level blanket CORS
        # preflight that must respond to every OPTIONS request throughout the
        # test. `side_effect=[Response(200)]` would exhaust after one call.
        # Per-test GET/POST routes still must use `side_effect=[...]`.
        respx.route(method="OPTIONS").mock(return_value=httpx.Response(200))
        yield


def dump_fansly_calls(calls, label: str = "Fansly API calls") -> None:
    """Print request/response details for each Fansly API call.

    Works with respx route.calls or respx.calls. Use in try/finally blocks
    when debugging test failures to see exactly what HTTP calls were made:

        route = respx.get(...).mock(side_effect=[...])
        try:
            await function_under_test()
        finally:
            dump_fansly_calls(route.calls)

    Handles three response states per call:

    - **Response present** (normal case): prints status code.
    - **No response** (``side_effect=[<Exception>]`` raised before producing a
      response): prints ``"NO RESPONSE (exception raised)"``. Without this
      branch, accessing ``call.response`` would raise ``ValueError`` from
      respx and the dump itself would crash inside the test's ``finally:`` —
      masking the real failure with a fixture-side error.
    - **Bad call object** (defensive): any other AttributeError/ValueError
      from ``call.request``/``call.response`` is caught + reported so the
      dumper never raises during teardown.

    Args:
        calls: respx route.calls or respx.calls list
        label: Header label for the output
    """
    print(f"\n{'=' * 70}")
    print(f"  {label} ({len(calls)} total)")
    print(f"{'=' * 70}")
    for i, call in enumerate(calls):
        try:
            req = call.request
        except Exception as exc:  # pragma: no cover  (defensive — shouldn't happen)
            print(f"\n  [{i}] <unable to access call.request: {exc!r}>")
            continue

        # ``call.response`` raises ValueError when respx's side_effect was an
        # Exception class/instance (no Response was produced). Use the
        # ``has_response`` / ``optional_response`` API to handle that
        # gracefully.
        if getattr(call, "has_response", True):
            resp = call.optional_response
            status = resp.status_code if resp is not None else "NO RESPONSE"
        else:
            status = "NO RESPONSE (exception raised)"

        print(f"\n  [{i}] {req.method} {req.url}")
        if req.content:
            print(f"      body: {req.content[:200]}")
        print(f"      → {status}")
    print(f"\n{'=' * 70}\n")


__all__ = [
    "create_mock_json_response",
    "dump_fansly_calls",
    "fansly_api",
    "fansly_api_factory",
    "fansly_api_with_respx",
    "mock_fansly_account_response",
    "mock_fansly_timeline_response",
    "respx_fansly_api",
    "respx_ivs_cdn",
]
