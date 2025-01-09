"""Unit tests for Gallery dataclass."""

from datetime import datetime, timezone
from typing import cast

import pytest
from stashapi.stashapp import StashInterface

from stash.gallery import Gallery, GalleryChapter


@pytest.fixture
def mock_stash_interface(mocker):
    """Create a mock StashInterface."""
    return cast(StashInterface, mocker.Mock(spec=StashInterface))


@pytest.fixture
def sample_gallery_data():
    """Create sample gallery data."""
    now = datetime.now(timezone.utc)
    return {
        "id": "123",
        "title": "Test Gallery",
        "code": "TEST123",
        "urls": ["http://example.com"],
        "date": now.isoformat(),
        "details": "Test Details",
        "photographer": "Test Photographer",
        "rating100": 75,
        "organized": True,
        "files": [],
        "folder": "/path/to/folder",
        "chapters": [
            {
                "id": "ch1",
                "title": "Chapter 1",
                "image_index": 0,
                "gallery_id": "123",
                "created_at": now.isoformat(),
                "updated_at": now.isoformat(),
            }
        ],
        "scenes": [],
        "studio": None,
        "image_count": 10,
        "tags": [],
        "performers": [],
        "cover": "/path/to/cover.jpg",
        "paths": {
            "thumbnail": "/path/to/thumb.jpg",
            "preview": "/path/to/preview.jpg",
        },
        "created_at": now.isoformat(),
        "updated_at": now.isoformat(),
    }


def test_gallery_creation(sample_gallery_data):
    """Test creating a Gallery instance."""
    gallery = Gallery.from_dict(sample_gallery_data)

    assert gallery.id == "123"
    assert gallery.title == "Test Gallery"
    assert gallery.code == "TEST123"
    assert gallery.urls == ["http://example.com"]
    assert isinstance(gallery.date, datetime)
    assert gallery.details == "Test Details"
    assert gallery.photographer == "Test Photographer"
    assert gallery.rating100 == 75
    assert gallery.organized is True
    assert gallery.files == []
    assert gallery.folder == "/path/to/folder"
    assert len(gallery.chapters) == 1
    assert isinstance(gallery.chapters[0], GalleryChapter)
    assert gallery.chapters[0].id == "ch1"
    assert gallery.chapters[0].title == "Chapter 1"
    assert gallery.scenes == []
    assert gallery.studio is None
    assert gallery.image_count == 10
    assert gallery.tags == []
    assert gallery.performers == []
    assert gallery.cover == "/path/to/cover.jpg"
    assert gallery.paths == {
        "thumbnail": "/path/to/thumb.jpg",
        "preview": "/path/to/preview.jpg",
    }
    assert isinstance(gallery.created_at, datetime)
    assert isinstance(gallery.updated_at, datetime)


def test_gallery_chapter_creation(sample_gallery_data):
    """Test creating a GalleryChapter instance."""
    chapter_data = sample_gallery_data["chapters"][0]
    chapter = GalleryChapter(
        id=chapter_data["id"],
        title=chapter_data["title"],
        image_index=chapter_data["image_index"],
        gallery_id=chapter_data["gallery_id"],
        created_at=datetime.fromisoformat(chapter_data["created_at"]),
        updated_at=datetime.fromisoformat(chapter_data["updated_at"]),
    )

    assert chapter.id == "ch1"
    assert chapter.title == "Chapter 1"
    assert chapter.image_index == 0
    assert chapter.gallery_id == "123"
    assert isinstance(chapter.created_at, datetime)
    assert isinstance(chapter.updated_at, datetime)


def test_gallery_to_dict(sample_gallery_data):
    """Test converting a Gallery instance to dictionary."""
    gallery = Gallery.from_dict(sample_gallery_data)
    data = gallery.to_dict()

    assert data["id"] == "123"
    assert data["title"] == "Test Gallery"
    assert data["code"] == "TEST123"
    assert data["urls"] == ["http://example.com"]
    assert isinstance(data["date"], str)  # Should be ISO format
    assert data["details"] == "Test Details"
    assert data["photographer"] == "Test Photographer"
    assert data["rating100"] == 75
    assert data["organized"] is True
    assert data["files"] == []
    assert data["folder"] == "/path/to/folder"
    assert len(data["chapters"]) == 1
    assert data["chapters"][0]["id"] == "ch1"
    assert data["chapters"][0]["title"] == "Chapter 1"
    assert data["scenes"] == []
    assert data["studio"] is None
    assert data["image_count"] == 10
    assert data["tags"] == []
    assert data["performers"] == []
    assert data["cover"] == "/path/to/cover.jpg"
    assert data["paths"] == {
        "thumbnail": "/path/to/thumb.jpg",
        "preview": "/path/to/preview.jpg",
    }
    assert isinstance(data["created_at"], str)  # Should be ISO format
    assert isinstance(data["updated_at"], str)  # Should be ISO format


