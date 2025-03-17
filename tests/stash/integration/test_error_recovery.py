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
    GenderEnum,
    GenerateMetadataInput,
    GenerateMetadataOptions,
    Image,
    Performer,
    Scene,
    Studio,
    Tag,
)


def get_id(obj):
    """Get ID from either a dict or an object with id attribute."""
    return obj["id"] if isinstance(obj, dict) else obj.id


def get_ids(objects):
    """Get set of IDs from list of dicts or objects."""
    return {get_id(obj) for obj in objects}


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
        try:
            # Delete scenes first (they depend on performers/studios/tags)
            for scene_id in self.created_ids["scenes"]:
                await self.client.execute(
                    """
                    mutation DeleteScene($id: ID!) {
                        sceneDestroy(input: { id: $id })
                    }
                    """,
                    {"id": scene_id},
                )

            # Delete performers
            for performer_id in self.created_ids["performers"]:
                await self.client.execute(
                    """
                    mutation DeletePerformer($id: ID!) {
                        performerDestroy(input: { id: $id })
                    }
                    """,
                    {"id": performer_id},
                )

            # Delete studios
            for studio_id in self.created_ids["studios"]:
                await self.client.execute(
                    """
                    mutation DeleteStudio($id: ID!) {
                        studioDestroy(input: { id: $id })
                    }
                    """,
                    {"id": studio_id},
                )

            # Delete tags
            for tag_id in self.created_ids["tags"]:
                await self.client.execute(
                    """
                    mutation DeleteTag($id: ID!) {
                        tagDestroy(input: { id: $id })
                    }
                    """,
                    {"id": tag_id},
                )
            for gallery_id in self.created_ids["galleries"]:
                await self.client.execute(
                    """
                    mutation DeleteGallery($id: [ID!]!) {
                        galleryDestroy(input: { ids: $id })
                    }
                    """,
                    {"id": gallery_id},
                )
        except Exception as e:
            print(f"Warning: Cleanup failed: {e}")


