"""Integration tests for error recovery and data validation.

These tests require a running Stash instance.
"""

import asyncio
from datetime import UTC, datetime

import pytest

from stash import StashClient
from stash.types import (
    Gallery,
    GenderEnum,
    GenerateMetadataInput,
    GenerateMetadataOptions,
    Performer,
    Scene,
    Studio,
    Tag,
)


def get_id(obj):
    """Get ID from either a dict or an object with id attribute."""
    if isinstance(obj, dict):
        return obj.get("id")
    return getattr(obj, "id", None)


def get_ids(objects):
    """Get set of IDs from list of dicts or objects."""
    return {get_id(obj) for obj in objects}


def get_attribute(obj, attr_name):
    """Get attribute from either a dict or an object."""
    if isinstance(obj, dict):
        return obj.get(attr_name)
    return getattr(obj, attr_name, None)


def get_attribute_list(obj, attr_name):
    """Get attribute list from either a dict or an object."""
    attr = get_attribute(obj, attr_name)
    if attr is None:
        return []
    return attr


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
        performer_id = get_id(performer)
        ctx.created_ids["performers"].append(performer_id)

        # Test duplicate studio creation
        studio_name = "test_duplicate_studio"
        studio1 = Studio(
            id="new",
            name=studio_name,
        )
        studio1 = await stash_client.create_studio(studio1)
        studio1_id = get_id(studio1)
        ctx.created_ids["studios"].append(studio1_id)

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
        studio_id = get_id(studio)
        ctx.created_ids["studios"].append(studio_id)

        # Test duplicate tag creation - Stash returns existing tag
        tag_name = "test_duplicate_tag"
        tag1 = Tag(
            id="new",
            name=tag_name,
            description="First tag",  # Add description to differentiate
        )
        tag1 = await stash_client.create_tag(tag1)
        tag1_id = get_id(tag1)
        ctx.created_ids["tags"].append(tag1_id)

        # Try to create another tag with the same name
        tag2 = Tag(
            id="new",
            name=tag_name,  # Same name
            description="Second tag",  # Different description
        )
        tag2 = await stash_client.create_tag(tag2)

        # Should get back the first tag
        assert get_id(tag2) == tag1_id
        assert get_attribute(tag2, "description") == get_attribute(
            tag1, "description"
        )  # Description unchanged

        # Create valid tag
        tag = Tag(
            id="new",
            name="[TEST] Data Validation - Tag",
            description="Created by error recovery test",
        )
        tag = await stash_client.create_tag(tag)
        tag_id = get_id(tag)
        ctx.created_ids["tags"].append(tag_id)

        # Create scene with valid fields
        scene = Scene(
            id="new",
            title="[TEST] Data Validation - Scene",
            details="Created by data validation test",
            date=datetime.now(UTC).strftime("%Y-%m-%d"),
            urls=["https://test.example.com/data-validation/scene"],
            organized=True,
            performers=[performer],
            studio=studio,
            tags=[tag],
        )
        scene = await stash_client.create_scene(scene)
        scene_id = get_id(scene)
        ctx.created_ids["scenes"].append(scene_id)

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
        gallery_id = get_id(gallery)
        ctx.created_ids["galleries"].append(gallery_id)

        # Test relationship validation
        invalid_performer = Performer(
            id="999999",  # Non-existent ID
            name="Invalid",
        )

        # Handle both object and dict cases for performers
        if isinstance(scene, dict):
            print(f"Scene dict: {scene}")
            if "performers" in scene:
                scene["performers"].append(
                    invalid_performer.__dict__
                    if hasattr(invalid_performer, "__dict__")
                    else invalid_performer
                )
        else:
            scene.performers.append(invalid_performer)
            scene.__is_dirty__ = True

        print(f"Scene performers: {scene.performers}")
        print(f"Scene: {scene}")
        print(f"Scene to_input(): {await scene.to_input()}")
        try:
            await stash_client.update_scene(scene)
            pytest.fail("Should have failed with invalid performer")
        except Exception:
            # Remove invalid performer and retry
            if isinstance(scene, dict):
                scene["performers"] = [
                    performer.__dict__ if hasattr(performer, "__dict__") else performer
                ]
            else:
                scene.performers = [performer]
            scene = await stash_client.update_scene(scene)

        # Verify data integrity
        scene = await stash_client.find_scene(scene_id)
        assert scene is not None

        performers = get_attribute_list(scene, "performers")
        assert performer_id in get_ids(performers)

        scene_studio = get_attribute(scene, "studio")
        assert get_id(scene_studio) == studio_id

        tags = get_attribute_list(scene, "tags")
        assert tag_id in get_ids(tags)

        gallery = await stash_client.find_gallery(gallery_id)
        assert gallery is not None

        gallery_performers = get_attribute_list(gallery, "performers")
        assert performer_id in get_ids(gallery_performers)

        gallery_studio = get_attribute(gallery, "studio")
        assert get_id(gallery_studio) == studio_id

        gallery_tags = get_attribute_list(gallery, "tags")
        assert tag_id in get_ids(gallery_tags)

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
            name="[TEST] Concurrent Error - Test Performer",
            gender=GenderEnum.FEMALE,
        )
        performer = await stash_client.create_performer(performer)
        ctx.created_ids["performers"].append(performer.id)

        studio = Studio(
            id="new",
            name="[TEST] Concurrent Error - Test Studio",
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
                ctx.created_ids["scenes"].append(
                    created.id
                )  # Use attribute notation instead of dictionary access
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
        # Create test performer first and verify it exists
        performer = Performer(
            id="new",
            name="[TEST] Metadata Error - Performer",  # Unique name to avoid conflicts
            gender=GenderEnum.FEMALE,
        )
        performer = await stash_client.create_performer(performer)
        assert performer and performer.id, "Performer creation failed"
        ctx.created_ids["performers"].append(performer.id)

        # Create scene with the verified performer
        scene = Scene(
            id="new",
            title="[TEST] Metadata Error - Scene",  # Unique name to avoid conflicts
            details="Test details",
            date="2024-01-01",
            urls=["https://example.com/scene"],
            organized=True,
            performers=[performer],
        )
        scene = await stash_client.create_scene(scene)
        assert scene and scene.id, "Scene creation failed"
        ctx.created_ids["scenes"].append(scene.id)

        # Test metadata generation with invalid paths
        try:
            options = GenerateMetadataOptions(
                previews=True,
                previewOptions={
                    "previewSegments": 12,
                    "previewSegmentDuration": 0.5,
                },
            )
            input_data = GenerateMetadataInput(
                sceneIDs=[scene.id],
                paths=["/nonexistent/path"],  # Invalid path should cause error
            )
            await stash_client.metadata_generate(options, input_data)
            pytest.fail("Should have failed with invalid path")
        except Exception as e:
            print(f"Expected failure with invalid path: {e}")

        # Test valid metadata generation
        input_data = GenerateMetadataInput(
            sceneIDs=[scene.id],
            previews=True,
        )
        job_id = await stash_client.metadata_generate(options, input_data)
        assert job_id is not None, "Should return a job ID for valid generation"

        # Don't wait for job completion since we just want to test error handling
        # Verify scene still exists
        scene = await stash_client.find_scene(scene.id)
        assert scene is not None

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
        # Create test data with unique names to avoid conflicts
        performer = Performer(
            id="new",
            name="[TEST] Relationship Error - Performer",  # Unique name
            gender=GenderEnum.FEMALE,
        )
        performer = await stash_client.create_performer(performer)
        assert performer and performer.id, "Performer creation failed"
        performer_id = get_id(performer)
        ctx.created_ids["performers"].append(performer_id)

        studio = Studio(
            id="new",
            name="[TEST] Relationship Error - Studio",  # Unique name
        )
        studio = await stash_client.create_studio(studio)
        assert studio and studio.id, "Studio creation failed"
        studio_id = get_id(studio)
        ctx.created_ids["studios"].append(studio_id)

        tag = Tag(
            id="new",
            name="[TEST] relationship_error_tag",  # Unique name
        )
        tag = await stash_client.create_tag(tag)
        assert tag and tag.id, "Tag creation failed"
        tag_id = get_id(tag)
        ctx.created_ids["tags"].append(tag_id)

        # Create scene with relationships
        scene = Scene(
            id="new",
            title="[TEST] Relationship Error - Scene",  # Unique name
            details="Test details",
            date="2024-01-01",
            urls=["https://example.com/scene"],
            organized=True,
            performers=[performer],
            studio=studio,
            tags=[tag],
        )
        scene = await stash_client.create_scene(scene)
        assert scene and scene.id, "Scene creation failed"
        scene_id = get_id(scene)
        ctx.created_ids["scenes"].append(scene_id)

        # Test invalid performer relationship
        try:
            invalid_performer = Performer(
                id="999999",
                name="Invalid",
            )

            # Handle both object and dict cases
            if isinstance(scene, dict):
                scene["performers"] = [
                    (
                        invalid_performer.__dict__
                        if hasattr(invalid_performer, "__dict__")
                        else invalid_performer
                    )
                ]
            else:
                scene.performers = [invalid_performer]

            await stash_client.update_scene(scene)
            pytest.fail("Should have failed with invalid performer")
        except Exception as e:
            print(f"Expected failure with invalid performer: {e}")
            # Restore valid performer
            if isinstance(scene, dict):
                scene["performers"] = [
                    performer.__dict__ if hasattr(performer, "__dict__") else performer
                ]
            else:
                scene.performers = [performer]

            scene = await stash_client.update_scene(scene)

        # Test invalid studio relationship
        try:
            invalid_studio = Studio(
                id="999999",
                name="Invalid",
            )

            # Handle both object and dict cases
            if isinstance(scene, dict):
                scene["studio"] = (
                    invalid_studio.__dict__
                    if hasattr(invalid_studio, "__dict__")
                    else invalid_studio
                )
            else:
                scene.studio = invalid_studio

            await stash_client.update_scene(scene)
            pytest.fail("Should have failed with invalid studio")
        except Exception as e:
            print(f"Expected failure with invalid studio: {e}")
            # Restore valid studio
            if isinstance(scene, dict):
                scene["studio"] = (
                    studio.__dict__ if hasattr(studio, "__dict__") else studio
                )
            else:
                scene.studio = studio

            scene = await stash_client.update_scene(scene)

        # Test invalid tag relationship
        try:
            invalid_tag = Tag(
                id="999999",
                name="invalid",
            )

            # Handle both object and dict cases
            if isinstance(scene, dict):
                scene["tags"] = [
                    (
                        invalid_tag.__dict__
                        if hasattr(invalid_tag, "__dict__")
                        else invalid_tag
                    )
                ]
            else:
                scene.tags = [invalid_tag]

            await stash_client.update_scene(scene)
            pytest.fail("Should have failed with invalid tag")
        except Exception as e:
            print(f"Expected failure with invalid tag: {e}")
            # Restore valid tag
            if isinstance(scene, dict):
                scene["tags"] = [tag.__dict__ if hasattr(tag, "__dict__") else tag]
            else:
                scene.tags = [tag]

            scene = await stash_client.update_scene(scene)

        # Verify final state
        scene = await stash_client.find_scene(scene_id)
        assert scene is not None

        performers = get_attribute_list(scene, "performers")
        assert len(performers) == 1
        assert performer_id in get_ids(performers)

        scene_studio = get_attribute(scene, "studio")
        assert get_id(scene_studio) == studio_id

        tags = get_attribute_list(scene, "tags")
        assert len(tags) == 1
        assert tag_id in get_ids(tags)

    except RuntimeError as e:
        if "Stash instance" in str(e):
            pytest.skip("Test requires running Stash instance: {e}")
        else:
            raise e
    finally:
        await ctx.cleanup()
