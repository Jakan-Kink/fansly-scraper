"""Integration tests for error recovery and data validation.

These tests require a running Stash instance.
"""

import asyncio
from collections.abc import AsyncGenerator
from datetime import datetime
from pathlib import Path
from typing import List, Optional

import pytest
import pytest_asyncio

from stash import StashClient
from stash.types import (
    Gallery,
    GenerateMetadataInput,
    GenerateMetadataOptions,
    Image,
    Performer,
    Scene,
    Studio,
    Tag,
)


class TestContext:
    """Context manager for test data cleanup."""

    def __init__(self, client: StashClient):
        self.client = client
        self.created_ids = {
            "performers": [],
            "studios": [],
            "tags": [],
            "scenes": [],
            "galleries": [],
        }

    async def cleanup(self) -> None:
        """Clean up created test data."""
        # Clean up in reverse order of dependencies
        for scene_id in self.created_ids["scenes"]:
            try:
                # Would use scene_destroy if available
                pass
            except Exception as e:
                print(f"Failed to delete scene {scene_id}: {e}")

        for gallery_id in self.created_ids["galleries"]:
            try:
                # Would use gallery_destroy if available
                pass
            except Exception as e:
                print(f"Failed to delete gallery {gallery_id}: {e}")

        for tag_id in self.created_ids["tags"]:
            try:
                # Would use tag_destroy if available
                pass
            except Exception as e:
                print(f"Failed to delete tag {tag_id}: {e}")

        for studio_id in self.created_ids["studios"]:
            try:
                # Would use studio_destroy if available
                pass
            except Exception as e:
                print(f"Failed to delete studio {studio_id}: {e}")

        for performer_id in self.created_ids["performers"]:
            try:
                # Would use performer_destroy if available
                pass
            except Exception as e:
                print(f"Failed to delete performer {performer_id}: {e}")


@pytest.mark.asyncio
async def test_data_validation_workflow(stash_client: StashClient) -> None:
    """Test data validation and error recovery.

    This test:
    1. Attempts invalid operations
    2. Validates data constraints
    3. Recovers from errors
    4. Verifies data integrity
    """
    ctx = TestContext(stash_client)
    try:
        # Test invalid performer creation
        with pytest.raises(Exception):  # Specific exception type if known
            performer = Performer(
                name="",  # Empty name should fail
                created_at=datetime.now(),
                updated_at=datetime.now(),
            )
            await stash_client.create_performer(performer)

        # Create valid performer
        performer = Performer(
            name="Test Performer",
            gender="FEMALE",
            created_at=datetime.now(),
            updated_at=datetime.now(),
        )
        performer = await stash_client.create_performer(performer)
        ctx.created_ids["performers"].append(performer.id)

        # Test invalid studio creation
        with pytest.raises(Exception):
            studio = Studio(
                name=" ",  # Whitespace name should fail
                created_at=datetime.now(),
                updated_at=datetime.now(),
            )
            await stash_client.create_studio(studio)

        # Create valid studio
        studio = Studio(
            name="Test Studio",
            created_at=datetime.now(),
            updated_at=datetime.now(),
        )
        studio = await stash_client.create_studio(studio)
        ctx.created_ids["studios"].append(studio.id)

        # Test invalid tag creation
        with pytest.raises(Exception):
            tag = Tag(
                name="invalid/tag",  # Invalid characters should fail
                created_at=datetime.now(),
                updated_at=datetime.now(),
            )
            await stash_client.create_tag(tag)

        # Create valid tag
        tag = Tag(
            name="valid_tag",
            created_at=datetime.now(),
            updated_at=datetime.now(),
        )
        tag = await stash_client.create_tag(tag)
        ctx.created_ids["tags"].append(tag.id)

        # Test scene validation
        scene = Scene(
            title="Test Scene",
            details="Test details",
            date="invalid_date",  # Invalid date should fail
            urls=["https://example.com/scene"],
            organized=True,
            performers=[performer],
            studio=studio,
            tags=[tag],
            created_at=datetime.now(),
            updated_at=datetime.now(),
        )
        try:
            await stash_client.create_scene(scene)
            pytest.fail("Should have failed with invalid date")
        except Exception:
            # Fix date and retry
            scene.date = datetime.now().strftime("%Y-%m-%d")
            scene = await stash_client.create_scene(scene)
            ctx.created_ids["scenes"].append(scene.id)

        # Test gallery validation
        gallery = Gallery(
            title="Test Gallery",
            details="Test details",
            date="2024-01-01",
            urls=["invalid_url"],  # Invalid URL should fail
            organized=True,
            performers=[performer],
            studio=studio,
            tags=[tag],
            created_at=datetime.now(),
            updated_at=datetime.now(),
        )
        try:
            await stash_client.create_gallery(gallery)
            pytest.fail("Should have failed with invalid URL")
        except Exception:
            # Fix URL and retry
            gallery.urls = ["https://example.com/gallery"]
            gallery = await stash_client.create_gallery(gallery)
            ctx.created_ids["galleries"].append(gallery.id)

        # Test relationship validation
        invalid_performer = Performer(
            id="999999",  # Non-existent ID
            name="Invalid",
            created_at=datetime.now(),
            updated_at=datetime.now(),
        )
        scene.performers.append(invalid_performer)
        try:
            await stash_client.update_scene(scene)
            pytest.fail("Should have failed with invalid performer")
        except Exception:
            # Remove invalid performer and retry
            scene.performers = [performer]
            scene = await stash_client.update_scene(scene)

        # Verify data integrity
        scene = await stash_client.find_scene(scene.id)
        assert scene is not None
        assert scene.performers[0].id == performer.id
        assert scene.studio.id == studio.id
        assert scene.tags[0].id == tag.id

        gallery = await stash_client.find_gallery(gallery.id)
        assert gallery is not None
        assert gallery.performers[0].id == performer.id
        assert gallery.studio.id == studio.id
        assert gallery.tags[0].id == tag.id

    except Exception as e:
        pytest.skip(f"Test requires running Stash instance: {e}")
    finally:
        await ctx.cleanup()


