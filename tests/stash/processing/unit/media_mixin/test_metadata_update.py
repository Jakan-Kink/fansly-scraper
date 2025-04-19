"""Tests for metadata update methods in MediaProcessingMixin."""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestMetadataUpdate:
    """Test metadata update methods in MediaProcessingMixin."""

    @pytest.mark.asyncio
    async def test_update_stash_metadata_basic(
        self, mixin, mock_item, media_mock_account, mock_image
    ):
        """Test _update_stash_metadata method with basic metadata."""
        # Call method
        await mixin._update_stash_metadata(
            stash_obj=mock_image,
            item=mock_item,
            account=media_mock_account,
            media_id="media_123",
        )

        # Verify basic metadata was set
        assert mock_image.title == "Test Title"
        assert mock_image.details == mock_item.content
        assert mock_image.date == mock_item.createdAt.strftime("%Y-%m-%d")
        assert mock_image.code == "media_123"

        # Verify URL was added (since item is a Post)
        assert f"https://fansly.com/post/{mock_item.id}" in mock_image.urls

        # Verify title generation call
        mixin._generate_title_from_content.assert_called_once_with(
            content=mock_item.content,
            username=media_mock_account.username,
            created_at=mock_item.createdAt,
        )

        # Verify save was called
        mock_image.save.assert_called_once_with(mixin.context.client)

    @pytest.mark.asyncio
    async def test_update_stash_metadata_already_organized(
        self, mixin, mock_item, media_mock_account, mock_image
    ):
        """Test _update_stash_metadata method with already organized object."""
        # Mark as already organized
        mock_image.organized = True

        # Call method
        await mixin._update_stash_metadata(
            stash_obj=mock_image,
            item=mock_item,
            account=media_mock_account,
            media_id="media_123",
        )

        # Verify metadata was not updated
        assert mock_image.title != "Test Title"
        assert mock_image.code != "media_123"

        # Verify save was not called
        mock_image.save.assert_not_called()

    @pytest.mark.asyncio
    async def test_update_stash_metadata_later_date(
        self, mixin, mock_item, media_mock_account, mock_image
    ):
        """Test _update_stash_metadata method with later date."""
        # Setup earlier date in mock_image
        mock_image.date = "2024-03-01"  # Earlier than item date (2024-04-01)

        # Call method
        await mixin._update_stash_metadata(
            stash_obj=mock_image,
            item=mock_item,
            account=media_mock_account,
            media_id="media_123",
        )

        # Verify metadata was updated (because item date is later)
        assert mock_image.title == "Test Title"
        assert mock_image.date == mock_item.createdAt.strftime("%Y-%m-%d")
        assert mock_image.code == "media_123"

        # Verify save was called
        mock_image.save.assert_called_once()

        # Reset for next test
        mock_image.save.reset_mock()

        # Now test with an item date that's earlier than the object date
        mock_image.date = "2024-05-01"  # Later than item date
        earlier_item = MagicMock()
        earlier_item.content = "Earlier content"
        earlier_item.createdAt = datetime(
            2024, 4, 1, 0, 0, 0
        )  # Earlier than image date
        earlier_item.hashtags = []
        earlier_item.accountMentions = []
        earlier_item.awaitable_attrs = MagicMock()
        earlier_item.awaitable_attrs.hashtags = AsyncMock()
        earlier_item.awaitable_attrs.accountMentions = AsyncMock()
        earlier_item.__class__.__name__ = "Post"

        # Call method with earlier item
        await mixin._update_stash_metadata(
            stash_obj=mock_image,
            item=earlier_item,
            account=media_mock_account,
            media_id="media_123",
        )

        # Verify metadata was not updated (because item date is earlier than existing)
        assert mock_image.date == "2024-05-01"  # Still has the later date

        # Verify save was not called
        mock_image.save.assert_not_called()

    @pytest.mark.asyncio
    async def test_update_stash_metadata_performers(
        self, mixin, mock_item, media_mock_account, mock_image
    ):
        """Test _update_stash_metadata method with performers."""
        # Setup main performer
        main_performer = MagicMock()
        main_performer.id = "performer_123"
        mixin._find_existing_performer.return_value = main_performer

        # Get account mentions from mock_item fixture
        # (We're using the mentions already set up in the fixture)
        mention1, mention2 = mock_item.accountMentions

        # Setup mentioned performers
        mention_performer1 = MagicMock()
        mention_performer1.id = "performer_456"
        # Second mention will need a performer to be created

        # We're using account mentions from the fixture, which is already set up correctly

        # Mock find_existing_performer to return different values for each call
        mixin._find_existing_performer.side_effect = [
            main_performer,  # For main account
            mention_performer1,  # For first mention
            None,  # For second mention (will need to create)
        ]

        # Setup performer creation
        new_performer = MagicMock()
        new_performer.id = "performer_789"
        with patch("stash.types.Performer.from_account", return_value=new_performer):
            # Call method
            await mixin._update_stash_metadata(
                stash_obj=mock_image,
                item=mock_item,
                account=media_mock_account,
                media_id="media_123",
            )

        # Verify performers were added
        assert len(mock_image.performers) == 3
        assert mock_image.performers[0] == main_performer
        assert mock_image.performers[1] == mention_performer1
        assert mock_image.performers[2] == new_performer

        # Verify new performer was saved
        new_performer.save.assert_called_once_with(mixin.context.client)

        # Verify account stash ID was updated
        mixin._update_account_stash_id.assert_called_once_with(mention2, new_performer)

    @pytest.mark.asyncio
    async def test_update_stash_metadata_studio(
        self, mixin, mock_item, media_mock_account, mock_image
    ):
        """Test _update_stash_metadata method with studio."""
        # Setup studio
        mock_studio = MagicMock()
        mock_studio.id = "studio_123"
        mixin._find_existing_studio.return_value = mock_studio

        # Call method
        await mixin._update_stash_metadata(
            stash_obj=mock_image,
            item=mock_item,
            account=media_mock_account,
            media_id="media_123",
        )

        # Verify studio was set
        assert mock_image.studio == mock_studio

        # Verify studio finding was called
        mixin._find_existing_studio.assert_called_once_with(media_mock_account)

    @pytest.mark.asyncio
    async def test_update_stash_metadata_tags(
        self, mixin, mock_item, media_mock_account, mock_image
    ):
        """Test _update_stash_metadata method with tags."""
        # Setup hashtags
        hashtag1 = MagicMock()
        hashtag1.value = "test_tag"
        hashtag2 = MagicMock()
        hashtag2.value = "another_tag"
        mock_item.hashtags = [hashtag1, hashtag2]

        # Setup tag processing
        mock_tag1 = MagicMock()
        mock_tag1.id = "tag_123"
        mock_tag2 = MagicMock()
        mock_tag2.id = "tag_456"
        mixin._process_hashtags_to_tags.return_value = [mock_tag1, mock_tag2]

        # Call method
        await mixin._update_stash_metadata(
            stash_obj=mock_image,
            item=mock_item,
            account=media_mock_account,
            media_id="media_123",
        )

        # Verify tags were set
        assert mock_image.tags == [mock_tag1, mock_tag2]

        # Verify hashtag processing was called
        mixin._process_hashtags_to_tags.assert_called_once_with(mock_item.hashtags)

    @pytest.mark.asyncio
    async def test_update_stash_metadata_preview(
        self, mixin, mock_item, media_mock_account, mock_image
    ):
        """Test _update_stash_metadata method with preview flag."""
        # Call method with preview flag
        await mixin._update_stash_metadata(
            stash_obj=mock_image,
            item=mock_item,
            account=media_mock_account,
            media_id="media_123",
            is_preview=True,
        )

        # Verify preview tag was added
        mixin._add_preview_tag.assert_called_once_with(mock_image)

        # Reset for comparison
        mixin._add_preview_tag.reset_mock()

        # Call method without preview flag
        await mixin._update_stash_metadata(
            stash_obj=mock_image,
            item=mock_item,
            account=media_mock_account,
            media_id="media_123",
            is_preview=False,
        )

        # Verify preview tag was not added
        mixin._add_preview_tag.assert_not_called()

    @pytest.mark.asyncio
    async def test_update_stash_metadata_no_changes(
        self, mixin, mock_item, media_mock_account, mock_image
    ):
        """Test _update_stash_metadata method when no changes are needed."""
        # Mark object as not dirty
        mock_image.is_dirty.return_value = False

        # Call method
        await mixin._update_stash_metadata(
            stash_obj=mock_image,
            item=mock_item,
            account=media_mock_account,
            media_id="media_123",
        )

        # Verify save was not called (since object isn't dirty)
        mock_image.save.assert_not_called()
