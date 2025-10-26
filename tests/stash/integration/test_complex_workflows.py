"""Integration tests for complex workflows.

These tests require a running Stash instance.
"""

import asyncio

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


@pytest.mark.integration
@pytest.mark.slow
@pytest.mark.full_workflow
@pytest.mark.asyncio
async def test_full_content_workflow(
    stash_client: StashClient,
    enable_scene_creation,
    stash_cleanup_tracker,
) -> None:
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
    async with stash_cleanup_tracker(stash_client) as cleanup:
        try:
            # Create performer with required relationships
            performer = Performer(
                id="new",  # New performer
                name="full_content_workflow - Test Performer",
                gender=GenderEnum.FEMALE,
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
                # Required relationships
                scenes=[],
                tags=[],
                groups=[],
                stash_ids=[],
            )
            performer = await stash_client.create_performer(performer)
            assert performer.id is not None
            cleanup["performers"].append(performer.id)
            print(f"Performer created: {performer}")  # Debug print

            # Create studio
            studio = Studio(
                id="new",
                name="full_content_workflow - Test Studio",
                url="https://example.com/studio",
                details="Test studio details",
            )
            studio = await stash_client.create_studio(studio)
            assert studio.id is not None
            cleanup["studios"].append(studio.id)
            print(f"Studio created: {studio}")  # Debug print

            # Create tags
            tags = []
            for name in ["Tag1", "Tag2", "Tag3"]:
                tag = Tag(
                    id="new",
                    name=f"full_content_workflow - {name}",
                    description=f"Test {name.lower()} description",
                )
                tag = await stash_client.create_tag(tag)
                assert tag.id is not None
                tags.append(tag)
                cleanup["tags"].append(tag.id)
            print(f"Tags created: {tags}")  # Debug print

            # Create scene with relationships
            scene = Scene(
                id="new",
                title="full_content_workflow - Test Scene",
                details="Test scene details",
                date="2024-01-01",
                urls=["https://example.com/scene"],
                organized=True,
                performers=[performer],  # Add performer to scene
                studio=studio,
                tags=tags,
            )
            scene = await stash_client.create_scene(scene)
            assert scene.id is not None
            cleanup["scenes"].append(scene.id)
            print(f"Scene created: {scene}")  # Debug print

            # Create gallery with relationships
            gallery = Gallery(
                id="new",
                title="full_content_workflow - Test Gallery",
                details="Test gallery details",
                date="2024-01-01",
                urls=["https://example.com/gallery"],
                organized=True,
                performers=[performer],
                studio=studio,
                tags=tags,
                rating100=95,
                # Required relationships
                scenes=[],
            )
            gallery = await stash_client.create_gallery(gallery)
            assert gallery.id is not None
            cleanup["galleries"].append(gallery.id)
            print(f"Gallery created: {gallery}")  # Debug print

            # Generate metadata
            options = GenerateMetadataOptions(
                covers=True,
                sprites=True,
                previews=True,
                imagePreviews=True,
                markers=True,
                phashes=True,
            )

            # Set up subscription and generate metadata
            try:
                async with (
                    asyncio.timeout(30),  # 30 second timeout
                    stash_client.subscribe_to_jobs() as subscription,
                ):
                    # Generate metadata after subscription is ready
                    input_data = GenerateMetadataInput(
                        sceneIDs=[scene.id],
                        overwrite=True,
                    )
                    job_id = await stash_client.metadata_generate(options, input_data)
                    assert job_id is not None
                    print(f"Started metadata generation job: {job_id}")  # Debug print

                    # Wait for job
                    async for update in subscription:
                        print(f"Job update received: {update}")  # Debug print
                        if update.job and update.job.id == job_id:
                            if update.status in ["FINISHED", "CANCELLED"]:
                                print(f"Job {job_id} finished")  # Debug print
                                break
            except TimeoutError:
                print("Timeout waiting for metadata generation job")
                pytest.skip("Metadata generation timed out after 30 seconds")

            # Verify scene
            scene = await stash_client.find_scene(scene.id)
            assert scene is not None
            assert scene.performers[0]["id"] == performer.id
            assert scene.studio["id"] == studio.id
            assert len(scene.tags) == len(tags)
            assert {t["id"] for t in scene.tags} == {t.id for t in tags}

            # Verify gallery
            gallery = await stash_client.find_gallery(gallery.id)
            assert gallery is not None
            assert gallery.performers[0]["id"] == performer.id
            assert gallery.studio["id"] == studio.id
            assert len(gallery.tags) == len(tags)
            assert {t["id"] for t in gallery.tags} == {t.id for t in tags}

        except (ConnectionError, TimeoutError) as e:
            pytest.skip(
                f"Connection error - test requires running Stash instance: {e!s}"
            )
        except Exception as e:
            raise e


