"""Integration tests for stash module."""

import asyncio
from datetime import datetime, timezone
from pathlib import Path

import pytest

from config import FanslyConfig
from download.core import DownloadState
from metadata.database import Database
from stash import Gallery, Group, Image, Performer, Scene, StashInterface, Studio, Tag
from stash.processing import StashProcessing


@pytest.fixture
def stash_interface():
    """Create a real StashInterface instance.

    Note: This requires a running Stash instance with the test API key.
    """
    return StashInterface("http://localhost:9999", "test_api_key")


@pytest.fixture
def config():
    """Create a test FanslyConfig instance."""
    config = FanslyConfig(program_version="test")
    config.separate_metadata = True
    config.metadata_db_file = Path("/tmp/test_metadata.db")
    config.stash_context_conn = "test_conn"
    return config


@pytest.fixture
def state():
    """Create a test DownloadState instance."""
    state = DownloadState(creator_name="test_creator")
    state.creator_id = "123"
    state.download_path = Path("/tmp/test_downloads")
    return state


@pytest.fixture
def database(config):
    """Create a test Database instance."""
    return Database(config)


@pytest.mark.integration
async def test_full_workflow(stash_interface, config, state, database):
    """Test full workflow from processing to saving in Stash."""
    # Create test data
    now = datetime.now(timezone.utc)
    performer = Performer(
        id="new",
        name="Test Performer",
        urls=["https://example.com"],
        created_at=now,
        updated_at=now,
    )

    scene = Scene(
        id="new",
        title="Test Scene",
        urls=["https://example.com/scene"],
        created_at=now,
        updated_at=now,
    )

    # Create performer in Stash
    performer_data = performer.stash_create(stash_interface)
    performer.id = performer_data["id"]

    # Add performer to scene
    scene.performers = [performer]

    # Create scene in Stash
    scene_data = scene.stash_create(stash_interface)
    scene.id = scene_data["id"]

    # Verify performer exists with default fragment
    found_performer = Performer.find(performer.id, stash_interface)
    assert found_performer is not None
    assert found_performer.name == "Test Performer"

    # Verify performer exists with custom fragment
    found_performer_custom = stash_interface.find_performer(
        performer.id, fragment="{ id name }"
    )
    assert found_performer_custom is not None
    assert found_performer_custom["name"] == "Test Performer"

    # Verify scene exists and has performer with default fragment
    found_scene = Scene.find(scene.id, stash_interface)
    assert found_scene is not None
    assert found_scene.title == "Test Scene"
    assert len(found_scene.performers) == 1
    assert found_scene.performers[0].id == performer.id

    # Verify scene exists with custom fragment
    found_scene_custom = stash_interface.find_scene(scene.id, fragment="{ id title }")
    assert found_scene_custom is not None
    assert found_scene_custom["title"] == "Test Scene"

    # Update performer
    performer.name = "Updated Test Performer"
    performer.save(stash_interface)

    # Verify update
    found_performer = Performer.find(performer.id, stash_interface)
    assert found_performer.name == "Updated Test Performer"

    # Test processing
    processor = StashProcessing.from_config(config, state)
    await processor.start_creator_processing()

    # Clean up
    # Note: In a real test environment, we would clean up the test data
    # but since this is just an example, we'll skip that part