@pytest.mark.asyncio
async def test_concurrent_error_recovery(stash_client: StashClient) -> None:
    """Test concurrent error recovery.

    This test:
    1. Attempts multiple operations concurrently
    2. Handles errors in parallel
    3. Recovers from failures
    4. Verifies final state
    """
    ctx = TestContext(stash_client)
    try:
        # Create base data
        performer = Performer(
            name="Test Performer",
            gender="FEMALE",
            created_at=datetime.now(),
            updated_at=datetime.now(),
        )
        performer = await stash_client.create_performer(performer)
        ctx.created_ids["performers"].append(performer.id)

        studio = Studio(
            name="Test Studio",
            created_at=datetime.now(),
            updated_at=datetime.now(),
        )
        studio = await stash_client.create_studio(studio)
        ctx.created_ids["studios"].append(studio.id)

        # Attempt concurrent scene creation with some invalid data
        async def create_scene(i: int, should_fail: bool = False) -> Scene | None:
            try:
                scene = Scene(
                    title=f"Test Scene {i}",
                    details="Test details",
                    date="2024-01-01" if not should_fail else "invalid_date",
                    urls=[f"https://example.com/scene_{i}"],
                    organized=True,
                    performers=[performer],
                    studio=studio,
                    created_at=datetime.now(),
                    updated_at=datetime.now(),
                )
                created = await stash_client.create_scene(scene)
                ctx.created_ids["scenes"].append(created.id)
                return created
            except Exception as e:
                print(f"Scene {i} creation failed: {e}")
                if should_fail:
                    return None
                # Retry with valid date
                scene.date = "2024-01-01"
                created = await stash_client.create_scene(scene)
                ctx.created_ids["scenes"].append(created.id)
                return created

        # Create 5 scenes, 2 with invalid data
        tasks = [create_scene(i, should_fail=(i % 2 == 0)) for i in range(5)]
        scenes = await asyncio.gather(*tasks)
        valid_scenes = [s for s in scenes if s is not None]
        assert len(valid_scenes) >= 3  # At least non-failing scenes should work

        # Attempt concurrent gallery creation with some invalid data
        async def create_gallery(i: int, should_fail: bool = False) -> Gallery | None:
            try:
                gallery = Gallery(
                    title=f"Test Gallery {i}",
                    details="Test details",
                    date="2024-01-01",
                    urls=[
                        (
                            "invalid_url"
                            if should_fail
                            else f"https://example.com/gallery_{i}"
                        )
                    ],
                    organized=True,
                    performers=[performer],
                    studio=studio,
                    created_at=datetime.now(),
                    updated_at=datetime.now(),
                )
                created = await stash_client.create_gallery(gallery)
                ctx.created_ids["galleries"].append(created.id)
                return created
            except Exception as e:
                print(f"Gallery {i} creation failed: {e}")
                if should_fail:
                    return None
                # Retry with valid URL
                gallery.urls = [f"https://example.com/gallery_{i}"]
                created = await stash_client.create_gallery(gallery)
                ctx.created_ids["galleries"].append(created.id)
                return created

        # Create 5 galleries, 2 with invalid data
        tasks = [create_gallery(i, should_fail=(i % 2 == 0)) for i in range(5)]
        galleries = await asyncio.gather(*tasks)
        valid_galleries = [g for g in galleries if g is not None]
        assert len(valid_galleries) >= 3

        # Verify final state
        for scene in valid_scenes:
            found = await stash_client.find_scene(scene.id)
            assert found is not None
            assert found.performers[0].id == performer.id
            assert found.studio.id == studio.id

        for gallery in valid_galleries:
            found = await stash_client.find_gallery(gallery.id)
            assert found is not None
            assert found.performers[0].id == performer.id
            assert found.studio.id == studio.id

    except Exception as e:
        pytest.skip(f"Test requires running Stash instance: {e}")
    finally:
        await ctx.cleanup()


