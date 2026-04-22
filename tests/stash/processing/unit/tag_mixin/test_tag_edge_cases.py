"""Tests for edge cases in TagProcessingMixin.

Tests migrated to use respx_stash_processor fixture for HTTP boundary mocking.
"""

from unittest.mock import patch

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
from tests.fixtures.stash.stash_api_fixtures import dump_graphql_calls


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

    # Process the hashtag - _get_or_create_tag raises on save failure,
    # asyncio.gather(return_exceptions=True) catches it, filter removes it
    tags = await respx_stash_processor._process_hashtags_to_tags([hashtag])

    # Verify no tags returned (save failure propagates as exception,
    # filtered out by asyncio.gather return_exceptions pattern)
    assert len(tags) == 0


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


@pytest.mark.asyncio
async def test_get_or_create_tag_cache_hit(respx_stash_processor):
    """Tag found in local cache returns immediately (line 61).

    When a tag is already in the identity-map cache (e.g., from preload),
    _get_or_create_tag returns it without any GraphQL call.
    """
    # Pre-populate the store cache by saving a tag
    # save() with an existing ID triggers an update mutation, which populates the cache
    existing_tag = TagFactory.build(id="cached_tag_1", name="cached_tag")

    # Mock the GraphQL mutation for the save, then the tag is cached
    tag_dict = create_tag_dict(id="cached_tag_1", name="cached_tag")
    route = respx.post("http://localhost:9999/graphql").mock(
        side_effect=[
            httpx.Response(
                200,
                json=create_graphql_response("tagUpdate", tag_dict),
            ),
        ]
    )

    try:
        await respx_stash_processor.store.save(existing_tag)
    finally:
        dump_graphql_calls(route.calls, "test_get_or_create_tag_cache_hit (save)")

    # Reset route so no further GraphQL calls are allowed
    respx.reset()

    # Now the cache lookup should succeed without any GraphQL call
    tag = await respx_stash_processor._get_or_create_tag("cached_tag")

    assert tag.id == "cached_tag_1"
    assert tag.name == "cached_tag"


@pytest.mark.asyncio
async def test_get_or_create_tag_alias_hit(respx_stash_processor):
    """Tag found by alias fallback returns that tag (line 71).

    When name search returns nothing but alias search matches,
    the aliased tag is returned.
    """
    empty_result = create_find_tags_result(count=0, tags=[])
    aliased_tag_dict = create_tag_dict(
        id="alias_match_tag",
        name="original_name",
        aliases=["my_alias"],
    )
    alias_result = create_find_tags_result(count=1, tags=[aliased_tag_dict])

    route = respx.post("http://localhost:9999/graphql").mock(
        side_effect=[
            httpx.Response(
                200,
                json=create_graphql_response("findTags", empty_result),
            ),
            httpx.Response(
                200,
                json=create_graphql_response("findTags", alias_result),
            ),
        ]
    )

    try:
        tag = await respx_stash_processor._get_or_create_tag("my_alias")
    finally:
        dump_graphql_calls(route.calls, "test_get_or_create_tag_alias_hit")

    assert tag.name == "original_name"
    assert tag.id == "alias_match_tag"


@pytest.mark.asyncio
async def test_process_hashtags_batch_exception_fallback(respx_stash_processor):
    """Batch gather failure falls back to sequential processing (lines 134-143).

    When asyncio.gather itself raises (not individual return_exceptions),
    the code falls back to processing tags one at a time.
    """
    hashtag1 = HashtagFactory.build(value="fallback1")
    hashtag2 = HashtagFactory.build(value="fallback2")

    tag1_dict = create_tag_dict(id="fb_tag_1", name="fallback1")
    tag2_dict = create_tag_dict(id="fb_tag_2", name="fallback2")
    result1 = create_find_tags_result(count=1, tags=[tag1_dict])
    result2 = create_find_tags_result(count=1, tags=[tag2_dict])

    async def failing_gather(*coros, **kwargs):
        """Simulate gather failure — cancel coroutines to avoid warnings."""
        for coro in coros:
            coro.close()
        raise RuntimeError("gather exploded")

    route = respx.post("http://localhost:9999/graphql").mock(
        side_effect=[
            httpx.Response(
                200,
                json=create_graphql_response("findTags", result1),
            ),
            httpx.Response(
                200,
                json=create_graphql_response("findTags", result2),
            ),
        ]
    )

    try:
        with patch("asyncio.gather", side_effect=failing_gather):
            tags = await respx_stash_processor._process_hashtags_to_tags(
                [hashtag1, hashtag2]
            )
    finally:
        dump_graphql_calls(
            route.calls, "test_process_hashtags_batch_exception_fallback"
        )

    assert len(tags) == 2
    tag_names = {t.name for t in tags}
    assert "fallback1" in tag_names
    assert "fallback2" in tag_names
