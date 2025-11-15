"""Tests for edge cases in TagProcessingMixin."""

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
@respx.mock
async def test_process_hashtags_to_tags_alias_match(tag_mixin):
    """Test finding a tag by alias when exact name match fails."""
    # Create real hashtag using factory
    hashtag = HashtagFactory.build(value="alias_name")

    # Create responses
    empty_result = create_find_tags_result(count=0, tags=[])
    tag_dict = create_tag_dict(id="tag_123", name="Original Name")
    alias_result = create_find_tags_result(count=1, tags=[tag_dict])

    # Mock GraphQL responses - two calls with different results
    respx.post("http://localhost:9999/graphql").mock(
        side_effect=[
            # First call: name search returns empty
            httpx.Response(
                200,
                json=create_graphql_response("findTags", empty_result),
            ),
            # Second call: alias search returns match
            httpx.Response(
                200,
                json=create_graphql_response("findTags", alias_result),
            ),
        ]
    )

    # Initialize client
    await tag_mixin.context.get_client()

    # Process the hashtag
    tags = await tag_mixin._process_hashtags_to_tags([hashtag])

    assert len(tags) == 1
    assert tags[0].id == "tag_123"
    assert tags[0].name == "Original Name"


@pytest.mark.asyncio
@respx.mock
async def test_process_hashtags_to_tags_creation_error_exists(tag_mixin):
    """Test handling when client's create_tag returns existing tag.

    The client now handles "already exists" errors internally and returns
    the existing tag, so we just verify the tag is returned correctly.
    """
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

    # Initialize client
    await tag_mixin.context.get_client()

    # Process the hashtag
    tags = await tag_mixin._process_hashtags_to_tags([hashtag])

    assert len(tags) == 1
    assert tags[0].id == "tag_123"
    assert tags[0].name == "test_tag"


@pytest.mark.asyncio
@respx.mock
async def test_process_hashtags_to_tags_creation_error_other(tag_mixin):
    """Test handling of tag creation with other errors."""
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

    # Initialize client
    await tag_mixin.context.get_client()

    # Process the hashtag and expect the error to be raised
    with pytest.raises(Exception, match="Some other error") as exc_info:
        await tag_mixin._process_hashtags_to_tags([hashtag])

    assert "Some other error" in str(exc_info.value)


@pytest.mark.asyncio
@respx.mock
async def test_add_preview_tag_existing_tag(tag_mixin):
    """Test _add_preview_tag when tag is already present."""
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

    # Initialize client
    await tag_mixin.context.get_client()

    # Verify tag is already present
    assert len(scene.tags) == 1
    assert scene.tags[0].id == "preview_tag_123"

    # Add the tag again
    await tag_mixin._add_preview_tag(scene)

    # Verify tag wasn't duplicated
    assert len(scene.tags) == 1
    assert scene.tags[0].id == "preview_tag_123"


@pytest.mark.asyncio
@respx.mock
async def test_add_preview_tag_no_tag_found(tag_mixin):
    """Test _add_preview_tag when preview tag doesn't exist."""
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

    # Initialize client
    await tag_mixin.context.get_client()

    # Add the tag
    await tag_mixin._add_preview_tag(scene)

    # Verify no tag was added since none was found
    assert len(scene.tags) == 0
