"""Unit tests for StashClient."""

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest
from gql.transport.exceptions import (
    TransportError,
    TransportQueryError,
    TransportServerError,
)
from graphql import (
    GraphQLField,
    GraphQLObjectType,
    GraphQLSchema,
    GraphQLString,
)

from stash import StashClient


@pytest.mark.asyncio
async def test_client_init() -> None:
    """Test client initialization."""
    # Test with minimal conn - use direct instantiation since we're testing create()
    client = await StashClient.create(conn={})
    assert client.url == "http://localhost:9999/graphql"
    assert not any(h.get("ApiKey") for h in [getattr(client.client, "headers", {}), {}])

    # Test with full conn
    client = await StashClient.create(
        conn={
            "Scheme": "http",
            "Host": "localhost",
            "Port": 9999,
            "ApiKey": "",
            "Logger": logging.getLogger("test"),
        },
        verify_ssl=False,
    )
    assert client.url == "http://localhost:9999/graphql"
    assert client.log.name == "test"

    # Test with 0.0.0.0 host (should convert to 127.0.0.1)
    client = await StashClient.create(conn={"Host": "0.0.0.0"})
    assert "127.0.0.1" in client.url

    # Test with None conn (should use defaults)
    client = await StashClient.create(conn=None)
    assert client.url == "http://localhost:9999/graphql"


@pytest.mark.asyncio
async def test_client_validation_error(mock_session, mock_client) -> None:
    """Test client-side GraphQL validation errors."""
    client = await StashClient.create(conn={})
    client._ensure_initialized = MagicMock()
    client.log = MagicMock()

    # Create a real schema with a valid 'hello' field but no 'test' field
    print("\nSetting up validation error test...")
    query_type = GraphQLObjectType(
        name="Query",
        fields={
            "hello": GraphQLField(
                type_=GraphQLString,
                resolve=lambda obj, info: "Hello!",
            )
        },
    )
    schema = GraphQLSchema(query=query_type)
    client.schema = schema
    print(f"Created schema: {schema}")

    mock_client.__aenter__.return_value = mock_session
    mock_client.close_async = AsyncMock()
    client.client = mock_client
    print(f"Mock client setup complete: {mock_client}")

    try:
        print("\nExecuting query...")
        result = await client.execute("query { test }")
        print(f"\nUnexpected success - result type: {type(result)}")
        print(f"Unexpected success - result value: {result}")
        raise AssertionError("Expected ValueError was not raised")
    except Exception as e:
        print(f"\nCaught exception type: {type(e).__name__}")
        print(f"Caught exception value: {e!s}")
        print(f"Caught exception repr: {e!r}")
        if not isinstance(e, ValueError):
            raise AssertionError(
                f"Expected ValueError but got {type(e).__name__}: {e!s}"
            )
        error_msg = str(e)
        assert "Invalid GraphQL query" in error_msg
        assert "Cannot query field 'test' on type 'Query'" in error_msg


