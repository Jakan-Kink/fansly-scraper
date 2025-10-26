"""Centralized fixtures for FanslyApi testing.

This module provides reusable fixtures for testing the Fansly API client,
eliminating the need for local fixture duplication across test files.

Fixtures:
    - mock_http_session: Mock httpx.Client for unit tests
    - fansly_api: Real FanslyApi instance with mocked HTTP client
    - respx_mock: respx router for realistic HTTP endpoint mocking (optional)

Usage:
    # Simple unit test with mocked HTTP
    def test_something(fansly_api):
        assert fansly_api.token == "test_token"

    # Integration test with respx
    @respx.mock
    async def test_api_call(fansly_api):
        respx.get("https://apiv3.fansly.com/api/v1/account").mock(
            return_value=httpx.Response(200, json={"response": {...}})
        )
        result = await fansly_api.get_account_info()

Note:
    FanslyApi is imported lazily inside fixtures to avoid circular import issues.
    The circular dependency chain is: api.fansly -> config.logging -> config.fanslyconfig -> api
    By importing FanslyApi inside fixtures (not at module level), we allow config to be
    imported first, which breaks the cycle.
"""

import json
from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest


# NOTE: FanslyApi is NOT imported at module level to avoid circular import.
# It is imported inside each fixture function (lazy import pattern).


@pytest.fixture
def mock_http_session():
    """Create a mock httpx.Client for testing.

    This fixture provides a fully mocked HTTP client that can be used
    to test FanslyApi without making real HTTP requests.

    Returns:
        MagicMock: Mocked httpx.Client with default responses configured

    Example:
        def test_api_method(fansly_api, mock_http_session):
            # Customize mock response for specific test
            mock_http_session.get.return_value.json.return_value = {
                "success": True,
                "response": {"data": "test"}
            }
            result = fansly_api.some_method()
    """
    mock = MagicMock()

    # Configure default mock response
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.reason_phrase = "OK"
    mock_response.json.return_value = {
        "success": "true",
        "response": "test_device_id",
    }

    # Configure mock methods to return the mock response
    mock.get.return_value = mock_response
    mock.post.return_value = mock_response
    mock.put.return_value = mock_response
    mock.patch.return_value = mock_response
    mock.delete.return_value = mock_response
    mock.options.return_value = mock_response

    return mock


@pytest.fixture
def fansly_api(mock_http_session):
    """Create a FanslyApi instance with mocked HTTP client.

    This fixture provides a real FanslyApi instance with a mocked HTTP
    session, suitable for unit testing API methods without making real
    HTTP requests.

    Args:
        mock_http_session: The mocked httpx.Client from mock_http_session fixture

    Returns:
        FanslyApi: Configured API instance with mocked HTTP client

    Example:
        def test_api_initialization(fansly_api):
            assert fansly_api.token == "test_token"
            assert fansly_api.user_agent == "test_user_agent"

        def test_api_method(fansly_api, mock_http_session):
            # Customize response for this test
            mock_http_session.get.return_value.json.return_value = {
                "response": {"account": {"id": "123"}}
            }
            result = fansly_api.get_account("123")
    """
    # Lazy import to avoid circular dependency
    # Circular chain: api.fansly -> config.logging -> config.fanslyconfig -> api
    from api.fansly import FanslyApi  # noqa: PLC0415

    # Initialize with test device ID to avoid real HTTP request
    api = FanslyApi(
        token="test_token",
        user_agent="test_user_agent",
        check_key="test_check_key",
        device_id="test_device_id",  # Provide device_id to skip device initialization
        device_id_timestamp=int(
            datetime.now(UTC).timestamp() * 1000
        ),  # Current timestamp
    )

    # Replace the http_session with our mock
    api.http_session = mock_http_session

    return api


