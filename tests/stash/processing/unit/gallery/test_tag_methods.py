"""Tests for tag-related methods in GalleryProcessingMixin."""

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
    @respx.mock
    async def test_process_hashtags_to_tags(
        self, factory_async_session, session, gallery_mixin
    ):
        """Test _process_hashtags_to_tags method."""
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

        # Initialize client
        await gallery_mixin.context.get_client()

        # Call the method
        tags = await gallery_mixin._process_hashtags_to_tags(hashtags)

        # Verify results
        assert len(tags) == 2
        assert tags[0].id == "tag_123"
        assert tags[0].name == "test_tag"
        assert tags[1].id == "tag_456"
        assert tags[1].name == "new_tag"

    @pytest.mark.asyncio
    @respx.mock
    async def test_process_hashtags_to_tags_already_exists(
        self, factory_async_session, session, gallery_mixin
    ):
        """Test _process_hashtags_to_tags when tag already exists.

        The client now handles "already exists" errors internally and returns
        the existing tag, so we just verify the tag is returned correctly.
        """
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

        # Initialize client
        await gallery_mixin.context.get_client()

        # Call the method
        tags = await gallery_mixin._process_hashtags_to_tags([hashtag1])

        # Verify results
        assert len(tags) == 1
        assert tags[0].id == "tag_123"
        assert tags[0].name == "test_tag"

    @pytest.mark.asyncio
    @respx.mock
    async def test_process_hashtags_to_tags_error(
        self, factory_async_session, session, gallery_mixin
    ):
        """Test _process_hashtags_to_tags with other errors."""
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

        # Initialize client
        await gallery_mixin.context.get_client()

        # Call the method and expect error
        with pytest.raises(
            Exception
        ) as excinfo:  # noqa: PT011 - message validated by assertion below
            await gallery_mixin._process_hashtags_to_tags([hashtag1])

        # Verify the error is re-raised
        assert "network error" in str(excinfo.value)

    @pytest.mark.asyncio
    @respx.mock
    async def test_add_preview_tag(self, gallery_mixin, mock_image):
        """Test _add_preview_tag method."""
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

        # Initialize client
        await gallery_mixin.context.get_client()

        # Test on image with no existing tags
        mock_image.tags = []

        # Call the method
        await gallery_mixin._add_preview_tag(mock_image)

        # Verify the tag was added
        assert len(mock_image.tags) == 1
        assert mock_image.tags[0].id == "tag_trailer"

    @pytest.mark.asyncio
    @respx.mock
    async def test_add_preview_tag_existing(self, gallery_mixin):
        """Test _add_preview_tag with existing tag (should not add duplicate)."""
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

        # Initialize client
        await gallery_mixin.context.get_client()

        # Call the method
        await gallery_mixin._add_preview_tag(mock_image)

        # Verify no additional tag was added
        assert len(mock_image.tags) == 1

    @pytest.mark.asyncio
    @respx.mock
    async def test_add_preview_tag_not_found(self, gallery_mixin, mock_image):
        """Test _add_preview_tag when preview tag doesn't exist."""
        # Create response
        empty_result = create_find_tags_result(count=0, tags=[])

        # Mock GraphQL response
        respx.post("http://localhost:9999/graphql").mock(
            return_value=httpx.Response(
                200,
                json=create_graphql_response("findTags", empty_result),
            )
        )

        # Initialize client
        await gallery_mixin.context.get_client()

        # Test on image with no existing tags
        mock_image.tags = []

        # Call the method
        await gallery_mixin._add_preview_tag(mock_image)

        # Verify no tag was added
        assert len(mock_image.tags) == 0
