"""Tests for tag-related methods in GalleryProcessingMixin."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from tests.fixtures.metadata.metadata_factories import HashtagFactory


class TestTagMethods:
    """Test tag-related methods in GalleryProcessingMixin."""

    @pytest.mark.asyncio
    async def test_process_hashtags_to_tags(
        self, factory_async_session, session, gallery_mixin, mock_tag
    ):
        """Test _process_hashtags_to_tags method."""
        # Create real Hashtag objects
        hashtag1 = HashtagFactory(id=1001, value="test_tag")
        hashtag2 = HashtagFactory(id=1002, value="new_tag")

        factory_async_session.commit()

        hashtags = [hashtag1, hashtag2]

        # Setup tag search results
        tag_results1 = MagicMock()
        tag_results1.count = 1
        tag_results1.tags = [{"id": "tag_123", "name": "test_tag"}]

        tag_results2 = MagicMock()
        tag_results2.count = 0
        tag_results2.tags = []

        # Setup client responses
        gallery_mixin.context.client.find_tags = AsyncMock(
            side_effect=[
                tag_results1,  # First tag found by name
                tag_results2,  # Second tag not found by name
                tag_results2,  # Second tag not found by alias
            ]
        )

        # Setup tag creation
        new_tag = MagicMock()
        new_tag.id = "tag_456"
        new_tag.name = "new_tag"
        gallery_mixin.context.client.create_tag = AsyncMock(return_value=new_tag)

        # Call the method
        tags = await gallery_mixin._process_hashtags_to_tags(hashtags)

        # Verify results
        assert len(tags) == 2
        assert tags[0].id == "tag_123"
        assert tags[0].name == "test_tag"
        assert tags[1].id == "tag_456"
        assert tags[1].name == "new_tag"

        # Verify find_tags calls
        assert gallery_mixin.context.client.find_tags.call_count == 3

        # First call: search by name for first tag
        first_call_args = gallery_mixin.context.client.find_tags.call_args_list[
            0
        ].kwargs
        assert "tag_filter" in first_call_args
        assert first_call_args["tag_filter"]["name"]["value"] == "test_tag"
        assert first_call_args["tag_filter"]["name"]["modifier"] == "EQUALS"

        # Second call: search by name for second tag
        second_call_args = gallery_mixin.context.client.find_tags.call_args_list[
            1
        ].kwargs
        assert "tag_filter" in second_call_args
        assert second_call_args["tag_filter"]["name"]["value"] == "new_tag"
        assert second_call_args["tag_filter"]["name"]["modifier"] == "EQUALS"

        # Third call: search by alias for second tag
        third_call_args = gallery_mixin.context.client.find_tags.call_args_list[
            2
        ].kwargs
        assert "tag_filter" in third_call_args
        assert third_call_args["tag_filter"]["aliases"]["value"] == "new_tag"
        assert third_call_args["tag_filter"]["aliases"]["modifier"] == "INCLUDES"

        # Verify create_tag call
        gallery_mixin.context.client.create_tag.assert_called_once()
        assert (
            gallery_mixin.context.client.create_tag.call_args.args[0].name == "new_tag"
        )

        # Reset for next test
        gallery_mixin.context.client.find_tags.reset_mock()
        gallery_mixin.context.client.create_tag.reset_mock()

        # Test with create_tag handling "already exists" internally (client-side)
        # The client now handles the error and returns the existing tag
        gallery_mixin.context.client.find_tags = AsyncMock(
            side_effect=[
                tag_results2,  # Tag not found by name
                tag_results2,  # Tag not found by alias
            ]
        )

        # Setup create_tag to return existing tag (client handles "already exists" internally)
        existing_tag = MagicMock()
        existing_tag.id = "tag_123"
        existing_tag.name = "test_tag"
        gallery_mixin.context.client.create_tag = AsyncMock(return_value=existing_tag)

        # Call the method with a single hashtag
        tags = await gallery_mixin._process_hashtags_to_tags([hashtag1])

        # Verify results
        assert len(tags) == 1
        assert tags[0].id == "tag_123"
        assert tags[0].name == "test_tag"

        # Verify client was called (no retries needed, client handles internally)
        assert gallery_mixin.context.client.find_tags.call_count == 2
        gallery_mixin.context.client.create_tag.assert_called_once()

        # Reset
        gallery_mixin.context.client.find_tags.reset_mock()
        gallery_mixin.context.client.create_tag.reset_mock()

        # Test with other type of error
        gallery_mixin.context.client.find_tags = AsyncMock(
            side_effect=[
                tag_results2,  # Tag not found by name
                tag_results2,  # Tag not found by alias
            ]
        )

        # Setup create_tag to fail with other error
        error_msg = "network error"
        gallery_mixin.context.client.create_tag = AsyncMock(
            side_effect=Exception(error_msg)
        )

        # Call the method and expect error
        with pytest.raises(Exception) as excinfo:  # noqa: PT011 - message validated by assertion below
            await gallery_mixin._process_hashtags_to_tags([hashtag1])

        # Verify the error is re-raised
        assert "network error" in str(excinfo.value)

    @pytest.mark.asyncio
    async def test_add_preview_tag(self, gallery_mixin, mock_image):
        """Test _add_preview_tag method."""
        # Setup tag search results
        tag_results = MagicMock()
        tag_results.count = 1

        # Create a mock tag object instead of a dict
        trailer_tag = MagicMock()
        trailer_tag.id = "tag_trailer"
        trailer_tag.name = "Trailer"
        tag_results.tags = [trailer_tag]

        # Setup client response
        gallery_mixin.context.client.find_tags = AsyncMock(return_value=tag_results)

        # Test on image with no existing tags
        mock_image.tags = []

        # Call the method
        await gallery_mixin._add_preview_tag(mock_image)

        # Verify the tag was added
        assert len(mock_image.tags) == 1
        assert mock_image.tags[0].id == "tag_trailer"

        # Reset
        gallery_mixin.context.client.find_tags.reset_mock()

        # Test with existing tag (should not add duplicate)
        existing_tag = MagicMock()
        existing_tag.id = "tag_trailer"
        mock_image.tags = [existing_tag]

        # Call the method
        await gallery_mixin._add_preview_tag(mock_image)

        # Verify no additional tag was added
        assert len(mock_image.tags) == 1

        # Test with no tags found
        mock_image.tags = []
        tag_results.count = 0
        tag_results.tags = []

        # Call the method
        await gallery_mixin._add_preview_tag(mock_image)

        # Verify no tag was added
        assert len(mock_image.tags) == 0
