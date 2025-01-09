"""Integration tests for Scene workflows."""

from datetime import datetime, timezone

import pytest
from stashapi.stash_types import Gender

from stash import Performer, Scene, Studio, Tag


def test_scene_with_relationships(mock_stash_interface, utc_now):
    """Test creating and updating a scene with all its relationships."""
    # Create related objects
    studio = Studio(
        id="s123",
        name="Test Studio",
        url="http://example.com",
        created_at=utc_now,
        updated_at=utc_now,
    )

    performer = Performer(
        id="p123",
        name="Test Performer",
        gender=Gender.FEMALE,
        birthdate=utc_now,
        created_at=utc_now,
        updated_at=utc_now,
    )

    tag = Tag(
        id="t123",
        name="Test Tag",
        created_at=utc_now,
        updated_at=utc_now,
    )

    # Create scene with relationships
    scene = Scene(
        id="123",
        title="Test Scene",
        studio=studio,
        performers=[performer],
        tags=[tag],
        created_at=utc_now,
        updated_at=utc_now,
    )

    # Mock the find methods to return our objects
    mock_stash_interface.find_studio.return_value = studio.to_dict()
    mock_stash_interface.find_performer.return_value = performer.to_dict()
    mock_stash_interface.find_tag.return_value = tag.to_dict()
    mock_stash_interface.find_scene.return_value = scene.to_dict()

    # Test finding the scene with relationships
    found_scene = Scene.find("123", mock_stash_interface)
    assert found_scene is not None
    assert found_scene.title == "Test Scene"
    assert found_scene.studio.name == "Test Studio"
    assert len(found_scene.performers) == 1
    assert found_scene.performers[0].name == "Test Performer"
    assert len(found_scene.tags) == 1
    assert found_scene.tags[0].name == "Test Tag"

    # Test updating the scene
    found_scene.title = "Updated Scene"
    found_scene.save(mock_stash_interface)

    # Verify the update call
    mock_stash_interface.update_scene.assert_called_once()
    update_data = mock_stash_interface.update_scene.call_args[0][0]
    assert update_data["title"] == "Updated Scene"
    assert update_data["studio"]["id"] == "s123"
    assert update_data["performers"][0]["id"] == "p123"
    assert update_data["tags"][0]["id"] == "t123"
