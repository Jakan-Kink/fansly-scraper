"""Unit tests for Group dataclass."""

from datetime import datetime, timezone
from typing import cast

import pytest
from stashapi.stashapp import StashInterface

from stash.group import Group, GroupDescription


@pytest.fixture
def mock_stash_interface(mocker):
    """Create a mock StashInterface."""
    return cast(StashInterface, mocker.Mock(spec=StashInterface))


@pytest.fixture
def sample_group_data():
    """Create sample group data."""
    now = datetime.now(timezone.utc)
    return {
        "id": "123",
        "name": "Test Group",
        "aliases": "Test, Group",
        "duration": 3600,
        "date": "2024-01-01",
        "rating100": 75,
        "director": "Test Director",
        "synopsis": "Test Synopsis",
        "urls": ["http://example.com"],
        "front_image_path": "/path/to/front.jpg",
        "back_image_path": "/path/to/back.jpg",
        "studio": None,
        "tags": [],
        "containing_groups": [],
        "sub_groups": [],
        "scenes": [],
        "performers": [],
        "galleries": [],
        "images": [],
        "created_at": now.isoformat(),
        "updated_at": now.isoformat(),
    }


@pytest.fixture
def sample_sub_group_data():
    """Create sample sub-group data."""
    now = datetime.now(timezone.utc)
    return {
        "id": "456",
        "name": "Sub Group",
        "aliases": "Sub",
        "duration": 1800,
        "date": "2024-01-02",
        "created_at": now.isoformat(),
        "updated_at": now.isoformat(),
    }


def test_group_creation(sample_group_data):
    """Test creating a Group instance."""
    group = Group.from_dict(sample_group_data)

    assert group.id == "123"
    assert group.name == "Test Group"
    assert group.aliases == "Test, Group"
    assert group.duration == 3600
    assert group.date == "2024-01-01"
    assert group.rating100 == 75
    assert group.director == "Test Director"
    assert group.synopsis == "Test Synopsis"
    assert group.urls == ["http://example.com"]
    assert group.front_image_path == "/path/to/front.jpg"
    assert group.back_image_path == "/path/to/back.jpg"
    assert group.studio is None
    assert group.tags == []
    assert group.containing_groups == []
    assert group.sub_groups == []
    assert group.scenes == []
    assert group.performers == []
    assert group.galleries == []
    assert group.images == []
    assert isinstance(group.created_at, datetime)
    assert isinstance(group.updated_at, datetime)


def test_group_with_hierarchy(sample_group_data, sample_sub_group_data):
    """Test group with containing/sub group relationships."""
    # Create containing group
    containing = Group.from_dict(sample_group_data)

    # Create sub group
    sub = Group.from_dict(sample_sub_group_data)

    # Create relationship
    description = GroupDescription(
        containing_group=containing, sub_group=sub, description="Test relationship"
    )

    # Update relationships
    containing.sub_groups = [description]
    sub.containing_groups = [description]

    assert len(containing.sub_groups) == 1
    assert containing.sub_groups[0].sub_group.id == sub.id
    assert containing.sub_groups[0].description == "Test relationship"
    assert len(sub.containing_groups) == 1
    assert sub.containing_groups[0].containing_group.id == containing.id
    assert sub.containing_groups[0].description == "Test relationship"


def test_group_to_dict(sample_group_data):
    """Test converting a Group instance to dictionary."""
    group = Group.from_dict(sample_group_data)
    data = group.to_dict()

    assert data["id"] == "123"
    assert data["name"] == "Test Group"
    assert data["aliases"] == "Test, Group"
    assert data["duration"] == 3600
    assert data["date"] == "2024-01-01"
    assert data["rating100"] == 75
    assert data["director"] == "Test Director"
    assert data["synopsis"] == "Test Synopsis"
    assert data["urls"] == ["http://example.com"]
    assert data["front_image_path"] == "/path/to/front.jpg"
    assert data["back_image_path"] == "/path/to/back.jpg"
    assert data["studio"] is None
    assert data["tags"] == []
    assert data["containing_groups"] == []
    assert data["sub_groups"] == []
    assert data["scenes"] == []
    assert data["performers"] == []
    assert data["galleries"] == []
    assert data["images"] == []
    assert isinstance(data["created_at"], str)  # Should be ISO format
    assert isinstance(data["updated_at"], str)  # Should be ISO format