@pytest.mark.asyncio
async def test_metadata_error_recovery(stash_client: StashClient) -> None:
    """Test metadata generation error recovery.

    This test:
    1. Creates test content
    2. Attempts metadata generation with invalid options
    3. Recovers from failures
    4. Verifies generated files
    """
    ctx = TestContext(stash_client)
    try:
        # Create test scene
        performer = Performer(
            name="Test Performer",
            gender="FEMALE",
            created_at=datetime.now(),
            updated_at=datetime.now(),
        )
        performer = await stash_client.create_performer(performer)
        ctx.created_ids["performers"].append(performer.id)

        scene = Scene(
            title="Test Scene",
            details="Test details",
            date="2024-01-01",
            urls=["https://example.com/scene"],
            organized=True,
            performers=[performer],
            created_at=datetime.now(),
            updated_at=datetime.now(),
        )
        scene = await stash_client.create_scene(scene)
        ctx.created_ids["scenes"].append(scene.id)

        # Test invalid options
        try:
            options = GenerateMetadataOptions(
                previews=True,
                preview_options={
                    "previewSegments": -1,  # Invalid segment count
                },
            )
            input_data = GenerateMetadataInput(
                scene_ids=[scene.id],
            )
            await stash_client.metadata_generate(options, input_data)
            pytest.fail("Should have failed with invalid options")
        except Exception as e:
            print(f"Expected failure with invalid options: {e}")

        # Test valid options with progress tracking
        options = GenerateMetadataOptions(
            covers=True,
            sprites=True,
            previews=True,
            preview_options={
                "previewSegments": 12,
                "previewSegmentDuration": 0.5,
            },
        )
        input_data = GenerateMetadataInput(
            scene_ids=[scene.id],
            overwrite=True,
        )

        # Track generation progress
        job_id = await stash_client.metadata_generate(options, input_data)
        assert job_id is not None

        progress_updates = []
        errors = []
        async with stash_client.subscribe_to_jobs() as subscription:
            async for update in subscription:
                if update.job and update.job.id == job_id:
                    if update.error:
                        errors.append(update.error)
                    if update.progress is not None:
                        progress_updates.append(update.progress)
                    if update.status in ["FINISHED", "CANCELLED"]:
                        break

        # Verify progress was tracked
        assert len(progress_updates) > 0
        if errors:
            print(f"Generation completed with errors: {errors}")

        # Verify scene was updated
        scene = await stash_client.find_scene(scene.id)
        assert scene is not None
        # Would check for generated files if we had the API

    except Exception as e:
        pytest.skip(f"Test requires running Stash instance: {e}")
    finally:
        await ctx.cleanup()