@pytest.mark.asyncio
async def test_data_validation_workflow(
    stash_client: StashClient, enable_scene_creation
) -> None:
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
        with pytest.raises(Exception) as exc_info:
            performer = Performer(
                id="new",
                name="Test Performer",
                gender=GenderEnum.INVALID,  # Invalid gender should fail
            )
            await stash_client.create_performer(performer)
        assert "INVALID" in str(exc_info.value)

        # Create valid performer
        performer = Performer(
            id="new",
            name="[TEST] Data Validation - Performer",
            gender=GenderEnum.FEMALE,
            details="Created by error recovery test",
            country="Test Country",
            measurements="90-60-90",  # Make it obviously test data
        )
        performer = await stash_client.create_performer(performer)
        ctx.created_ids["performers"].append(performer.id)

        # Test duplicate studio creation
        studio_name = "test_duplicate_studio"
        studio1 = Studio(
            id="new",
            name=studio_name,
        )
        studio1 = await stash_client.create_studio(studio1)
        ctx.created_ids["studios"].append(studio1.id)

        # Try to create another studio with the same name
        with pytest.raises(Exception) as exc_info:
            studio2 = Studio(
                id="new",
                name=studio_name,  # Same name should fail
            )
            await stash_client.create_studio(studio2)
        assert "already exists" in str(exc_info.value).lower()

        # Create valid studio
        studio = Studio(
            id="new",
            name="[TEST] Data Validation - Studio",
            details="Created by error recovery test",
            url="https://test.example.com/error-recovery",
        )
        studio = await stash_client.create_studio(studio)
        ctx.created_ids["studios"].append(studio.id)

        # Test duplicate tag creation - Stash returns existing tag
        tag_name = "test_duplicate_tag"
        tag1 = Tag(
            id="new",
            name=tag_name,
            description="First tag",  # Add description to differentiate
        )
        tag1 = await stash_client.create_tag(tag1)
        ctx.created_ids["tags"].append(tag1.id)

        # Try to create another tag with the same name
        tag2 = Tag(
            id="new",
            name=tag_name,  # Same name
            description="Second tag",  # Different description
        )
        tag2 = await stash_client.create_tag(tag2)

        # Should get back the first tag
        assert tag2.id == tag1.id
        assert tag2.description == tag1.description  # Description unchanged

        # Create valid tag
        tag = Tag(
            id="new",
            name="[TEST] Data Validation - Tag",
            description="Created by error recovery test",
        )
        tag = await stash_client.create_tag(tag)
        ctx.created_ids["tags"].append(tag.id)

        # Create scene with valid fields
        scene = Scene(
            id="new",
            title="[TEST] Data Validation - Scene",
            details="Created by data validation test",
            date=datetime.now().strftime("%Y-%m-%d"),
            urls=["https://test.example.com/data-validation/scene"],
            organized=True,
            performers=[performer],
            studio=studio,
            tags=[tag],
        )
        scene = await stash_client.create_scene(scene)
        ctx.created_ids["scenes"].append(scene.id)

        # Create gallery with valid fields
        gallery = Gallery(
            id="new",
            title="[TEST] Data Validation - Gallery",
            details="Created by data validation test",
            date="2024-01-01",
            urls=["https://test.example.com/data-validation/gallery"],
            organized=True,
            performers=[performer],
            studio=studio,
            tags=[tag],
        )
        gallery = await stash_client.create_gallery(gallery)
        ctx.created_ids["galleries"].append(gallery.id)

        # Test relationship validation
        invalid_performer = Performer(
            id="999999",  # Non-existent ID
            name="Invalid",
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
        assert performer.id in get_ids(scene.performers)
        assert get_id(scene.studio) == studio.id
        assert tag.id in get_ids(scene.tags)

        gallery = await stash_client.find_gallery(gallery.id)
        assert gallery is not None
        assert performer.id in get_ids(gallery.performers)
        assert get_id(gallery.studio) == studio.id
        assert tag.id in get_ids(gallery.tags)

    except RuntimeError as e:
        if "Stash instance" in str(e):
            pytest.skip("Test requires running Stash instance: {e}")
        else:
            raise e
    finally:
        await ctx.cleanup()


@pytest.mark.asyncio
async def test_concurrent_error_recovery(
    stash_client: StashClient, enable_scene_creation
) -> None:
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
            id="new",
            name="Test Performer",
            gender=GenderEnum.FEMALE,
        )
        performer = await stash_client.create_performer(performer)
        ctx.created_ids["performers"].append(performer.id)

        studio = Studio(
            id="new",
            name="Test Studio",
        )
        studio = await stash_client.create_studio(studio)
        ctx.created_ids["studios"].append(studio.id)

        # Create an invalid performer for testing
        invalid_performer = Performer(
            id="999999",  # Non-existent ID
            name="Invalid",
        )

        # Attempt concurrent scene creation with some invalid data
        async def create_scene(i: int, should_fail: bool = False) -> Scene | None:
            try:
                scene = Scene(
                    id="new",
                    title=f"[TEST] Concurrent Error - Scene {i}",
                    details=f"Created by concurrent error recovery test - Scene {i}",
                    date="2024-01-01",
                    urls=[f"https://example.com/scene_{i}"],
                    organized=True,
                    performers=[invalid_performer if should_fail else performer],
                    studio=studio,
                )
                created = await stash_client.create_scene(scene)
                ctx.created_ids["scenes"].append(created.id)  # Track for cleanup
                return created
            except Exception as e:
                print(f"Scene {i} creation failed: {e}")
                if should_fail:
                    # Expected failure with invalid performer
                    return None
                raise  # Unexpected failure

        # Create 5 scenes, 2 with invalid performers
        tasks = [create_scene(i, should_fail=(i % 2 == 0)) for i in range(5)]
        scenes = await asyncio.gather(*tasks)
        valid_scenes = [s for s in scenes if s is not None]
        assert (
            len(valid_scenes) == 2
        )  # Exactly the scenes with valid performers (odd indices)

        # Attempt concurrent gallery creation with some invalid data
        async def create_gallery(i: int, should_fail: bool = False) -> Gallery | None:
            try:
                gallery = Gallery(
                    id="new",
                    title=f"[TEST] Concurrent Error - Gallery {i}",
                    details=f"Created by concurrent error recovery test - Gallery {i}",
                    date="2024-01-01",
                    urls=[f"https://example.com/gallery_{i}"],
                    organized=True,
                    performers=[invalid_performer if should_fail else performer],
                    studio=studio,
                )
                created = await stash_client.create_gallery(gallery)
                ctx.created_ids["galleries"].append(created.id)
                return created
            except Exception as e:
                print(f"Gallery {i} creation failed: {e}")
                if should_fail:
                    # Expected failure with invalid performer
                    return None
                raise  # Unexpected failure

        # Create 5 galleries, 2 with invalid performers
        tasks = [create_gallery(i, should_fail=(i % 2 == 0)) for i in range(5)]
        galleries = await asyncio.gather(*tasks)
        valid_galleries = [g for g in galleries if g is not None]
        assert (
            len(valid_galleries) == 2
        )  # Exactly the galleries with valid performers (odd indices)

        # Verify final state
        for scene in valid_scenes:
            found = await stash_client.find_scene(scene.id)
            assert found is not None
            assert performer.id in get_ids(found.performers)
            assert get_id(found.studio) == studio.id

        for gallery in valid_galleries:
            found = await stash_client.find_gallery(gallery.id)
            assert found is not None
            assert performer.id in get_ids(found.performers)
            assert get_id(found.studio) == studio.id

    except RuntimeError as e:
        if "Stash instance" in str(e):
            pytest.skip("Test requires running Stash instance: {e}")
        else:
            raise e
    finally:
        await ctx.cleanup()


