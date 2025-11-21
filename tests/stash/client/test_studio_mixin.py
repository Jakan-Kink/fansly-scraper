"""Unit tests for StudioClientMixin.

These tests mock at the HTTP boundary using respx, allowing real code execution
through the entire GraphQL client stack including serialization/deserialization.
"""

import json

import httpx
import pytest
import respx
import strawberry

from stash import StashClient
from tests.fixtures import StudioFactory


def _studio_to_response_dict(studio) -> dict:
    """Convert a StudioFactory instance to a GraphQL response dict.

    This converts the Strawberry type to a plain dict suitable for
    mocking a GraphQL response.
    """
    data = strawberry.asdict(studio)
    # Handle nested objects that need conversion
    if data.get("parent_studio"):
        data["parent_studio"] = {"id": data["parent_studio"]["id"]}
    if data.get("tags"):
        data["tags"] = [{"id": t["id"]} for t in data["tags"]]
    return data


@pytest.mark.asyncio
async def test_find_studio(respx_stash_client: StashClient) -> None:
    """Test finding a studio by ID."""
    # Create test studio using factory
    test_studio = StudioFactory.build(
        id="123",
        name="Test Studio",
        urls=["https://example.com"],
        details="Test studio details",
        aliases=["Studio Test", "TestCo"],
    )
    studio_data = _studio_to_response_dict(test_studio)

    graphql_route = respx.post("http://localhost:9999/graphql").mock(
        return_value=httpx.Response(200, json={"data": {"findStudio": studio_data}})
    )

    studio = await respx_stash_client.find_studio("123")

    # Verify the result
    assert studio is not None
    assert studio.id == "123"
    assert studio.name == "Test Studio"
    assert studio.urls == ["https://example.com"]
    # Test backward compatibility - url property should check membership
    assert studio.url == "https://example.com"
    assert studio.details == "Test studio details"
    assert studio.aliases == ["Studio Test", "TestCo"]

    # Verify GraphQL call
    assert len(graphql_route.calls) == 1
    req = json.loads(graphql_route.calls[0].request.content)
    assert "findStudio" in req["query"]
    assert req["variables"]["id"] == "123"


@pytest.mark.asyncio
async def test_find_studio_not_found(respx_stash_client: StashClient) -> None:
    """Test finding a studio that doesn't exist."""
    graphql_route = respx.post("http://localhost:9999/graphql").mock(
        return_value=httpx.Response(200, json={"data": {"findStudio": None}})
    )

    studio = await respx_stash_client.find_studio("999")

    assert studio is None

    # Verify GraphQL call
    assert len(graphql_route.calls) == 1
    req = json.loads(graphql_route.calls[0].request.content)
    assert "findStudio" in req["query"]
    assert req["variables"]["id"] == "999"


@pytest.mark.asyncio
async def test_find_studio_error(respx_stash_client: StashClient) -> None:
    """Test handling errors when finding a studio."""
    graphql_route = respx.post("http://localhost:9999/graphql").mock(
        return_value=httpx.Response(500, json={"errors": [{"message": "Test error"}]})
    )

    # Use a unique ID that won't be cached
    studio = await respx_stash_client.find_studio("test_error_studio_999")

    assert studio is None

    # Verify GraphQL call was made
    assert len(graphql_route.calls) == 1


@pytest.mark.asyncio
async def test_find_studios(respx_stash_client: StashClient) -> None:
    """Test finding studios with filters."""
    # Create test studio using factory
    test_studio = StudioFactory.build(
        id="123",
        name="Test Studio",
        urls=["https://example.com"],
    )
    studio_data = _studio_to_response_dict(test_studio)

    graphql_route = respx.post("http://localhost:9999/graphql").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {
                    "findStudios": {
                        "count": 1,
                        "studios": [studio_data],
                    }
                }
            },
        )
    )

    result = await respx_stash_client.find_studios()

    # Verify the results
    assert result.count == 1
    assert len(result.studios) == 1
    assert result.studios[0].id == "123"
    assert result.studios[0].name == "Test Studio"

    # Verify GraphQL call
    assert len(graphql_route.calls) == 1
    req = json.loads(graphql_route.calls[0].request.content)
    assert "findStudios" in req["query"]


@pytest.mark.asyncio
async def test_find_studios_with_filter(respx_stash_client: StashClient) -> None:
    """Test finding studios with custom filter parameters."""
    # Create test studio using factory
    test_studio = StudioFactory.build(id="123", name="Test Studio")
    studio_data = _studio_to_response_dict(test_studio)

    graphql_route = respx.post("http://localhost:9999/graphql").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {
                    "findStudios": {
                        "count": 1,
                        "studios": [studio_data],
                    }
                }
            },
        )
    )

    result = await respx_stash_client.find_studios(
        filter_={"per_page": 10, "page": 1},
        q="Test",
    )

    # Verify the results
    assert result.count == 1
    assert len(result.studios) == 1

    # Verify GraphQL call includes filter params
    assert len(graphql_route.calls) == 1
    req = json.loads(graphql_route.calls[0].request.content)
    assert "findStudios" in req["query"]
    assert req["variables"]["filter"]["q"] == "Test"
    assert req["variables"]["filter"]["per_page"] == 10