def test_group_create_input_dict(sample_group_data):
    """Test creating input dictionary for group creation."""
    group = Group.from_dict(sample_group_data)
    data = group.to_create_input_dict()

    assert "id" not in data  # ID should not be included in create input
    assert data["name"] == "Test Group"
    assert data["aliases"] == "Test, Group"
    assert data["duration"] == 3600
    assert data["date"] == "2024-01-01"
    assert data["rating100"] == 75
    assert data["director"] == "Test Director"
    assert data["synopsis"] == "Test Synopsis"
    assert data["urls"] == ["http://example.com"]
    assert data["front_image_path"] == "/path/to/front.jpg"
    assert data["back_image_path"] == "/path/to/back.jpg"
    assert data["studio_id"] is None
    assert data["tag_ids"] == []
    assert data["scene_ids"] == []
    assert data["performer_ids"] == []
    assert data["gallery_ids"] == []
    assert data["image_ids"] == []


def test_group_update_input_dict(sample_group_data):
    """Test creating input dictionary for group update."""
    group = Group.from_dict(sample_group_data)
    data = group.to_update_input_dict()

    assert data["id"] == "123"  # ID should be included in update input
    assert data["name"] == "Test Group"
    assert data["aliases"] == "Test, Group"
    assert data["duration"] == 3600
    assert data["date"] == "2024-01-01"
    assert data["rating100"] == 75
    assert data["director"] == "Test Director"
    assert data["synopsis"] == "Test Synopsis"
    assert data["urls"] == ["http://example.com"]
    assert data["front_image_path"] == "/path/to/front.jpg"
    assert data["back_image_path"] == "/path/to/back.jpg"
    assert data["studio_id"] is None
    assert data["tag_ids"] == []
    assert data["scene_ids"] == []
    assert data["performer_ids"] == []
    assert data["gallery_ids"] == []
    assert data["image_ids"] == []


def test_group_find(mock_stash_interface, sample_group_data):
    """Test finding a group by ID."""
    mock_stash_interface.find_group.return_value = sample_group_data
    group = Group.find("123", mock_stash_interface)

    assert group is not None
    assert group.id == "123"
    assert group.name == "Test Group"
    mock_stash_interface.find_group.assert_called_once_with("123")


def test_group_find_all(mock_stash_interface, sample_group_data):
    """Test finding all groups."""
    mock_stash_interface.find_groups.return_value = [sample_group_data]
    groups = Group.find_all(mock_stash_interface, filter={"per_page": 10}, q="test")

    assert len(groups) == 1
    assert groups[0].id == "123"
    assert groups[0].name == "Test Group"
    mock_stash_interface.find_groups.assert_called_once_with(
        filter={"per_page": 10}, q="test"
    )


def test_group_save(mock_stash_interface, sample_group_data):
    """Test saving group changes."""
    group = Group.from_dict(sample_group_data)
    group.save(mock_stash_interface)

    mock_stash_interface.update_group.assert_called_once()
    assert mock_stash_interface.update_group.call_args[0][0] == group.to_dict()


def test_group_create_batch(mock_stash_interface, sample_group_data):
    """Test creating multiple groups at once."""
    groups = [Group.from_dict(sample_group_data)]
    Group.create_batch(mock_stash_interface, groups)

    mock_stash_interface.create_groups.assert_called_once()
    assert mock_stash_interface.create_groups.call_args[0][0] == [
        groups[0].to_create_input_dict()
    ]


def test_group_update_batch(mock_stash_interface, sample_group_data):
    """Test updating multiple groups at once."""
    groups = [Group.from_dict(sample_group_data)]
    Group.update_batch(mock_stash_interface, groups)

    mock_stash_interface.update_groups.assert_called_once()
    assert mock_stash_interface.update_groups.call_args[0][0] == [
        groups[0].to_update_input_dict()
    ]
