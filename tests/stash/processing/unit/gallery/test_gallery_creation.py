"""Tests for gallery creation methods in GalleryProcessingMixin."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestGalleryCreation:
    """Test gallery creation methods in GalleryProcessingMixin."""

    @pytest.mark.asyncio
    async def test_create_new_gallery(self, mixin, mock_item):
        """Test _create_new_gallery method."""
        # Call method
        gallery = await mixin._create_new_gallery(mock_item, "Test Title")

        # Verify gallery properties
        assert gallery.id == "new"
        assert gallery.title == "Test Title"
        assert gallery.details == mock_item.content
        assert gallery.code == str(mock_item.id)
        assert gallery.date == mock_item.createdAt.strftime("%Y-%m-%d")
        assert gallery.organized is True

    @pytest.mark.asyncio
    async def test_get_gallery_metadata(self, mixin, mock_item, gallery_mock_account):
        """Test _get_gallery_metadata method."""
        # Call method
        url_pattern = "https://test.com/{username}/post/{id}"
        username, title, url = await mixin._get_gallery_metadata(
            mock_item, gallery_mock_account, url_pattern
        )

        # Verify results
        assert username == "test_user"
        assert title == "Test Title"  # From the mock
        assert url == "https://test.com/test_user/post/12345"

        # Verify calls
        mixin._generate_title_from_content.assert_called_once_with(
            content=mock_item.content,
            username="test_user",
            created_at=mock_item.createdAt,
        )

    @pytest.mark.asyncio
    async def test_setup_gallery_performers(
        self, mixin, mock_gallery, mock_item, gallery_mock_performer
    ):
        """Test _setup_gallery_performers method."""
        # Setup for mentioned performers and mock awaitable attributes
        mention1 = MagicMock()
        mention2 = MagicMock()
        mock_item.accountMentions = [mention1, mention2]

        # Set up awaitable_attrs for performer
        gallery_mock_performer.awaitable_attrs = MagicMock()
        gallery_mock_performer.awaitable_attrs.id = AsyncMock(
            return_value=gallery_mock_performer.id
        )

        # Mock performers for mentions
        mention_performer1 = MagicMock()
        mention_performer1.id = "mention1"
        mention_performer1.awaitable_attrs = MagicMock()
        mention_performer1.awaitable_attrs.id = AsyncMock(return_value="mention1")

        mention_performer2 = MagicMock()
        mention_performer2.id = "mention2"
        mention_performer2.awaitable_attrs = MagicMock()
        mention_performer2.awaitable_attrs.id = AsyncMock(return_value="mention2")

        # Setup mixin method to return performers for mentions
        mixin._find_existing_performer.side_effect = [
            mention_performer1,  # First mention
            mention_performer2,  # Second mention
        ]

        # Call method
        await mixin._setup_gallery_performers(
            mock_gallery, mock_item, gallery_mock_performer
        )

        # Verify gallery performers
        assert len(mock_gallery.performers) == 3
        assert mock_gallery.performers[0] == gallery_mock_performer
        assert mock_gallery.performers[1] == mention_performer1
        assert mock_gallery.performers[2] == mention_performer2

        # Verify _find_existing_performer calls
        assert mixin._find_existing_performer.call_count == 2
        mixin._find_existing_performer.assert_any_call(mention1)
        mixin._find_existing_performer.assert_any_call(mention2)

        # Reset
        mock_gallery.performers = []
        mixin._find_existing_performer.reset_mock()

        # Test with no mentioned accounts
        mock_item.accountMentions = []

        # Call method
        await mixin._setup_gallery_performers(
            mock_gallery, mock_item, gallery_mock_performer
        )

        # Verify gallery performers (only main performer)
        assert len(mock_gallery.performers) == 1
        assert mock_gallery.performers[0] == gallery_mock_performer

        # Verify no calls to _find_existing_performer
        mixin._find_existing_performer.assert_not_called()

        # Reset
        mock_gallery.performers = []

        # Test with mentioned accounts but no performers found
        mock_item.accountMentions = [mention1, mention2]
        mixin._find_existing_performer.side_effect = [None, None]

        # Call method
        await mixin._setup_gallery_performers(
            mock_gallery, mock_item, gallery_mock_performer
        )

        # Verify gallery performers (only main performer)
        assert len(mock_gallery.performers) == 1
        assert mock_gallery.performers[0] == gallery_mock_performer

        # Reset
        mock_gallery.performers = []

        # Test with no main performer
        mock_item.accountMentions = [mention1]
        mixin._find_existing_performer.return_value = mention_performer1

        # Call method
        await mixin._setup_gallery_performers(mock_gallery, mock_item, None)

        # Verify gallery performers (only mentioned performer)
        assert len(mock_gallery.performers) == 1
        assert mock_gallery.performers[0] == mention_performer1

    @pytest.mark.asyncio
    async def test_get_or_create_gallery(
        self,
        mixin,
        mock_item,
        gallery_mock_account,
        gallery_mock_performer,
        gallery_mock_studio,
        mock_gallery,
    ):
        """Test _get_or_create_gallery method."""
        # Setup
        url_pattern = "https://test.com/{username}/post/{id}"

        # Mock _has_media_content to return True
        mixin._has_media_content = AsyncMock(return_value=True)

        # Mock _get_gallery_metadata
        mixin._get_gallery_metadata = AsyncMock(
            return_value=(
                "test_user",
                "Test Title",
                "https://test.com/test_user/post/12345",
            )
        )

        # Test when gallery found by stash_id
        mixin._get_gallery_by_stash_id = AsyncMock(return_value=mock_gallery)
        mixin._get_gallery_by_code = AsyncMock(return_value=None)
        mixin._get_gallery_by_title = AsyncMock(return_value=None)
        mixin._get_gallery_by_url = AsyncMock(return_value=None)

        gallery = await mixin._get_or_create_gallery(
            mock_item,
            gallery_mock_account,
            gallery_mock_performer,
            gallery_mock_studio,
            "post",
            url_pattern,
        )

        # Verify
        assert gallery == mock_gallery
        mixin._get_gallery_by_stash_id.assert_called_once_with(mock_item)
        mixin._get_gallery_by_code.assert_not_called()
        mixin._get_gallery_by_title.assert_not_called()
        mixin._get_gallery_by_url.assert_not_called()

        # Reset
        mixin._get_gallery_by_stash_id.reset_mock()

        # Test when gallery found by code
        mixin._get_gallery_by_stash_id = AsyncMock(return_value=None)
        mixin._get_gallery_by_code = AsyncMock(return_value=mock_gallery)

        gallery = await mixin._get_or_create_gallery(
            mock_item,
            gallery_mock_account,
            gallery_mock_performer,
            gallery_mock_studio,
            "post",
            url_pattern,
        )

        # Verify
        assert gallery == mock_gallery
        mixin._get_gallery_by_stash_id.assert_called_once_with(mock_item)
        mixin._get_gallery_by_code.assert_called_once_with(mock_item)
        mixin._get_gallery_by_title.assert_not_called()
        mixin._get_gallery_by_url.assert_not_called()

        # Reset
        mixin._get_gallery_by_stash_id.reset_mock()
        mixin._get_gallery_by_code.reset_mock()

        # Test when gallery found by title
        mixin._get_gallery_by_stash_id = AsyncMock(return_value=None)
        mixin._get_gallery_by_code = AsyncMock(return_value=None)
        mixin._get_gallery_by_title = AsyncMock(return_value=mock_gallery)

        gallery = await mixin._get_or_create_gallery(
            mock_item,
            gallery_mock_account,
            gallery_mock_performer,
            gallery_mock_studio,
            "post",
            url_pattern,
        )

        # Verify
        assert gallery == mock_gallery
        mixin._get_gallery_by_stash_id.assert_called_once_with(mock_item)
        mixin._get_gallery_by_code.assert_called_once_with(mock_item)
        mixin._get_gallery_by_title.assert_called_once_with(
            mock_item, "Test Title", gallery_mock_studio
        )
        mixin._get_gallery_by_url.assert_not_called()

        # Reset
        mixin._get_gallery_by_stash_id.reset_mock()
        mixin._get_gallery_by_code.reset_mock()
        mixin._get_gallery_by_title.reset_mock()

        # Test when gallery found by URL
        mixin._get_gallery_by_stash_id = AsyncMock(return_value=None)
        mixin._get_gallery_by_code = AsyncMock(return_value=None)
        mixin._get_gallery_by_title = AsyncMock(return_value=None)
        mixin._get_gallery_by_url = AsyncMock(return_value=mock_gallery)

        gallery = await mixin._get_or_create_gallery(
            mock_item,
            gallery_mock_account,
            gallery_mock_performer,
            gallery_mock_studio,
            "post",
            url_pattern,
        )

        # Verify
        assert gallery == mock_gallery
        mixin._get_gallery_by_stash_id.assert_called_once_with(mock_item)
        mixin._get_gallery_by_code.assert_called_once_with(mock_item)
        mixin._get_gallery_by_title.assert_called_once_with(
            mock_item, "Test Title", gallery_mock_studio
        )
        mixin._get_gallery_by_url.assert_called_once_with(
            mock_item, "https://test.com/test_user/post/12345"
        )

        # Reset
        mixin._get_gallery_by_stash_id.reset_mock()
        mixin._get_gallery_by_code.reset_mock()
        mixin._get_gallery_by_title.reset_mock()
        mixin._get_gallery_by_url.reset_mock()

        # Test when no gallery found (create new)
        mixin._get_gallery_by_stash_id = AsyncMock(return_value=None)
        mixin._get_gallery_by_code = AsyncMock(return_value=None)
        mixin._get_gallery_by_title = AsyncMock(return_value=None)
        mixin._get_gallery_by_url = AsyncMock(return_value=None)

        # Mock create and setup methods
        new_gallery = MagicMock()
        new_gallery.id = "new"
        new_gallery.performers = []
        new_gallery.urls = []
        new_gallery.chapters = []
        new_gallery.save = AsyncMock()

        mixin._create_new_gallery = AsyncMock(return_value=new_gallery)
        mixin._setup_gallery_performers = AsyncMock()

        gallery = await mixin._get_or_create_gallery(
            mock_item,
            gallery_mock_account,
            gallery_mock_performer,
            gallery_mock_studio,
            "post",
            url_pattern,
        )

        # Verify
        assert gallery == new_gallery
        mixin._get_gallery_by_stash_id.assert_called_once_with(mock_item)
        mixin._get_gallery_by_code.assert_called_once_with(mock_item)
        mixin._get_gallery_by_title.assert_called_once_with(
            mock_item, "Test Title", gallery_mock_studio
        )
        mixin._get_gallery_by_url.assert_called_once_with(
            mock_item, "https://test.com/test_user/post/12345"
        )
        mixin._create_new_gallery.assert_called_once_with(mock_item, "Test Title")
        mixin._setup_gallery_performers.assert_called_once_with(
            new_gallery, mock_item, gallery_mock_performer
        )
        assert new_gallery.studio == gallery_mock_studio
        assert url_pattern in new_gallery.urls
        new_gallery.save.assert_called_once_with(mixin.context.client)

        # Test when item has no media content
        mixin._has_media_content = AsyncMock(return_value=False)
        mixin._get_gallery_metadata = AsyncMock()  # Reset mock

        gallery = await mixin._get_or_create_gallery(
            mock_item,
            gallery_mock_account,
            gallery_mock_performer,
            gallery_mock_studio,
            "post",
            url_pattern,
        )

        # Verify
        assert gallery is None
        mixin._get_gallery_metadata.assert_not_called()  # Should return early
