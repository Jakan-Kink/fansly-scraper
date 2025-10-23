"""Tests for gallery creation methods in GalleryProcessingMixin."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from metadata import Account, Post, post_mentions
from stash.types import Gallery, Performer
from tests.fixtures import AccountFactory, PostFactory


class TestGalleryCreation:
    """Test gallery creation methods in GalleryProcessingMixin."""

    @pytest.mark.asyncio
    async def test_create_new_gallery(
        self,
        factory_async_session,
        session,
        gallery_mixin,
    ):
        """Test _create_new_gallery method."""
        # Create account first (FK requirement)
        account = AccountFactory(id=10000, username="test_user")

        # Create real Post object with factory
        post = PostFactory(
            id=12345,
            accountId=10000,
            content="Test content #test #hashtag",
            createdAt=datetime(2024, 4, 1, 12, 0, 0, tzinfo=timezone.utc),
        )

        # Query fresh from async session (use .unique() for joined eager loads)
        result = await session.execute(select(Post).where(Post.id == 12345))
        post_item = result.unique().scalar_one()

        # Call method
        gallery = await gallery_mixin._create_new_gallery(post_item, "Test Title")

        # Verify gallery properties
        assert gallery.id == "new"
        assert gallery.title == "Test Title"
        assert gallery.details == post_item.content
        assert gallery.code == str(post_item.id)
        assert gallery.date == post_item.createdAt.strftime("%Y-%m-%d")
        assert gallery.organized is True

    @pytest.mark.asyncio
    async def test_get_gallery_metadata(
        self,
        factory_async_session,
        session,
        gallery_mixin,
    ):
        """Test _get_gallery_metadata method."""
        # Create real Account and Post with factories
        account = AccountFactory(id=12345, username="test_user")
        post = PostFactory(
            id=67890,
            accountId=12345,
            content="Test content #test",
            createdAt=datetime(2024, 4, 1, 12, 0, 0, tzinfo=timezone.utc),
        )

        # Query fresh from async session
        result = await session.execute(select(Account).where(Account.id == 12345))
        account_obj = result.scalar_one()

        result = await session.execute(select(Post).where(Post.id == 67890))
        post_obj = result.unique().scalar_one()

        # Call method
        url_pattern = "https://test.com/{username}/post/{id}"
        username, title, url = await gallery_mixin._get_gallery_metadata(
            post_obj, account_obj, url_pattern
        )

        # Verify results
        assert username == "test_user"
        assert title == "Test Title"  # From the mocked _generate_title_from_content
        assert url == "https://test.com/test_user/post/67890"

        # Verify calls
        gallery_mixin._generate_title_from_content.assert_called_once_with(
            content=post_obj.content,
            username="test_user",
            created_at=post_obj.createdAt,
        )

    @pytest.mark.asyncio
    async def test_setup_gallery_performers(
        self,
        factory_async_session,
        session,
        gallery_mixin,
        mock_gallery,
        gallery_mock_performer,
    ):
        """Test _setup_gallery_performers method."""
        # Create post author account (FK requirement)
        post_author = AccountFactory(id=10000, username="post_author")

        # Create real accounts for mentions
        mention_account1 = AccountFactory(id=20001, username="mention1")
        mention_account2 = AccountFactory(id=20002, username="mention2")

        # Note: post_mentions has postId as PRIMARY KEY, so only one mention per post.
        # To test multiple mentions, create a "virtual" post with mentions by testing the relationship.
        # For this test, we'll focus on testing the method logic with the main post and mock find_existing_performer.

        # Create main post for testing
        post = PostFactory(id=77777, accountId=10000, content="Test post")

        # Create mention relationship (postId is PK, so only one per post)
        await session.execute(
            post_mentions.insert().values(
                {"postId": 77777, "accountId": 20001, "handle": "mention1"}
            )
        )
        await session.commit()

        # Query post with mentions loaded
        result = await session.execute(
            select(Post)
            .where(Post.id == 77777)
            .options(selectinload(Post.accountMentions))
        )
        post_obj = result.unique().scalar_one()

        # Verify mention was loaded
        assert len(post_obj.accountMentions) == 1

        # Mock Stash performer for mention
        mention_performer1 = MagicMock(spec=Performer)
        mention_performer1.id = "stash_mention1"

        # Setup mixin method to return performer for mention
        gallery_mixin._find_existing_performer.return_value = mention_performer1

        # Call method
        await gallery_mixin._setup_gallery_performers(
            mock_gallery, post_obj, gallery_mock_performer
        )

        # Verify gallery performers (main + 1 mention)
        assert len(mock_gallery.performers) == 2
        assert mock_gallery.performers[0] == gallery_mock_performer
        assert mock_gallery.performers[1] == mention_performer1

        # Verify _find_existing_performer was called for the mention
        assert gallery_mixin._find_existing_performer.call_count == 1

        # Test with no mentioned accounts - create separate post
        post2 = PostFactory(id=99999, accountId=10000, content="Test post no mentions")
        await session.commit()  # Commit so post2 is visible

        result = await session.execute(
            select(Post)
            .where(Post.id == 99999)
            .options(selectinload(Post.accountMentions))
        )
        post_obj2 = result.unique().scalar_one()

        mock_gallery.performers = []
        gallery_mixin._find_existing_performer.reset_mock()

        await gallery_mixin._setup_gallery_performers(
            mock_gallery, post_obj2, gallery_mock_performer
        )

        # Verify gallery performers (only main performer)
        assert len(mock_gallery.performers) == 1
        assert mock_gallery.performers[0] == gallery_mock_performer
        gallery_mixin._find_existing_performer.assert_not_called()

        # Test with mentioned accounts but no performers found
        mock_gallery.performers = []
        gallery_mixin._find_existing_performer.reset_mock()
        gallery_mixin._find_existing_performer.return_value = (
            None  # No performer found for mention
        )

        await gallery_mixin._setup_gallery_performers(
            mock_gallery, post_obj, gallery_mock_performer
        )

        # Verify gallery performers (only main performer when mention not found in Stash)
        assert len(mock_gallery.performers) == 1
        assert mock_gallery.performers[0] == gallery_mock_performer

        # Test with no main performer
        mock_gallery.performers = []
        gallery_mixin._find_existing_performer.reset_mock()
        gallery_mixin._find_existing_performer.return_value = mention_performer1

        await gallery_mixin._setup_gallery_performers(mock_gallery, post_obj, None)

        # Verify gallery performers (only mentioned performer found)
        assert len(mock_gallery.performers) == 1
        assert mock_gallery.performers[0] == mention_performer1

    @pytest.mark.asyncio
    async def test_get_or_create_gallery(
        self,
        factory_async_session,
        session,
        gallery_mixin,
        gallery_mock_performer,
        gallery_mock_studio,
        mock_gallery,
    ):
        """Test _get_or_create_gallery method."""
        # Create real account and post with factory
        account = AccountFactory(id=12345, username="test_user")
        post = PostFactory(
            id=67890,
            accountId=12345,
            content="Test post content",
            createdAt=datetime(2024, 4, 1, 12, 0, 0, tzinfo=timezone.utc),
        )

        # Query fresh from async session
        result = await session.execute(select(Account).where(Account.id == 12345))
        account_obj = result.scalar_one()

        result = await session.execute(select(Post).where(Post.id == 67890))
        post_obj = result.unique().scalar_one()

        # Setup
        url_pattern = "https://test.com/{username}/post/{id}"

        # Mock _has_media_content to return True
        gallery_mixin._has_media_content = AsyncMock(return_value=True)

        # Mock _get_gallery_metadata
        gallery_mixin._get_gallery_metadata = AsyncMock(
            return_value=(
                "test_user",
                "Test Title",
                "https://test.com/test_user/post/67890",
            )
        )

        # Test when gallery found by stash_id
        gallery_mixin._get_gallery_by_stash_id = AsyncMock(return_value=mock_gallery)
        gallery_mixin._get_gallery_by_code = AsyncMock(return_value=None)
        gallery_mixin._get_gallery_by_title = AsyncMock(return_value=None)
        gallery_mixin._get_gallery_by_url = AsyncMock(return_value=None)

        gallery = await gallery_mixin._get_or_create_gallery(
            post_obj,
            account_obj,
            gallery_mock_performer,
            gallery_mock_studio,
            "post",
            url_pattern,
        )

        # Verify
        assert gallery == mock_gallery
        gallery_mixin._get_gallery_by_stash_id.assert_called_once_with(post_obj)
        gallery_mixin._get_gallery_by_code.assert_not_called()
        gallery_mixin._get_gallery_by_title.assert_not_called()
        gallery_mixin._get_gallery_by_url.assert_not_called()

        # Reset
        gallery_mixin._get_gallery_by_stash_id.reset_mock()

        # Test when gallery found by code
        gallery_mixin._get_gallery_by_stash_id = AsyncMock(return_value=None)
        gallery_mixin._get_gallery_by_code = AsyncMock(return_value=mock_gallery)

        gallery = await gallery_mixin._get_or_create_gallery(
            post_obj,
            account_obj,
            gallery_mock_performer,
            gallery_mock_studio,
            "post",
            url_pattern,
        )

        # Verify
        assert gallery == mock_gallery
        gallery_mixin._get_gallery_by_stash_id.assert_called_once_with(post_obj)
        gallery_mixin._get_gallery_by_code.assert_called_once_with(post_obj)
        gallery_mixin._get_gallery_by_title.assert_not_called()
        gallery_mixin._get_gallery_by_url.assert_not_called()

        # Reset
        gallery_mixin._get_gallery_by_stash_id.reset_mock()
        gallery_mixin._get_gallery_by_code.reset_mock()

        # Test when gallery found by title
        gallery_mixin._get_gallery_by_stash_id = AsyncMock(return_value=None)
        gallery_mixin._get_gallery_by_code = AsyncMock(return_value=None)
        gallery_mixin._get_gallery_by_title = AsyncMock(return_value=mock_gallery)

        gallery = await gallery_mixin._get_or_create_gallery(
            post_obj,
            account_obj,
            gallery_mock_performer,
            gallery_mock_studio,
            "post",
            url_pattern,
        )

        # Verify
        assert gallery == mock_gallery
        gallery_mixin._get_gallery_by_stash_id.assert_called_once_with(post_obj)
        gallery_mixin._get_gallery_by_code.assert_called_once_with(post_obj)
        gallery_mixin._get_gallery_by_title.assert_called_once_with(
            post_obj, "Test Title", gallery_mock_studio
        )
        gallery_mixin._get_gallery_by_url.assert_not_called()

        # Reset
        gallery_mixin._get_gallery_by_stash_id.reset_mock()
        gallery_mixin._get_gallery_by_code.reset_mock()
        gallery_mixin._get_gallery_by_title.reset_mock()

        # Test when gallery found by URL
        gallery_mixin._get_gallery_by_stash_id = AsyncMock(return_value=None)
        gallery_mixin._get_gallery_by_code = AsyncMock(return_value=None)
        gallery_mixin._get_gallery_by_title = AsyncMock(return_value=None)
        gallery_mixin._get_gallery_by_url = AsyncMock(return_value=mock_gallery)

        gallery = await gallery_mixin._get_or_create_gallery(
            post_obj,
            account_obj,
            gallery_mock_performer,
            gallery_mock_studio,
            "post",
            url_pattern,
        )

        # Verify
        assert gallery == mock_gallery
        gallery_mixin._get_gallery_by_stash_id.assert_called_once_with(post_obj)
        gallery_mixin._get_gallery_by_code.assert_called_once_with(post_obj)
        gallery_mixin._get_gallery_by_title.assert_called_once_with(
            post_obj, "Test Title", gallery_mock_studio
        )
        gallery_mixin._get_gallery_by_url.assert_called_once_with(
            post_obj, "https://test.com/test_user/post/67890"
        )

        # Reset
        gallery_mixin._get_gallery_by_stash_id.reset_mock()
        gallery_mixin._get_gallery_by_code.reset_mock()
        gallery_mixin._get_gallery_by_title.reset_mock()
        gallery_mixin._get_gallery_by_url.reset_mock()

        # Test when no gallery found (create new)
        gallery_mixin._get_gallery_by_stash_id = AsyncMock(return_value=None)
        gallery_mixin._get_gallery_by_code = AsyncMock(return_value=None)
        gallery_mixin._get_gallery_by_title = AsyncMock(return_value=None)
        gallery_mixin._get_gallery_by_url = AsyncMock(return_value=None)

        # Mock create and setup methods with Stash Gallery object
        new_gallery = MagicMock(spec=Gallery)
        new_gallery.id = "new"
        new_gallery.performers = []
        new_gallery.urls = []
        new_gallery.chapters = []
        new_gallery.save = AsyncMock()

        gallery_mixin._create_new_gallery = AsyncMock(return_value=new_gallery)
        gallery_mixin._setup_gallery_performers = AsyncMock()

        gallery = await gallery_mixin._get_or_create_gallery(
            post_obj,
            account_obj,
            gallery_mock_performer,
            gallery_mock_studio,
            "post",
            url_pattern,
        )

        # Verify
        assert gallery == new_gallery
        gallery_mixin._get_gallery_by_stash_id.assert_called_once_with(post_obj)
        gallery_mixin._get_gallery_by_code.assert_called_once_with(post_obj)
        gallery_mixin._get_gallery_by_title.assert_called_once_with(
            post_obj, "Test Title", gallery_mock_studio
        )
        gallery_mixin._get_gallery_by_url.assert_called_once_with(
            post_obj, "https://test.com/test_user/post/67890"
        )
        gallery_mixin._create_new_gallery.assert_called_once_with(
            post_obj, "Test Title"
        )
        gallery_mixin._setup_gallery_performers.assert_called_once_with(
            new_gallery, post_obj, gallery_mock_performer
        )
        assert new_gallery.studio == gallery_mock_studio
        assert "https://test.com/test_user/post/67890" in new_gallery.urls
        new_gallery.save.assert_called_once_with(gallery_mixin.context.client)

        # Test when item has no media content
        gallery_mixin._has_media_content = AsyncMock(return_value=False)
        gallery_mixin._get_gallery_metadata = AsyncMock()  # Reset mock

        gallery = await gallery_mixin._get_or_create_gallery(
            post_obj,
            account_obj,
            gallery_mock_performer,
            gallery_mock_studio,
            "post",
            url_pattern,
        )

        # Verify
        assert gallery is None
        gallery_mixin._get_gallery_metadata.assert_not_called()  # Should return early