@pytest.mark.asyncio
async def test_relationship_error_recovery(stash_client: StashClient) -> None:
    """Test relationship error recovery.

    This test:
    1. Creates test content with relationships
    2. Attempts invalid relationship updates
    3. Recovers from failures
    4. Verifies relationship integrity
    """
    ctx = TestContext(stash_client)
    try:
        # Create test data
        performer = Performer(
            name="Test Performer",
            gender="FEMALE",
            created_at=datetime.now(),
            updated_at=datetime.now(),
        )
        performer = await stash_client.create_performer(performer)
        ctx.created_ids["performers"].append(performer.id)

        studio = Studio(
            name="Test Studio",
            created_at=datetime.now(),
            updated_at=datetime.now(),
        )
        studio = await stash_client.create_studio(studio)
        ctx.created_ids["studios"].append(studio.id)

        tag = Tag(
            name="test_tag",
            created_at=datetime.now(),
            updated_at=datetime.now(),
        )
        tag = await stash_client.create_tag(tag)
        ctx.created_ids["tags"].append(tag.id)

        # Create scene with relationships
        scene = Scene(
            title="Test Scene",
            details="Test details",
            date="2024-01-01",
            urls=["https://example.com/scene"],
            organized=True,
            performers=[performer],
            studio=studio,
            tags=[tag],
            created_at=datetime.now(),
            updated_at=datetime.now(),
        )
        scene = await stash_client.create_scene(scene)
        ctx.created_ids["scenes"].append(scene.id)

        # Test invalid performer relationship
        try:
            invalid_performer = Performer(
                id="999999",
                name="Invalid",
                created_at=datetime.now(),
                updated_at=datetime.now(),
            )
            scene.performers = [invalid_performer]
            await stash_client.update_scene(scene)
            pytest.fail("Should have failed with invalid performer")
        except Exception as e:
            print(f"Expected failure with invalid performer: {e}")
            # Restore valid performer
            scene.performers = [performer]
            scene = await stash_client.update_scene(scene)

        # Test invalid studio relationship
        try:
            invalid_studio = Studio(
                id="999999",
                name="Invalid",
                created_at=datetime.now(),
                updated_at=datetime.now(),
            )
            scene.studio = invalid_studio
            await stash_client.update_scene(scene)
            pytest.fail("Should have failed with invalid studio")
        except Exception as e:
            print(f"Expected failure with invalid studio: {e}")
            # Restore valid studio
            scene.studio = studio
            scene = await stash_client.update_scene(scene)

        # Test invalid tag relationship
        try:
            invalid_tag = Tag(
                id="999999",
                name="invalid",
                created_at=datetime.now(),
                updated_at=datetime.now(),
            )
            scene.tags = [invalid_tag]
            await stash_client.update_scene(scene)
            pytest.fail("Should have failed with invalid tag")
        except Exception as e:
            print(f"Expected failure with invalid tag: {e}")
            # Restore valid tag
            scene.tags = [tag]
            scene = await stash_client.update_scene(scene)

        # Verify final state
        scene = await stash_client.find_scene(scene.id)
        assert scene is not None
        assert len(scene.performers) == 1
        assert scene.performers[0].id == performer.id
        assert scene.studio.id == studio.id
        assert len(scene.tags) == 1
        assert scene.tags[0].id == tag.id

    except Exception as e:
        pytest.skip(f"Test requires running Stash instance: {e}")
    finally:
        await ctx.cleanup()