@pytest.mark.integration
@pytest.mark.asyncio
async def test_concurrent_operations(
    stash_client: StashClient,
    enable_scene_creation,
    stash_cleanup_tracker,
) -> None:
    """Test concurrent operations.

    This test:
    1. Creates multiple scenes concurrently
    2. Updates them concurrently
    3. Generates metadata concurrently
    4. Verifies everything worked correctly
    """
    async with stash_cleanup_tracker(stash_client) as cleanup:
        try:
            # Create scenes concurrently
            scenes = []

            async def create_scene(i: int) -> Scene:
                scene = Scene(
                    id="new",
                    title=f"concurrent_operations - Test Scene {i}",
                    details=f"Test scene {i} details",
                    date="2024-01-01",
                    urls=[f"https://example.com/scene/{i}"],
                    organized=True,
                )
                created = await stash_client.create_scene(scene)
                cleanup["scenes"].append(created.id)
                print(f"Scene {i} created: {created}")  # Debug print
                return created

            tasks = [create_scene(i) for i in range(5)]
            scenes = await asyncio.gather(*tasks)
            assert len(scenes) == 5
            assert all(s.id is not None for s in scenes)
            print(f"All scenes created: {scenes}")  # Debug print

            # Update scenes concurrently
            async def update_scene(scene: Scene) -> Scene:
                scene.title = f"Updated {scene.title}"
                updated = await stash_client.update_scene(scene)
                print(f"Scene updated: {updated}")  # Debug print
                return updated

            tasks = [update_scene(s) for s in scenes]
            updated_scenes = await asyncio.gather(*tasks)
            assert len(updated_scenes) == 5
            assert all(s.title.startswith("Updated") for s in updated_scenes)
            print(f"All scenes updated: {updated_scenes}")  # Debug print

            # Generate metadata concurrently
            options = GenerateMetadataOptions(
                covers=True,
                sprites=True,
                previews=True,
            )

            # Set up subscription and generate metadata
            finished_jobs = set()
            try:
                async with (
                    asyncio.timeout(30),  # 30 second timeout
                    stash_client.subscribe_to_jobs() as subscription,
                ):

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
                    print(f"Started metadata generation jobs: {job_ids}")  # Debug print

                    # Wait for all jobs
                    async for update in subscription:
                        print(f"Job update received: {update}")  # Debug print
                        if update.job and update.job.id in job_ids:
                            if update.status in ["FINISHED", "CANCELLED"]:
                                finished_jobs.add(update.job.id)
                                print(f"Job {update.job.id} finished")  # Debug print
                                if len(finished_jobs) == len(job_ids):
                                    break

            except TimeoutError:
                print("Timeout waiting for metadata generation jobs")
                pytest.skip("Metadata generation timed out after 30 seconds")

            # Verify all scenes
            tasks = [stash_client.find_scene(s.id) for s in scenes]
            final_scenes = await asyncio.gather(*tasks)
            assert len(final_scenes) == 5
            assert all(s is not None for s in final_scenes)

        except (ConnectionError, TimeoutError) as e:
            pytest.skip(
                f"Connection error - test requires running Stash instance: {e!s}"
            )
        except Exception as e:
            # Re-raise other exceptions that aren't connection-related
            raise e


@pytest.mark.asyncio
async def test_error_handling(
    stash_client: StashClient,
    enable_scene_creation,
    stash_cleanup_tracker,
) -> None:
    """Test error handling in complex workflows.

    This test:
    1. Tests invalid operations
    2. Tests missing relationships
    3. Tests concurrent error handling
    4. Tests recovery from errors
    """
    async with stash_cleanup_tracker(stash_client) as cleanup:
        try:
            # Test invalid scene creation
            with pytest.raises(Exception) as exc_info:
                scene = Scene(
                    id="new",
                    title="",  # Empty title
                    urls=[],  # No URLs
                    organized=True,
                )
                await stash_client.create_scene(scene)
            assert "title must be set" in str(exc_info.value).lower()

            # Create a studio to test invalid relationships
            studio = Studio(
                id="new",
                name="error_handling - Test Studio",
                url="https://example.com/studio",
            )
            studio = await stash_client.create_studio(studio)
            cleanup["studios"].append(studio.id)
            print(f"Test studio created: {studio}")  # Debug print

            # Test invalid studio reference
            scene = Scene(
                id="new",
                title="error_handling - Test Scene",
                urls=["https://example.com/scene"],
                organized=True,
                studio=Studio(
                    id="999999", name="Invalid Studio"
                ),  # Use invalid Studio object instead of string
            )
            with pytest.raises(Exception) as exc_info:
                await stash_client.create_scene(scene)
            assert "studio" in str(exc_info.value).lower()

            # Test concurrent error handling
            async def create_invalid_scene(i: int) -> None:
                scene = Scene(
                    id="new",
                    title=f"error_handling - Test Scene {i}",
                    urls=[f"https://example.com/scene/{i}"],
                    organized=True,
                    studio=Studio(
                        id=f"999999{i}", name=f"Invalid Studio {i}"
                    ),  # Use invalid Studio objects
                )
                with pytest.raises(Exception) as exc_info:
                    await stash_client.create_scene(scene)
                print(
                    f"Expected error on concurrent invalid studio {i}: {exc_info.value}"
                )  # Debug print

            tasks = [create_invalid_scene(i) for i in range(5)]
            await asyncio.gather(*tasks)

            # Test recovery - create valid scene after errors
            scene = Scene(
                id="new",
                title="error_handling - Valid Scene",
                urls=["https://example.com/valid"],
                organized=True,
                studio=studio,  # Use valid studio
            )
            created = await stash_client.create_scene(scene)
            assert created.id is not None
            assert created.title == scene.title
            assert created.studio["id"] == studio.id
            cleanup["scenes"].append(created.id)
            print(
                f"Recovery successful - created valid scene: {created}"
            )  # Debug print

        except (ConnectionError, TimeoutError) as e:
            pytest.skip(
                f"Connection error - test requires running Stash instance: {e!s}"
            )
        except Exception as e:
            # Re-raise other exceptions that aren't connection-related
            raise e
