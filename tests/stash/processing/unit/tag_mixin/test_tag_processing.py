"""Tests for tag processing methods in TagProcessingMixin.

Tests migrated to use respx_stash_processor fixture for HTTP boundary mocking.
"""

import httpx
import pytest
import respx

from tests.fixtures import (
    HashtagFactory,
    SceneFactory,
    TagFactory,
    create_find_scenes_result,
    create_find_tags_result,
    create_graphql_response,
    create_tag_create_result,
    create_tag_dict,
)
from tests.fixtures.stash.stash_api_fixtures import (
    assert_op,
    assert_op_with_vars,
    dump_graphql_calls,
)


@pytest.fixture
def mock_stash_tag():
    """Create a mock Stash tag using factory."""
    return TagFactory.build(
        id="123",
        name="test_tag",
    )


@pytest.mark.asyncio
async def test_process_hashtags_to_tags_empty(respx_stash_processor):
    """Empty input short-circuits at tag.py:93 before any HTTP call."""
    tags = await respx_stash_processor._process_hashtags_to_tags([])

    assert tags == []


@pytest.mark.asyncio
async def test_process_hashtags_to_tags_single(respx_stash_processor):
    """Test processing a single hashtag to tag."""
    # Create hashtag using factory
    hashtag = HashtagFactory.build(value="testTag")

    # Create tag response
    tag_dict = create_tag_dict(
        id="123",
        name="testtag",
        aliases=[],
        parents=[],
        children=[],
        description=None,
        image_path=None,
    )

    # Mock GraphQL responses
    find_tags_result = create_find_tags_result(count=1, tags=[tag_dict])
    respx.post("http://localhost:9999/graphql").mock(
        side_effect=[
            httpx.Response(
                200,
                json=create_graphql_response("findTags", find_tags_result),
            )
        ]
    )

    # Test processing hashtag
    tags = await respx_stash_processor._process_hashtags_to_tags([hashtag])

    # Verify tag was returned
    assert len(tags) == 1
    # Note: Don't assert on ID - library generates UUIDs (implementation detail)
    assert tags[0].name == "testtag"
    assert hasattr(tags[0], "id")  # Verify ID exists


@pytest.mark.asyncio
async def test_process_hashtags_to_tags_not_found_creates_new(respx_stash_processor):
    """Test processing a hashtag that doesn't exist creates a new tag."""
    # Create hashtag using factory
    hashtag = HashtagFactory.build(value="newTag")

    # Create responses
    empty_result = create_find_tags_result(count=0, tags=[])
    new_tag_dict = create_tag_dict(
        id="200",
        name="newtag",
    )

    # Mock findTags (empty) and tagCreate responses
    respx.post("http://localhost:9999/graphql").mock(
        side_effect=[
            # First call: findTags by name returns empty
            httpx.Response(
                200,
                json=create_graphql_response("findTags", empty_result),
            ),
            # Second call: findTags by alias returns empty
            httpx.Response(
                200,
                json=create_graphql_response("findTags", empty_result),
            ),
            # Third call: tagCreate returns new tag
            httpx.Response(
                200,
                json=create_graphql_response(
                    "tagCreate", create_tag_create_result(new_tag_dict)
                ),
            ),
        ]
    )

    # Test processing hashtag
    tags = await respx_stash_processor._process_hashtags_to_tags([hashtag])

    # Verify tag was created and returned
    assert len(tags) == 1
    # Note: Don't assert on ID - library generates UUIDs (implementation detail)
    assert tags[0].name == "newtag"
    assert hasattr(tags[0], "id")  # Verify ID exists


@pytest.mark.asyncio
async def test_process_hashtags_to_tags_multiple(respx_stash_processor):
    """Test processing multiple hashtags."""
    # Create hashtags using factory
    hashtag1 = HashtagFactory.build(value="tag1")
    hashtag2 = HashtagFactory.build(value="tag2")

    # Create tag responses
    tag1_dict = create_tag_dict(id="201", name="tag1")
    tag2_dict = create_tag_dict(id="202", name="tag2")

    # Mock GraphQL responses - need to handle two findTags calls
    result1 = create_find_tags_result(count=1, tags=[tag1_dict])
    result2 = create_find_tags_result(count=1, tags=[tag2_dict])

    respx.post("http://localhost:9999/graphql").mock(
        side_effect=[
            httpx.Response(200, json=create_graphql_response("findTags", result1)),
            httpx.Response(200, json=create_graphql_response("findTags", result2)),
        ]
    )

    # Test processing hashtags
    tags = await respx_stash_processor._process_hashtags_to_tags([hashtag1, hashtag2])

    # Verify both tags were returned
    assert len(tags) == 2
    # Note: Don't assert on IDs - library generates UUIDs (implementation detail)
    assert tags[0].name == "tag1"
    assert tags[1].name == "tag2"
    # Verify IDs exist
    assert hasattr(tags[0], "id")
    assert hasattr(tags[1], "id")


