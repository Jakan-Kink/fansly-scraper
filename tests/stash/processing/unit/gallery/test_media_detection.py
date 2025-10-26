"""Tests for media detection methods in GalleryProcessingMixin."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestMediaDetection:
    """Test media detection methods in GalleryProcessingMixin."""

    @pytest.mark.asyncio
    async def test_check_aggregated_posts(self, mixin):
        """Test _check_aggregated_posts method."""
        # Setup mock posts
        post1 = MagicMock()
        post2 = MagicMock()
        posts = [post1, post2]

        # Test when no posts have media
        mixin._has_media_content = AsyncMock(return_value=False)

        result = await mixin._check_aggregated_posts(posts)

        # Verify
        assert result is False
        assert mixin._has_media_content.call_count == 2

        # Reset
        mixin._has_media_content.reset_mock()

        # Test when first post has media
        mixin._has_media_content.side_effect = [True, False]

        result = await mixin._check_aggregated_posts(posts)

        # Verify
        assert result is True
        assert mixin._has_media_content.call_count == 1  # Should return early

        # Reset
        mixin._has_media_content.reset_mock()

        # Test when second post has media
        mixin._has_media_content.side_effect = [False, True]

        result = await mixin._check_aggregated_posts(posts)

        # Verify
        assert result is True
        assert mixin._has_media_content.call_count == 2

        # Reset
        mixin._has_media_content.reset_mock()

        # Test with empty list
        result = await mixin._check_aggregated_posts([])

        # Verify
        assert result is False
        mixin._has_media_content.assert_not_called()

    @pytest.mark.asyncio
    async def test_has_media_content(self, mixin, mock_item):
        """Test _has_media_content method."""
        # Setup attachments
        attachment1 = MagicMock()
        attachment1.contentType = "ACCOUNT_MEDIA"

        attachment2 = MagicMock()
        attachment2.contentType = "TEXT"

        attachment3 = MagicMock()
        attachment3.contentType = "ACCOUNT_MEDIA_BUNDLE"

        # Test with direct media content
        mock_item.attachments = [attachment1, attachment2]

        result = await mixin._has_media_content(mock_item)

        # Verify
        assert result is True

        # Test with no media content
        mock_item.attachments = [attachment2]  # Only TEXT

        result = await mixin._has_media_content(mock_item)

        # Verify
        assert result is False

        # Test with different media content type
        mock_item.attachments = [attachment3]  # ACCOUNT_MEDIA_BUNDLE

        result = await mixin._has_media_content(mock_item)

        # Verify
        assert result is True

        # Test with aggregated posts
        aggregated_attachment = MagicMock()
        aggregated_attachment.contentType = "AGGREGATED_POSTS"
        mock_post = MagicMock()
        aggregated_attachment.resolve_content = AsyncMock(return_value=mock_post)

        mock_item.attachments = [aggregated_attachment]

        # Mock _check_aggregated_posts to return True
        with patch.object(
            mixin, "_check_aggregated_posts", AsyncMock(return_value=True)
        ):
            result = await mixin._has_media_content(mock_item)

            # Verify
            assert result is True
            mixin._check_aggregated_posts.assert_called_once_with([mock_post])

        # Test with aggregated posts but no media
        with patch.object(
            mixin, "_check_aggregated_posts", AsyncMock(return_value=False)
        ):
            result = await mixin._has_media_content(mock_item)

            # Verify
            assert result is False
            mixin._check_aggregated_posts.assert_called_once_with([mock_post])

        # Test with no attachments
        mock_item.attachments = []

        result = await mixin._has_media_content(mock_item)

        # Verify
        assert result is False
