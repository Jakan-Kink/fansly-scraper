"""Unit tests for GalleryClientMixin."""

from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest

from stash import StashClient
from stash.types import (
    FindGalleriesResultType,
    Fingerprint,
    Gallery,
    GalleryChapter,
    GalleryFile,
    Image,
    Performer,
    Studio,
    Tag,
)


@pytest.fixture
def mock_gallery() -> Gallery:
    """Create a mock gallery for testing."""
    return Gallery(
        id="123",
        title="Test Gallery",
        code="TEST001",
        details="Test gallery details",
        date="2024-01-01",
        urls=["https://example.com/gallery"],
        photographer="Test Photographer",
        rating100=85,
        organized=True,
        image_count=10,
        studio=Studio(
            id="456",
            name="Test Studio",
        ),
        performers=[
            Performer(
                id="789",
                name="Test Performer",
            )
        ],
        tags=[
            Tag(
                id="012",
                name="Test Tag",
            )
        ],
        files=[
            GalleryFile(
                id="345",
                path="/path/to/gallery",
                basename="gallery",
                parent_folder_id="123",
                mod_time=datetime.now(),
                size=1024,
                fingerprints=[Fingerprint(type="md5", value="abc123")],
            )
        ],
    )


@pytest.fixture
def mock_chapter() -> GalleryChapter:
    """Create a mock gallery chapter for testing."""
    return GalleryChapter(
        id="678",
        title="Test Chapter",
        image_index=0,
        gallery=Gallery(
            id="123",
            title="Test Gallery",
        ),
    )


@pytest.mark.asyncio
async def test_find_gallery(stash_client: StashClient, mock_gallery: Gallery) -> None:
    """Test finding a gallery by ID."""
    # Test successful find
    with patch.object(
        stash_client,
        "find_gallery",
        new_callable=AsyncMock,
        return_value=mock_gallery,
    ):
        gallery = await stash_client.find_gallery("123")
        assert gallery is not None
        assert gallery.id == mock_gallery.id
        assert gallery.title == mock_gallery.title
        assert gallery.code == mock_gallery.code
        assert gallery.details == mock_gallery.details
        assert gallery.photographer == mock_gallery.photographer
        assert gallery.rating100 == mock_gallery.rating100
        assert gallery.organized == mock_gallery.organized
        assert gallery.image_count == mock_gallery.image_count
        assert gallery.studio.id == mock_gallery.studio.id
        assert len(gallery.performers) == 1
        assert len(gallery.tags) == 1
        assert len(gallery.files) == 1

    # Test not found
    with patch.object(
        stash_client,
        "find_gallery",
        new_callable=AsyncMock,
        return_value=None,
    ):
        gallery = await stash_client.find_gallery("456")
        assert gallery is None

    # Test error handling
    with patch.object(
        stash_client,
        "find_gallery",
        new_callable=AsyncMock,
        side_effect=ValueError("Test error"),
    ):
        gallery = await stash_client.find_gallery("789")
        assert gallery is None


@pytest.mark.asyncio
async def test_find_galleries(stash_client: StashClient, mock_gallery: Gallery) -> None:
    """Test finding galleries with filters."""
    mock_result = FindGalleriesResultType(
        count=1,
        galleries=[mock_gallery],
    )

    # Test with gallery filter
    with patch.object(
        stash_client,
        "find_galleries",
        new_callable=AsyncMock,
        return_value=mock_result,
    ):
        result = await stash_client.find_galleries(
            gallery_filter={
                "title": {"modifier": "EQUALS", "value": "Test Gallery"},
                "organized": True,
                "rating100": {"modifier": "GREATER_THAN", "value": 80},
            }
        )
        assert result.count == 1
        assert len(result.galleries) == 1
        assert result.galleries[0].id == mock_gallery.id

    # Test with general filter
    with patch.object(
        stash_client,
        "find_galleries",
        new_callable=AsyncMock,
        return_value=mock_result,
    ):
        result = await stash_client.find_galleries(
            filter_={
                "page": 1,
                "per_page": 10,
                "sort": "title",
                "direction": "ASC",
            }
        )
        assert result.count == 1
        assert len(result.galleries) == 1

    # Test with search query
    with patch.object(
        stash_client,
        "find_galleries",
        new_callable=AsyncMock,
        return_value=mock_result,
    ):
        result = await stash_client.find_galleries(q="test")
        assert result.count == 1
        assert len(result.galleries) == 1

    # Test error handling
    with patch.object(
        stash_client,
        "find_galleries",
        new_callable=AsyncMock,
        side_effect=ValueError("Test error"),
    ):
        result = await stash_client.find_galleries()
        assert result.count == 0
        assert len(result.galleries) == 0