@pytest.mark.asyncio
async def test_add_preview_tag_not_found(respx_stash_processor):
    """Test add_preview_tag when Trailer tag doesn't exist."""
    # Create Scene using factory

    scene = SceneFactory.build(id="300", title="Test Scene", tags=[])

    # Mock empty findTags response
    empty_result = create_find_tags_result(count=0, tags=[])
    respx.post("http://localhost:9999/graphql").mock(
        side_effect=[
            httpx.Response(
                200,
                json=create_graphql_response("findTags", empty_result),
            )
        ]
    )

    # Test with scene
    await respx_stash_processor._add_preview_tag(scene)

    # Verify no tag was added since not found
    assert scene.tags == []


@pytest.mark.asyncio
async def test_add_preview_tag_found_adds_tag(respx_stash_processor):
    """Test add_preview_tag when Trailer tag exists and is added."""
    # Create Scene using factory

    scene = SceneFactory.build(id="300", title="Test Scene", tags=[])

    # Create Trailer tag response
    trailer_tag_dict = create_tag_dict(id="400", name="Trailer")
    result = create_find_tags_result(count=1, tags=[trailer_tag_dict])

    # _add_preview_tag makes exactly 2 GraphQL calls:
    #   1. findTags — look up the Trailer tag by name
    #   2. findScenes — dedup check: are there scenes that already have the tag?
    empty_scenes = create_find_scenes_result(count=0, scenes=[])
    graphql_route = respx.post("http://localhost:9999/graphql").mock(
        side_effect=[
            httpx.Response(200, json=create_graphql_response("findTags", result)),
            httpx.Response(
                200, json=create_graphql_response("findScenes", empty_scenes)
            ),
        ]
    )

    try:
        await respx_stash_processor._add_preview_tag(scene)
    finally:
        dump_graphql_calls(graphql_route.calls, "test_add_preview_tag_found_adds_tag")

    # Verify the tag was added to scene
    assert len(scene.tags) == 1
    assert scene.tags[0].id == "400"
    assert scene.tags[0].name == "Trailer"

    # Exact count + per-call request + response verification.
    assert len(graphql_route.calls) == 2, (
        f"Expected exactly 2 calls (findTags + findScenes), "
        f"got {len(graphql_route.calls)}"
    )
    # Call 0: findTags filtering on "Trailer"
    assert_op_with_vars(
        graphql_route.calls[0],
        "findTags",
        tag_filter__name__value="Trailer",
    )
    resp0 = graphql_route.calls[0].response.json()
    assert resp0["data"]["findTags"]["count"] == 1
    assert resp0["data"]["findTags"]["tags"][0]["name"] == "Trailer"
    # Call 1: findScenes dedup check (tags.value includes the found tag id)
    assert_op(graphql_route.calls[1], "findScenes")
    resp1 = graphql_route.calls[1].response.json()
    assert resp1["data"]["findScenes"]["count"] == 0


@pytest.mark.asyncio
async def test_add_preview_tag_already_has_tag(respx_stash_processor):
    """Test add_preview_tag when scene already has the Trailer tag."""
    # Create Trailer tag using factory
    trailer_tag = TagFactory.build(id="400", name="Trailer")

    # Create Scene with Trailer tag already added

    scene = SceneFactory.build(
        id="300",
        title="Test Scene",
        tags=[trailer_tag],
    )

    # Create response
    trailer_tag_dict = create_tag_dict(id="400", name="Trailer")
    result = create_find_tags_result(count=1, tags=[trailer_tag_dict])

    respx.post("http://localhost:9999/graphql").mock(
        side_effect=[
            httpx.Response(
                200,
                json=create_graphql_response("findTags", result),
            )
        ]
    )

    # Test with scene
    await respx_stash_processor._add_preview_tag(scene)

    # Verify the tag was NOT added again (still only one)
    assert len(scene.tags) == 1
    assert scene.tags[0].id == "400"
