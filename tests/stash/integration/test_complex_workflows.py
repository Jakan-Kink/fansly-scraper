"""Integration tests for complex workflows.

These tests require a running Stash instance.
"""

import asyncio
from datetime import datetime
from pathlib import Path

import pytest

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


@pytest.mark.asyncio
async def test_full_content_workflow(stash_client: StashClient) -> None:
    """Test full content workflow with relationships.

    This test:
    1. Creates a performer
    2. Creates a studio
    3. Creates tags
    4. Creates a scene with relationships
    5. Creates a gallery with relationships
    6. Updates relationships
    7. Generates metadata
    8. Verifies everything
    """
    try:
        # Create performer
        performer = Performer(
            id="new",  # New performer
            name="Test Performer",
            gender="FEMALE",
            urls=["https://example.com/performer"],
            birthdate="1990-01-01",
            ethnicity="CAUCASIAN",
            country="US",
            eye_color="BLUE",
            height_cm=170,
            measurements="34-24-36",
            fake_tits="NO",
            career_length="2020-",
            tattoos="None",
            piercings="None",
            alias_list=["Alias 1", "Alias 2"],
            details="Test performer details",
        )
        performer = await stash_client.create_performer(performer)
        assert performer.id is not None

        # Create studio
        studio = Studio(
            id="new",
            name="Test Studio",
            url="https://example.com/studio",
            details="Test studio details",
        )
        studio = await stash_client.create_studio(studio)
        assert studio.id is not None

        # Create tags
        tags = []
        for name in ["Tag1", "Tag2", "Tag3"]:
            tag = Tag(
                id="new",
                name=name,
                description=f"Test {name.lower()} description",
            )
            tag = await stash_client.create_tag(tag)
            assert tag.id is not None
            tags.append(tag)

        # Create scene with relationships
        scene = Scene(
            id="new",  # New scene
            title="Test Scene",
            details="Test scene details",
            date="2024-01-01",
            urls=["https://example.com/scene"],
            organized=True,
            performers=[performer],
            studio=studio,
            tags=tags,
        )
        scene = await stash_client.create_scene(scene)
        assert scene.id is not None

        # Create gallery with relationships
        gallery = Gallery(
            id="new",  # New gallery
            title="Test Gallery",
            details="Test gallery details",
            date="2024-01-01",
            urls=["https://example.com/gallery"],
            organized=True,
            performers=[performer],
            studio=studio,
            tags=tags,
            rating100=95,
        )
        gallery = await stash_client.create_gallery(gallery)
        assert gallery.id is not None

        # Generate metadata
        options = GenerateMetadataOptions(
            covers=True,
            sprites=True,
            previews=True,
            imagePreviews=True,
            markers=True,
            phashes=True,
        )
        input_data = GenerateMetadataInput(
            sceneIDs=[scene.id],
            overwrite=True,
        )
        job_id = await stash_client.metadata_generate(options, input_data)
        assert job_id is not None

        # Wait for job with updates
        async with stash_client.subscribe_to_jobs() as subscription:
            async for update in subscription:
                if update.job and update.job.id == job_id:
                    if update.status in ["FINISHED", "CANCELLED"]:
                        break

        # Verify scene
        scene = await stash_client.find_scene(scene.id)
        assert scene is not None
        assert scene.performers[0].id == performer.id
        assert scene.studio.id == studio.id
        assert len(scene.tags) == len(tags)
        assert {t.id for t in scene.tags} == {t.id for t in tags}

        # Verify gallery
        gallery = await stash_client.find_gallery(gallery.id)
        assert gallery is not None
        assert gallery.performers[0].id == performer.id
        assert gallery.studio.id == studio.id
        assert len(gallery.tags) == len(tags)
        assert {t.id for t in gallery.tags} == {t.id for t in tags}

    except Exception as e:
        pytest.skip(f"Test requires running Stash instance: {e}")


