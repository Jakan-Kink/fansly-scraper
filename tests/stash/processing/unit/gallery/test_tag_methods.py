"""Tests for tag-related methods in GalleryProcessingMixin."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestTagMethods:
    """Test tag-related methods in GalleryProcessingMixin."""

    @pytest.mark.asyncio
    async def test_process_hashtags_to_tags(self, mixin, mock_tag):
        """Test _process_hashtags_to_tags method."""
        # Setup hashtags
        hashtag1 = MagicMock()
        hashtag1.value = "test_tag"

        hashtag2 = MagicMock()
        hashtag2.value = "new_tag"

        hashtags = [hashtag1, hashtag2]

        # Setup tag search results
        tag_results1 = MagicMock()
        tag_results1.count = 1
        tag_results1.tags = [{"id": "tag_123", "name": "test_tag"}]

        tag_results2 = MagicMock()
        tag_results2.count = 0
        tag_results2.tags = []

        # Setup client responses
        mixin.context.client.find_tags = AsyncMock(
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
        mixin.context.client.create_tag = AsyncMock(return_value=new_tag)

        # Call the method
        tags = await mixin._process_hashtags_to_tags(hashtags)

        # Verify results
        assert len(tags) == 2
        assert tags[0].id == "tag_123"
        assert tags[0].name == "test_tag"
        assert tags[1].id == "tag_456"
        assert tags[1].name == "new_tag"

        # Verify find_tags calls
        assert mixin.context.client.find_tags.call_count == 3

        # First call: search by name for first tag
        first_call_args = mixin.context.client.find_tags.call_args_list[0].kwargs
        assert "tag_filter" in first_call_args
        assert first_call_args["tag_filter"]["name"]["value"] == "test_tag"
        assert first_call_args["tag_filter"]["name"]["modifier"] == "EQUALS"

        # Second call: search by name for second tag
        second_call_args = mixin.context.client.find_tags.call_args_list[1].kwargs
        assert "tag_filter" in second_call_args
        assert second_call_args["tag_filter"]["name"]["value"] == "new_tag"
        assert second_call_args["tag_filter"]["name"]["modifier"] == "EQUALS"

        # Third call: search by alias for second tag
        third_call_args = mixin.context.client.find_tags.call_args_list[2].kwargs
        assert "tag_filter" in third_call_args
        assert third_call_args["tag_filter"]["aliases"]["value"] == "new_tag"
        assert third_call_args["tag_filter"]["aliases"]["modifier"] == "INCLUDES"

        # Verify create_tag call
        mixin.context.client.create_tag.assert_called_once()
        assert mixin.context.client.create_tag.call_args.args[0].name == "new_tag"

        # Reset for next test
        mixin.context.client.find_tags.reset_mock()
        mixin.context.client.create_tag.reset_mock()

        # Test with create_tag returning None (tag already exists)
        mixin.context.client.find_tags = AsyncMock(
            side_effect=[
                tag_results2,  # Tag not found by name
                tag_results2,  # Tag not found by alias
                tag_results1,  # Tag found after create_tag fails
            ]
        )

        # Setup create_tag to fail with "already exists" error
        error_msg = "tag with name 'test_tag' already exists"
        mixin.context.client.create_tag = AsyncMock(side_effect=Exception(error_msg))

        # Mock the logger to prevent test failure from log.warning
        with patch.object(mixin, "log") as mock_log:
            # Call the method with a single hashtag
            tags = await mixin._process_hashtags_to_tags([hashtag1])

        # Verify results
        assert len(tags) == 1
        assert tags[0].id == "tag_123"
        assert tags[0].name == "test_tag"

        # Verify error handling
        assert mixin.context.client.find_tags.call_count == 3
        mixin.context.client.create_tag.assert_called_once()
        mock_log.warning.assert_called_once()

        # Reset
        mixin.context.client.find_tags.reset_mock()
        mixin.context.client.create_tag.reset_mock()

        # Test with other type of error
        mixin.context.client.find_tags = AsyncMock(
            side_effect=[
                tag_results2,  # Tag not found by name
                tag_results2,  # Tag not found by alias
            ]
        )

        # Setup create_tag to fail with other error
        error_msg = "network error"
        mixin.context.client.create_tag = AsyncMock(side_effect=Exception(error_msg))

        # Call the method and expect error
        with pytest.raises(Exception) as excinfo:  # noqa: PT011 - message validated by assertion below
            await mixin._process_hashtags_to_tags([hashtag1])

        # Verify the error is re-raised
        assert "network error" in str(excinfo.value)

    @pytest.mark.asyncio
    async def test_add_preview_tag(self, mixin, mock_image):
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
        mixin.context.client.find_tags = AsyncMock(return_value=tag_results)

        # Test on image with no existing tags
        mock_image.tags = []

        # Call the method
        await mixin._add_preview_tag(mock_image)

        # Verify the tag was added
        assert len(mock_image.tags) == 1
        assert mock_image.tags[0].id == "tag_trailer"

        # Reset
        mixin.context.client.find_tags.reset_mock()

        # Test with existing tag (should not add duplicate)
        existing_tag = MagicMock()
        existing_tag.id = "tag_trailer"
        mock_image.tags = [existing_tag]

        # Call the method
        await mixin._add_preview_tag(mock_image)

        # Verify no additional tag was added
        assert len(mock_image.tags) == 1

        # Test with no tags found
        mock_image.tags = []
        tag_results.count = 0
        tag_results.tags = []

        # Call the method
        await mixin._add_preview_tag(mock_image)

        # Verify no tag was added
        assert len(mock_image.tags) == 0
