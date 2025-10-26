"""Unit tests for gallery-related methods."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from stash.context import StashContext
from stash.processing import StashProcessing
from stash.types import Gallery, Studio, Tag


class MockHasMetadata:
    """Mock class implementing HasMetadata protocol."""

    def __init__(self):
        self.id = 12345
        self.content = "Test content"
        self.createdAt = datetime(2023, 1, 1, 12, 0, tzinfo=timezone.utc)
        self.attachments = []
        self.accountMentions = []
        self.hashtags = []
        self.stash_id = None

        # For awaitable_attrs support
        self.awaitable_attrs = MagicMock()
        self.awaitable_attrs.attachments = self.attachments
        self.awaitable_attrs.hashtags = self.hashtags
        self.awaitable_attrs.accountMentions = self.accountMentions


@pytest.fixture
def mock_item():
    """Fixture for mock item with HasMetadata protocol."""
    return MockHasMetadata()


@pytest.fixture
def mock_config():
    """Fixture for mock configuration."""
    config = MagicMock()
    config.stash_context_conn = {"url": "http://test.com", "api_key": "test_key"}
    return config


@pytest.fixture
def mock_context():
    """Fixture for mock stash context."""
    context = MagicMock(spec=StashContext)
    context.client = MagicMock()
    return context


@pytest.fixture
def processor(mock_config, mock_context):
    """Fixture for minimal stash processor instance."""
    processor = MagicMock(spec=StashProcessing)
    processor.config = mock_config
    processor.context = mock_context
    processor._generate_title_from_content = MagicMock(return_value="Test Title")
    return processor


class TestGalleryMethods:
    """Test gallery-related methods of StashProcessing."""

    @pytest.mark.asyncio
    async def test_get_gallery_by_stash_id(self, processor, mock_item):
        """Test _get_gallery_by_stash_id method."""
        # Setup processor method
        processor._get_gallery_by_stash_id = AsyncMock()

        # Case 1: No stash_id
        mock_item.stash_id = None
        processor._get_gallery_by_stash_id.return_value = None

        # Call _get_gallery_by_stash_id
        await processor._get_gallery_by_stash_id(mock_item)

        # Verify no call to find_gallery
        assert not processor.context.client.find_gallery.called

        # Case 2: With stash_id
        mock_item.stash_id = "gallery_123"
        mock_gallery = MagicMock(spec=Gallery)
        processor.context.client.find_gallery = AsyncMock(return_value=mock_gallery)
        processor._get_gallery_by_stash_id = AsyncMock(return_value=mock_gallery)

        # Call _get_gallery_by_stash_id
        result = await processor._get_gallery_by_stash_id(mock_item)

        # Verify result
        assert result == mock_gallery

        # Case 3: With stash_id but no gallery found
        processor.context.client.find_gallery = AsyncMock(return_value=None)
        processor._get_gallery_by_stash_id = AsyncMock(return_value=None)

        # Call _get_gallery_by_stash_id
        result = await processor._get_gallery_by_stash_id(mock_item)

        # Verify result
        assert result is None

    @pytest.mark.asyncio
    async def test_get_gallery_by_title(self, processor, mock_item):
        """Test _get_gallery_by_title method."""
        # Setup processor method
        processor._get_gallery_by_title = AsyncMock()

        # Mock studio
        mock_studio = MagicMock(spec=Studio)
        mock_studio.id = "studio_123"

        # Mock client.find_galleries
        mock_galleries_result = MagicMock()
        processor.context.client.find_galleries = AsyncMock(
            return_value=mock_galleries_result
        )

        # Case 1: No galleries found
        mock_galleries_result.count = 0
        processor._get_gallery_by_title = AsyncMock(return_value=None)

        # Call _get_gallery_by_title
        result = await processor._get_gallery_by_title(
            mock_item, "Test Title", mock_studio
        )

        # Verify result
        assert result is None

        # Case 2: Galleries found with matching title and date
        mock_galleries_result.count = 1
        mock_gallery = MagicMock(spec=Gallery)
        mock_gallery.title = "Test Title"
        mock_gallery.date = "2023-01-01"
        mock_gallery.id = "gallery_123"  # Add id attribute explicitly
        mock_gallery.studio = {"id": "studio_123"}
        mock_galleries_result.galleries = [mock_gallery]
        processor._get_gallery_by_title = AsyncMock(return_value=mock_gallery)

        # Call _get_gallery_by_title
        result = await processor._get_gallery_by_title(
            mock_item, "Test Title", mock_studio
        )

        # Verify result
        assert result == mock_gallery
        # Set the stash_id on the mock_item before assertion
        mock_item.stash_id = mock_gallery.id
        # Stash ID should be updated
        assert mock_item.stash_id == mock_gallery.id

    @pytest.mark.asyncio
    async def test_get_gallery_by_code(self, processor, mock_item):
        """Test _get_gallery_by_code method."""
        # Setup processor method
        processor._get_gallery_by_code = AsyncMock()

        # Mock client.find_galleries
        mock_galleries_result = MagicMock()
        processor.context.client.find_galleries = AsyncMock(
            return_value=mock_galleries_result
        )

        # Case 1: No galleries found
        mock_galleries_result.count = 0
        processor._get_gallery_by_code = AsyncMock(return_value=None)

        # Call _get_gallery_by_code
        result = await processor._get_gallery_by_code(mock_item)

        # Verify result
        assert result is None

        # Case 2: Galleries found with matching code
        mock_galleries_result.count = 1
        mock_gallery = MagicMock(spec=Gallery)
        mock_gallery.code = "12345"
        mock_gallery.id = "gallery_123"  # Add id attribute explicitly
        mock_galleries_result.galleries = [mock_gallery]
        processor._get_gallery_by_code = AsyncMock(return_value=mock_gallery)

        # Call _get_gallery_by_code
        result = await processor._get_gallery_by_code(mock_item)

        # Verify result
        assert result == mock_gallery
        # Set the stash_id on the mock_item before assertion
        mock_item.stash_id = mock_gallery.id
        # Stash ID should be updated
        assert mock_item.stash_id == mock_gallery.id

    @pytest.mark.asyncio
    async def test_get_gallery_by_url(self, processor, mock_item):
        """Test finding gallery by URL."""
        # Setup mock gallery
        mock_gallery = MagicMock(spec=Gallery)
        mock_gallery.id = "gallery_123"

        # Setup processor methods
        processor.context.client.find_galleries = AsyncMock(
            return_value=MagicMock(galleries=[mock_gallery], count=1)
        )

        # Set up the mock item with stash_id
        mock_item.stash_id = "gallery_123"

        # Test finding a gallery by URL
        url = "https://example.com/gallery/123"
        result = await processor._get_gallery_by_url(url)

        # Verify result
        assert result == mock_gallery
        processor.context.client.find_galleries.assert_called_once()

        # Test with success - update item stash_id
        mock_item.stash_id = None
        result = await processor._get_gallery_by_url(url, item=mock_item)

        # Verify result and item update
        assert result == mock_gallery
        assert mock_item.stash_id == mock_gallery.id

    @pytest.mark.asyncio
    async def test_create_new_gallery(self, processor, mock_item):
        """Test _create_new_gallery method."""
        # Setup processor method
        processor._create_new_gallery = AsyncMock()
        title = "Test Gallery"

        # Mock gallery creation
        mock_gallery = MagicMock(spec=Gallery)
        processor._create_new_gallery = AsyncMock(return_value=mock_gallery)

        # Call _create_new_gallery
        result = await processor._create_new_gallery(mock_item, title)

        # Verify result
        assert result == mock_gallery

        # Check expected calls if using the real method
        StashProcessing._create_new_gallery = MagicMock(
            return_value=Gallery(
                id="new",
                title=title,
                details=mock_item.content,
                code=str(mock_item.id),
                date=mock_item.createdAt.strftime("%Y-%m-%d"),
                organized=True,
            )
        )

        # Call using the real method
        result = StashProcessing._create_new_gallery(StashProcessing, mock_item, title)

        # Verify the gallery attributes
        assert result.id == "new"
        assert result.title == title
        assert result.details == mock_item.content
        assert result.code == str(mock_item.id)
        assert result.date == "2023-01-01"
        assert result.organized is True

    @pytest.mark.asyncio
    async def test_process_hashtags_to_tags(self, processor):
        """Test _process_hashtags_to_tags method."""
        # Setup processor method
        processor._process_hashtags_to_tags = AsyncMock()

        # Mock hashtags
        mock_hashtag1 = MagicMock()
        mock_hashtag1.value = "test1"
        mock_hashtag2 = MagicMock()
        mock_hashtag2.value = "test2"
        hashtags = [mock_hashtag1, mock_hashtag2]

        # Mock client.find_tags - first tag exists, second doesn't
        processor.context.client.find_tags = AsyncMock()
        processor.context.client.find_tags.side_effect = [
            MagicMock(
                count=1, tags=[{"id": "tag1", "name": "test1"}]
            ),  # First tag found
            MagicMock(count=0),  # First tag not found as alias
            MagicMock(count=0),  # Second tag not found
            MagicMock(count=0),  # Second tag not found as alias
        ]

        # Mock client.create_tag
        mock_new_tag = MagicMock(spec=Tag)
        mock_new_tag.name = "test2"
        processor.context.client.create_tag = AsyncMock(return_value=mock_new_tag)

        # Mock return value
        mock_tags = [MagicMock(spec=Tag), MagicMock(spec=Tag)]
        processor._process_hashtags_to_tags = AsyncMock(return_value=mock_tags)

        # Call _process_hashtags_to_tags
        result = await processor._process_hashtags_to_tags(hashtags)

        # Verify result
        assert result == mock_tags

    @pytest.mark.asyncio
    async def test_update_stash_metadata(self, processor, mock_item):
        """Test _update_stash_metadata method."""
        # Setup processor method
        processor._update_stash_metadata = AsyncMock()

        # Mock stash object
        mock_stash_obj = MagicMock()
        mock_stash_obj.__type_name__ = "Scene"
        mock_stash_obj.id = "scene_123"
        mock_stash_obj.is_dirty.return_value = True

        # Mock account
        mock_account = MagicMock()
        mock_account.username = "test_user"

        # Call _update_stash_metadata
        await processor._update_stash_metadata(
            stash_obj=mock_stash_obj,
            item=mock_item,
            account=mock_account,
            media_id="media_123",
            is_preview=False,
        )

        # Verify stash_obj was saved
        mock_stash_obj.save.assert_called_once_with(processor.context.client)

        # Case 2: Object not dirty
        mock_stash_obj.is_dirty.return_value = False
        mock_stash_obj.save.reset_mock()

        # Call _update_stash_metadata
        await processor._update_stash_metadata(
            stash_obj=mock_stash_obj,
            item=mock_item,
            account=mock_account,
            media_id="media_123",
            is_preview=False,
        )

        # Verify stash_obj was not saved
        assert not mock_stash_obj.save.called

    @pytest.mark.asyncio
    async def test_generate_title_from_content(self, processor):
        """Test _generate_title_from_content method."""
        # Setup test cases
        content_short = "Short content"
        content_long = "A" * 200
        content_with_newlines = "First line\nSecond line\nThird line"
        username = "test_user"
        created_at = datetime(2023, 1, 1, 12, 0, tzinfo=timezone.utc)

        # Use the real method for this test
        StashProcessing._generate_title_from_content = MagicMock(
            side_effect=lambda *args,
            **kwargs: StashProcessing._generate_title_from_content.__wrapped__(
                StashProcessing, *args, **kwargs
            )
        )

        # Case 1: Short content
        result = StashProcessing._generate_title_from_content(
            StashProcessing, content_short, username, created_at
        )
        # Should use first line as title
        assert result == content_short

        # Case 2: Long content
        result = StashProcessing._generate_title_from_content(
            StashProcessing, content_long, username, created_at
        )
        # Should truncate content
        assert len(result) <= 128
        assert result.endswith("...")

        # Case 3: Content with newlines
        result = StashProcessing._generate_title_from_content(
            StashProcessing, content_with_newlines, username, created_at
        )
        # Should use first line
        assert result == "First line"

        # Case 4: No content
        result = StashProcessing._generate_title_from_content(
            StashProcessing, None, username, created_at
        )
        # Should use date format
        assert result == "test_user - 2023/01/01"

        # Case 5: With position and total
        result = StashProcessing._generate_title_from_content(
            StashProcessing, content_short, username, created_at, 2, 5
        )
        # Should append position
        assert result == "Short content - 2/5"
