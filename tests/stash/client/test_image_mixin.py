"""Unit tests for ImageClientMixin.

These tests mock at the HTTP boundary using respx, allowing real code execution
through the entire GraphQL client stack including serialization/deserialization.
"""

import json

import httpx
import pytest
import respx
import strawberry

from stash import StashClient
from tests.fixtures import ImageFactory, ImageFileFactory


def _image_to_response_dict(image) -> dict:
    """Convert an ImageFactory instance to a GraphQL response dict.

    This converts the Strawberry type to a plain dict suitable for
    mocking a GraphQL response.
    """
    data = strawberry.asdict(image)
    # Handle nested objects that need conversion
    if data.get("visual_files"):
        data["visual_files"] = [
            {
                "id": f["id"],
                "path": f["path"],
                "basename": f["basename"],
                "size": f["size"],
                "width": f["width"],
                "height": f["height"],
                "parent_folder_id": f["parent_folder_id"],
                "mod_time": f["mod_time"].isoformat() if f.get("mod_time") else None,
                "fingerprints": f.get("fingerprints", []),
            }
            for f in data["visual_files"]
        ]
    if data.get("tags"):
        data["tags"] = [{"id": t["id"]} for t in data["tags"]]
    if data.get("performers"):
        data["performers"] = [{"id": p["id"]} for p in data["performers"]]
    if data.get("galleries"):
        data["galleries"] = [{"id": g["id"]} for g in data["galleries"]]
    if data.get("studio"):
        data["studio"] = {"id": data["studio"]["id"]}
    return data


@pytest.mark.asyncio
async def test_find_image(respx_stash_client: StashClient) -> None:
    """Test finding an image by ID."""
    # Create test image with visual files using factory
    image_file = ImageFileFactory.build(
        id="456",
        path="/path/to/image.jpg",
        basename="image.jpg",
        size=512000,
        width=1920,
        height=1080,
    )
    test_image = ImageFactory.build(
        id="123",
        title="Test Image",
        date="2024-01-01",
        urls=["https://example.com/image"],
        organized=True,
        visual_files=[image_file],
    )
    image_data = _image_to_response_dict(test_image)

    graphql_route = respx.post("http://localhost:9999/graphql").mock(
        return_value=httpx.Response(200, json={"data": {"findImage": image_data}})
    )

    image = await respx_stash_client.find_image("123")

    # Verify the result
    assert image is not None
    assert image.id == "123"
    assert image.title == "Test Image"
    assert image.date == "2024-01-01"
    assert image.urls == ["https://example.com/image"]
    assert image.organized is True
    assert len(image.visual_files) == 1
    assert image.visual_files[0].path == "/path/to/image.jpg"

    # Verify GraphQL call
    assert len(graphql_route.calls) == 1
    req = json.loads(graphql_route.calls[0].request.content)
    assert "findImage" in req["query"]
    assert req["variables"]["id"] == "123"


@pytest.mark.asyncio
async def test_find_image_not_found(respx_stash_client: StashClient) -> None:
    """Test finding an image that doesn't exist."""
    graphql_route = respx.post("http://localhost:9999/graphql").mock(
        return_value=httpx.Response(200, json={"data": {"findImage": None}})
    )

    image = await respx_stash_client.find_image("999")

    assert image is None

    # Verify GraphQL call
    assert len(graphql_route.calls) == 1
    req = json.loads(graphql_route.calls[0].request.content)
    assert "findImage" in req["query"]
    assert req["variables"]["id"] == "999"


@pytest.mark.asyncio
async def test_find_image_error(respx_stash_client: StashClient) -> None:
    """Test handling errors when finding an image."""
    graphql_route = respx.post("http://localhost:9999/graphql").mock(
        return_value=httpx.Response(500, json={"errors": [{"message": "Test error"}]})
    )

    # Use a unique ID that won't be cached
    image = await respx_stash_client.find_image("test_error_image_999")

    assert image is None

    # Verify GraphQL call was made
    assert len(graphql_route.calls) == 1


