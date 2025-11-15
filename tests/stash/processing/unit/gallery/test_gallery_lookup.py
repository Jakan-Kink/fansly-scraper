"""Tests for gallery lookup functionality."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import select

from metadata import Post
from tests.fixtures.metadata.metadata_factories import AccountFactory, PostFactory
from tests.fixtures.stash.stash_type_factories import GalleryFactory


@pytest.fixture
def test_studio():
    """Fixture for test studio instance."""
    studio = MagicMock()
    studio.id = "test_studio_123"
    return studio


class TestGalleryLookup:
    """Test gallery lookup methods in GalleryProcessingMixin."""

    @pytest.mark.asyncio
    async def test_get_gallery_by_stash_id(
        self, factory_async_session, session, gallery_mixin
    ):
        """Test _get_gallery_by_stash_id method."""
        # Create real Post object
        account = AccountFactory(id=12345, username="test_user")
        post = PostFactory(id=67890, accountId=12345)
        factory_async_session.commit()

        # Query fresh from async session
        result = await session.execute(select(Post).where(Post.id == 67890))
        post_obj = result.unique().scalar_one()

        # Test with valid stash_id (numeric)
        post_obj.stash_id = 123

        mock_gallery = GalleryFactory(id="123", title="Test Gallery")
        await gallery_mixin.context.get_client()
        gallery_mixin.context.client.find_gallery = AsyncMock(return_value=mock_gallery)

        gallery = await gallery_mixin._get_gallery_by_stash_id(post_obj)

        # Verify
        assert gallery == mock_gallery
        gallery_mixin.context.client.find_gallery.assert_called_once_with("123")

        # Test with no stash_id
        gallery_mixin.context.client.find_gallery.reset_mock()
        post_obj.stash_id = None

        gallery = await gallery_mixin._get_gallery_by_stash_id(post_obj)

        # Verify
        assert gallery is None
        gallery_mixin.context.client.find_gallery.assert_not_called()

        # Test with gallery not found
        gallery_mixin.context.client.find_gallery.reset_mock()
        post_obj.stash_id = 123
        gallery_mixin.context.client.find_gallery.return_value = None

        gallery = await gallery_mixin._get_gallery_by_stash_id(post_obj)

        # Verify
        assert gallery is None
        gallery_mixin.context.client.find_gallery.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_gallery_by_title(
        self, factory_async_session, session, gallery_mixin, gallery_mock_studio
    ):
        """Test _get_gallery_by_title method."""
        # Create real Post object with specific date
        account = AccountFactory(id=12345, username="test_user")
        post = PostFactory(
            id=67890,
            accountId=12345,
            createdAt=datetime(2024, 4, 1, 12, 0, 0, tzinfo=UTC),
        )
        factory_async_session.commit()

        # Query fresh from async session
        result = await session.execute(select(Post).where(Post.id == 67890))
        post_obj = result.unique().scalar_one()

        # Setup for matching gallery (numeric ID)
        mock_results = MagicMock()
        mock_results.count = 1
        mock_results.galleries = [
            {
                "id": "123",
                "title": "Test Title",
                "date": "2024-04-01",
                "studio": {"id": "123"},  # Must match gallery_mock_studio.id
            }
        ]
        await gallery_mixin.context.get_client()
        gallery_mixin.context.client.find_galleries = AsyncMock(
            return_value=mock_results
        )

        # Test with matching gallery (with studio)
        gallery = await gallery_mixin._get_gallery_by_title(
            post_obj, "Test Title", gallery_mock_studio
        )

        # Verify
        assert gallery is not None
        assert gallery.id == "123"
        assert gallery.title == "Test Title"
        assert post_obj.stash_id == 123  # Should update item's stash_id as int
        gallery_mixin.context.client.find_galleries.assert_called_once()

        # Reset
        gallery_mixin.context.client.find_galleries.reset_mock()

        # Test with no matching galleries
        mock_results.count = 0
        mock_results.galleries = []

        gallery = await gallery_mixin._get_gallery_by_title(
            post_obj, "Test Title", gallery_mock_studio
        )

        # Verify
        assert gallery is None
        gallery_mixin.context.client.find_galleries.assert_called_once()

        # Reset
        gallery_mixin.context.client.find_galleries.reset_mock()

        # Test with galleries but no match (different title)
        mock_results.count = 1
        mock_results.galleries = [
            {
                "id": "124",
                "title": "Different Title",
                "date": "2024-04-01",
                "studio": {"id": "studio_123"},
            }
        ]

        gallery = await gallery_mixin._get_gallery_by_title(
            post_obj, "Test Title", gallery_mock_studio
        )

        # Verify
        assert gallery is None
        gallery_mixin.context.client.find_galleries.assert_called_once()

        # Reset
        gallery_mixin.context.client.find_galleries.reset_mock()

        # Test with galleries but no match (different date)
        mock_results.count = 1
        mock_results.galleries = [
            {
                "id": "125",
                "title": "Test Title",
                "date": "2024-04-02",
                "studio": {"id": "studio_123"},
            }
        ]

        gallery = await gallery_mixin._get_gallery_by_title(
            post_obj, "Test Title", gallery_mock_studio
        )

        # Verify
        assert gallery is None
        gallery_mixin.context.client.find_galleries.assert_called_once()

        # Reset
        gallery_mixin.context.client.find_galleries.reset_mock()

        # Test with galleries but no match (different studio)
        mock_results.count = 1
        mock_results.galleries = [
            {
                "id": "126",
                "title": "Test Title",
                "date": "2024-04-01",
                "studio": {"id": "different_studio"},
            }
        ]

        gallery = await gallery_mixin._get_gallery_by_title(
            post_obj, "Test Title", gallery_mock_studio
        )

        # Verify
        assert gallery is None
        gallery_mixin.context.client.find_galleries.assert_called_once()

        # Reset
        gallery_mixin.context.client.find_galleries.reset_mock()

        # Test with no studio parameter
        mock_results.count = 1
        mock_results.galleries = [
            {
                "id": "127",
                "title": "Test Title",
                "date": "2024-04-01",
                "studio": {"id": "studio_123"},
            }
        ]

        gallery = await gallery_mixin._get_gallery_by_title(
            post_obj, "Test Title", None
        )

        # Verify
        assert gallery is not None
        assert gallery.id == "127"
        gallery_mixin.context.client.find_galleries.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_gallery_by_code(
        self, factory_async_session, session, gallery_mixin
    ):
        """Test _get_gallery_by_code method."""
        # Create real Post object
        account = AccountFactory(id=12345, username="test_user")
        post = PostFactory(id=67890, accountId=12345)
        factory_async_session.commit()

        # Query fresh from async session
        result = await session.execute(select(Post).where(Post.id == 67890))
        post_obj = result.unique().scalar_one()

        # Setup for matching gallery (numeric ID)
        mock_results = MagicMock()
        mock_results.count = 1
        mock_results.galleries = [{"id": "200", "code": "67890"}]
        await gallery_mixin.context.get_client()
        gallery_mixin.context.client.find_galleries = AsyncMock(
            return_value=mock_results
        )

        # Test with matching gallery
        gallery = await gallery_mixin._get_gallery_by_code(post_obj)

        # Verify
        assert gallery is not None
        assert gallery.id == "200"
        assert gallery.code == "67890"
        assert post_obj.stash_id == 200  # Should update item's stash_id as int
        gallery_mixin.context.client.find_galleries.assert_called_once()

        # Reset
        gallery_mixin.context.client.find_galleries.reset_mock()

        # Test with no matching galleries
        mock_results.count = 0
        mock_results.galleries = []

        gallery = await gallery_mixin._get_gallery_by_code(post_obj)

        # Verify
        assert gallery is None
        gallery_mixin.context.client.find_galleries.assert_called_once()

        # Reset
        gallery_mixin.context.client.find_galleries.reset_mock()

        # Test with galleries but no match (different code)
        mock_results.count = 1
        mock_results.galleries = [{"id": "201", "code": "54321"}]

        gallery = await gallery_mixin._get_gallery_by_code(post_obj)

        # Verify
        assert gallery is None
        gallery_mixin.context.client.find_galleries.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_gallery_by_url(
        self, factory_async_session, session, gallery_mixin
    ):
        """Test _get_gallery_by_url method."""
        # Create real Post object
        account = AccountFactory(id=12345, username="test_user")
        post = PostFactory(id=67890, accountId=12345)
        factory_async_session.commit()

        # Query fresh from async session
        result = await session.execute(select(Post).where(Post.id == 67890))
        post_obj = result.unique().scalar_one()

        # Setup for matching gallery (numeric ID, needs execute mock for save)
        mock_results = MagicMock()
        mock_results.count = 1
        mock_results.galleries = [
            {"id": "300", "urls": ["https://test.com/post/67890"]}
        ]
        await gallery_mixin.context.get_client()
        gallery_mixin.context.client.find_galleries = AsyncMock(
            return_value=mock_results
        )

        # Mock execute for gallery.save (which updates code)
        gallery_mixin.context.client.execute = AsyncMock(
            return_value={
                "galleryUpdate": {
                    "id": "300",
                    "code": "67890",
                    "urls": ["https://test.com/post/67890"],
                }
            }
        )

        # Test URL
        test_url = "https://test.com/post/67890"

        # Test with matching gallery
        gallery = await gallery_mixin._get_gallery_by_url(post_obj, test_url)

        # Verify
        assert gallery is not None
        assert gallery.id == "300"
        assert test_url in gallery.urls
        assert post_obj.stash_id == 300  # Should update item's stash_id as int
        gallery_mixin.context.client.find_galleries.assert_called_once()
        gallery_mixin.context.client.execute.assert_called_once()  # gallery.save called

        # Reset
        gallery_mixin.context.client.find_galleries.reset_mock()
        gallery_mixin.context.client.execute.reset_mock()

        # Test with no matching galleries
        mock_results.count = 0
        mock_results.galleries = []

        gallery = await gallery_mixin._get_gallery_by_url(post_obj, test_url)

        # Verify
        assert gallery is None
        gallery_mixin.context.client.find_galleries.assert_called_once()

        # Reset
        gallery_mixin.context.client.find_galleries.reset_mock()

        # Test with galleries but no match (different URL)
        mock_results.count = 1
        mock_results.galleries = [
            {"id": "301", "urls": ["https://test.com/post/54321"]}
        ]

        gallery = await gallery_mixin._get_gallery_by_url(post_obj, test_url)

        # Verify
        assert gallery is None
        gallery_mixin.context.client.find_galleries.assert_called_once()


@pytest.mark.asyncio
async def test_get_gallery_by_title_matching_studio(
    factory_async_session, session, gallery_mixin, gallery_mock_studio
):
    """Test _get_gallery_by_title with matching studio."""
    # Create real Post object with specific date
    account = AccountFactory(id=12345, username="test_user")
    post = PostFactory(
        id=67890,
        accountId=12345,
        createdAt=datetime(2024, 4, 1, 12, 0, 0, tzinfo=UTC),
    )
    factory_async_session.commit()

    # Query fresh from async session
    result = await session.execute(select(Post).where(Post.id == 67890))
    post_obj = result.unique().scalar_one()

    title = "Test Gallery"

    # Mock client find_galleries response (must be dict, not MagicMock)
    mock_galleries_result = MagicMock()
    mock_galleries_result.count = 1
    mock_galleries_result.galleries = [
        {
            "id": "400",
            "title": title,
            "date": "2024-04-01",
            "studio": {"id": "123"},  # Must match gallery_mock_studio.id
        }
    ]
    await gallery_mixin.context.get_client()

    gallery_mixin.context.client.find_galleries = AsyncMock(
        return_value=mock_galleries_result
    )

    result = await gallery_mixin._get_gallery_by_title(
        post_obj, title, gallery_mock_studio
    )
    assert result is not None
    assert result.id == "400"