@pytest.mark.asyncio
async def test_create_gallery(stash_client: StashClient, mock_gallery: Gallery) -> None:
    """Test creating a gallery."""
    # Test create with minimum fields
    with patch.object(
        stash_client,
        "create_gallery",
        new_callable=AsyncMock,
        return_value=mock_gallery,
    ):
        gallery = Gallery(
            id="new_gallery_id",
            title="New Gallery",
            urls=["https://example.com/new"],
            organized=False,
        )
        created = await stash_client.create_gallery(gallery)
        assert created.id == mock_gallery.id
        assert created.title == mock_gallery.title

    # Test create with all fields
    with patch.object(
        stash_client,
        "create_gallery",
        new_callable=AsyncMock,
        return_value=mock_gallery,
    ):
        gallery = mock_gallery
        gallery.id = "new"  # Force create
        created = await stash_client.create_gallery(gallery)
        assert created.id == mock_gallery.id
        assert created.title == mock_gallery.title
        assert created.code == mock_gallery.code
        assert created.details == mock_gallery.details
        assert created.photographer == mock_gallery.photographer
        assert created.rating100 == mock_gallery.rating100
        assert created.organized == mock_gallery.organized
        assert created.studio.id == mock_gallery.studio.id
        assert len(created.performers) == 1
        assert len(created.tags) == 1

    # Test error handling
    with patch.object(
        stash_client,
        "create_gallery",
        new_callable=AsyncMock,
        side_effect=ValueError("Test error"),
    ):
        with pytest.raises(ValueError, match="Test error"):
            await stash_client.create_gallery(gallery)


@pytest.mark.asyncio
async def test_update_gallery(stash_client: StashClient, mock_gallery: Gallery) -> None:
    """Test updating a gallery."""
    # Test update single field
    with patch.object(
        stash_client,
        "update_gallery",
        new_callable=AsyncMock,
        return_value=mock_gallery,
    ):
        gallery = mock_gallery
        gallery.title = "Updated Title"
        updated = await stash_client.update_gallery(gallery)
        assert updated.id == mock_gallery.id
        assert updated.title == mock_gallery.title

    # Test update multiple fields
    with patch.object(
        stash_client,
        "update_gallery",
        new_callable=AsyncMock,
        return_value=mock_gallery,
    ):
        gallery.details = "Updated details"
        gallery.photographer = "Updated Photographer"
        gallery.rating100 = 90
        updated = await stash_client.update_gallery(gallery)
        assert updated.id == mock_gallery.id
        assert updated.details == mock_gallery.details
        assert updated.photographer == mock_gallery.photographer
        assert updated.rating100 == mock_gallery.rating100

    # Test update relationships
    with patch.object(
        stash_client,
        "update_gallery",
        new_callable=AsyncMock,
        return_value=mock_gallery,
    ):
        new_performer = Performer(
            id="999",
            name="New Performer",
        )
        gallery.performers.append(new_performer)
        updated = await stash_client.update_gallery(gallery)
        assert updated.id == mock_gallery.id
        assert len(updated.performers) == 1

    # Test error handling
    with patch.object(
        stash_client,
        "update_gallery",
        new_callable=AsyncMock,
        side_effect=ValueError("Test error"),
    ):
        with pytest.raises(ValueError, match="Test error"):
            await stash_client.update_gallery(gallery)


@pytest.mark.asyncio
async def test_gallery_images(stash_client: StashClient, mock_gallery: Gallery) -> None:
    """Test gallery image operations."""
    mock_images = [
        Image(
            id=str(i),
            title=f"Test Image {i}",
        )
        for i in range(3)
    ]

    # Test add images
    with patch.object(
        stash_client,
        "add_gallery_images",
        new_callable=AsyncMock,
        return_value=True,
    ):
        result = await stash_client.add_gallery_images(
            mock_gallery.id,
            [img.id for img in mock_images],
        )
        assert result is True

    # Test add images error
    with patch.object(
        stash_client,
        "add_gallery_images",
        new_callable=AsyncMock,
        side_effect=ValueError("Test error"),
    ):
        with pytest.raises(ValueError, match="Test error"):
            await stash_client.add_gallery_images(
                mock_gallery.id,
                [img.id for img in mock_images],
            )

    # Test remove images
    with patch.object(
        stash_client,
        "remove_gallery_images",
        new_callable=AsyncMock,
        return_value=True,
    ):
        result = await stash_client.remove_gallery_images(
            mock_gallery.id,
            [img.id for img in mock_images[:1]],  # Remove first image
        )
        assert result is True

    # Test remove images error
    with patch.object(
        stash_client,
        "remove_gallery_images",
        new_callable=AsyncMock,
        side_effect=ValueError("Test error"),
    ):
        with pytest.raises(ValueError, match="Test error"):
            await stash_client.remove_gallery_images(
                mock_gallery.id,
                [img.id for img in mock_images[:1]],
            )


