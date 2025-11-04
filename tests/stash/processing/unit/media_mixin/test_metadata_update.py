"""Tests for metadata update methods in MediaProcessingMixin."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, Mock

import pytest

from stash.types import FindStudiosResultType
from tests.fixtures.stash_type_factories import (
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
        # Mock ONLY external API calls (not internal methods)
        # Provide proper test data for what the real methods will call

        # find_performer will be called by _find_existing_performer
        media_mixin.context.client.find_performer = AsyncMock(return_value=None)

        # find_studios will be called by process_creator_studio
        # It looks for "Fansly (network)" first, then creator studio
        # GraphQL returns dicts that get unpacked by production code
        fansly_studio_result = FindStudiosResultType(
            count=1,
            studios=[
                {"id": "1", "name": "Fansly (network)"}
            ],  # Dict like GraphQL returns
        )

        creator_studio_result = FindStudiosResultType(
            count=0,  # Creator studio doesn't exist yet
            studios=[],
        )

        media_mixin.context.client.find_studios = AsyncMock(
            side_effect=[fansly_studio_result, creator_studio_result]
        )

        # create_studio will be called to create the creator studio
        created_studio = StudioFactory(
            id="created_123",
            name=f"{mock_account.username} (Fansly)",
            url=f"https://fansly.com/{mock_account.username}",
        )
        media_mixin.context.client.create_studio = AsyncMock(
            return_value=created_studio
        )

        # execute will be called by stash_obj.save() for GraphQL mutations
        # GraphQL returns dict, not mock object
        media_mixin.context.client.execute = AsyncMock(
            return_value={
                "imageUpdate": {
                    "id": mock_image.id,
                    "title": mock_image.title,
                    "code": mock_image.code,
                    "date": mock_image.date,
                    "details": mock_image.details,
                }
            }
        )

        # Call method - let real internal methods run
        await media_mixin._update_stash_metadata(
            stash_obj=mock_image,
            item=mock_item,
            account=mock_account,
            media_id="media_123",
        )

        # Verify basic metadata was set (check RESULTS, not mock calls)
        assert mock_image.details == mock_item.content
        assert mock_image.date == mock_item.createdAt.strftime("%Y-%m-%d")
        assert mock_image.code == "media_123"
        # Title should be set by real _generate_title_from_content method
        assert mock_image.title is not None
        assert len(mock_image.title) > 0

        # Verify URL was added (since item is a Post)
        assert f"https://fansly.com/post/{mock_item.id}" in mock_image.urls

        # No need to verify save() was called - if it failed, we'd have gotten an exception
        # With real objects, we verify RESULTS not mock calls

    @pytest.mark.asyncio
    async def test_update_stash_metadata_already_organized(
        self, media_mixin, mock_item, mock_account, mock_image
    ):
        """Test _update_stash_metadata method with already organized object."""
        # Mark as already organized and save original values
        mock_image.organized = True
        original_title = mock_image.title
        original_code = mock_image.code
        original_details = mock_image.details

        # Call method
        await media_mixin._update_stash_metadata(
            stash_obj=mock_image,
            item=mock_item,
            account=mock_account,
            media_id="media_123",
        )

        # Verify metadata was NOT updated (values unchanged)
        assert mock_image.title == original_title
        assert mock_image.code == original_code
        assert mock_image.details == original_details
        # No need to check save() - method exits early, no exception = success

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
        original_code = mock_image.code

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
        assert mock_image.code == original_code  # Code unchanged
        # No exception = method exited early correctly

        # Test 2: Item is EARLIER than existing date - should UPDATE
        mock_image.date = "2024-05-01"  # Later date in storage

        # Create item with earlier date using PostFactory
        from tests.fixtures.metadata_factories import PostFactory

        earlier_item = PostFactory.build(
            id=99999,
            accountId=mock_account.id,
            content="Earlier content",
            createdAt=datetime(2024, 3, 1, 0, 0, 0, tzinfo=UTC),  # Earlier!
        )
        earlier_item.hashtags = []
        earlier_item.accountMentions = []

        # Mock external API calls for the update path
        media_mixin.context.client.find_performer = AsyncMock(return_value=None)

        fansly_studio_result = FindStudiosResultType(
            count=1, studios=[{"id": "1", "name": "Fansly (network)"}]
        )
        creator_studio_result = FindStudiosResultType(count=0, studios=[])

        media_mixin.context.client.find_studios = AsyncMock(
            side_effect=[fansly_studio_result, creator_studio_result]
        )

        created_studio = StudioFactory(
            id="created_123",
            name=f"{mock_account.username} (Fansly)",
            url=f"https://fansly.com/{mock_account.username}",
        )
        media_mixin.context.client.create_studio = AsyncMock(
            return_value=created_studio
        )

        media_mixin.context.client.execute = AsyncMock(
            return_value={
                "imageUpdate": {
                    "id": mock_image.id,
                    "title": "Earlier content",
                    "code": "media_456",
                    "date": "2024-03-01",
                    "details": "Earlier content",
                }
            }
        )

        # Call method with earlier item
        await media_mixin._update_stash_metadata(
            stash_obj=mock_image,
            item=earlier_item,
            account=mock_account,
            media_id="media_456",
        )

        # Verify metadata WAS updated (item is earlier, replace with earlier)
        assert mock_image.date == "2024-03-01"  # Updated to earlier date
        assert mock_image.code == "media_456"  # Updated
        assert mock_image.details == "Earlier content"  # Updated

    @pytest.mark.asyncio
    async def test_update_stash_metadata_performers(
        self, media_mixin, mock_item, mock_account, mock_image, session
    ):
        """Test _update_stash_metadata method with performers."""
        # Create account mentions using AccountFactory
        from contextlib import asynccontextmanager

        from tests.fixtures.metadata_factories import AccountFactory

        mention1 = AccountFactory.build(
            id=22222,
            username="mention_user1",
        )
        mention2 = AccountFactory.build(
            id=33333,
            username="mention_user2",
        )
        mock_item.accountMentions = [mention1, mention2]

        # Create REAL Account objects for database operations (_update_account_stash_id needs to query them)
        # These have the same IDs as the mock objects used in the test
        real_main_account = AccountFactory.build(
            id=mock_account.id,
            username=mock_account.username,
        )
        real_mention1 = AccountFactory.build(
            id=22222,
            username="mention_user1",
        )
        real_mention2 = AccountFactory.build(
            id=33333,
            username="mention_user2",
        )

        # Add REAL accounts to session so they exist in database for _update_account_stash_id
        session.add(real_main_account)
        session.add(real_mention1)
        session.add(real_mention2)
        await session.commit()

        # Mock database.async_session_scope() for @with_session() decorator
        @asynccontextmanager
        async def mock_session_scope():
            yield session

        media_mixin.database.async_session_scope = mock_session_scope

        # Mock ONLY external API calls (not _find_existing_performer internal method!)
        # find_performer will be called 3 times by _find_existing_performer:
        # 1. For main account (found)
        # 2. For mention1 (found)
        # 3. For mention2 (not found, will create)

        main_performer_dict = {
            "id": "performer_123",
            "name": mock_account.username,
            "urls": [f"https://fansly.com/{mock_account.username}"],
        }
        mention1_performer_dict = {
            "id": "performer_456",
            "name": mention1.username,
            "urls": [f"https://fansly.com/{mention1.username}"],
        }

        media_mixin.context.client.find_performer = AsyncMock(
            side_effect=[
                main_performer_dict,  # Main account found
                mention1_performer_dict,  # Mention1 found
                None,  # Mention2 not found
            ]
        )

        # create_performer will be called for mention2
        new_performer_dict = {
            "id": "789",  # Numeric string for Stash ID
            "name": mention2.username,
            "urls": [f"https://fansly.com/{mention2.username}"],
        }
        media_mixin.context.client.create_performer = AsyncMock(
            return_value=new_performer_dict
        )

        # Mock external API calls for studio processing
        fansly_studio_result = FindStudiosResultType(
            count=1, studios=[{"id": "1", "name": "Fansly (network)"}]
        )
        creator_studio_result = FindStudiosResultType(count=0, studios=[])

        media_mixin.context.client.find_studios = AsyncMock(
            side_effect=[fansly_studio_result, creator_studio_result]
        )

        created_studio = StudioFactory(
            id="created_123",
            name=f"{mock_account.username} (Fansly)",
        )
        media_mixin.context.client.create_studio = AsyncMock(
            return_value=created_studio
        )

        # execute will be called twice: once for performerCreate, once for imageUpdate
        media_mixin.context.client.execute = AsyncMock(
            side_effect=[
                {
                    "performerCreate": {"id": "789", "name": mention2.username}
                },  # New performer
                {
                    "imageUpdate": {
                        "id": mock_image.id,
                        "title": mock_image.title,
                        "code": "media_123",
                    }
                },  # Image save
            ]
        )

        # Call method - real _find_existing_performer runs
        await media_mixin._update_stash_metadata(
            stash_obj=mock_image,
            item=mock_item,
            account=mock_account,
            media_id="media_123",
        )

        # Verify performers were added (check RESULTS)
        assert len(mock_image.performers) == 3
        # Verify performers have correct names
        # Performers are dicts from GraphQL (not Performer objects)
        performer_names = [
            p["name"] if isinstance(p, dict) else p.name for p in mock_image.performers
        ]
        assert mock_account.username in performer_names
        assert mention1.username in performer_names
        # mention2 is newly created, so it might have "Display " prefix from Performer.from_account()
        assert any(mention2.username in name for name in performer_names)

    @pytest.mark.asyncio
    async def test_update_stash_metadata_studio(
        self, factory_session, media_mixin, mock_item, mock_account, mock_image
    ):
        """Test _update_stash_metadata method with studio."""
        # Mock ONLY external API calls (not _find_existing_studio internal method!)
        # _find_existing_studio calls process_creator_studio which calls find_studios

        media_mixin.context.client.find_performer = AsyncMock(return_value=None)

        # find_studios for Fansly network and creator studio
        fansly_studio_result = FindStudiosResultType(
            count=1, studios=[{"id": "1", "name": "Fansly (network)"}]
        )
        creator_studio_result = FindStudiosResultType(
            count=1,
            studios=[
                {
                    "id": "studio_123",
                    "name": f"{mock_account.username} (Fansly)",
                    "url": f"https://fansly.com/{mock_account.username}",
                }
            ],
        )

        media_mixin.context.client.find_studios = AsyncMock(
            side_effect=[fansly_studio_result, creator_studio_result]
        )

        # execute for save()
        media_mixin.context.client.execute = AsyncMock(
            return_value={
                "imageUpdate": {
                    "id": mock_image.id,
                    "title": mock_image.title,
                    "code": "media_123",
                }
            }
        )

        # Call method - real _find_existing_studio runs
        await media_mixin._update_stash_metadata(
            stash_obj=mock_image,
            item=mock_item,
            account=mock_account,
            media_id="media_123",
        )

        # Verify studio was set (check RESULTS)
        assert mock_image.studio is not None
        assert mock_image.studio.name == f"{mock_account.username} (Fansly)"

    @pytest.mark.asyncio
    async def test_update_stash_metadata_tags(
        self, factory_session, media_mixin, mock_item, mock_account, mock_image
    ):
        """Test _update_stash_metadata method with tags."""
        # Create real hashtag objects using HashtagFactory
        from tests.fixtures.metadata_factories import HashtagFactory

        hashtag1 = HashtagFactory.build(value="test_tag")
        hashtag2 = HashtagFactory.build(value="another_tag")
        mock_item.hashtags = [hashtag1, hashtag2]

        # Mock ONLY external API calls (not _process_hashtags_to_tags internal method!)
        # _process_hashtags_to_tags calls find_tags TWICE (once per hashtag) and unpacks dicts

        from stash.types import FindTagsResultType

        # find_tags returns dicts (GraphQL responses) which get unpacked by _process_hashtags_to_tags
        tag1_result = FindTagsResultType(
            count=1, tags=[{"id": "tag_123", "name": "test_tag"}]
        )
        tag2_result = FindTagsResultType(
            count=1, tags=[{"id": "tag_456", "name": "another_tag"}]
        )

        media_mixin.context.client.find_tags = AsyncMock(
            side_effect=[tag1_result, tag2_result]  # Called twice, once per hashtag
        )

        # Mock other required external calls
        media_mixin.context.client.find_performer = AsyncMock(return_value=None)

        fansly_studio_result = FindStudiosResultType(
            count=1, studios=[{"id": "1", "name": "Fansly (network)"}]
        )
        creator_studio_result = FindStudiosResultType(count=0, studios=[])

        media_mixin.context.client.find_studios = AsyncMock(
            side_effect=[fansly_studio_result, creator_studio_result]
        )

        created_studio = StudioFactory(
            id="created_123", name=f"{mock_account.username} (Fansly)"
        )
        media_mixin.context.client.create_studio = AsyncMock(
            return_value=created_studio
        )

        media_mixin.context.client.execute = AsyncMock(
            return_value={
                "imageUpdate": {
                    "id": mock_image.id,
                    "title": mock_image.title,
                    "code": "media_123",
                }
            }
        )

        # Call method - real _process_hashtags_to_tags runs
        await media_mixin._update_stash_metadata(
            stash_obj=mock_image,
            item=mock_item,
            account=mock_account,
            media_id="media_123",
        )

        # Verify tags were set (check RESULTS)
        assert len(mock_image.tags) == 2
        tag_names = [t.name for t in mock_image.tags]
        assert "test_tag" in tag_names
        assert "another_tag" in tag_names

    @pytest.mark.asyncio
    async def test_update_stash_metadata_preview(
        self, media_mixin, mock_item, mock_account, mock_image
    ):
        """Test _update_stash_metadata method with preview flag."""
        # Mock ONLY external API calls (not _add_preview_tag internal method!)
        # _add_preview_tag calls find_tags with q="Trailer"

        from stash.types import FindTagsResultType

        # find_tags for "Trailer" tag (not "preview" - see _add_preview_tag line 117)
        preview_tag = TagFactory(id="preview_tag_id", name="Trailer")
        preview_tag_result = FindTagsResultType(
            count=1,
            tags=[preview_tag],  # Tag object, not dict
        )

        media_mixin.context.client.find_performer = AsyncMock(return_value=None)

        # find_tags will be called for "Trailer" tag
        media_mixin.context.client.find_tags = AsyncMock(
            return_value=preview_tag_result
        )

        fansly_studio_result = FindStudiosResultType(
            count=1, studios=[{"id": "1", "name": "Fansly (network)"}]
        )
        creator_studio_result = FindStudiosResultType(count=0, studios=[])

        media_mixin.context.client.find_studios = AsyncMock(
            side_effect=[fansly_studio_result, creator_studio_result]
        )

        created_studio = StudioFactory(
            id="created_123", name=f"{mock_account.username} (Fansly)"
        )
        media_mixin.context.client.create_studio = AsyncMock(
            return_value=created_studio
        )

        media_mixin.context.client.execute = AsyncMock(
            return_value={
                "imageUpdate": {
                    "id": mock_image.id,
                    "title": mock_image.title,
                    "code": "media_123",
                }
            }
        )

        # Call method with preview flag - real _add_preview_tag runs
        await media_mixin._update_stash_metadata(
            stash_obj=mock_image,
            item=mock_item,
            account=mock_account,
            media_id="media_123",
            is_preview=True,
        )

        # Verify "Trailer" tag was added (check RESULTS)
        tag_names = [t.name for t in mock_image.tags]
        assert "Trailer" in tag_names

    @pytest.mark.asyncio
    async def test_update_stash_metadata_no_changes(
        self, media_mixin, mock_item, mock_account, mock_image
    ):
        """Test _update_stash_metadata method when no changes are needed."""
        # Mark object as not dirty
        mock_image.is_dirty = Mock(return_value=False)

        # Mock external API calls (may be called before dirty check)
        media_mixin.context.client.find_performer = AsyncMock(return_value=None)

        # find_studios might be called before dirty check
        fansly_studio_result = FindStudiosResultType(
            count=1, studios=[{"id": "1", "name": "Fansly (network)"}]
        )
        media_mixin.context.client.find_studios = AsyncMock(
            return_value=fansly_studio_result
        )

        media_mixin.context.client.execute = AsyncMock()

        # Call method
        await media_mixin._update_stash_metadata(
            stash_obj=mock_image,
            item=mock_item,
            account=mock_account,
            media_id="media_123",
        )

        # Verify execute was not called (object not dirty, so save() skipped)
        media_mixin.context.client.execute.assert_not_called()
