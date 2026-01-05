"""Tests for tag processing methods in TagProcessingMixin.

Tests migrated to use respx_stash_processor fixture for HTTP boundary mocking.
"""

import httpx
import pytest
import respx

from tests.fixtures import (
    HashtagFactory,
    TagFactory,
    create_find_tags_result,
    create_graphql_response,
    create_tag_create_result,
    create_tag_dict,
)


@pytest.fixture
def mock_stash_tag():
    """Create a mock Stash tag using factory."""
    return TagFactory.build(
        id="tag_123",
        name="test_tag",
    )


@pytest.mark.asyncio
async def test_process_hashtags_to_tags_empty(respx_stash_processor):
    """Test processing an empty list of hashtags."""
    # Note: respx_stash_processor already has respx.mock wrapper
    respx.post("http://localhost:9999/graphql").mock(
        return_value=httpx.Response(200, json={"data": {}})
    )

    hashtags = []
    tags = await respx_stash_processor._process_hashtags_to_tags(hashtags)

    # Verify no tags were returned
    assert tags == []


@pytest.mark.asyncio
async def test_process_hashtags_to_tags_single(respx_stash_processor):
    """Test processing a single hashtag to tag."""
    # Create hashtag using factory
    hashtag = HashtagFactory.build(value="testTag")

    # Create tag response
    tag_dict = create_tag_dict(
        id="tag_123",
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
        return_value=httpx.Response(
            200,
            json=create_graphql_response("findTags", find_tags_result),
        )
    )

    # Test processing hashtag
    tags = await respx_stash_processor._process_hashtags_to_tags([hashtag])

    # Verify tag was returned
    assert len(tags) == 1
    assert tags[0].id == "tag_123"
    assert tags[0].name == "testtag"


@pytest.mark.asyncio
async def test_process_hashtags_to_tags_not_found_creates_new(respx_stash_processor):
    """Test processing a hashtag that doesn't exist creates a new tag."""
    # Create hashtag using factory
    hashtag = HashtagFactory.build(value="newTag")

    # Create responses
    empty_result = create_find_tags_result(count=0, tags=[])
    new_tag_dict = create_tag_dict(
        id="new_tag_123",
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
    assert tags[0].id == "new_tag_123"
    assert tags[0].name == "newtag"


@pytest.mark.asyncio
async def test_process_hashtags_to_tags_multiple(respx_stash_processor):
    """Test processing multiple hashtags."""
    # Create hashtags using factory
    hashtag1 = HashtagFactory.build(value="tag1")
    hashtag2 = HashtagFactory.build(value="tag2")

    # Create tag responses
    tag1_dict = create_tag_dict(id="tag_1", name="tag1")
    tag2_dict = create_tag_dict(id="tag_2", name="tag2")

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
    assert tags[0].id == "tag_1"
    assert tags[0].name == "tag1"
    assert tags[1].id == "tag_2"
    assert tags[1].name == "tag2"


@pytest.mark.asyncio
async def test_add_preview_tag_not_found(respx_stash_processor):
    """Test add_preview_tag when Trailer tag doesn't exist."""
    # Create Scene using factory
    from tests.fixtures import SceneFactory

    scene = SceneFactory.build(id="scene_123", title="Test Scene", tags=[])

    # Mock empty findTags response
    empty_result = create_find_tags_result(count=0, tags=[])
    respx.post("http://localhost:9999/graphql").mock(
        return_value=httpx.Response(
            200,
            json=create_graphql_response("findTags", empty_result),
        )
    )

    # Test with scene
    await respx_stash_processor._add_preview_tag(scene)

    # Verify no tag was added since not found
    assert scene.tags == []


@pytest.mark.asyncio
async def test_add_preview_tag_found_adds_tag(respx_stash_processor):
    """Test add_preview_tag when Trailer tag exists and is added."""
    # Create Scene using factory
    from tests.fixtures import SceneFactory

    scene = SceneFactory.build(id="scene_123", title="Test Scene", tags=[])

    # Create Trailer tag response
    trailer_tag_dict = create_tag_dict(id="trailer_tag_123", name="Trailer")
    result = create_find_tags_result(count=1, tags=[trailer_tag_dict])

    respx.post("http://localhost:9999/graphql").mock(
        return_value=httpx.Response(
            200,
            json=create_graphql_response("findTags", result),
        )
    )

    # Test with scene
    await respx_stash_processor._add_preview_tag(scene)

    # Verify the tag was added to scene
    assert len(scene.tags) == 1
    assert scene.tags[0].id == "trailer_tag_123"
    assert scene.tags[0].name == "Trailer"


@pytest.mark.asyncio
async def test_add_preview_tag_already_has_tag(respx_stash_processor):
    """Test add_preview_tag when scene already has the Trailer tag."""
    # Create Trailer tag using factory
    trailer_tag = TagFactory.build(id="trailer_tag_123", name="Trailer")

    # Create Scene with Trailer tag already added
    from tests.fixtures import SceneFactory

    scene = SceneFactory.build(
        id="scene_123",
        title="Test Scene",
        tags=[trailer_tag],
    )

    # Create response
    trailer_tag_dict = create_tag_dict(id="trailer_tag_123", name="Trailer")
    result = create_find_tags_result(count=1, tags=[trailer_tag_dict])

    respx.post("http://localhost:9999/graphql").mock(
        return_value=httpx.Response(
            200,
            json=create_graphql_response("findTags", result),
        )
    )

    # Test with scene
    await respx_stash_processor._add_preview_tag(scene)

    # Verify the tag was NOT added again (still only one)
    assert len(scene.tags) == 1
    assert scene.tags[0].id == "trailer_tag_123"
