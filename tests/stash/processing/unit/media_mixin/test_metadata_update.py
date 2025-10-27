"""Tests for metadata update methods in MediaProcessingMixin."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.fixtures.database_fixtures import AwaitableAttrsMock
from tests.fixtures.stash_type_factories import (
    PerformerFactory,
    StudioFactory,
    TagFactory,
)


class TestMetadataUpdate:
    """Test metadata update methods in MediaProcessingMixin."""

    @pytest.mark.asyncio
    async def test_update_stash_metadata_basic(
        self, media_mixin, mock_item, mock_account, mock_image
    ):
        """Test _update_stash_metadata method with basic metadata."""
        # Call method
        await media_mixin._update_stash_metadata(
            stash_obj=mock_image,
            item=mock_item,
            account=mock_account,
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
        media_mixin._generate_title_from_content.assert_called_once_with(
            content=mock_item.content,
            username=mock_account.username,
            created_at=mock_item.createdAt,
        )

        # Verify save was called
        mock_image.save.assert_called_once_with(media_mixin.context.client)

    @pytest.mark.asyncio
    async def test_update_stash_metadata_already_organized(
        self, media_mixin, mock_item, mock_account, mock_image
    ):
        """Test _update_stash_metadata method with already organized object."""
        # Mark as already organized
        mock_image.organized = True

        # Call method
        await media_mixin._update_stash_metadata(
            stash_obj=mock_image,
            item=mock_item,
            account=mock_account,
            media_id="media_123",
        )

        # Verify metadata was not updated
        assert mock_image.title != "Test Title"
        assert mock_image.code != "media_123"

        # Verify save was not called
        mock_image.save.assert_not_called()

    @pytest.mark.asyncio
    async def test_update_stash_metadata_later_date(
        self, media_mixin, mock_item, mock_account, mock_image
    ):
        """Test _update_stash_metadata preserves earliest metadata.

        The method should SKIP updates when the new item is LATER than existing,
        to preserve the earliest occurrence's metadata.
        """
        # Test 1: Item is LATER than existing date - should NOT update
        mock_image.date = "2024-03-01"  # Earlier date already stored
        original_title = mock_image.title  # Save original

        # mock_item has createdAt = 2024-04-01 (later than 2024-03-01)
        await media_mixin._update_stash_metadata(
            stash_obj=mock_image,
            item=mock_item,
            account=mock_account,
            media_id="media_123",
        )

        # Verify metadata was NOT updated (item is later, keep earliest)
        assert mock_image.title == original_title  # Title unchanged
        assert mock_image.date == "2024-03-01"  # Date unchanged

        # Verify save was not called (no changes)
        mock_image.save.assert_not_called()

        # Reset for next test
        mock_image.save.reset_mock()

        # Test 2: Item is EARLIER than existing date - should UPDATE
        mock_image.date = "2024-05-01"  # Later date in storage

        # Create item with earlier date
        earlier_item = MagicMock()
        earlier_item.id = 99999
        earlier_item.content = "Earlier content"
        earlier_item.createdAt = datetime(2024, 3, 1, 0, 0, 0, tzinfo=UTC)  # Earlier!
        earlier_item.hashtags = []
        earlier_item.accountMentions = []
        earlier_item.__class__.__name__ = "Post"

        # Use the generic AwaitableAttrsMock
        earlier_item.awaitable_attrs = AwaitableAttrsMock(earlier_item)

        # Call method with earlier item
        await media_mixin._update_stash_metadata(
            stash_obj=mock_image,
            item=earlier_item,
            account=mock_account,
            media_id="media_456",
        )

        # Verify metadata WAS updated (item is earlier, replace with earlier)
        assert mock_image.title == "Test Title"  # Updated
        assert mock_image.date == "2024-03-01"  # Updated to earlier date
        assert mock_image.code == "media_456"  # Updated

        # Verify save was called
        mock_image.save.assert_called_once()

    @pytest.mark.asyncio
    async def test_update_stash_metadata_performers(
        self, media_mixin, mock_item, mock_account, mock_image, factory_session
    ):
        """Test _update_stash_metadata method with performers."""
        # Setup main performer using real Performer type
        main_performer = PerformerFactory(
            id="performer_123",
            name=mock_account.username,
            urls=[f"https://fansly.com/{mock_account.username}"],
        )

        # Get account mentions from mock_item fixture
        # (We're using the mentions already set up in the fixture)
        mention1, mention2 = mock_item.accountMentions

        # Setup mentioned performers using real Performer types
        mention_performer1 = PerformerFactory(
            id="performer_456",
            name=mention1.username,
            urls=[f"https://fansly.com/{mention1.username}"],
        )
        # Second mention will need a performer to be created

        # Mock find_existing_performer to return different values for each call
        media_mixin._find_existing_performer.side_effect = [
            main_performer,  # For main account
            mention_performer1,  # For first mention
            None,  # For second mention (will need to create)
        ]

        # Setup performer creation using real Performer type
        new_performer = PerformerFactory(
            id="performer_789",
            name=mention2.username,
            urls=[f"https://fansly.com/{mention2.username}"],
        )
        # Mock the save method to track calls
        new_performer.save = AsyncMock()

        with patch("stash.types.Performer.from_account", return_value=new_performer):
            # Call method
            await media_mixin._update_stash_metadata(
                stash_obj=mock_image,
                item=mock_item,
                account=mock_account,
                media_id="media_123",
            )

        # Verify performers were added
        assert len(mock_image.performers) == 3
        assert mock_image.performers[0] == main_performer
        assert mock_image.performers[1] == mention_performer1
        assert mock_image.performers[2] == new_performer

        # Verify new performer was saved
        new_performer.save.assert_called_once_with(media_mixin.context.client)

        # Verify account stash ID was updated
        media_mixin._update_account_stash_id.assert_called_once_with(
            mention2, new_performer
        )

    @pytest.mark.asyncio
    async def test_update_stash_metadata_studio(
        self, media_mixin, mock_item, mock_account, mock_image
    ):
        """Test _update_stash_metadata method with studio."""
        # Setup studio using real Studio type
        studio = StudioFactory(
            id="studio_123",
            name=mock_account.username,
            url=f"https://fansly.com/{mock_account.username}",
        )
        media_mixin._find_existing_studio.return_value = studio

        # Call method
        await media_mixin._update_stash_metadata(
            stash_obj=mock_image,
            item=mock_item,
            account=mock_account,
            media_id="media_123",
        )

        # Verify studio was set
        assert mock_image.studio == studio

        # Verify studio finding was called
        media_mixin._find_existing_studio.assert_called_once_with(mock_account)

    @pytest.mark.asyncio
    async def test_update_stash_metadata_tags(
        self, media_mixin, mock_item, mock_account, mock_image
    ):
        """Test _update_stash_metadata method with tags."""

        # Create simple hashtag objects (just need value attribute for this test)
        class SimpleHashtag:
            def __init__(self, value):
                self.value = value

        hashtag1 = SimpleHashtag("test_tag")
        hashtag2 = SimpleHashtag("another_tag")
        mock_item.hashtags = [hashtag1, hashtag2]

        # Setup tag processing using real Tag types
        tag1 = TagFactory(id="tag_123", name="test_tag")
        tag2 = TagFactory(id="tag_456", name="another_tag")
        media_mixin._process_hashtags_to_tags.return_value = [tag1, tag2]

        # Call method
        await media_mixin._update_stash_metadata(
            stash_obj=mock_image,
            item=mock_item,
            account=mock_account,
            media_id="media_123",
        )

        # Verify tags were set
        assert mock_image.tags == [tag1, tag2]

        # Verify hashtag processing was called
        media_mixin._process_hashtags_to_tags.assert_called_once_with(
            mock_item.hashtags
        )

    @pytest.mark.asyncio
    async def test_update_stash_metadata_preview(
        self, media_mixin, mock_item, mock_account, mock_image
    ):
        """Test _update_stash_metadata method with preview flag."""
        # Call method with preview flag
        await media_mixin._update_stash_metadata(
            stash_obj=mock_image,
            item=mock_item,
            account=mock_account,
            media_id="media_123",
            is_preview=True,
        )

        # Verify preview tag was added
        media_mixin._add_preview_tag.assert_called_once_with(mock_image)

        # Reset for comparison
        media_mixin._add_preview_tag.reset_mock()

        # Call method without preview flag
        await media_mixin._update_stash_metadata(
            stash_obj=mock_image,
            item=mock_item,
            account=mock_account,
            media_id="media_123",
            is_preview=False,
        )

        # Verify preview tag was not added
        media_mixin._add_preview_tag.assert_not_called()

    @pytest.mark.asyncio
    async def test_update_stash_metadata_no_changes(
        self, media_mixin, mock_item, mock_account, mock_image
    ):
        """Test _update_stash_metadata method when no changes are needed."""
        # Mark object as not dirty
        mock_image.is_dirty.return_value = False

        # Call method
        await media_mixin._update_stash_metadata(
            stash_obj=mock_image,
            item=mock_item,
            account=mock_account,
            media_id="media_123",
        )

        # Verify save was not called (since object isn't dirty)
        mock_image.save.assert_not_called()
