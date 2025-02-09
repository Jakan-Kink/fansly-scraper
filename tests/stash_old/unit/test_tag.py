"""Tests for Tag dataclass."""

from datetime import datetime, timezone

import pytest

from stash.tag import Tag


def test_tag_creation():
    """Test basic Tag creation."""
    now = datetime.now(timezone.utc)
    tag = Tag(
        id="123",
        name="Test Tag",
        description="Test Description",
        aliases=["Test Alias 1", "Test Alias 2"],
        ignore_auto_tag=False,
        image_path="/image/path",
        favorite=True,
        created_at=now,
        updated_at=now,
    )

    assert tag.id == "123"
    assert tag.name == "Test Tag"
    assert tag.description == "Test Description"
    assert tag.aliases == ["Test Alias 1", "Test Alias 2"]
    assert tag.ignore_auto_tag is False
    assert tag.image_path == "/image/path"
    assert tag.favorite is True
    assert tag.created_at == now
    assert tag.updated_at == now


def test_tag_to_dict():
    """Test Tag to_dict method."""
    now = datetime.now(timezone.utc)
    tag = Tag(
        id="123",
        name="Test Tag",
        description="Test Description",
        aliases=["Test Alias 1", "Test Alias 2"],
        ignore_auto_tag=False,
        image_path="/image/path",
        favorite=True,
        created_at=now,
        updated_at=now,
    )

    data = tag.to_dict()
    assert data["id"] == "123"
    assert data["name"] == "Test Tag"
    assert data["description"] == "Test Description"
    assert data["aliases"] == ["Test Alias 1", "Test Alias 2"]
    assert data["ignore_auto_tag"] is False
    assert data["image_path"] == "/image/path"
    assert data["favorite"] is True
    assert data["created_at"] == now.isoformat()
    assert data["updated_at"] == now.isoformat()


def test_tag_from_dict():
    """Test Tag from_dict method."""
    now = datetime.now(timezone.utc)
    data = {
        "id": "123",
        "name": "Test Tag",
        "description": "Test Description",
        "aliases": ["Test Alias 1", "Test Alias 2"],
        "ignore_auto_tag": False,
        "image_path": "/image/path",
        "favorite": True,
        "created_at": now.isoformat(),
        "updated_at": now.isoformat(),
    }

    tag = Tag.from_dict(data)
    assert tag.id == "123"
    assert tag.name == "Test Tag"
    assert tag.description == "Test Description"
    assert tag.aliases == ["Test Alias 1", "Test Alias 2"]
    assert tag.ignore_auto_tag is False
    assert tag.image_path == "/image/path"
    assert tag.favorite is True
    assert tag.created_at == now
    assert tag.updated_at == now


def test_tag_hierarchy():
    """Test Tag parent/child relationships."""
    parent = Tag(id="123", name="Parent Tag")
    child1 = Tag(id="456", name="Child Tag 1")
    child2 = Tag(id="789", name="Child Tag 2")

    # Set up relationships
    child1.parents = [parent]
    child2.parents = [parent]
    parent.children = [child1, child2]

    assert child1.parents == [parent]
    assert child2.parents == [parent]
    assert parent.children == [child1, child2]

    # Test serialization
    data = child1.to_dict()
    assert len(data["parents"]) == 1
    assert data["parents"][0]["id"] == "123"
    assert data["parents"][0]["name"] == "Parent Tag"

    data = parent.to_dict()
    assert len(data["children"]) == 2
    assert {c["id"] for c in data["children"]} == {"456", "789"}
    assert {c["name"] for c in data["children"]} == {"Child Tag 1", "Child Tag 2"}
