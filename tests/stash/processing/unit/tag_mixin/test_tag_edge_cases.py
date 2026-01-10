"""Tests for edge cases in TagProcessingMixin.

Tests migrated to use respx_stash_processor fixture for HTTP boundary mocking.
"""

import httpx
import pytest
import respx

from tests.fixtures import (
    HashtagFactory,
    SceneFactory,
    TagFactory,
    create_find_tags_result,
    create_graphql_response,
    create_tag_create_result,
    create_tag_dict,
)


@pytest.mark.asyncio
async def test_process_hashtags_to_tags_alias_match(respx_stash_processor):
    """Test get_or_create finding existing tag by name.

    After ORM migration: store.get_or_create(Tag, name="alias_name") will:
    1. Search for tag with name="alias_name"
    2. If found, return it; if not found, create it
    """
    # Note: respx_stash_processor already has respx.mock wrapper
    # Create real hashtag using factory
    hashtag = HashtagFactory.build(value="alias_name")

    # Create responses for get_or_create flow
    tag_dict = create_tag_dict(id="tag_123", name="alias_name")
    found_result = create_find_tags_result(count=1, tags=[tag_dict])

    # Mock GraphQL responses - get_or_create searches by name
    respx.post("http://localhost:9999/graphql").mock(
        side_effect=[
            # store.get_or_create searches for existing tag
            httpx.Response(
                200,
                json=create_graphql_response("findTags", found_result),
            ),
        ]
    )

    # Process the hashtag
    tags = await respx_stash_processor._process_hashtags_to_tags([hashtag])

    assert len(tags) == 1
    assert tags[0].id == "tag_123"
    assert tags[0].name == "alias_name"


@pytest.mark.asyncio
async def test_process_hashtags_to_tags_creation_error_exists(respx_stash_processor):
    """Test handling when client's create_tag returns existing tag.

    The client now handles "already exists" errors internally and returns
    the existing tag, so we just verify the tag is returned correctly.
    """
    # Note: respx_stash_processor already has respx.mock wrapper
    # Create real hashtag using factory
    hashtag = HashtagFactory.build(value="test_tag")

    # Create responses
    empty_result = create_find_tags_result(count=0, tags=[])
    tag_dict = create_tag_dict(id="tag_123", name="test_tag")

    # Mock GraphQL responses
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
                    "tagCreate", create_tag_create_result(tag_dict)
                ),
            ),
        ]
    )

    # Process the hashtag
    tags = await respx_stash_processor._process_hashtags_to_tags([hashtag])

    assert len(tags) == 1
    # Note: Don't assert on ID - library generates UUIDs for new tags (implementation detail)
    assert tags[0].name == "test_tag"
    assert hasattr(tags[0], "id")  # Verify ID exists


@pytest.mark.asyncio
async def test_process_hashtags_to_tags_creation_error_other(respx_stash_processor):
    """Test handling of tag creation with other errors."""
    # Note: respx_stash_processor already has respx.mock wrapper
    # Create real hashtag using factory
    hashtag = HashtagFactory.build(value="test_tag")

    # Create responses
    empty_result = create_find_tags_result(count=0, tags=[])

    # Mock GraphQL responses to return errors
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
            # Third call: tagCreate returns GraphQL error
            httpx.Response(
                200,
                json={
                    "errors": [{"message": "Some other error"}],
                    "data": None,
                },
            ),
        ]
    )

    # Process the hashtag - library creates local Tag object with UUID even if save fails
    # (asyncio.gather uses return_exceptions=True, so exceptions are caught)
    tags = await respx_stash_processor._process_hashtags_to_tags([hashtag])

    # Verify a local tag object was created (library doesn't raise for save failures)
    assert len(tags) == 1
    assert tags[0].name == "test_tag"
    assert hasattr(tags[0], "id")  # Has UUID even if save failed


@pytest.mark.asyncio
async def test_add_preview_tag_existing_tag(respx_stash_processor):
    """Test _add_preview_tag when tag is already present."""
    # Note: respx_stash_processor already has respx.mock wrapper
    # Create preview tag using factory
    preview_tag = TagFactory.build(
        id="preview_tag_123",
        name="Trailer",
    )

    # Create Scene with existing preview tag
    scene = SceneFactory.build(
        id="scene_123",
        title="Test Scene",
        tags=[preview_tag],
    )

    # Create response
    tag_dict = create_tag_dict(id="preview_tag_123", name="Trailer")
    tag_result = create_find_tags_result(count=1, tags=[tag_dict])

    # Mock GraphQL response
    respx.post("http://localhost:9999/graphql").mock(
        return_value=httpx.Response(
            200,
            json=create_graphql_response("findTags", tag_result),
        )
    )

    # Verify tag is already present
    assert len(scene.tags) == 1
    assert scene.tags[0].id == "preview_tag_123"

    # Add the tag again
    await respx_stash_processor._add_preview_tag(scene)

    # Verify tag wasn't duplicated
    assert len(scene.tags) == 1
    assert scene.tags[0].id == "preview_tag_123"


@pytest.mark.asyncio
async def test_add_preview_tag_no_tag_found(respx_stash_processor):
    """Test _add_preview_tag when preview tag doesn't exist."""
    # Note: respx_stash_processor already has respx.mock wrapper
    # Create Scene without tags
    scene = SceneFactory.build(
        id="scene_123",
        title="Test Scene",
        tags=[],
    )

    # Mock tag search to return no results
    empty_result = create_find_tags_result(count=0, tags=[])
    respx.post("http://localhost:9999/graphql").mock(
        return_value=httpx.Response(
            200,
            json=create_graphql_response("findTags", empty_result),
        )
    )

    # Add the tag
    await respx_stash_processor._add_preview_tag(scene)

    # Verify no tag was added since none was found
    assert len(scene.tags) == 0
