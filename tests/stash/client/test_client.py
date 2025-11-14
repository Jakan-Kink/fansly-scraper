"""Unit tests for StashClient."""

import logging

import httpx
import pytest
import respx
from graphql import GraphQLField, GraphQLObjectType, GraphQLSchema, GraphQLString

from errors import StashConnectionError, StashGraphQLError, StashServerError
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
    bad_host = "0.0.0.0"  # noqa: S104 - testing host conversion
    client = await StashClient.create(conn={"Host": bad_host})
    assert "127.0.0.1" in client.url

    # Test with None conn (should use defaults)
    client = await StashClient.create(conn=None)
    assert client.url == "http://localhost:9999/graphql"


@respx.mock
@pytest.mark.asyncio
async def test_client_validation_error() -> None:
    """Test GraphQL schema validation errors (querying non-existent field)."""
    # Mock GraphQL endpoint for initialization
    graphql_route = respx.post("http://localhost:9999/graphql")
    graphql_route.mock(return_value=httpx.Response(200, json={"data": {}}))

    # Create client
    client = await StashClient.create(conn={})

    # Create a real schema with a valid 'hello' field but no 'test' field
    query_type = GraphQLObjectType(
        name="Query",
        fields={
            "hello": GraphQLField(
                type_=GraphQLString,
                resolve=lambda _obj, _info: "Hello!",
            )
        },
    )
    schema = GraphQLSchema(query=query_type)
    client.schema = schema

    # Mock GraphQL response with validation error for non-existent field
    error_data = {
        "errors": [
            {
                "message": "Cannot query field 'test' on type 'Query'. Did you mean 'hello'?",
                "locations": [{"line": 1, "column": 9}],
            }
        ],
        "data": None,
    }
    graphql_route.mock(return_value=httpx.Response(200, json=error_data))

    # Test validation error - query non-existent field
    with pytest.raises(StashGraphQLError) as exc_info:
        await client.execute("query { test }")

    error_msg = str(exc_info.value)
    assert "GraphQL query error" in error_msg
    assert "Cannot query field 'test' on type 'Query'" in error_msg

    await client.close()


@respx.mock
@pytest.mark.asyncio
async def test_client_query_error() -> None:
    """Test client-side query validation errors."""
    # Mock GraphQL endpoint for initialization
    graphql_route = respx.post("http://localhost:9999/graphql")
    graphql_route.mock(return_value=httpx.Response(200, json={"data": {}}))

    # Create client
    client = await StashClient.create(conn={})

    # Create a schema with a field that will trigger a runtime error
    query_type = GraphQLObjectType(
        name="Query",
        fields={"findScene": GraphQLField(type_=GraphQLString)},
    )
    schema = GraphQLSchema(query=query_type)
    client.schema = schema

    # Mock GraphQL response with validation error
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
    graphql_route.mock(return_value=httpx.Response(200, json=error_data))

    # Test query error
    with pytest.raises(StashGraphQLError) as exc_info:
        await client.execute("query { findScene }")

    error_msg = str(exc_info.value)
    assert "GraphQL query error" in error_msg

    await client.close()


@respx.mock
@pytest.mark.asyncio
async def test_client_successful_response() -> None:
    """Test successful response handling."""
    # Mock GraphQL endpoint for initialization
    graphql_route = respx.post("http://localhost:9999/graphql")
    graphql_route.mock(return_value=httpx.Response(200, json={"data": {}}))

    # Create client
    client = await StashClient.create(conn={})

    # Set up mock response data
    expected_scene = {
        "id": "123",
        "title": "Test Scene",
        "details": "Scene details",
    }

    # Mock successful GraphQL response
    graphql_route.mock(
        return_value=httpx.Response(200, json={"data": {"findScene": expected_scene}})
    )

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

    # Verify response - client.execute() returns only the data portion
    assert isinstance(result, dict)
    assert "findScene" in result
    assert result["findScene"] == expected_scene

    await client.close()


