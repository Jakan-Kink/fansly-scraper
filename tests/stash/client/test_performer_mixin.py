"""Unit tests for PerformerClientMixin.

These tests mock at the HTTP boundary using respx, allowing real code execution
through the entire GraphQL client stack including serialization/deserialization.
"""

import json
from pathlib import Path

import httpx
import pytest
import respx
import strawberry

from stash import StashClient
from stash.types.enums import GenderEnum
from stash.types.performer import Performer
from tests.fixtures import PerformerFactory, TagFactory
from tests.fixtures.metadata.metadata_factories import AccountFactory


def _performer_to_response_dict(performer) -> dict:
    """Convert a PerformerFactory instance to a GraphQL response dict.

    This converts the Strawberry type to a plain dict suitable for
    mocking a GraphQL response.
    """
    data = strawberry.asdict(performer)
    # Handle nested objects that need conversion
    if data.get("tags"):
        data["tags"] = [
            {"id": t["id"], "name": t.get("name", "")} for t in data["tags"]
        ]
    if data.get("scenes"):
        data["scenes"] = [{"id": s["id"]} for s in data["scenes"]]
    if data.get("groups"):
        data["groups"] = [{"id": g["id"]} for g in data["groups"]]
    if data.get("stash_ids"):
        data["stash_ids"] = [
            {"endpoint": s.get("endpoint", ""), "stash_id": s.get("stash_id", "")}
            for s in data["stash_ids"]
        ]
    return data


@pytest.mark.asyncio
async def test_find_performer(respx_stash_client: StashClient) -> None:
    """Test finding a performer by ID."""
    # Create test performer with tags using factory
    test_tag = TagFactory.build(id="456", name="Test Tag")
    test_performer = PerformerFactory.build(
        id="123",
        name="Test Performer",
        gender="FEMALE",
        birthdate="1990-01-15",
        measurements="34-24-36",
        alias_list=["Alias1", "Alias2"],
        tags=[test_tag],
    )
    performer_data = _performer_to_response_dict(test_performer)

    graphql_route = respx.post("http://localhost:9999/graphql").mock(
        return_value=httpx.Response(
            200, json={"data": {"findPerformer": performer_data}}
        )
    )

    performer = await respx_stash_client.find_performer("123")

    # Verify the result
    assert performer is not None
    assert performer.id == "123"
    assert performer.name == "Test Performer"
    assert performer.gender == "FEMALE"
    assert performer.birthdate == "1990-01-15"
    assert performer.measurements == "34-24-36"
    assert performer.alias_list == ["Alias1", "Alias2"]
    assert len(performer.tags) == 1

    # Verify GraphQL call
    assert len(graphql_route.calls) == 1
    req = json.loads(graphql_route.calls[0].request.content)
    assert "findPerformer" in req["query"]
    assert req["variables"]["id"] == "123"


@pytest.mark.asyncio
async def test_find_performer_by_name(respx_stash_client: StashClient) -> None:
    """Test finding a performer by name."""
    test_performer = PerformerFactory.build(
        id="123",
        name="Jane Doe",
    )
    performer_data = _performer_to_response_dict(test_performer)

    graphql_route = respx.post("http://localhost:9999/graphql").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {
                    "findPerformers": {
                        "count": 1,
                        "performers": [performer_data],
                    }
                }
            },
        )
    )

    # Find by name (triggers find_performers internally)
    performer = await respx_stash_client.find_performer({"name": "Jane Doe"})

    # Verify the result
    assert performer is not None
    assert performer.id == "123"
    assert performer.name == "Jane Doe"

    # Verify GraphQL call used findPerformers with name filter
    assert len(graphql_route.calls) == 1
    req = json.loads(graphql_route.calls[0].request.content)
    assert "findPerformers" in req["query"]