@pytest.mark.asyncio
async def test_find_studios_error(respx_stash_client: StashClient) -> None:
    """Test handling errors when finding studios."""
    graphql_route = respx.post("http://localhost:9999/graphql").mock(
        return_value=httpx.Response(500, json={"errors": [{"message": "Test error"}]})
    )

    result = await respx_stash_client.find_studios()

    assert result.count == 0
    assert len(result.studios) == 0

    # Verify GraphQL call was attempted
    assert len(graphql_route.calls) == 1


@pytest.mark.asyncio
async def test_create_studio(respx_stash_client: StashClient) -> None:
    """Test creating a studio."""
    # Create response studio (what Stash returns)
    response_studio = StudioFactory.build(
        id="123",
        name="Test Studio",
        urls=["https://example.com"],
        details="Test studio details",
    )
    response_data = _studio_to_response_dict(response_studio)

    graphql_route = respx.post("http://localhost:9999/graphql").mock(
        return_value=httpx.Response(200, json={"data": {"studioCreate": response_data}})
    )

    # Create studio to send (input)
    studio = StudioFactory.build(
        id="new",
        name="New Studio",
        urls=[],
        details=None,
        aliases=[],
    )

    created = await respx_stash_client.create_studio(studio)

    # Verify the result
    assert created.id == "123"
    assert created.name == "Test Studio"

    # Verify GraphQL call
    assert len(graphql_route.calls) == 1
    req = json.loads(graphql_route.calls[0].request.content)
    assert "studioCreate" in req["query"]
    assert "input" in req["variables"]


@pytest.mark.asyncio
async def test_create_studio_with_all_fields(respx_stash_client: StashClient) -> None:
    """Test creating a studio with all optional fields."""
    # Create response studio
    response_studio = StudioFactory.build(
        id="123",
        name="Test Studio",
        urls=["https://example.com"],
    )
    response_data = _studio_to_response_dict(response_studio)

    graphql_route = respx.post("http://localhost:9999/graphql").mock(
        return_value=httpx.Response(200, json={"data": {"studioCreate": response_data}})
    )

    # Create studio with all fields
    studio = StudioFactory.build(
        id="new",
        name="Full Studio",
        urls=["https://example.com/full"],
        details="Full studio details",
        aliases=["Full Test"],
        image_path="/path/to/image.jpg",
    )

    created = await respx_stash_client.create_studio(studio)

    # Verify the result
    assert created.id == "123"
    assert created.name == "Test Studio"

    # Verify GraphQL call
    assert len(graphql_route.calls) == 1
    req = json.loads(graphql_route.calls[0].request.content)
    assert "studioCreate" in req["query"]


@pytest.mark.asyncio
async def test_create_studio_error(respx_stash_client: StashClient) -> None:
    """Test handling errors when creating a studio."""
    respx.post("http://localhost:9999/graphql").mock(
        return_value=httpx.Response(500, json={"errors": [{"message": "Test error"}]})
    )

    studio = StudioFactory.build(id="new", name="Test Studio")

    with pytest.raises(Exception):
        await respx_stash_client.create_studio(studio)


@pytest.mark.asyncio
async def test_update_studio(respx_stash_client: StashClient) -> None:
    """Test updating a studio."""
    # Create response studio with updated values
    updated_studio = StudioFactory.build(
        id="123",
        name="Updated Name",
        urls=["https://example.com"],
        details="Test studio details",
        aliases=["Studio Test", "TestCo"],
    )
    response_data = _studio_to_response_dict(updated_studio)

    graphql_route = respx.post("http://localhost:9999/graphql").mock(
        return_value=httpx.Response(200, json={"data": {"studioUpdate": response_data}})
    )

    # Create studio with original values
    studio = StudioFactory.build(
        id="123",
        name="Original Name",
        urls=["https://example.com"],
        details="Test studio details",
        aliases=["Studio Test", "TestCo"],
    )
    # Actually change the value to trigger dirty tracking
    studio.name = "Updated Name"

    updated = await respx_stash_client.update_studio(studio)

    # Verify the result
    assert updated.id == "123"
    assert updated.name == "Updated Name"

    # Verify GraphQL call
    assert len(graphql_route.calls) == 1
    req = json.loads(graphql_route.calls[0].request.content)
    assert "studioUpdate" in req["query"]
    assert "input" in req["variables"]