@pytest.fixture
def fansly_api_factory(mock_http_session):
    """Factory fixture for creating FanslyApi instances with custom parameters.

    This fixture provides a factory function that can create FanslyApi
    instances with custom configuration for tests that need specific
    API settings.

    Args:
        mock_http_session: The mocked httpx.Client from mock_http_session fixture

    Returns:
        Callable: Factory function that creates FanslyApi instances

    Example:
        def test_custom_api(fansly_api_factory):
            # Create API with custom token
            api = fansly_api_factory(token="custom_token")
            assert api.token == "custom_token"

            # Create API with custom device ID
            api2 = fansly_api_factory(device_id="custom_device_id")
            assert api2.device_id == "custom_device_id"
    """

    def _create_api(
        token: str = "test_token",
        user_agent: str = "test_user_agent",
        check_key: str = "test_check_key",
        device_id: str = "test_device_id",
        device_id_timestamp: int | None = None,
        on_device_updated=None,
    ):
        """Create a FanslyApi instance with specified parameters."""
        # Lazy import to avoid circular dependency
        # Circular chain: api.fansly -> config.logging -> config.fanslyconfig -> api
        from api.fansly import FanslyApi  # noqa: PLC0415

        if device_id_timestamp is None:
            device_id_timestamp = int(datetime.now(UTC).timestamp() * 1000)

        api = FanslyApi(
            token=token,
            user_agent=user_agent,
            check_key=check_key,
            device_id=device_id,
            device_id_timestamp=device_id_timestamp,
            on_device_updated=on_device_updated,
        )

        # Replace with mock session
        api.http_session = mock_http_session

        return api

    return _create_api


def create_mock_response(status_code=200, json_data=None, text="", reason_phrase=None):
    """Create a properly structured mock HTTP response.

    This utility function creates mock response objects with all required attributes
    for testing HTTP interactions. Use this instead of manually creating MagicMock
    response objects to ensure consistency across tests.

    Args:
        status_code: HTTP status code (default 200)
        json_data: Dictionary to return from response.json()
        text: Response text content (auto-generated from json_data if not provided)
        reason_phrase: HTTP reason phrase (auto-generated based on status_code if None)

    Returns:
        MagicMock: Configured mock response with status_code, reason_phrase, text, and json()

    Example:
        # Success response
        response = create_mock_response(
            status_code=200,
            json_data={"response": {"account": {"id": "123"}}}
        )

        # Error response
        error_response = create_mock_response(
            status_code=401,
            json_data={"error": "Unauthorized"}
        )

        # Use in test
        mock_http_session.get.return_value = response
    """
    mock = MagicMock()

    # Set status code
    mock.status_code = status_code

    # Auto-generate reason phrase if not provided
    if reason_phrase is None:
        reason_phrases = {
            200: "OK",
            201: "Created",
            204: "No Content",
            400: "Bad Request",
            401: "Unauthorized",
            403: "Forbidden",
            404: "Not Found",
            418: "I'm a teapot",  # RFC 2324 - HTCPCP/1.0
            429: "Too Many Requests",
            500: "Internal Server Error",
            502: "Bad Gateway",
            503: "Service Unavailable",
        }
        reason_phrase = reason_phrases.get(status_code, "Unknown")

    mock.reason_phrase = reason_phrase

    # Set text content (auto-generate from json_data if not provided)
    if text:
        mock.text = text
    elif json_data is not None:
        mock.text = json.dumps(json_data)
    else:
        mock.text = ""

    # Set json() method
    mock.json.return_value = json_data or {}

    return mock


# Note: For integration tests using respx for HTTP mocking, use:
#
# import respx
# import httpx
#
# @respx.mock
# async def test_real_http_mocking(fansly_api):
#     # Mock specific endpoints
#     respx.get("https://apiv3.fansly.com/api/v1/account/12345").mock(
#         return_value=httpx.Response(
#             200,
#             json={
#                 "success": True,
#                 "response": {
#                     "account": {
#                         "id": "12345",
#                         "username": "test_user"
#                     }
#                 }
#             }
#         )
#     )
#
#     # Use real FanslyApi with mocked endpoints
#     result = await fansly_api.get_account_info("12345")
#     assert result["account"]["username"] == "test_user"