@pytest.mark.asyncio
async def test_find_performer_not_found(respx_stash_client: StashClient) -> None:
    """Test finding a performer that doesn't exist."""
    graphql_route = respx.post("http://localhost:9999/graphql").mock(
        return_value=httpx.Response(200, json={"data": {"findPerformer": None}})
    )

    performer = await respx_stash_client.find_performer("999")

    assert performer is None

    # Verify GraphQL call
    assert len(graphql_route.calls) == 1
    req = json.loads(graphql_route.calls[0].request.content)
    assert "findPerformer" in req["query"]
    assert req["variables"]["id"] == "999"


@pytest.mark.asyncio
async def test_find_performer_error(respx_stash_client: StashClient) -> None:
    """Test handling errors when finding a performer."""
    graphql_route = respx.post("http://localhost:9999/graphql").mock(
        return_value=httpx.Response(500, json={"errors": [{"message": "Test error"}]})
    )

    # Use a numeric ID (non-numeric triggers name search with 2 calls)
    performer = await respx_stash_client.find_performer("99999")

    assert performer is None

    # Verify GraphQL call was made
    assert len(graphql_route.calls) == 1


@pytest.mark.asyncio
async def test_find_performers(respx_stash_client: StashClient) -> None:
    """Test finding performers with filters."""
    # Create test performer using factory
    test_performer = PerformerFactory.build(
        id="123",
        name="Test Performer",
        gender="FEMALE",
    )
    performer_data = _performer_to_response_dict(test_performer)

    graphql_route = respx.post("http://localhost:9999/graphql").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {
                    "findPerformers": {
                        "count": 1,
                        "performers": [performer_data],
                    }
                }
            },
        )
    )

    result = await respx_stash_client.find_performers()

    # Verify the results
    assert result.count == 1
    assert len(result.performers) == 1

    # Verify GraphQL call
    assert len(graphql_route.calls) == 1
    req = json.loads(graphql_route.calls[0].request.content)
    assert "findPerformers" in req["query"]


@pytest.mark.asyncio
async def test_find_performers_with_filter(respx_stash_client: StashClient) -> None:
    """Test finding performers with custom filter parameters."""
    # Create test performer using factory
    test_performer = PerformerFactory.build(
        id="123",
        name="Test Performer",
        gender="FEMALE",
    )
    performer_data = _performer_to_response_dict(test_performer)

    graphql_route = respx.post("http://localhost:9999/graphql").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {
                    "findPerformers": {
                        "count": 1,
                        "performers": [performer_data],
                    }
                }
            },
        )
    )

    result = await respx_stash_client.find_performers(
        filter_={"per_page": 10, "page": 1},
        performer_filter={
            "name": {"modifier": "EQUALS", "value": "Test Performer"},
            "gender": "FEMALE",
        },
    )

    # Verify the results
    assert result.count == 1
    assert len(result.performers) == 1

    # Verify GraphQL call includes filter params
    assert len(graphql_route.calls) == 1
    req = json.loads(graphql_route.calls[0].request.content)
    assert "findPerformers" in req["query"]
    assert req["variables"]["filter"]["per_page"] == 10
    assert req["variables"]["performer_filter"]["gender"] == "FEMALE"


@pytest.mark.asyncio
async def test_find_performers_error(respx_stash_client: StashClient) -> None:
    """Test handling errors when finding performers."""
    graphql_route = respx.post("http://localhost:9999/graphql").mock(
        return_value=httpx.Response(500, json={"errors": [{"message": "Test error"}]})
    )

    result = await respx_stash_client.find_performers()

    # Should return empty result with default values
    assert result.count == 0
    assert len(result.performers) == 0

    # Verify GraphQL call was attempted
    assert len(graphql_route.calls) == 1


