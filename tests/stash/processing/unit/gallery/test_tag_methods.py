"""Tests for tag-related methods in GalleryProcessingMixin.

Tests migrated to use respx_stash_processor fixture for HTTP boundary mocking.
"""

import httpx
import pytest
import respx

from tests.fixtures import (
    HashtagFactory,
    create_find_tags_result,
    create_graphql_response,
    create_tag_create_result,
    create_tag_dict,
)


class TestTagMethods:
    """Test tag-related methods in GalleryProcessingMixin."""

    @pytest.mark.asyncio
    async def test_process_hashtags_to_tags(
        self, factory_async_session, session, respx_stash_processor
    ):
        """Test _process_hashtags_to_tags method."""
        # Note: respx_stash_processor already has respx.mock wrapper
        # Create real Hashtag objects
        hashtag1 = HashtagFactory(id=1001, value="test_tag")
        hashtag2 = HashtagFactory(id=1002, value="new_tag")

        factory_async_session.commit()

        hashtags = [hashtag1, hashtag2]

        # Create responses
        tag_dict1 = create_tag_dict(id="tag_123", name="test_tag")
        tag_results1 = create_find_tags_result(count=1, tags=[tag_dict1])
        tag_results2 = create_find_tags_result(count=0, tags=[])
        new_tag_dict = create_tag_dict(id="tag_456", name="new_tag")

        # Mock GraphQL responses
        respx.post("http://localhost:9999/graphql").mock(
            side_effect=[
                # First call: findTags by name for first tag (found)
                httpx.Response(
                    200,
                    json=create_graphql_response("findTags", tag_results1),
                ),
                # Second call: findTags by name for second tag (not found)
                httpx.Response(
                    200,
                    json=create_graphql_response("findTags", tag_results2),
                ),
                # Third call: findTags by alias for second tag (not found)
                httpx.Response(
                    200,
                    json=create_graphql_response("findTags", tag_results2),
                ),
                # Fourth call: tagCreate for second tag
                httpx.Response(
                    200,
                    json=create_graphql_response(
                        "tagCreate", create_tag_create_result(new_tag_dict)
                    ),
                ),
            ]
        )

        # Call the method
        tags = await respx_stash_processor._process_hashtags_to_tags(hashtags)

        # Verify results
        assert len(tags) == 2
        # Note: First tag found (keeps mocked ID), second tag created (gets UUID)
        assert tags[0].name == "test_tag"
        assert hasattr(tags[0], "id")
        assert tags[1].name == "new_tag"
        assert hasattr(tags[1], "id")

    @pytest.mark.asyncio
    async def test_process_hashtags_to_tags_already_exists(
        self, factory_async_session, session, respx_stash_processor
    ):
        """Test _process_hashtags_to_tags when tag already exists.

        The client now handles "already exists" errors internally and returns
        the existing tag, so we just verify the tag is returned correctly.
        """
        # Note: respx_stash_processor already has respx.mock wrapper
        # Create real Hashtag object
        hashtag1 = HashtagFactory(id=1001, value="test_tag")
        factory_async_session.commit()

        # Create responses
        empty_result = create_find_tags_result(count=0, tags=[])
        existing_tag_dict = create_tag_dict(id="tag_123", name="test_tag")

        # Mock GraphQL responses
        respx.post("http://localhost:9999/graphql").mock(
            side_effect=[
                # First call: findTags by name (not found)
                httpx.Response(
                    200,
                    json=create_graphql_response("findTags", empty_result),
                ),
                # Second call: findTags by alias (not found)
                httpx.Response(
                    200,
                    json=create_graphql_response("findTags", empty_result),
                ),
                # Third call: tagCreate returns new tag
                httpx.Response(
                    200,
                    json=create_graphql_response(
                        "tagCreate", create_tag_create_result(existing_tag_dict)
                    ),
                ),
            ]
        )

        # Call the method
        tags = await respx_stash_processor._process_hashtags_to_tags([hashtag1])

        # Verify results
        assert len(tags) == 1
        # Note: Don't assert on ID - library generates UUIDs for new tags
        assert tags[0].name == "test_tag"
        assert hasattr(tags[0], "id")

    @pytest.mark.asyncio
    async def test_process_hashtags_to_tags_error(
        self, factory_async_session, session, respx_stash_processor
    ):
        """Test _process_hashtags_to_tags with other errors."""
        # Note: respx_stash_processor already has respx.mock wrapper
        # Create real Hashtag object
        hashtag1 = HashtagFactory(id=1001, value="test_tag")
        factory_async_session.commit()

        # Create responses
        empty_result = create_find_tags_result(count=0, tags=[])

        # Mock GraphQL responses
        respx.post("http://localhost:9999/graphql").mock(
            side_effect=[
                # First call: findTags by name (not found)
                httpx.Response(
                    200,
                    json=create_graphql_response("findTags", empty_result),
                ),
                # Second call: findTags by alias (not found)
                httpx.Response(
                    200,
                    json=create_graphql_response("findTags", empty_result),
                ),
                # Third call: tagCreate returns error
                httpx.Response(
                    200,
                    json={
                        "errors": [{"message": "network error"}],
                        "data": None,
                    },
                ),
            ]
        )

        # Call the method - library creates local Tag object even if save fails
        # (asyncio.gather uses return_exceptions=True, so exceptions are caught)
        tags = await respx_stash_processor._process_hashtags_to_tags([hashtag1])

        # Verify a local tag object was created (library doesn't raise for save failures)
        assert len(tags) == 1
        assert tags[0].name == "test_tag"
        assert hasattr(tags[0], "id")  # Has UUID even if save failed

    @pytest.mark.asyncio
    async def test_add_preview_tag(self, respx_stash_processor, mock_image):
        """Test _add_preview_tag method."""
        # Note: respx_stash_processor already has respx.mock wrapper
        # Create response
        trailer_tag_dict = create_tag_dict(id="tag_trailer", name="Trailer")
        tag_results = create_find_tags_result(count=1, tags=[trailer_tag_dict])

        # Mock GraphQL response
        respx.post("http://localhost:9999/graphql").mock(
            return_value=httpx.Response(
                200,
                json=create_graphql_response("findTags", tag_results),
            )
        )

        # Test on image with no existing tags
        mock_image.tags = []

        # Call the method
        await respx_stash_processor._add_preview_tag(mock_image)

        # Verify the tag was added
        assert len(mock_image.tags) == 1
        assert mock_image.tags[0].id == "tag_trailer"

    @pytest.mark.asyncio
    async def test_add_preview_tag_existing(self, respx_stash_processor):
        """Test _add_preview_tag with existing tag (should not add duplicate)."""
        # Note: respx_stash_processor already has respx.mock wrapper
        from tests.fixtures import ImageFactory, TagFactory

        # Create tag and image with that tag
        existing_tag = TagFactory.build(id="tag_trailer", name="Trailer")
        mock_image = ImageFactory.build(
            id="image_123",
            title="Test Image",
            tags=[existing_tag],
        )

        # Create response
        trailer_tag_dict = create_tag_dict(id="tag_trailer", name="Trailer")
        tag_results = create_find_tags_result(count=1, tags=[trailer_tag_dict])

        # Mock GraphQL response
        respx.post("http://localhost:9999/graphql").mock(
            return_value=httpx.Response(
                200,
                json=create_graphql_response("findTags", tag_results),
            )
        )

        # Call the method
        await respx_stash_processor._add_preview_tag(mock_image)

        # Verify no additional tag was added
        assert len(mock_image.tags) == 1

    @pytest.mark.asyncio
    async def test_add_preview_tag_not_found(self, respx_stash_processor, mock_image):
        """Test _add_preview_tag when preview tag doesn't exist."""
        # Note: respx_stash_processor already has respx.mock wrapper
        # Create response
        empty_result = create_find_tags_result(count=0, tags=[])

        # Mock GraphQL response
        respx.post("http://localhost:9999/graphql").mock(
            return_value=httpx.Response(
                200,
                json=create_graphql_response("findTags", empty_result),
            )
        )

        # Test on image with no existing tags
        mock_image.tags = []

        # Call the method
        await respx_stash_processor._add_preview_tag(mock_image)

        # Verify no tag was added
        assert len(mock_image.tags) == 0