@pytest.mark.asyncio
async def test_find_images(respx_stash_client: StashClient) -> None:
    """Test finding images with filters."""
    # Create test image using factory
    test_image = ImageFactory.build(
        id="123",
        title="Test Image",
        urls=["https://example.com/image"],
    )
    image_data = _image_to_response_dict(test_image)

    graphql_route = respx.post("http://localhost:9999/graphql").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {
                    "findImages": {
                        "count": 1,
                        "megapixels": 2.07,
                        "filesize": 512000.0,
                        "images": [image_data],
                    }
                }
            },
        )
    )

    result = await respx_stash_client.find_images()

    # Verify the results
    assert result.count == 1
    assert result.megapixels == 2.07
    assert result.filesize == 512000.0
    assert len(result.images) == 1

    # Verify GraphQL call
    assert len(graphql_route.calls) == 1
    req = json.loads(graphql_route.calls[0].request.content)
    assert "findImages" in req["query"]


@pytest.mark.asyncio
async def test_find_images_with_filter(respx_stash_client: StashClient) -> None:
    """Test finding images with custom filter parameters."""
    # Create test image using factory
    test_image = ImageFactory.build(id="123", title="Test Image")
    image_data = _image_to_response_dict(test_image)

    graphql_route = respx.post("http://localhost:9999/graphql").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {
                    "findImages": {
                        "count": 1,
                        "megapixels": 2.07,
                        "filesize": 512000.0,
                        "images": [image_data],
                    }
                }
            },
        )
    )

    result = await respx_stash_client.find_images(
        filter_={"per_page": 10, "page": 1},
        q="Test",
    )

    # Verify the results
    assert result.count == 1
    assert len(result.images) == 1

    # Verify GraphQL call includes filter params
    assert len(graphql_route.calls) == 1
    req = json.loads(graphql_route.calls[0].request.content)
    assert "findImages" in req["query"]
    assert req["variables"]["filter"]["q"] == "Test"
    assert req["variables"]["filter"]["per_page"] == 10


@pytest.mark.asyncio
async def test_find_images_error(respx_stash_client: StashClient) -> None:
    """Test handling errors when finding images."""
    graphql_route = respx.post("http://localhost:9999/graphql").mock(
        return_value=httpx.Response(500, json={"errors": [{"message": "Test error"}]})
    )

    result = await respx_stash_client.find_images()

    # Should return empty result with default values
    assert result.count == 0
    assert len(result.images) == 0
    assert result.megapixels == 0.0
    assert result.filesize == 0.0

    # Verify GraphQL call was attempted
    assert len(graphql_route.calls) == 1


@pytest.mark.asyncio
async def test_update_image(respx_stash_client: StashClient) -> None:
    """Test updating an image."""
    # Create response image with updated values
    updated_image = ImageFactory.build(
        id="123",
        title="Updated Title",
        urls=["https://example.com"],
        date="2024-01-01",
        organized=True,
    )
    response_data = _image_to_response_dict(updated_image)

    graphql_route = respx.post("http://localhost:9999/graphql").mock(
        return_value=httpx.Response(200, json={"data": {"imageUpdate": response_data}})
    )

    # Create image with original values
    image = ImageFactory.build(
        id="123",
        title="Original Title",
        urls=["https://example.com"],
        date="2024-01-01",
        organized=True,
    )
    # Actually change the value to trigger dirty tracking
    image.title = "Updated Title"

    updated = await respx_stash_client.update_image(image)

    # Verify the result
    assert updated.id == "123"
    assert updated.title == "Updated Title"

    # Verify GraphQL call
    assert len(graphql_route.calls) == 1
    req = json.loads(graphql_route.calls[0].request.content)
    assert "imageUpdate" in req["query"]
    assert "input" in req["variables"]


@pytest.mark.asyncio
async def test_update_image_multiple_fields(respx_stash_client: StashClient) -> None:
    """Test updating multiple image fields at once."""
    # Create response image with updated values
    updated_image = ImageFactory.build(
        id="123",
        title="Test Image",
        urls=["https://example.com/updated"],
        date="2024-02-01",
        organized=False,
    )
    response_data = _image_to_response_dict(updated_image)

    graphql_route = respx.post("http://localhost:9999/graphql").mock(
        return_value=httpx.Response(200, json={"data": {"imageUpdate": response_data}})
    )

    # Create image with original values
    image = ImageFactory.build(
        id="123",
        title="Test Image",
        urls=["https://example.com/original"],
        date="2024-01-01",
        organized=True,
    )
    # Actually change multiple field values to trigger dirty tracking
    image.urls = ["https://example.com/updated"]
    image.date = "2024-02-01"
    image.organized = False

    updated = await respx_stash_client.update_image(image)

    # Verify the result
    assert updated.id == "123"
    assert updated.urls == ["https://example.com/updated"]
    assert updated.date == "2024-02-01"
    assert updated.organized is False

    # Verify GraphQL call
    assert len(graphql_route.calls) == 1
    req = json.loads(graphql_route.calls[0].request.content)
    assert "imageUpdate" in req["query"]