@pytest.mark.asyncio
async def test_client_query_error(mock_session, mock_client) -> None:
    """Test client-side query validation errors."""
    client = await StashClient.create(conn={})
    client._ensure_initialized = MagicMock()
    client.log = MagicMock()

    # Create a schema with a field that will trigger a runtime error
    print("\nSetting up query error test...")
    query_type = GraphQLObjectType(
        name="Query",
        fields={"findScene": GraphQLField(type_=GraphQLString)},
    )
    schema = GraphQLSchema(query=query_type)
    client.schema = schema
    print(f"Created schema: {schema}")

    error_data = {
        "errors": [
            {
                "message": "GRAPHQL_VALIDATION_FAILED: Field does not exist",
                "locations": [{"line": 2, "column": 3}],
                "path": ["findScene"],
            }
        ],
        "data": None,
    }

    print("\nSetting up mocks...")
    transport_error = TransportQueryError(
        str(error_data["errors"][0]["message"]), errors=error_data["errors"]
    )
    print(f"Created transport error: {transport_error}")

    # Create a mock session that raises our error
    mock_session.execute.side_effect = transport_error
    print(f"Mock session execute: {mock_session.execute}")
    print(f"Mock session execute side_effect: {mock_session.execute.side_effect}")

    # Create a mock client that returns our session from __aenter__
    mock_client.__aenter__.return_value = mock_session
    mock_client.close_async = AsyncMock()
    client.client = mock_client
    print(f"Mock client setup complete: {mock_client}")

    try:
        print("\nExecuting query...")
        result = await client.execute("query { findScene }")
        print(f"\nUnexpected success - result type: {type(result)}")
        print(f"Unexpected success - result value: {result}")
        raise AssertionError("Expected ValueError was not raised")
    except Exception as e:
        print(f"\nCaught exception type: {type(e).__name__}")
        print(f"Caught exception value: {e!s}")
        print(f"Caught exception repr: {e!r}")
        if not isinstance(e, ValueError):
            raise AssertionError(
                f"Expected ValueError but got {type(e).__name__}: {e!s}"
            )
        error_msg = str(e)
        assert "GraphQL query error" in error_msg


@pytest.mark.asyncio
async def test_client_successful_response(mock_session, mock_client) -> None:
    """Test successful response handling."""
    client = await StashClient.create(conn={})
    client._ensure_initialized = MagicMock()
    client.log = MagicMock()

    # Set up mock response data
    expected_scene = {
        "id": "123",
        "title": "Test Scene",
        "details": "Scene details",
    }

    # Mock response with successful data
    success_response = {"findScene": expected_scene}
    mock_session.execute = AsyncMock(return_value=success_response)
    mock_client.__aenter__.return_value = mock_session
    mock_client.close_async = AsyncMock()  # Add mock for close_async
    client.client = mock_client

    # Execute query
    result = await client.execute(
        """
        query FindScene($id: ID!) {
            findScene(id: $id) {
                id
                title
                details
            }
        }
        """,
        {"id": "123"},
    )

    # Verify response
    assert isinstance(result, dict)
    assert "findScene" in result
    assert result["findScene"] == expected_scene


@pytest.mark.asyncio
async def test_client_null_response(mock_session, mock_client) -> None:
    """Test successful GraphQL query returning null data."""
    client = await StashClient.create(conn={})
    client._ensure_initialized = MagicMock()
    client.log = MagicMock()
    client.schema = {}  # Add schema to prevent validation issues

    # Set up mock session with null response
    null_data = {"findScene": None}
    mock_session.execute = AsyncMock(return_value=null_data)
    mock_client.__aenter__.return_value = mock_session
    mock_client.close_async = AsyncMock()  # Add mock for close_async
    client.client = mock_client

    # Execute query
    result = await client.execute(
        """
        query FindScene($id: ID!) {
            findScene(id: $id) {
                id
            }
        }
        """,
        {"id": "123"},
    )

    assert isinstance(result, dict)
    assert "findScene" in result
    assert result["findScene"] is None


@pytest.mark.asyncio
async def test_client_network_error(mock_session, mock_client) -> None:
    """Test network errors during GraphQL query."""
    client = await StashClient.create(conn={})
    client._ensure_initialized = MagicMock()
    client.log = MagicMock()

    # Create basic schema
    print("\nSetting up network error test...")
    query_type = GraphQLObjectType(
        name="Query",
        fields={"test": GraphQLField(type_=GraphQLString)},
    )
    schema = GraphQLSchema(query=query_type)
    client.schema = schema
    print(f"Created schema: {schema}")

    print("\nSetting up network error mocks...")
    transport_error = TransportError("Network timeout")
    print(f"Created transport error: {transport_error}")

    # Create a mock session that raises our error
    mock_session.execute = AsyncMock(side_effect=transport_error)
    print(f"Mock session execute: {mock_session.execute}")
    print(f"Mock session execute side_effect: {mock_session.execute.side_effect}")

    # Create a mock client that returns our session from __aenter__
    mock_client.__aenter__.return_value = mock_session
    mock_client.close_async = AsyncMock()
    client.client = mock_client
    print(f"Mock client setup complete: {mock_client}")

    try:
        print("\nExecuting query...")
        result = await client.execute("query { test }")
        print(f"\nUnexpected success - result type: {type(result)}")
        print(f"Unexpected success - result value: {result}")
        raise AssertionError("Expected ValueError was not raised")
    except Exception as e:
        print(f"\nCaught exception type: {type(e).__name__}")
        print(f"Caught exception value: {e!s}")
        print(f"Caught exception repr: {e!r}")
        if not isinstance(e, ValueError):
            raise AssertionError(
                f"Expected ValueError but got {type(e).__name__}: {e!s}"
            )
        error_msg = str(e)
        assert "Failed to connect" in error_msg