def test_gallery_create_input_dict(sample_gallery_data):
    """Test creating input dictionary for gallery creation."""
    gallery = Gallery.from_dict(sample_gallery_data)
    data = gallery.to_create_input_dict()

    assert "id" not in data  # ID should not be included in create input
    assert data["title"] == "Test Gallery"
    assert data["code"] == "TEST123"
    assert data["urls"] == ["http://example.com"]
    assert isinstance(data["date"], str)  # Should be ISO format
    assert data["details"] == "Test Details"
    assert data["photographer"] == "Test Photographer"
    assert data["rating100"] == 75
    assert data["organized"] is True
    assert data["scene_ids"] == []
    assert data["studio_id"] is None
    assert data["tag_ids"] == []
    assert data["performer_ids"] == []
    assert data["cover"] == "/path/to/cover.jpg"


def test_gallery_update_input_dict(sample_gallery_data):
    """Test creating input dictionary for gallery update."""
    gallery = Gallery.from_dict(sample_gallery_data)
    data = gallery.to_update_input_dict()

    assert data["id"] == "123"  # ID should be included in update input
    assert data["title"] == "Test Gallery"
    assert data["code"] == "TEST123"
    assert data["urls"] == ["http://example.com"]
    assert isinstance(data["date"], str)  # Should be ISO format
    assert data["details"] == "Test Details"
    assert data["photographer"] == "Test Photographer"
    assert data["rating100"] == 75
    assert data["organized"] is True
    assert data["scene_ids"] == []
    assert data["studio_id"] is None
    assert data["tag_ids"] == []
    assert data["performer_ids"] == []
    assert data["cover"] == "/path/to/cover.jpg"


def test_gallery_find(mock_stash_interface, sample_gallery_data):
    """Test finding a gallery by ID."""
    mock_stash_interface.find_gallery.return_value = sample_gallery_data
    gallery = Gallery.find("123", mock_stash_interface)

    assert gallery is not None
    assert gallery.id == "123"
    assert gallery.title == "Test Gallery"
    mock_stash_interface.find_gallery.assert_called_once_with("123")


def test_gallery_find_all(mock_stash_interface, sample_gallery_data):
    """Test finding all galleries."""
    mock_stash_interface.find_galleries.return_value = [sample_gallery_data]
    galleries = Gallery.find_all(
        mock_stash_interface, filter={"per_page": 10}, q="test"
    )

    assert len(galleries) == 1
    assert galleries[0].id == "123"
    assert galleries[0].title == "Test Gallery"
    mock_stash_interface.find_galleries.assert_called_once_with(
        filter={"per_page": 10}, q="test"
    )


def test_gallery_save(mock_stash_interface, sample_gallery_data):
    """Test saving gallery changes."""
    gallery = Gallery.from_dict(sample_gallery_data)
    gallery.save(mock_stash_interface)

    mock_stash_interface.update_gallery.assert_called_once()
    assert mock_stash_interface.update_gallery.call_args[0][0] == gallery.to_dict()


def test_gallery_create_batch(mock_stash_interface, sample_gallery_data):
    """Test creating multiple galleries at once."""
    galleries = [Gallery.from_dict(sample_gallery_data)]
    Gallery.create_batch(mock_stash_interface, galleries)

    mock_stash_interface.create_galleries.assert_called_once()
    assert mock_stash_interface.create_galleries.call_args[0][0] == [
        galleries[0].to_create_input_dict()
    ]


def test_gallery_update_batch(mock_stash_interface, sample_gallery_data):
    """Test updating multiple galleries at once."""
    galleries = [Gallery.from_dict(sample_gallery_data)]
    Gallery.update_batch(mock_stash_interface, galleries)

    mock_stash_interface.update_galleries.assert_called_once()
    assert mock_stash_interface.update_galleries.call_args[0][0] == [
        galleries[0].to_update_input_dict()
    ]