@pytest.mark.asyncio
async def test_update_image_no_changes(respx_stash_client: StashClient) -> None:
    """Test updating an image with no actual changes skips the API call."""
    graphql_route = respx.post("http://localhost:9999/graphql").mock(
        return_value=httpx.Response(200, json={"data": {}})
    )

    # Create image with no dirty fields
    image = ImageFactory.build(
        id="123",
        title="Test Image",
    )
    # Ensure no dirty attrs (simulating a freshly loaded image)
    image._dirty_attrs.clear()

    result = await respx_stash_client.update_image(image)

    # Should return the original image without making API call
    assert result.id == "123"
    assert result.title == "Test Image"

    # No GraphQL call should be made
    assert len(graphql_route.calls) == 0


@pytest.mark.asyncio
async def test_update_image_error(respx_stash_client: StashClient) -> None:
    """Test handling errors when updating an image."""
    respx.post("http://localhost:9999/graphql").mock(
        return_value=httpx.Response(500, json={"errors": [{"message": "Test error"}]})
    )

    # Create image with original value
    image = ImageFactory.build(id="123", title="Original")
    # Actually change the value to trigger dirty tracking
    image.title = "Updated"

    with pytest.raises(Exception):
        await respx_stash_client.update_image(image)


@pytest.mark.asyncio
async def test_find_image_caching(respx_stash_client: StashClient) -> None:
    """Test that find_image results are cached."""
    # Create test image
    test_image = ImageFactory.build(id="123", title="Test Image")
    image_data = _image_to_response_dict(test_image)

    graphql_route = respx.post("http://localhost:9999/graphql").mock(
        side_effect=[
            httpx.Response(200, json={"data": {"findImage": image_data}}),
            httpx.Response(200, json={"data": {"findImage": None}}),
        ]
    )

    # First call should hit the API
    image1 = await respx_stash_client.find_image("123")
    assert image1 is not None
    assert len(graphql_route.calls) == 1

    # Second call with same ID should use cache
    image2 = await respx_stash_client.find_image("123")
    assert image2 is not None
    assert len(graphql_route.calls) == 1  # Still only 1 call

    # Different ID should hit API again
    image3 = await respx_stash_client.find_image("456")
    assert image3 is None
    assert len(graphql_route.calls) == 2


@pytest.mark.asyncio
async def test_update_image_clears_cache(respx_stash_client: StashClient) -> None:
    """Test that update_image clears the find caches."""
    # Create test image
    test_image = ImageFactory.build(id="123", title="Test Image")
    image_data = _image_to_response_dict(test_image)

    # Updated image data for response
    updated_image = ImageFactory.build(id="123", title="Updated Title")
    updated_data = _image_to_response_dict(updated_image)

    graphql_route = respx.post("http://localhost:9999/graphql").mock(
        side_effect=[
            # First find_image call
            httpx.Response(200, json={"data": {"findImage": image_data}}),
            # update_image call
            httpx.Response(200, json={"data": {"imageUpdate": updated_data}}),
            # Second find_image call (after cache clear)
            httpx.Response(200, json={"data": {"findImage": updated_data}}),
        ]
    )

    # Populate cache
    image = await respx_stash_client.find_image("123")
    assert len(graphql_route.calls) == 1
    assert image is not None

    # Update should clear cache
    image.title = "Updated Title"
    await respx_stash_client.update_image(image)
    assert len(graphql_route.calls) == 2

    # Next find should hit API again (cache was cleared)
    updated = await respx_stash_client.find_image("123")
    assert len(graphql_route.calls) == 3
    assert updated is not None
    assert updated.title == "Updated Title"