@pytest.mark.asyncio
async def test_client_server_error(mock_session, mock_client) -> None:
    """Test server errors during GraphQL query."""
    client = await StashClient.create(conn={})
    client._ensure_initialized = MagicMock()
    client.log = MagicMock()

    # Create basic schema
    print("\nSetting up server error test...")
    query_type = GraphQLObjectType(
        name="Query",
        fields={"test": GraphQLField(type_=GraphQLString)},
    )
    schema = GraphQLSchema(query=query_type)
    client.schema = schema
    print(f"Created schema: {schema}")

    print("\nSetting up server error mocks...")
    error_message = "Internal server error"
    server_error = TransportServerError(
        f"Server responded with status 500: {error_message}",
    )
    print(f"Created server error: {server_error}")

    # Create a mock session that raises our error
    mock_session.execute = AsyncMock(side_effect=server_error)
    print(f"Mock session execute: {mock_session.execute}")
    print(f"Mock session execute side_effect: {mock_session.execute.side_effect}")

    # Create a mock client that returns our session from __aenter__
    mock_client.__aenter__.return_value = mock_session
    mock_client.close_async = AsyncMock()
    client.client = mock_client
    print(f"Mock client setup complete: {mock_client}")

    try:
        print("\nExecuting query...")
        result = await client.execute("query { test }")
        print(f"\nUnexpected success - result type: {type(result)}")
        print(f"Unexpected success - result value: {result}")
        raise AssertionError("Expected ValueError was not raised")
    except Exception as e:
        print(f"\nCaught exception type: {type(e).__name__}")
        print(f"Caught exception value: {e!s}")
        print(f"Caught exception repr: {e!r}")
        if not isinstance(e, ValueError):
            raise AssertionError(
                f"Expected ValueError but got {type(e).__name__}: {e!s}"
            )
        error_msg = str(e)
        assert "GraphQL server error" in error_msg


@pytest.mark.asyncio
async def test_query_errors(mock_session, mock_client) -> None:
    """Test handling of GraphQL query syntax errors."""
    client = await StashClient.create(conn={})
    client._ensure_initialized = MagicMock()
    client.log = MagicMock()

    # Create basic schema
    query_type = GraphQLObjectType(
        name="Query",
        fields={"test": GraphQLField(type_=GraphQLString)},
    )
    schema = GraphQLSchema(query=query_type)
    client.schema = schema

    # Mock response with syntax error
    error_data = {
        "errors": [
            {
                "message": "Syntax Error: Expected Name, found <EOF>",
                "locations": [{"line": 1, "column": 7}],
            }
        ],
        "data": None,
    }

    mock_session.execute = AsyncMock(
        side_effect=TransportQueryError(
            str(error_data["errors"][0]["message"]), errors=error_data["errors"]
        )
    )
    mock_client.__aenter__.return_value = mock_session
    mock_client.close_async = AsyncMock()
    client.client = mock_client

    with pytest.raises(ValueError) as exc_info:
        await client.execute("query {", {})  # Intentionally malformed query

    error_msg = str(exc_info.value)
    assert "Invalid GraphQL query" in error_msg
    assert "Syntax Error" in error_msg