@pytest.mark.asyncio
async def test_gallery_cover(stash_client: StashClient, mock_gallery: Gallery) -> None:
    """Test gallery cover operations."""
    mock_image = Image(
        id="001",
        title="Cover Image",
    )

    # Test set cover
    with patch.object(
        stash_client,
        "set_gallery_cover",
        new_callable=AsyncMock,
        return_value=True,
    ):
        result = await stash_client.set_gallery_cover(
            mock_gallery.id,
            mock_image.id,
        )
        assert result is True

    # Test set cover error
    with patch.object(
        stash_client,
        "set_gallery_cover",
        new_callable=AsyncMock,
        side_effect=ValueError("Test error"),
    ):
        with pytest.raises(ValueError, match="Test error"):
            await stash_client.set_gallery_cover(
                mock_gallery.id,
                mock_image.id,
            )

    # Test reset cover
    with patch.object(
        stash_client,
        "reset_gallery_cover",
        new_callable=AsyncMock,
        return_value=True,
    ):
        result = await stash_client.reset_gallery_cover(mock_gallery.id)
        assert result is True

    # Test reset cover error
    with patch.object(
        stash_client,
        "reset_gallery_cover",
        new_callable=AsyncMock,
        side_effect=ValueError("Test error"),
    ):
        with pytest.raises(ValueError, match="Test error"):
            await stash_client.reset_gallery_cover(mock_gallery.id)


@pytest.mark.asyncio
async def test_gallery_chapters(
    stash_client: StashClient,
    mock_gallery: Gallery,
    mock_chapter: GalleryChapter,
) -> None:
    """Test gallery chapter operations."""
    # Test create chapter
    with patch.object(
        stash_client,
        "gallery_chapter_create",
        new_callable=AsyncMock,
        return_value=mock_chapter,
    ):
        chapter = await stash_client.gallery_chapter_create(
            gallery_id=mock_gallery.id,
            title="New Chapter",
            image_index=0,
        )
        assert chapter.id == mock_chapter.id
        assert chapter.title == mock_chapter.title
        assert chapter.image_index == mock_chapter.image_index
        assert chapter.gallery.id == mock_gallery.id

    # Test create chapter error
    with patch.object(
        stash_client,
        "gallery_chapter_create",
        new_callable=AsyncMock,
        side_effect=ValueError("Test error"),
    ):
        with pytest.raises(ValueError, match="Test error"):
            await stash_client.gallery_chapter_create(
                gallery_id=mock_gallery.id,
                title="Error Chapter",
                image_index=0,
            )

    # Test update chapter
    with patch.object(
        stash_client,
        "gallery_chapter_update",
        new_callable=AsyncMock,
        return_value=mock_chapter,
    ):
        chapter.title = "Updated Chapter"
        updated = await stash_client.gallery_chapter_update(
            id=chapter.id, title=chapter.title, image_index=chapter.image_index
        )
        assert updated.id == mock_chapter.id
        assert updated.title == mock_chapter.title

    # Test update chapter error
    with patch.object(
        stash_client,
        "gallery_chapter_update",
        new_callable=AsyncMock,
        side_effect=ValueError("Test error"),
    ):
        with pytest.raises(ValueError, match="Test error"):
            await stash_client.gallery_chapter_update(
                id=chapter.id, title=chapter.title, image_index=chapter.image_index
            )

    # Test delete chapter
    with patch.object(
        stash_client,
        "gallery_chapter_destroy",
        new_callable=AsyncMock,
        return_value=True,
    ):
        result = await stash_client.gallery_chapter_destroy(chapter.id)
        assert result is True

    # Test delete chapter error
    with patch.object(
        stash_client,
        "gallery_chapter_destroy",
        new_callable=AsyncMock,
        side_effect=ValueError("Test error"),
    ):
        with pytest.raises(ValueError, match="Test error"):
            await stash_client.gallery_chapter_destroy(chapter.id)


@pytest.mark.asyncio
async def test_gallery_from_content(
    stash_client: StashClient, mock_gallery: Gallery
) -> None:
    """Test creating a gallery from content."""
    from metadata import Post

    # Create mock post
    post = Post(
        id=123,
        accountId=456,
        content="Test post content",
        createdAt=datetime.now(),
    )

    # Test successful conversion and creation
    # Convert post to gallery
    gallery = await Gallery.from_content(
        content=post,
        performer=mock_gallery.performers[0],
        studio=mock_gallery.studio,
    )
    assert gallery.title == f"{mock_gallery.studio.name} - {post.id}"
    assert gallery.details == post.content
    assert gallery.date == post.createdAt.strftime("%Y-%m-%d")

    # Mock gallery creation
    with patch.object(
        stash_client,
        "create_gallery",
        new_callable=AsyncMock,
        return_value=mock_gallery,
    ):
        # Create in Stash
        created = await gallery.save(stash_client)
        assert created.id == mock_gallery.id
        assert created.title == mock_gallery.title

    # Test creation error
    with patch.object(
        stash_client,
        "create_gallery",
        new_callable=AsyncMock,
        side_effect=ValueError("Test error"),
    ):
        gallery = await Gallery.from_content(
            content=post,
            performer=mock_gallery.performers[0],
            studio=mock_gallery.studio,
        )
        with pytest.raises(ValueError, match="Test error"):
            await gallery.save(stash_client)
