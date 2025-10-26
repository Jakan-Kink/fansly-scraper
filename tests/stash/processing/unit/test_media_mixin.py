"""Tests for the MediaProcessingMixin.

This module imports all the media mixin tests to ensure they are discovered by pytest.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Import the modules instead of the classes to avoid fixture issues
from stash.processing.mixins.media import MediaProcessingMixin


class TestMixinClass(MediaProcessingMixin):
    """Test class that implements MediaProcessingMixin for testing."""

    def __init__(self):
        """Initialize test class."""
        self.context = MagicMock()
        self.context.client = MagicMock()
        self.database = MagicMock()
        self.log = MagicMock()
        self._find_existing_performer = AsyncMock()
        self._find_existing_studio = AsyncMock()
        self._generate_title_from_content = MagicMock(return_value="Test Title")
        self._process_hashtags_to_tags = AsyncMock()
        self._add_preview_tag = AsyncMock()


@pytest.fixture
def mixin():
    """Fixture for MediaProcessingMixin instance."""
    return TestMixinClass()


class TestMediaProcessingWithRealData:
    """Test media processing mixin with real JSON data."""

    @pytest.mark.asyncio
    async def test_process_media_with_real_data(
        self, mixin, sample_post, sample_account
    ):
        """Test processing media with real data from JSON."""
        # Get a media attachment from the sample post
        attachment = sample_post.attachments[0]
        media = attachment.media.media

        # Mock Stash client search responses
        mock_image = MagicMock()
        mock_image.visual_files = [MagicMock()]
        mock_image.visual_files[0].path = f"path/to/{media.id}.jpg"
        mock_image.__type_name__ = "Image"
        mock_image.is_dirty = MagicMock(return_value=True)
        mock_image.save = AsyncMock()

        mock_image_result = MagicMock()
        mock_image_result.count = 1
        mock_image_result.images = [mock_image]

        mixin.context.client.find_images = AsyncMock(return_value=mock_image_result)

        # Create an empty result dictionary
        result = {"images": [], "scenes": []}

        # Call _process_media with real data
        await mixin._process_media(media, sample_post, sample_account, result)

        # Verify results
        assert len(result["images"]) == 1
        assert result["images"][0] == mock_image
        assert len(result["scenes"]) == 0

        # Verify client calls
        if hasattr(media, "stash_id") and media.stash_id:
            # Should try to find by stash_id first
            mixin.context.client.find_image.assert_called_once_with(media.stash_id)
        else:
            # Should try to find by path
            mixin.context.client.find_images.assert_called_once()
            path_filter = mixin.context.client.find_images.call_args[1]["image_filter"]
            assert str(media.id) in str(path_filter)

    @pytest.mark.asyncio
    async def test_process_creator_attachment_with_real_data(
        self, mixin, sample_post, sample_account
    ):
        """Test process_creator_attachment with real data from JSON."""
        # Get an attachment from the sample post
        attachment = sample_post.attachments[0]

        # Make sure the awaitable_attrs return awaitable coroutines with valid results
        # This is key to avoid the TypeError with the MagicMock
        # Create a proper AsyncMock for each awaitable_attrs attribute
        attachment.awaitable_attrs = MagicMock()
        attachment.awaitable_attrs.bundle = AsyncMock(return_value=None)
        attachment.awaitable_attrs.is_aggregated_post = AsyncMock(return_value=False)
        attachment.awaitable_attrs.aggregated_post = AsyncMock(return_value=None)
        attachment.awaitable_attrs.media = AsyncMock(return_value=attachment.media)

        # Mock Stash client search responses
        mock_image = MagicMock()
        mock_image.visual_files = [MagicMock()]
        mock_image.visual_files[0].path = f"path/to/{attachment.media.media.id}.jpg"
        mock_image.__type_name__ = "Image"
        mock_image.is_dirty = MagicMock(return_value=True)
        mock_image.save = AsyncMock()

        mock_image_result = MagicMock()
        mock_image_result.count = 1
        mock_image_result.images = [mock_image]

        mixin.context.client.find_images = AsyncMock(return_value=mock_image_result)

        # We'll use a patched version of the method to avoid issues with awaitable attributes
        with patch(
            "stash.processing.mixins.media.MediaProcessingMixin._process_media",
            new=AsyncMock(),
        ) as mock_process_media:
            # Call process_creator_attachment with real data
            await mixin.process_creator_attachment(
                attachment=attachment,
                item=sample_post,
                account=sample_account,
            )

            # Verify _process_media was called
            mock_process_media.assert_called()


# No need to import classes directly as they're discovered by pytest