@pytest.mark.asyncio
async def test_create_performer(respx_stash_client: StashClient) -> None:
    """Test creating a performer."""
    # Create response performer with server-generated ID
    created_performer = PerformerFactory.build(
        id="456",
        name="New Performer",
        gender="FEMALE",
    )
    response_data = _performer_to_response_dict(created_performer)

    graphql_route = respx.post("http://localhost:9999/graphql").mock(
        return_value=httpx.Response(
            200, json={"data": {"performerCreate": response_data}}
        )
    )

    # Create performer with minimum required fields
    performer = Performer(
        id="new",
        name="New Performer",
        gender=GenderEnum.FEMALE,
        groups=[],
        tags=[],
        scenes=[],
        stash_ids=[],
        alias_list=[],
    )
    created = await respx_stash_client.create_performer(performer)

    # Verify the result
    assert created.id == "456"
    assert created.name == "New Performer"
    assert created.gender == "FEMALE"

    # Verify GraphQL call
    assert len(graphql_route.calls) == 1
    req = json.loads(graphql_route.calls[0].request.content)
    assert "performerCreate" in req["query"]
    assert "input" in req["variables"]


@pytest.mark.asyncio
async def test_create_performer_with_metadata(respx_stash_client: StashClient) -> None:
    """Test creating a performer with full metadata."""
    # Create response performer
    created_performer = PerformerFactory.build(
        id="456",
        name="Jane Doe",
        gender="FEMALE",
        birthdate="1990-01-15",
        ethnicity="Caucasian",
        country="USA",
        eye_color="Blue",
        height_cm=170,
        measurements="34-24-36",
        alias_list=["Jane", "J.D."],
    )
    response_data = _performer_to_response_dict(created_performer)

    graphql_route = respx.post("http://localhost:9999/graphql").mock(
        return_value=httpx.Response(
            200, json={"data": {"performerCreate": response_data}}
        )
    )

    # Create performer with all metadata
    performer = Performer(
        id="new",
        name="Jane Doe",
        gender=GenderEnum.FEMALE,
        birthdate="1990-01-15",
        ethnicity="Caucasian",
        country="USA",
        eye_color="Blue",
        height_cm=170,
        measurements="34-24-36",
        alias_list=["Jane", "J.D."],
        groups=[],
        tags=[],
        scenes=[],
        stash_ids=[],
    )
    created = await respx_stash_client.create_performer(performer)

    # Verify the result
    assert created.id == "456"
    assert created.name == "Jane Doe"
    assert created.birthdate == "1990-01-15"
    assert created.measurements == "34-24-36"
    assert created.alias_list == ["Jane", "J.D."]

    # Verify GraphQL call
    assert len(graphql_route.calls) == 1
    req = json.loads(graphql_route.calls[0].request.content)
    assert "performerCreate" in req["query"]


@pytest.mark.asyncio
async def test_create_performer_error(respx_stash_client: StashClient) -> None:
    """Test handling errors when creating a performer."""
    respx.post("http://localhost:9999/graphql").mock(
        return_value=httpx.Response(500, json={"errors": [{"message": "Test error"}]})
    )

    performer = Performer(
        id="new",
        name="Test Performer",
        groups=[],
        tags=[],
        scenes=[],
        stash_ids=[],
        alias_list=[],
    )

    with pytest.raises(Exception):
        await respx_stash_client.create_performer(performer)


@pytest.mark.asyncio
async def test_update_performer(respx_stash_client: StashClient) -> None:
    """Test updating a performer."""
    # Create response performer with updated values
    updated_performer = PerformerFactory.build(
        id="123",
        name="Updated Name",
        details="Updated details",
    )
    response_data = _performer_to_response_dict(updated_performer)

    graphql_route = respx.post("http://localhost:9999/graphql").mock(
        return_value=httpx.Response(
            200, json={"data": {"performerUpdate": response_data}}
        )
    )

    # Create performer with original values
    performer = PerformerFactory.build(
        id="123",
        name="Original Name",
        details="Original details",
    )
    # Actually change the value to trigger dirty tracking
    performer.name = "Updated Name"
    performer.details = "Updated details"

    updated = await respx_stash_client.update_performer(performer)

    # Verify the result
    assert updated.id == "123"
    assert updated.name == "Updated Name"
    assert updated.details == "Updated details"

    # Verify GraphQL call
    assert len(graphql_route.calls) == 1
    req = json.loads(graphql_route.calls[0].request.content)
    assert "performerUpdate" in req["query"]
    assert "input" in req["variables"]