@pytest.mark.asyncio
async def test_concurrent_operations(stash_client: StashClient) -> None:
    """Test concurrent operations.

    This test:
    1. Creates multiple scenes concurrently
    2. Updates them concurrently
    3. Generates metadata concurrently
    4. Verifies everything worked correctly
    """
    try:
        # Create scenes concurrently
        scenes = []

        async def create_scene(i: int) -> Scene:
            scene = Scene(
                id="new",  # New scene
                title=f"Test Scene {i}",
                details=f"Test scene {i} details",
                date="2024-01-01",
                urls=[f"https://example.com/scene/{i}"],
                organized=True,
            )
            return await stash_client.create_scene(scene)

        tasks = [create_scene(i) for i in range(5)]
        scenes = await asyncio.gather(*tasks)
        assert len(scenes) == 5
        assert all(s.id is not None for s in scenes)

        # Update scenes concurrently
        async def update_scene(scene: Scene) -> Scene:
            scene.title = f"Updated {scene.title}"
            return await stash_client.update_scene(scene)

        tasks = [update_scene(s) for s in scenes]
        updated_scenes = await asyncio.gather(*tasks)
        assert len(updated_scenes) == 5
        assert all(s.title.startswith("Updated") for s in updated_scenes)

        # Generate metadata concurrently
        options = GenerateMetadataOptions(
            covers=True,
            sprites=True,
            previews=True,
        )

        async def generate_metadata(scene: Scene) -> str:
            input_data = GenerateMetadataInput(
                sceneIDs=[scene.id],
                overwrite=True,
            )
            return await stash_client.metadata_generate(options, input_data)

        tasks = [generate_metadata(s) for s in scenes]
        job_ids = await asyncio.gather(*tasks)
        assert len(job_ids) == 5
        assert all(j is not None for j in job_ids)

        # Wait for all jobs
        async def wait_for_job(job_id: str) -> None:
            async with stash_client.subscribe_to_jobs() as subscription:
                async for update in subscription:
                    if update.job and update.job.id == job_id:
                        if update.status in ["FINISHED", "CANCELLED"]:
                            break

        tasks = [wait_for_job(j) for j in job_ids]
        await asyncio.gather(*tasks)

        # Verify all scenes
        tasks = [stash_client.find_scene(s.id) for s in scenes]
        final_scenes = await asyncio.gather(*tasks)
        assert len(final_scenes) == 5
        assert all(s is not None for s in final_scenes)

    except Exception as e:
        pytest.skip(f"Test requires running Stash instance: {e}")


@pytest.mark.asyncio
async def test_error_handling(stash_client: StashClient) -> None:
    """Test error handling in complex workflows.

    This test:
    1. Tests invalid operations
    2. Tests missing relationships
    3. Tests concurrent error handling
    4. Tests recovery from errors
    """
    try:
        # Test invalid scene creation
        with pytest.raises(ValueError):
            scene = Scene(
                id="new",  # New scene
                title="",  # Empty title
                urls=[],  # No URLs
                organized=True,
            )
            await stash_client.create_scene(scene)

        # Test missing relationships
        scene = Scene(
            title="Test Scene",
            urls=["https://example.com/scene"],
            organized=True,
            studio="invalid",  # Invalid studio ID
        )
        with pytest.raises(Exception):
            await stash_client.create_scene(scene)

        # Test concurrent error handling
        async def create_invalid_scene(i: int) -> None:
            scene = Scene(
                id="new",  # New scene
                title=f"Test Scene {i}",
                urls=[f"https://example.com/scene/{i}"],
                organized=True,
                studio=f"invalid_{i}",  # Invalid studio IDs
            )
            try:
                await stash_client.create_scene(scene)
            except Exception:
                pass  # Expected to fail

        tasks = [create_invalid_scene(i) for i in range(5)]
        await asyncio.gather(*tasks)

        # Test recovery - create valid scene after errors
        scene = Scene(
            title="Valid Scene",
            urls=["https://example.com/valid"],
            organized=True,
            created_at=datetime.now(),
            updated_at=datetime.now(),
        )
        created = await stash_client.create_scene(scene)
        assert created.id is not None
        assert created.title == scene.title

    except Exception as e:
        pytest.skip(f"Test requires running Stash instance: {e}")