@respx.mock
@pytest.mark.asyncio
async def test_client_null_response() -> None:
    """Test successful GraphQL query returning null data."""
    # Mock GraphQL endpoint for initialization
    graphql_route = respx.post("http://localhost:9999/graphql")
    graphql_route.mock(return_value=httpx.Response(200, json={"data": {}}))

    # Create client
    client = await StashClient.create(conn={})

    # Mock GraphQL response with null data
    graphql_route.mock(
        return_value=httpx.Response(200, json={"data": {"findScene": None}})
    )

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

    # Verify response - client.execute() returns only the data portion
    assert isinstance(result, dict)
    assert "findScene" in result
    assert result["findScene"] is None

    await client.close()


@respx.mock
@pytest.mark.asyncio
async def test_client_network_error() -> None:
    """Test network errors during GraphQL query."""
    # Mock GraphQL endpoint for initialization
    graphql_route = respx.post("http://localhost:9999/graphql")
    graphql_route.mock(return_value=httpx.Response(200, json={"data": {}}))

    # Create client
    client = await StashClient.create(conn={})

    # Create basic schema
    query_type = GraphQLObjectType(
        name="Query",
        fields={"test": GraphQLField(type_=GraphQLString)},
    )
    schema = GraphQLSchema(query=query_type)
    client.schema = schema

    # Mock network error
    graphql_route.mock(side_effect=httpx.NetworkError("Network timeout"))

    # Test network error
    with pytest.raises(StashConnectionError) as exc_info:
        await client.execute("query { test }")

    error_msg = str(exc_info.value)
    assert "Failed to connect" in error_msg

    await client.close()


@respx.mock
@pytest.mark.asyncio
async def test_client_server_error() -> None:
    """Test server errors during GraphQL query."""
    # Mock GraphQL endpoint for initialization
    graphql_route = respx.post("http://localhost:9999/graphql")
    graphql_route.mock(return_value=httpx.Response(200, json={"data": {}}))

    # Create client
    client = await StashClient.create(conn={})

    # Create basic schema
    query_type = GraphQLObjectType(
        name="Query",
        fields={"test": GraphQLField(type_=GraphQLString)},
    )
    schema = GraphQLSchema(query=query_type)
    client.schema = schema

    # Mock server error - HTTP 500 response
    error_message = "Internal server error"
    graphql_route.mock(
        return_value=httpx.Response(500, text=error_message)
    )

    # Test server error
    with pytest.raises(StashServerError) as exc_info:
        await client.execute("query { test }")

    error_msg = str(exc_info.value)
    assert "GraphQL server error" in error_msg

    await client.close()


@respx.mock
@pytest.mark.asyncio
async def test_query_errors() -> None:
    """Test handling of GraphQL query syntax errors."""
    # Mock GraphQL endpoint for initialization
    graphql_route = respx.post("http://localhost:9999/graphql")
    graphql_route.mock(return_value=httpx.Response(200, json={"data": {}}))

    # Create client
    client = await StashClient.create(conn={})

    # Create basic schema
    query_type = GraphQLObjectType(
        name="Query",
        fields={"test": GraphQLField(type_=GraphQLString)},
    )
    schema = GraphQLSchema(query=query_type)
    client.schema = schema

    # Mock GraphQL response with syntax error
    error_data = {
        "errors": [
            {
                "message": "Syntax Error: Expected Name, found <EOF>",
                "locations": [{"line": 1, "column": 7}],
            }
        ],
        "data": None,
    }
    graphql_route.mock(return_value=httpx.Response(200, json=error_data))

    # Test syntax error
    with pytest.raises(ValueError) as exc_info:  # noqa: PT011 - message validated by assertions below
        await client.execute("query {", {})  # Intentionally malformed query

    error_msg = str(exc_info.value)
    assert "Invalid GraphQL query syntax" in error_msg
    assert "Syntax Error" in error_msg

    await client.close()
