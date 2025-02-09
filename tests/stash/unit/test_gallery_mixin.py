"""Unit tests for GalleryClientMixin."""

from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest

from stash import StashClient
from stash.types import (
    FindGalleriesResultType,
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
        created_at=datetime.now(),
        updated_at=datetime.now(),
        studio=Studio(
            id="456",
            name="Test Studio",
            created_at=datetime.now(),
            updated_at=datetime.now(),
        ),
        performers=[
            Performer(
                id="789",
                name="Test Performer",
                created_at=datetime.now(),
                updated_at=datetime.now(),
            )
        ],
        tags=[
            Tag(
                id="012",
                name="Test Tag",
                created_at=datetime.now(),
                updated_at=datetime.now(),
            )
        ],
        files=[
            GalleryFile(
                id="345",
                path="/path/to/gallery",
                basename="gallery",
                mod_time=datetime.now(),
                size=1024,
                created_at=datetime.now(),
                updated_at=datetime.now(),
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
            created_at=datetime.now(),
            updated_at=datetime.now(),
        ),
    )


@pytest.mark.asyncio
async def test_find_gallery(stash_client: StashClient, mock_gallery: Gallery) -> None:
    """Test finding a gallery by ID."""
    with patch.object(
        stash_client,
        "execute",
        new_callable=AsyncMock,
        return_value={"findGallery": mock_gallery.__dict__},
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


@pytest.mark.asyncio
async def test_find_galleries(stash_client: StashClient, mock_gallery: Gallery) -> None:
    """Test finding galleries with filters."""
    mock_result = FindGalleriesResultType(
        count=1,
        galleries=[mock_gallery],
    )

    with patch.object(
        stash_client,
        "execute",
        new_callable=AsyncMock,
        return_value={"findGalleries": mock_result.__dict__},
    ):
        # Test with gallery filter
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


@pytest.mark.asyncio
async def test_create_gallery(stash_client: StashClient, mock_gallery: Gallery) -> None:
    """Test creating a gallery."""
    with patch.object(
        stash_client,
        "execute",
        new_callable=AsyncMock,
        return_value={"galleryCreate": mock_gallery.__dict__},
    ):
        # Create with minimum fields
        gallery = Gallery(
            title="New Gallery",
            urls=["https://example.com/new"],
            organized=False,
            created_at=datetime.now(),
            updated_at=datetime.now(),
        )
        created = await stash_client.create_gallery(gallery)
        assert created.id == mock_gallery.id
        assert created.title == mock_gallery.title

        # Create with all fields
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


@pytest.mark.asyncio
async def test_update_gallery(stash_client: StashClient, mock_gallery: Gallery) -> None:
    """Test updating a gallery."""
    with patch.object(
        stash_client,
        "execute",
        new_callable=AsyncMock,
        return_value={"galleryUpdate": mock_gallery.__dict__},
    ):
        # Update single field
        gallery = mock_gallery
        gallery.title = "Updated Title"
        updated = await stash_client.update_gallery(gallery)
        assert updated.id == mock_gallery.id
        assert updated.title == mock_gallery.title

        # Update multiple fields
        gallery.details = "Updated details"
        gallery.photographer = "Updated Photographer"
        gallery.rating100 = 90
        updated = await stash_client.update_gallery(gallery)
        assert updated.id == mock_gallery.id
        assert updated.details == mock_gallery.details
        assert updated.photographer == mock_gallery.photographer
        assert updated.rating100 == mock_gallery.rating100

        # Update relationships
        new_performer = Performer(
            id="999",
            name="New Performer",
            created_at=datetime.now(),
            updated_at=datetime.now(),
        )
        gallery.performers.append(new_performer)
        updated = await stash_client.update_gallery(gallery)
        assert updated.id == mock_gallery.id
        assert len(updated.performers) == 2


@pytest.mark.asyncio
async def test_gallery_images(stash_client: StashClient, mock_gallery: Gallery) -> None:
    """Test gallery image operations."""
    mock_images = [
        Image(
            id=str(i),
            title=f"Test Image {i}",
            created_at=datetime.now(),
            updated_at=datetime.now(),
        )
        for i in range(3)
    ]

    with patch.object(
        stash_client,
        "execute",
        new_callable=AsyncMock,
        return_value={"addGalleryImages": True},
    ):
        # Add images
        result = await stash_client.add_gallery_images(
            mock_gallery.id,
            [img.id for img in mock_images],
        )
        assert result is True

    with patch.object(
        stash_client,
        "execute",
        new_callable=AsyncMock,
        return_value={"removeGalleryImages": True},
    ):
        # Remove images
        result = await stash_client.remove_gallery_images(
            mock_gallery.id,
            [img.id for img in mock_images[:1]],  # Remove first image
        )
        assert result is True


@pytest.mark.asyncio
async def test_gallery_cover(stash_client: StashClient, mock_gallery: Gallery) -> None:
    """Test gallery cover operations."""
    mock_image = Image(
        id="001",
        title="Cover Image",
        created_at=datetime.now(),
        updated_at=datetime.now(),
    )

    with patch.object(
        stash_client,
        "execute",
        new_callable=AsyncMock,
        return_value={"setGalleryCover": True},
    ):
        # Set cover
        result = await stash_client.set_gallery_cover(
            mock_gallery.id,
            mock_image.id,
        )
        assert result is True

    with patch.object(
        stash_client,
        "execute",
        new_callable=AsyncMock,
        return_value={"resetGalleryCover": True},
    ):
        # Reset cover
        result = await stash_client.reset_gallery_cover(mock_gallery.id)
        assert result is True


@pytest.mark.asyncio
async def test_gallery_chapters(
    stash_client: StashClient,
    mock_gallery: Gallery,
    mock_chapter: GalleryChapter,
) -> None:
    """Test gallery chapter operations."""
    with patch.object(
        stash_client,
        "execute",
        new_callable=AsyncMock,
        return_value={"galleryChapterCreate": mock_chapter.__dict__},
    ):
        # Create chapter
        chapter = await stash_client.create_gallery_chapter(
            gallery_id=mock_gallery.id,
            title="New Chapter",
            image_index=0,
        )
        assert chapter.id == mock_chapter.id
        assert chapter.title == mock_chapter.title
        assert chapter.image_index == mock_chapter.image_index
        assert chapter.gallery.id == mock_gallery.id

    with patch.object(
        stash_client,
        "execute",
        new_callable=AsyncMock,
        return_value={"galleryChapterUpdate": mock_chapter.__dict__},
    ):
        # Update chapter
        chapter.title = "Updated Chapter"
        updated = await stash_client.update_gallery_chapter(chapter)
        assert updated.id == mock_chapter.id
        assert updated.title == mock_chapter.title

    with patch.object(
        stash_client,
        "execute",
        new_callable=AsyncMock,
        return_value={"galleryChapterDestroy": True},
    ):
        # Delete chapter
        result = await stash_client.gallery_chapter_destroy(chapter.id)
        assert result is True


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

    with patch.object(
        stash_client,
        "execute",
        new_callable=AsyncMock,
        return_value={"galleryCreate": mock_gallery.__dict__},
    ):
        # Convert post to gallery
        gallery = await Gallery.from_content(
            content=post,
            performer=mock_gallery.performers[0],
            studio=mock_gallery.studio,
        )
        assert gallery.title == f"{mock_gallery.studio.name} - {post.id}"
        assert gallery.details == post.content
        assert gallery.date == post.createdAt.strftime("%Y-%m-%d")

        # Create in Stash
        created = await gallery.save(stash_client)
        assert created.id == mock_gallery.id
        assert created.title == mock_gallery.title
