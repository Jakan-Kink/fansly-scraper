"""Tests for Studio dataclass."""

from datetime import datetime, timezone

import pytest

from stash.studio import Studio


def test_studio_creation():
    """Test basic Studio creation."""
    now = datetime.now(timezone.utc)
    studio = Studio(
        id="123",
        name="Test Studio",
        url="http://example.com",
        aliases=["Test Alias 1", "Test Alias 2"],
        ignore_auto_tag=False,
        image_path="/image/path",
        rating100=75,
        favorite=True,
        details="Test Details",
        created_at=now,
        updated_at=now,
    )

    assert studio.id == "123"
    assert studio.name == "Test Studio"
    assert studio.url == "http://example.com"
    assert studio.aliases == ["Test Alias 1", "Test Alias 2"]
    assert studio.ignore_auto_tag is False
    assert studio.image_path == "/image/path"
    assert studio.rating100 == 75
    assert studio.favorite is True
    assert studio.details == "Test Details"
    assert studio.created_at == now
    assert studio.updated_at == now


def test_studio_to_dict():
    """Test Studio to_dict method."""
    now = datetime.now(timezone.utc)
    studio = Studio(
        id="123",
        name="Test Studio",
        url="http://example.com",
        aliases=["Test Alias 1", "Test Alias 2"],
        ignore_auto_tag=False,
        image_path="/image/path",
        rating100=75,
        favorite=True,
        details="Test Details",
        created_at=now,
        updated_at=now,
    )

    data = studio.to_dict()
    assert data["id"] == "123"
    assert data["name"] == "Test Studio"
    assert data["url"] == "http://example.com"
    assert data["aliases"] == ["Test Alias 1", "Test Alias 2"]
    assert data["ignore_auto_tag"] is False
    assert data["image_path"] == "/image/path"
    assert data["rating100"] == 75
    assert data["favorite"] is True
    assert data["details"] == "Test Details"
    assert data["created_at"] == now.isoformat()
    assert data["updated_at"] == now.isoformat()


def test_studio_from_dict():
    """Test Studio from_dict method."""
    now = datetime.now(timezone.utc)
    data = {
        "id": "123",
        "name": "Test Studio",
        "url": "http://example.com",
        "aliases": ["Test Alias 1", "Test Alias 2"],
        "ignore_auto_tag": False,
        "image_path": "/image/path",
        "rating100": 75,
        "favorite": True,
        "details": "Test Details",
        "created_at": now.isoformat(),
        "updated_at": now.isoformat(),
    }

    studio = Studio.from_dict(data)
    assert studio.id == "123"
    assert studio.name == "Test Studio"
    assert studio.url == "http://example.com"
    assert studio.aliases == ["Test Alias 1", "Test Alias 2"]
    assert studio.ignore_auto_tag is False
    assert studio.image_path == "/image/path"
    assert studio.rating100 == 75
    assert studio.favorite is True
    assert studio.details == "Test Details"
    assert studio.created_at == now
    assert studio.updated_at == now


def test_studio_hierarchy():
    """Test Studio parent/child relationships."""
    parent = Studio(id="123", name="Parent Studio")
    child1 = Studio(id="456", name="Child Studio 1", parent_studio=parent)
    child2 = Studio(id="789", name="Child Studio 2", parent_studio=parent)

    assert child1.parent_studio == parent
    assert child2.parent_studio == parent
    assert parent.child_studios == []  # Child studios are not automatically added

    # Test serialization
    data = child1.to_dict()
    assert data.get("parent_studio") is not None
    assert data["parent_studio"].get("id") == "123"
    assert data["parent_studio"].get("name") == "Parent Studio"