@pytest.mark.asyncio
async def test_update_performer_no_changes(respx_stash_client: StashClient) -> None:
    """Test updating a performer with no actual changes skips the API call."""
    graphql_route = respx.post("http://localhost:9999/graphql").mock(
        return_value=httpx.Response(200, json={"data": {}})
    )

    # Create performer with no dirty fields
    performer = PerformerFactory.build(
        id="123",
        name="Test Performer",
    )
    # Ensure no dirty attrs (simulating a freshly loaded performer)
    performer._dirty_attrs.clear()

    result = await respx_stash_client.update_performer(performer)

    # Should return the original performer without making API call
    assert result.id == "123"
    assert result.name == "Test Performer"

    # No GraphQL call should be made
    assert len(graphql_route.calls) == 0


@pytest.mark.asyncio
async def test_update_performer_error(respx_stash_client: StashClient) -> None:
    """Test handling errors when updating a performer."""
    respx.post("http://localhost:9999/graphql").mock(
        return_value=httpx.Response(500, json={"errors": [{"message": "Test error"}]})
    )

    # Create performer with original value
    performer = PerformerFactory.build(id="123", name="Original")
    # Actually change the value to trigger dirty tracking
    performer.name = "Updated"

    with pytest.raises(Exception):
        await respx_stash_client.update_performer(performer)


@pytest.mark.asyncio
async def test_update_performer_image(
    respx_stash_client: StashClient, tmp_path: Path
) -> None:
    """Test updating a performer's avatar image."""
    # Create a temporary image file
    image_path = tmp_path / "avatar.jpg"
    image_path.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 100)  # Minimal JPEG header

    # Create response performer with image_path set
    updated_performer = PerformerFactory.build(
        id="123",
        name="Test Performer",
        image_path="/path/to/avatar.jpg",
    )
    response_data = _performer_to_response_dict(updated_performer)

    graphql_route = respx.post("http://localhost:9999/graphql").mock(
        return_value=httpx.Response(
            200, json={"data": {"performerUpdate": response_data}}
        )
    )

    # Create performer and update avatar
    performer = PerformerFactory.build(
        id="123",
        name="Test Performer",
    )
    updated = await performer.update_avatar(respx_stash_client, image_path)

    # Verify the result
    assert updated.id == "123"
    assert updated.image_path == "/path/to/avatar.jpg"

    # Verify GraphQL call
    assert len(graphql_route.calls) == 1
    req = json.loads(graphql_route.calls[0].request.content)
    assert "performerUpdate" in req["query"]
    # Verify image data was sent as base64
    assert "image" in req["variables"]["input"]
    assert req["variables"]["input"]["image"].startswith("data:image/jpeg;base64,")


@pytest.mark.asyncio
async def test_update_performer_image_not_found(
    respx_stash_client: StashClient, tmp_path: Path
) -> None:
    """Test updating avatar with non-existent file raises error."""
    performer = PerformerFactory.build(id="123", name="Test Performer")

    with pytest.raises(FileNotFoundError):
        await performer.update_avatar(respx_stash_client, tmp_path / "nonexistent.jpg")


@pytest.mark.asyncio
async def test_performer_from_account(respx_stash_client: StashClient) -> None:
    """Test creating a performer from an account."""
    # Use AccountFactory
    account = AccountFactory.build(
        id=123,
        username="test_account",
        displayName="Test Display Name",
        about="Test account bio",
        location="US",
    )

    # Create response performer
    created_performer = PerformerFactory.build(
        id="456",
        name="Test Display Name",
        details="Test account bio",
        country="US",
    )
    response_data = _performer_to_response_dict(created_performer)

    graphql_route = respx.post("http://localhost:9999/graphql").mock(
        return_value=httpx.Response(
            200, json={"data": {"performerCreate": response_data}}
        )
    )

    # Convert account to performer
    performer = Performer.from_account(account)
    assert performer.name == "Test Display Name"
    assert performer.details == "Test account bio"
    assert performer.country == "US"

    # Create in Stash
    created = await respx_stash_client.create_performer(performer)
    assert created.id == "456"
    assert created.name == "Test Display Name"

    # Verify GraphQL call
    assert len(graphql_route.calls) == 1
    req = json.loads(graphql_route.calls[0].request.content)
    assert "performerCreate" in req["query"]


