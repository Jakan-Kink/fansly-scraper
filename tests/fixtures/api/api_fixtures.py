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
        # Mock the edge (HTTP response)
        respx.get("https://apiv3.fansly.com/api/v1/account").mock(
            return_value=httpx.Response(
                200,
                json={"success": True, "response": {"id": "123"}}
            )
        )

        # Test with real API instance
        result = await fansly_api_with_respx.get_account_info()
        assert result["id"] == "123"
"""

from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
import respx


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
            # Mock the HTTP response
            respx.get("https://apiv3.fansly.com/api/v1/timeline").mock(
                return_value=httpx.Response(200, json={"response": []})
            )

            # Use real API
            timeline = await fansly_api_with_respx.get_timeline()
            assert timeline == []
    """
    # Lazy import to avoid circular dependency
    from api.fansly import FanslyApi

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
    from api.fansly import FanslyApi

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
        # Lazy import to avoid circular dependency
        from api.fansly import FanslyApi

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

        # Use with respx
        respx.get("https://api.example.com/test").mock(
            return_value=response
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
                return_value=httpx.Response(200, json=mock_fansly_account_response)
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
                return_value=httpx.Response(200, json=mock_fansly_timeline_response)
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


__all__ = [
    "create_mock_json_response",
    "fansly_api",
    "fansly_api_factory",
    "fansly_api_with_respx",
    "mock_fansly_account_response",
    "mock_fansly_timeline_response",
]