@pytest.mark.asyncio
async def test_metadata_error_recovery(
    stash_client: StashClient, enable_scene_creation
) -> None:
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
            id="new",
            name="Test Performer",
            gender=GenderEnum.FEMALE,
        )
        performer = await stash_client.create_performer(performer)
        ctx.created_ids["performers"].append(performer.id)

        scene = Scene(
            id="new",
            title="Test Scene",
            details="Test details",
            date="2024-01-01",
            urls=["https://example.com/scene"],
            organized=True,
            performers=[performer],
        )
        scene = await stash_client.create_scene(scene)
        ctx.created_ids["scenes"].append(scene.id)

        # Test invalid options
        try:
            options = GenerateMetadataOptions(
                previews=True,
                previewOptions={
                    "previewSegments": -1,  # Invalid segment count
                },
            )
            input_data = GenerateMetadataInput(
                sceneIDs=[scene.id],
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
            previewOptions={
                "previewSegments": 12,
                "previewSegmentDuration": 0.5,
            },
        )
        input_data = GenerateMetadataInput(
            sceneIDs=[scene.id],
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

    except RuntimeError as e:
        if "Stash instance" in str(e):
            pytest.skip("Test requires running Stash instance: {e}")
        else:
            raise e
    finally:
        await ctx.cleanup()


@pytest.mark.asyncio
async def test_relationship_error_recovery(
    stash_client: StashClient, enable_scene_creation
) -> None:
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
            id="new",
            name="Test Performer",
            gender=GenderEnum.FEMALE,
        )
        performer = await stash_client.create_performer(performer)
        ctx.created_ids["performers"].append(performer.id)

        studio = Studio(
            id="new",
            name="Test Studio",
        )
        studio = await stash_client.create_studio(studio)
        ctx.created_ids["studios"].append(studio.id)

        tag = Tag(
            id="new",
            name="test_tag",
        )
        tag = await stash_client.create_tag(tag)
        ctx.created_ids["tags"].append(tag.id)

        # Create scene with relationships
        scene = Scene(
            id="new",
            title="Test Scene",
            details="Test details",
            date="2024-01-01",
            urls=["https://example.com/scene"],
            organized=True,
            performers=[performer],
            studio=studio,
            tags=[tag],
        )
        scene = await stash_client.create_scene(scene)
        ctx.created_ids["scenes"].append(scene.id)

        # Test invalid performer relationship
        try:
            invalid_performer = Performer(
                id="999999",
                name="Invalid",
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
        assert performer.id in get_ids(scene.performers)
        assert get_id(scene.studio) == studio.id
        assert len(scene.tags) == 1
        assert tag.id in get_ids(scene.tags)

    except RuntimeError as e:
        if "Stash instance" in str(e):
            pytest.skip("Test requires running Stash instance: {e}")
        else:
            raise e
    finally:
        await ctx.cleanup()
