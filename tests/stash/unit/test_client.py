"""Unit tests for StashClient."""

import logging
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from gql.transport.exceptions import (
    TransportError,
    TransportQueryError,
    TransportServerError,
)

from stash import StashClient


@pytest.mark.asyncio
async def test_client_init() -> None:
    """Test client initialization."""
    # Test with minimal conn
    client = StashClient(conn={})
    assert client.url == "http://localhost:9999/graphql"
    assert "ApiKey" not in client.client.headers

    # Test with full conn
    client = StashClient(
        conn={
            "Scheme": "https",
            "Host": "stash.example.com",
            "Port": 8008,
            "ApiKey": "test_api_key",
            "Logger": logging.getLogger("test"),
        },
        verify_ssl=False,
    )
    assert client.url == "https://stash.example.com:8008/graphql"
    assert client.client.headers.get("ApiKey") == "test_api_key"
    assert client.log.name == "test"

    # Test with 0.0.0.0 host (should convert to 127.0.0.1)
    client = StashClient(conn={"Host": "0.0.0.0"})
    assert "127.0.0.1" in client.url

    # Test with None conn (should use defaults)
    client = StashClient(conn=None)
    assert client.url == "http://localhost:9999/graphql"
    assert "ApiKey" not in client.client.headers


@pytest.mark.asyncio
async def test_client_execute(stash_client: StashClient) -> None:
    """Test client execute method."""
    # Test error response
    mock_execute = AsyncMock(
        side_effect=TransportQueryError(
            msg="GraphQL query error", errors=[{"message": "Invalid query"}]
        )
    )

    with patch.object(
        stash_client.client,
        "execute",
        new=mock_execute,
    ):
        with pytest.raises(
            ValueError, match="GraphQL errors: \\[{'message': 'Invalid query'}\\]"
        ):
            await stash_client.execute("invalid { query }")

    # Test successful response
    mock_response = AsyncMock()
    mock_response.json = AsyncMock(
        return_value={
            "data": {
                "findScene": {
                    "id": "123",
                    "title": "Test Scene",
                }
            }
        }
    )
    mock_response.raise_for_status = AsyncMock()
    mock_post = AsyncMock(return_value=mock_response)

    with patch.object(
        stash_client.client,
        "post",
        new=mock_post,
    ):
        query = """
        query TestQuery($id: ID!) {
            findScene(id: $id) {
                id
                title
            }
        }
        """
        variables = {"id": "123"}
        result = await stash_client.execute(query, variables)
        assert isinstance(result, dict)
        assert result["findScene"]["id"] == "123"
        assert result["findScene"]["title"] == "Test Scene"

    # Test HTTP error
    mock_response = AsyncMock()
    mock_response.raise_for_status = AsyncMock(
        side_effect=httpx.HTTPError("Test error")
    )
    mock_response.json = AsyncMock(return_value={"error": "HTTP error"})
    mock_response.text = "Test error"
    mock_response.response = mock_response
    mock_post = AsyncMock(return_value=mock_response)

    with patch.object(
        stash_client.client,
        "post",
        new=mock_post,
    ):
        with pytest.raises(httpx.HTTPError, match="Test error"):
            await stash_client.execute("query { test }")