@pytest.mark.asyncio
async def test_find_performer_caching(respx_stash_client: StashClient) -> None:
    """Test that find_performer results are cached."""
    # Create test performer
    test_performer = PerformerFactory.build(id="123", name="Test Performer")
    performer_data = _performer_to_response_dict(test_performer)

    graphql_route = respx.post("http://localhost:9999/graphql").mock(
        side_effect=[
            httpx.Response(200, json={"data": {"findPerformer": performer_data}}),
            httpx.Response(200, json={"data": {"findPerformer": None}}),
        ]
    )

    # First call should hit the API
    performer1 = await respx_stash_client.find_performer("123")
    assert performer1 is not None
    assert len(graphql_route.calls) == 1

    # Second call with same ID should use cache
    performer2 = await respx_stash_client.find_performer("123")
    assert performer2 is not None
    assert len(graphql_route.calls) == 1  # Still only 1 call

    # Different ID should hit API again
    performer3 = await respx_stash_client.find_performer("456")
    assert performer3 is None
    assert len(graphql_route.calls) == 2


@pytest.mark.asyncio
async def test_get_or_create_performer_existing(
    respx_stash_client: StashClient,
) -> None:
    """Test get_or_create_performer finds existing performer."""
    # Create test performer
    test_performer = PerformerFactory.build(
        id="123",
        name="Existing Performer",
        urls=["https://example.com/performer"],
    )
    performer_data = _performer_to_response_dict(test_performer)

    graphql_route = respx.post("http://localhost:9999/graphql").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {
                    "findPerformers": {
                        "count": 1,
                        "performers": [performer_data],
                    }
                }
            },
        )
    )

    # Try to get or create performer
    performer = Performer(
        id="new",
        name="Existing Performer",
        urls=["https://example.com/performer"],
        groups=[],
        tags=[],
        scenes=[],
        stash_ids=[],
        alias_list=[],
    )
    result = await respx_stash_client.get_or_create_performer(performer)

    # Should return existing performer
    assert result.id == "123"
    assert result.name == "Existing Performer"

    # Verify only findPerformers was called (not performerCreate)
    assert len(graphql_route.calls) == 1
    req = json.loads(graphql_route.calls[0].request.content)
    assert "findPerformers" in req["query"]


@pytest.mark.asyncio
async def test_get_or_create_performer_new(respx_stash_client: StashClient) -> None:
    """Test get_or_create_performer creates new performer when not found."""
    # Create response performer
    created_performer = PerformerFactory.build(
        id="456",
        name="New Performer",
    )
    response_data = _performer_to_response_dict(created_performer)

    graphql_route = respx.post("http://localhost:9999/graphql").mock(
        side_effect=[
            # First call - findPerformers returns empty
            httpx.Response(
                200,
                json={
                    "data": {
                        "findPerformers": {
                            "count": 0,
                            "performers": [],
                        }
                    }
                },
            ),
            # Second call - performerCreate
            httpx.Response(200, json={"data": {"performerCreate": response_data}}),
        ]
    )

    # Try to get or create performer
    performer = Performer(
        id="new",
        name="New Performer",
        groups=[],
        tags=[],
        scenes=[],
        stash_ids=[],
        alias_list=[],
    )
    result = await respx_stash_client.get_or_create_performer(performer)

    # Should return newly created performer
    assert result.id == "456"
    assert result.name == "New Performer"

    # Verify both calls were made
    assert len(graphql_route.calls) == 2
    req1 = json.loads(graphql_route.calls[0].request.content)
    assert "findPerformers" in req1["query"]
    req2 = json.loads(graphql_route.calls[1].request.content)
    assert "performerCreate" in req2["query"]
