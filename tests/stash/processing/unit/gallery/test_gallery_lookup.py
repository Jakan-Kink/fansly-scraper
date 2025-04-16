"""Tests for gallery lookup methods in GalleryProcessingMixin."""

from unittest.mock import AsyncMock, MagicMock

import pytest


class TestGalleryLookup:
    """Test gallery lookup methods in GalleryProcessingMixin."""

    @pytest.mark.asyncio
    async def test_get_gallery_by_stash_id(self, mixin, mock_item, mock_gallery):
        """Test _get_gallery_by_stash_id method."""
        # Setup
        mock_item.stash_id = "gallery_123"
        mixin.context.client.find_gallery = AsyncMock(return_value=mock_gallery)

        # Test with valid stash_id
        gallery = await mixin._get_gallery_by_stash_id(mock_item)

        # Verify
        assert gallery == mock_gallery
        mixin.context.client.find_gallery.assert_called_once_with("gallery_123")

        # Test with no stash_id
        mixin.context.client.find_gallery.reset_mock()
        mock_item.stash_id = None

        gallery = await mixin._get_gallery_by_stash_id(mock_item)

        # Verify
        assert gallery is None
        mixin.context.client.find_gallery.assert_not_called()

        # Test with gallery not found
        mixin.context.client.find_gallery.reset_mock()
        mock_item.stash_id = "gallery_123"
        mixin.context.client.find_gallery.return_value = None

        gallery = await mixin._get_gallery_by_stash_id(mock_item)

        # Verify
        assert gallery is None
        mixin.context.client.find_gallery.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_gallery_by_title(
        self, mixin, mock_item, mock_gallery, mock_studio
    ):
        """Test _get_gallery_by_title method."""
        # Setup for matching gallery
        mock_results = MagicMock()
        mock_results.count = 1
        mock_results.galleries = [
            {
                "id": "gallery_123",
                "title": "Test Title",
                "date": "2024-04-01",
                "studio": {"id": "studio_123"},
            }
        ]
        mixin.context.client.find_galleries = AsyncMock(return_value=mock_results)

        # Test with matching gallery (with studio)
        gallery = await mixin._get_gallery_by_title(
            mock_item, "Test Title", mock_studio
        )

        # Verify
        assert gallery is not None
        assert gallery.id == "gallery_123"
        assert gallery.title == "Test Title"
        assert mock_item.stash_id == "gallery_123"  # Should update item's stash_id
        mixin.context.client.find_galleries.assert_called_once()

        # Reset
        mixin.context.client.find_galleries.reset_mock()

        # Test with no matching galleries
        mock_results.count = 0
        mock_results.galleries = []

        gallery = await mixin._get_gallery_by_title(
            mock_item, "Test Title", mock_studio
        )

        # Verify
        assert gallery is None
        mixin.context.client.find_galleries.assert_called_once()

        # Reset
        mixin.context.client.find_galleries.reset_mock()

        # Test with galleries but no match (different title)
        mock_results.count = 1
        mock_results.galleries = [
            {
                "id": "gallery_123",
                "title": "Different Title",
                "date": "2024-04-01",
                "studio": {"id": "studio_123"},
            }
        ]

        gallery = await mixin._get_gallery_by_title(
            mock_item, "Test Title", mock_studio
        )

        # Verify
        assert gallery is None
        mixin.context.client.find_galleries.assert_called_once()

        # Reset
        mixin.context.client.find_galleries.reset_mock()

        # Test with galleries but no match (different date)
        mock_results.count = 1
        mock_results.galleries = [
            {
                "id": "gallery_123",
                "title": "Test Title",
                "date": "2024-04-02",
                "studio": {"id": "studio_123"},
            }
        ]

        gallery = await mixin._get_gallery_by_title(
            mock_item, "Test Title", mock_studio
        )

        # Verify
        assert gallery is None
        mixin.context.client.find_galleries.assert_called_once()

        # Reset
        mixin.context.client.find_galleries.reset_mock()

        # Test with galleries but no match (different studio)
        mock_results.count = 1
        mock_results.galleries = [
            {
                "id": "gallery_123",
                "title": "Test Title",
                "date": "2024-04-01",
                "studio": {"id": "different_studio"},
            }
        ]

        gallery = await mixin._get_gallery_by_title(
            mock_item, "Test Title", mock_studio
        )

        # Verify
        assert gallery is None
        mixin.context.client.find_galleries.assert_called_once()

        # Reset
        mixin.context.client.find_galleries.reset_mock()

        # Test with no studio parameter
        mock_results.count = 1
        mock_results.galleries = [
            {
                "id": "gallery_123",
                "title": "Test Title",
                "date": "2024-04-01",
                "studio": {"id": "studio_123"},
            }
        ]

        gallery = await mixin._get_gallery_by_title(mock_item, "Test Title", None)

        # Verify
        assert gallery is not None
        assert gallery.id == "gallery_123"
        mixin.context.client.find_galleries.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_gallery_by_code(self, mixin, mock_item, mock_gallery):
        """Test _get_gallery_by_code method."""
        # Setup for matching gallery
        mock_results = MagicMock()
        mock_results.count = 1
        mock_results.galleries = [{"id": "gallery_123", "code": "12345"}]
        mixin.context.client.find_galleries = AsyncMock(return_value=mock_results)

        # Test with matching gallery
        gallery = await mixin._get_gallery_by_code(mock_item)

        # Verify
        assert gallery is not None
        assert gallery.id == "gallery_123"
        assert gallery.code == "12345"
        assert mock_item.stash_id == "gallery_123"  # Should update item's stash_id
        mixin.context.client.find_galleries.assert_called_once()

        # Reset
        mixin.context.client.find_galleries.reset_mock()

        # Test with no matching galleries
        mock_results.count = 0
        mock_results.galleries = []

        gallery = await mixin._get_gallery_by_code(mock_item)

        # Verify
        assert gallery is None
        mixin.context.client.find_galleries.assert_called_once()

        # Reset
        mixin.context.client.find_galleries.reset_mock()

        # Test with galleries but no match (different code)
        mock_results.count = 1
        mock_results.galleries = [{"id": "gallery_123", "code": "54321"}]

        gallery = await mixin._get_gallery_by_code(mock_item)

        # Verify
        assert gallery is None
        mixin.context.client.find_galleries.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_gallery_by_url(self, mixin, mock_item, mock_gallery):
        """Test _get_gallery_by_url method."""
        # Setup for matching gallery
        mock_results = MagicMock()
        mock_results.count = 1
        mock_results.galleries = [
            {"id": "gallery_123", "urls": ["https://test.com/post/12345"]}
        ]
        mixin.context.client.find_galleries = AsyncMock(return_value=mock_results)

        # Test URL
        test_url = "https://test.com/post/12345"

        # Test with matching gallery
        gallery = await mixin._get_gallery_by_url(mock_item, test_url)

        # Verify
        assert gallery is not None
        assert gallery.id == "gallery_123"
        assert test_url in gallery.urls
        assert mock_item.stash_id == "gallery_123"  # Should update item's stash_id
        mixin.context.client.find_galleries.assert_called_once()
        gallery.save.assert_called_once()  # Should update gallery with code

        # Reset
        mixin.context.client.find_galleries.reset_mock()

        # Test with no matching galleries
        mock_results.count = 0
        mock_results.galleries = []

        gallery = await mixin._get_gallery_by_url(mock_item, test_url)

        # Verify
        assert gallery is None
        mixin.context.client.find_galleries.assert_called_once()

        # Reset
        mixin.context.client.find_galleries.reset_mock()

        # Test with galleries but no match (different URL)
        mock_results.count = 1
        mock_results.galleries = [
            {"id": "gallery_123", "urls": ["https://test.com/post/54321"]}
        ]

        gallery = await mixin._get_gallery_by_url(mock_item, test_url)

        # Verify
        assert gallery is None
        mixin.context.client.find_galleries.assert_called_once()
