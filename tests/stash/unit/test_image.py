"""Tests for Image dataclass."""

from datetime import datetime, timezone

import pytest

from stash.image import Image
from stash.image_paths import ImagePathsType


def test_image_creation():
    """Test basic Image creation."""
    now = datetime.now(timezone.utc)
    image = Image(
        id="123",
        title="Test Image",
        code="IMG123",
        rating100=75,
        date=now,
        details="Test Details",
        photographer="Test Photographer",
        o_counter=5,
        organized=True,
        paths=ImagePathsType(
            thumbnail="/thumb/path", preview="/preview/path", image="/image/path"
        ),
        created_at=now,
        updated_at=now,
    )

    assert image.id == "123"
    assert image.title == "Test Image"
    assert image.code == "IMG123"
    assert image.rating100 == 75
    assert image.date == now
    assert image.details == "Test Details"
    assert image.photographer == "Test Photographer"
    assert image.o_counter == 5
    assert image.organized is True
    assert image.paths.thumbnail == "/thumb/path"
    assert image.paths.preview == "/preview/path"
    assert image.paths.image == "/image/path"
    assert image.created_at == now
    assert image.updated_at == now


def test_image_to_dict():
    """Test Image to_dict method."""
    now = datetime.now(timezone.utc)
    image = Image(
        id="123",
        title="Test Image",
        code="IMG123",
        rating100=75,
        date=now,
        details="Test Details",
        photographer="Test Photographer",
        o_counter=5,
        organized=True,
        paths=ImagePathsType(
            thumbnail="/thumb/path", preview="/preview/path", image="/image/path"
        ),
        created_at=now,
        updated_at=now,
    )

    data = image.to_dict()
    assert data["id"] == "123"
    assert data["title"] == "Test Image"
    assert data["code"] == "IMG123"
    assert data["rating100"] == 75
    assert data["date"] == now.isoformat()
    assert data["details"] == "Test Details"
    assert data["photographer"] == "Test Photographer"
    assert data["o_counter"] == 5
    assert data["organized"] is True
    assert data["paths"] == {
        "thumbnail": "/thumb/path",
        "preview": "/preview/path",
        "image": "/image/path",
    }
    assert data["created_at"] == now.isoformat()
    assert data["updated_at"] == now.isoformat()


def test_image_from_dict():
    """Test Image from_dict method."""
    now = datetime.now(timezone.utc)
    data = {
        "id": "123",
        "title": "Test Image",
        "code": "IMG123",
        "rating100": 75,
        "date": now.isoformat(),
        "details": "Test Details",
        "photographer": "Test Photographer",
        "o_counter": 5,
        "organized": True,
        "paths": {
            "thumbnail": "/thumb/path",
            "preview": "/preview/path",
            "image": "/image/path",
        },
        "created_at": now.isoformat(),
        "updated_at": now.isoformat(),
    }

    image = Image.from_dict(data)
    assert image.id == "123"
    assert image.title == "Test Image"
    assert image.code == "IMG123"
    assert image.rating100 == 75
    assert image.date == now
    assert image.details == "Test Details"
    assert image.photographer == "Test Photographer"
    assert image.o_counter == 5
    assert image.organized is True
    assert image.paths.thumbnail == "/thumb/path"
    assert image.paths.preview == "/preview/path"
    assert image.paths.image == "/image/path"
    assert image.created_at == now
    assert image.updated_at == now