@pytest.mark.asyncio
async def test_update_studio_multiple_fields(respx_stash_client: StashClient) -> None:
    """Test updating multiple studio fields at once."""
    # Create response studio with updated values
    updated_studio = StudioFactory.build(
        id="123",
        name="Test Studio",
        urls=["https://example.com/updated"],
        details="Updated details",
        aliases=["Studio Test", "TestCo"],
    )
    response_data = _studio_to_response_dict(updated_studio)

    graphql_route = respx.post("http://localhost:9999/graphql").mock(
        return_value=httpx.Response(200, json={"data": {"studioUpdate": response_data}})
    )

    # Create studio with original values
    studio = StudioFactory.build(
        id="123",
        name="Test Studio",
        urls=["https://example.com/original"],
        details="Original details",
        aliases=["Studio Test", "TestCo"],
    )
    # Actually change multiple field values to trigger dirty tracking
    studio.urls = ["https://example.com/updated"]
    studio.details = "Updated details"

    updated = await respx_stash_client.update_studio(studio)

    # Verify the result
    assert updated.id == "123"
    assert updated.urls == ["https://example.com/updated"]
    assert updated.details == "Updated details"

    # Verify GraphQL call
    assert len(graphql_route.calls) == 1
    req = json.loads(graphql_route.calls[0].request.content)
    assert "studioUpdate" in req["query"]


@pytest.mark.asyncio
async def test_update_studio_no_changes(respx_stash_client: StashClient) -> None:
    """Test updating a studio with no actual changes skips the API call."""
    graphql_route = respx.post("http://localhost:9999/graphql").mock(
        return_value=httpx.Response(200, json={"data": {}})
    )

    # Create studio with no dirty fields
    studio = StudioFactory.build(
        id="123",
        name="Test Studio",
    )
    # Ensure no dirty attrs (simulating a freshly loaded studio)
    studio._dirty_attrs.clear()

    result = await respx_stash_client.update_studio(studio)

    # Should return the original studio without making API call
    assert result.id == "123"
    assert result.name == "Test Studio"

    # No GraphQL call should be made
    assert len(graphql_route.calls) == 0


@pytest.mark.asyncio
async def test_update_studio_error(respx_stash_client: StashClient) -> None:
    """Test handling errors when updating a studio."""
    respx.post("http://localhost:9999/graphql").mock(
        return_value=httpx.Response(500, json={"errors": [{"message": "Test error"}]})
    )

    # Create studio with original value
    studio = StudioFactory.build(id="123", name="Original")
    # Actually change the value to trigger dirty tracking
    studio.name = "Updated"

    with pytest.raises(Exception):
        await respx_stash_client.update_studio(studio)


@pytest.mark.asyncio
async def test_find_studio_caching(respx_stash_client: StashClient) -> None:
    """Test that find_studio results are cached."""
    # Create test studio
    test_studio = StudioFactory.build(id="123", name="Test Studio")
    studio_data = _studio_to_response_dict(test_studio)

    graphql_route = respx.post("http://localhost:9999/graphql").mock(
        side_effect=[
            httpx.Response(200, json={"data": {"findStudio": studio_data}}),
            httpx.Response(200, json={"data": {"findStudio": None}}),
        ]
    )

    # First call should hit the API
    studio1 = await respx_stash_client.find_studio("123")
    assert studio1 is not None
    assert len(graphql_route.calls) == 1

    # Second call with same ID should use cache
    studio2 = await respx_stash_client.find_studio("123")
    assert studio2 is not None
    assert len(graphql_route.calls) == 1  # Still only 1 call

    # Different ID should hit API again
    studio3 = await respx_stash_client.find_studio("456")
    assert studio3 is None
    assert len(graphql_route.calls) == 2


@pytest.mark.asyncio
async def test_create_studio_clears_cache(respx_stash_client: StashClient) -> None:
    """Test that create_studio clears the find caches."""
    # Create test studio
    test_studio = StudioFactory.build(id="123", name="Test Studio")
    studio_data = _studio_to_response_dict(test_studio)

    graphql_route = respx.post("http://localhost:9999/graphql").mock(
        side_effect=[
            # First find_studio call
            httpx.Response(200, json={"data": {"findStudio": studio_data}}),
            # create_studio call
            httpx.Response(200, json={"data": {"studioCreate": studio_data}}),
            # Second find_studio call (after cache clear)
            httpx.Response(200, json={"data": {"findStudio": studio_data}}),
        ]
    )

    # Populate cache
    await respx_stash_client.find_studio("123")
    assert len(graphql_route.calls) == 1

    # Create should clear cache
    studio = StudioFactory.build(id="new", name="New Studio")
    await respx_stash_client.create_studio(studio)
    assert len(graphql_route.calls) == 2

    # Next find should hit API again (cache was cleared)
    await respx_stash_client.find_studio("123")
    assert len(graphql_route.calls) == 3
