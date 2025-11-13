"""Unit tests for gallery-related methods."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from stash.types import FindGalleriesResultType
from tests.fixtures.metadata.metadata_factories import HashtagFactory
from tests.fixtures.stash.stash_type_factories import (
    GalleryFactory,
    StudioFactory,
    TagFactory,
)


class TestGalleryMethods:
    """Test gallery-related methods of StashProcessing."""

    @pytest.mark.asyncio
    async def test_get_gallery_by_stash_id_no_id(self, gallery_mixin, mock_item):
        """Test _get_gallery_by_stash_id with no stash_id."""
        # mock_item has stash_id = None by default
        assert mock_item.stash_id is None

        # Call method - should return None early without calling API
        result = await gallery_mixin._get_gallery_by_stash_id(mock_item)

        # Verify result
        assert result is None

    @pytest.mark.asyncio
    async def test_get_gallery_by_stash_id_found(self, gallery_mixin, mock_item):
        """Test _get_gallery_by_stash_id when gallery is found."""
        # Set stash_id on item
        mock_item.stash_id = 123

        # Mock external API call - find_gallery returns Gallery object
        mock_gallery = GalleryFactory(
            id="123",
            title="Test Gallery",
            code="12345",
            date="2024-04-01",
        )
        gallery_mixin.context.client.find_gallery = AsyncMock(return_value=mock_gallery)

        # Call method - real implementation runs
        result = await gallery_mixin._get_gallery_by_stash_id(mock_item)

        # Verify RESULTS
        assert result is not None
        assert result.id == "123"
        assert result.title == "Test Gallery"

    @pytest.mark.asyncio
    async def test_get_gallery_by_stash_id_not_found(self, gallery_mixin, mock_item):
        """Test _get_gallery_by_stash_id when gallery not found."""
        # Set stash_id on item
        mock_item.stash_id = 999

        # Mock external API call - gallery not found
        gallery_mixin.context.client.find_gallery = AsyncMock(return_value=None)

        # Call method
        result = await gallery_mixin._get_gallery_by_stash_id(mock_item)

        # Verify result
        assert result is None

    @pytest.mark.asyncio
    async def test_get_gallery_by_title_not_found(self, gallery_mixin, mock_item):
        """Test _get_gallery_by_title when no galleries match."""
        # Mock studio
        mock_studio = StudioFactory(id="studio_123", name="Test Studio")

        # Mock external API call - no galleries found
        empty_result = FindGalleriesResultType(count=0, galleries=[])
        gallery_mixin.context.client.find_galleries = AsyncMock(
            return_value=empty_result
        )

        # Call method
        result = await gallery_mixin._get_gallery_by_title(
            mock_item, "Test Title", mock_studio
        )

        # Verify result
        assert result is None

    @pytest.mark.asyncio
    async def test_get_gallery_by_title_found(self, gallery_mixin, mock_item):
        """Test _get_gallery_by_title when gallery matches."""
        # Mock studio
        mock_studio = StudioFactory(id="studio_123", name="Test Studio")

        # Mock external API call - gallery found (GraphQL returns dict with numeric ID)
        found_gallery_dict = {
            "id": "123",  # Numeric string that can be converted to int
            "title": "Test Title",
            "code": "12345",
            "date": "2024-04-01",
            "studio": {"id": "studio_123", "name": "Test Studio"},
        }
        galleries_result = FindGalleriesResultType(
            count=1, galleries=[found_gallery_dict]
        )
        gallery_mixin.context.client.find_galleries = AsyncMock(
            return_value=galleries_result
        )

        # Call method - real implementation runs
        result = await gallery_mixin._get_gallery_by_title(
            mock_item, "Test Title", mock_studio
        )

        # Verify RESULTS
        assert result is not None
        assert result.id == "123"
        assert result.title == "Test Title"
        # Stash ID should be updated on item
        assert mock_item.stash_id == 123

    @pytest.mark.asyncio
    async def test_get_gallery_by_code_not_found(self, gallery_mixin, mock_item):
        """Test _get_gallery_by_code when no galleries match."""
        # Mock external API call - no galleries found
        empty_result = FindGalleriesResultType(count=0, galleries=[])
        gallery_mixin.context.client.find_galleries = AsyncMock(
            return_value=empty_result
        )

        # Call method
        result = await gallery_mixin._get_gallery_by_code(mock_item)

        # Verify result
        assert result is None

    @pytest.mark.asyncio
    async def test_get_gallery_by_code_found(self, gallery_mixin, mock_item):
        """Test _get_gallery_by_code when gallery matches."""
        # Mock external API call - gallery found (GraphQL returns dict with numeric ID)
        found_gallery_dict = {
            "id": "456",  # Numeric string that can be converted to int
            "title": "Code Gallery",
            "code": "12345",  # Matches mock_item.id
            "date": "2024-04-01",
        }
        galleries_result = FindGalleriesResultType(
            count=1, galleries=[found_gallery_dict]
        )
        gallery_mixin.context.client.find_galleries = AsyncMock(
            return_value=galleries_result
        )

        # Call method
        result = await gallery_mixin._get_gallery_by_code(mock_item)

        # Verify RESULTS
        assert result is not None
        assert result.id == "456"
        assert result.code == "12345"
        # Stash ID should be updated on item
        assert mock_item.stash_id == 456

    @pytest.mark.asyncio
    async def test_get_gallery_by_url_found(self, gallery_mixin, mock_item):
        """Test _get_gallery_by_url when gallery is found with correct code."""
        # Mock external API call - gallery found with code already matching (no save needed)
        found_gallery_dict = {
            "id": "789",
            "title": "URL Gallery",
            "code": "12345",  # Already matches mock_item.id, so save() won't call execute
            "urls": ["https://example.com/gallery/123"],
        }
        galleries_result = FindGalleriesResultType(
            count=1, galleries=[found_gallery_dict]
        )
        gallery_mixin.context.client.find_galleries = AsyncMock(
            return_value=galleries_result
        )

        # No need to mock execute - save() won't call it because gallery not dirty

        # Call method - signature is (item, url)
        url = "https://example.com/gallery/123"
        result = await gallery_mixin._get_gallery_by_url(mock_item, url)

        # Verify RESULTS
        assert result is not None
        assert result.id == "789"
        assert result.code == "12345"

    @pytest.mark.asyncio
    async def test_get_gallery_by_url_with_item_update(self, gallery_mixin, mock_item):
        """Test _get_gallery_by_url updates item stash_id and gallery code."""
        # Mock external API call with numeric ID and DIFFERENT code
        found_gallery_dict = {
            "id": "999",
            "title": "URL Gallery",
            "code": "old_code",  # Different from mock_item.id, so save() will be called
            "urls": ["https://example.com/gallery/456"],
        }
        galleries_result = FindGalleriesResultType(
            count=1, galleries=[found_gallery_dict]
        )
        gallery_mixin.context.client.find_galleries = AsyncMock(
            return_value=galleries_result
        )

        # Mock execute for gallery.save() - will be called since code is dirty
        gallery_mixin.context.client.execute = AsyncMock(
            return_value={
                "galleryUpdate": {
                    "id": "999",
                    "title": "URL Gallery",
                    "code": "12345",  # Updated to mock_item.id
                    "urls": ["https://example.com/gallery/456"],
                }
            }
        )

        # Ensure item has no stash_id
        mock_item.stash_id = None

        # Call method - signature is (item, url)
        url = "https://example.com/gallery/456"
        result = await gallery_mixin._get_gallery_by_url(mock_item, url)

        # Verify RESULTS
        assert result is not None
        assert result.id == "999"
        # Item stash_id should be updated
        assert mock_item.stash_id == 999
        # Gallery code should be updated
        assert result.code == "12345"

    @pytest.mark.asyncio
    async def test_create_new_gallery(self, gallery_mixin, mock_item):
        """Test _create_new_gallery creates gallery with correct attributes."""
        title = "New Test Gallery"

        # Mock external API call for save()
        gallery_mixin.context.client.execute = AsyncMock(
            return_value={
                "galleryCreate": {
                    "id": "new_gallery_123",
                    "title": title,
                    "code": str(mock_item.id),
                    "date": mock_item.createdAt.strftime("%Y-%m-%d"),
                    "details": mock_item.content,
                    "organized": True,
                }
            }
        )

        # Call method - real implementation runs
        result = await gallery_mixin._create_new_gallery(mock_item, title)

        # Verify RESULTS
        assert result is not None
        assert result.title == title
        assert result.code == str(mock_item.id)
        assert result.date == "2024-04-01"
        assert result.details == mock_item.content
        assert result.organized is True

    @pytest.mark.asyncio
    async def test_process_hashtags_to_tags_existing_tags(self, gallery_mixin):
        """Test _process_hashtags_to_tags with existing tags."""
        # Create real hashtag objects
        hashtag1 = HashtagFactory.build(value="test1")
        hashtag2 = HashtagFactory.build(value="test2")
        hashtags = [hashtag1, hashtag2]

        # Mock external API calls - both tags exist (GraphQL returns dicts)
        from stash.types import FindTagsResultType

        tag1_result = FindTagsResultType(
            count=1, tags=[{"id": "tag_1", "name": "test1"}]
        )
        tag2_result = FindTagsResultType(
            count=1, tags=[{"id": "tag_2", "name": "test2"}]
        )

        gallery_mixin.context.client.find_tags = AsyncMock(
            side_effect=[tag1_result, tag2_result]
        )

        # Call method - real implementation runs
        result = await gallery_mixin._process_hashtags_to_tags(hashtags)

        # Verify RESULTS
        assert len(result) == 2
        assert result[0].name == "test1"
        assert result[1].name == "test2"

    @pytest.mark.asyncio
    async def test_process_hashtags_to_tags_create_new(self, gallery_mixin):
        """Test _process_hashtags_to_tags creates new tag when not found."""
        # Create real hashtag object
        hashtag = HashtagFactory.build(value="newtag")
        hashtags = [hashtag]

        # Mock external API calls - tag doesn't exist
        from stash.types import FindTagsResultType

        empty_result = FindTagsResultType(count=0, tags=[])
        gallery_mixin.context.client.find_tags = AsyncMock(return_value=empty_result)

        # Mock create_tag - returns Tag object (created from dict in production)
        new_tag = TagFactory(id="123", name="newtag")
        gallery_mixin.context.client.create_tag = AsyncMock(return_value=new_tag)

        # Call method
        result = await gallery_mixin._process_hashtags_to_tags(hashtags)

        # Verify RESULTS
        assert len(result) == 1
        assert result[0].name == "newtag"
        assert result[0].id == "123"

    @pytest.mark.asyncio
    async def test_generate_title_from_content_short(self, gallery_mixin):
        """Test _generate_title_from_content with short content."""
        content = "Short content"
        username = "test_user"
        created_at = datetime(2023, 1, 1, 12, 0, tzinfo=UTC)

        # Call method directly (not async, doesn't need external calls)
        result = gallery_mixin._generate_title_from_content(
            content, username, created_at
        )

        # Verify result uses content as title
        assert result == content

    @pytest.mark.asyncio
    async def test_generate_title_from_content_long(self, gallery_mixin):
        """Test _generate_title_from_content truncates long content."""
        content = "A" * 200  # Very long content
        username = "test_user"
        created_at = datetime(2023, 1, 1, 12, 0, tzinfo=UTC)

        # Call method
        result = gallery_mixin._generate_title_from_content(
            content, username, created_at
        )

        # Verify result is truncated
        assert len(result) <= 128
        assert result.endswith("...")

    @pytest.mark.asyncio
    async def test_generate_title_from_content_with_newlines(self, gallery_mixin):
        """Test _generate_title_from_content uses first line."""
        content = "First line\nSecond line\nThird line"
        username = "test_user"
        created_at = datetime(2023, 1, 1, 12, 0, tzinfo=UTC)

        # Call method
        result = gallery_mixin._generate_title_from_content(
            content, username, created_at
        )

        # Verify result uses first line only
        assert result == "First line"

    @pytest.mark.asyncio
    async def test_generate_title_from_content_no_content(self, gallery_mixin):
        """Test _generate_title_from_content with no content."""
        username = "test_user"
        created_at = datetime(2023, 1, 1, 12, 0, tzinfo=UTC)

        # Call method with None content
        result = gallery_mixin._generate_title_from_content(None, username, created_at)

        # Verify result uses date format
        assert result == "test_user - 2023/01/01"

    @pytest.mark.asyncio
    async def test_generate_title_from_content_with_position(self, gallery_mixin):
        """Test _generate_title_from_content with position info."""
        content = "Short content"
        username = "test_user"
        created_at = datetime(2023, 1, 1, 12, 0, tzinfo=UTC)

        # Call method with position (uses current_pos and total_media params)
        result = gallery_mixin._generate_title_from_content(
            content, username, created_at, current_pos=2, total_media=5
        )

        # Verify result includes position
        assert result == "Short content - 2/5"
