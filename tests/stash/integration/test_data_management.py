"""Integration tests for data management scenarios.

These tests require a running Stash instance.
"""

import asyncio
from collections.abc import AsyncGenerator
from datetime import datetime
from pathlib import Path
from pprint import pformat
from typing import List

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
    SceneCreateInput,
    Studio,
    Tag,
)


async def create_test_data(
    stash_client: StashClient,
    prefix: str = "test",
) -> tuple[Performer, Studio, list[Tag], list[Scene]]:
    """Create test data for cleanup."""
    # Enable scene creation
    Scene.__create_input_type__ = SceneCreateInput
    timestamp = datetime.now().timestamp()

    # Create performer
    performer = Performer(
        id="new",
        name=f"{prefix}_performer_{timestamp}",
        gender=GenderEnum.FEMALE,
        urls=["https://example.com/performer"],
        birthdate="1990-01-01",
    )
    performer = await stash_client.create_performer(performer)

    # Create studio
    studio = Studio(
        id="new",
        name=f"{prefix}_studio_{timestamp}",
    )
    studio = await stash_client.create_studio(studio)

    # Create tags
    tags = []
    for i in range(3):
        tag = Tag(
            id="new",
            name=f"{prefix}_tag_{i}_{timestamp}",
        )
        tag = await stash_client.create_tag(tag)
        tags.append(tag)

    # Create scenes
    scenes = []
    for i in range(2):
        scene = Scene(
            id="new",
            title=f"{prefix}_scene_{i}_{timestamp}",
            date="2025-04-12",
            details=f"Test scene {i}",
            studio=studio,
            urls=[f"https://example.com/{prefix}/scene_{i}"],
            performers=[performer],
            tags=tags,
            code="",
            organized=True,
        )
        scene = await stash_client.create_scene(scene)
        scenes.append(scene)

    return performer, studio, tags, scenes


@pytest.mark.asyncio
async def test_tag_cleanup_workflow(
    stash_client: StashClient, stash_cleanup_tracker, enable_scene_creation
) -> None:
    """Test tag cleanup workflow.

    This test:
    1. Creates test data with tags
    2. Simulates tag usage
    3. Merges duplicate tags
    4. Updates tag hierarchy
    5. Cleans up unused tags
    """
    try:
        async with stash_cleanup_tracker(stash_client) as cleanup:
            # Create initial data
            performer, studio, tags, scenes = await create_test_data(
                stash_client, prefix="tag_cleanup"
            )
            cleanup["performers"].append(performer.id)
            cleanup["studios"].append(studio.id)
            for tag in tags:
                cleanup["tags"].append(tag.id)
            for scene in scenes:
                cleanup["scenes"].append(scene.id)

            # Create duplicate tags
            duplicate_tags = []
            for tag in tags:
                dup_tag = Tag(
                    id="new",
                    name=f"{tag.name}_duplicate",
                    description=tag.description,
                )
                dup_tag = await stash_client.create_tag(dup_tag)
                duplicate_tags.append(dup_tag)

            # Add duplicate tags to scenes
            for scene in scenes:
                scene.tags.extend(duplicate_tags)
                await stash_client.update_scene(scene)

            # Merge duplicate tags
            for orig, dup in zip(tags, duplicate_tags):
                merged_tag = await stash_client.tags_merge(
                    source=[dup.id], destination=orig.id
                )
                assert merged_tag is not None

                # Verify scenes have merged tag
                for scene in scenes:
                    updated = await stash_client.find_scene(scene.id)
                    assert any(t["id"] == orig.id for t in updated.tags)
                    assert not any(t["id"] == dup.id for t in updated.tags)

            # Create tag hierarchy
            parent_tag = Tag(
                id="new",
                name="tag_cleanup_parent",
            )
            parent_tag = await stash_client.create_tag(parent_tag)

            # Update tags with parent
            for tag in tags:
                tag.parents = [parent_tag]
                updated = await stash_client.update_tag(tag)
                assert updated.parents[0]["id"] == parent_tag.id

            print(f"Parent Tag: \n{pformat(parent_tag)}")
            print(f"Tags: \n{pformat(tags)}")
            # Verify hierarchy
            parent = await stash_client.find_tag(parent_tag.id)
            print(f"Parent: {parent}")
            assert len(parent.children) == len(tags)
            assert all(t.id in [c["id"] for c in parent.children] for t in tags)

    except (ConnectionError, TimeoutError) as e:
        pytest.skip(
            f"Connection error - test requires running Stash instance: {str(e)}"
        )
    except Exception as e:
        # Re-raise other exceptions that aren't connection-related
        raise e


@pytest.mark.asyncio
async def test_performer_merge_workflow(
    stash_client: StashClient, stash_cleanup_tracker, enable_scene_creation
) -> None:
    """Test performer merge workflow.

    This test:
    1. Creates test performers
    2. Creates content for each
    3. Merges performers
    4. Verifies content is properly merged
    5. Cleans up
    """
    try:
        async with stash_cleanup_tracker(stash_client) as cleanup:
            # Create performers
            performers = []
            for i in range(2):
                performer = Performer(
                    id="new",
                    name=f"merge_performer_{i}",
                    gender=GenderEnum.FEMALE,  # Pass enum directly, not its value
                    urls=[f"https://example.com/performer/merge_{i}"],
                )
                performer = await stash_client.create_performer(performer)
                performers.append(performer)
                cleanup["performers"].append(performer.id)

            # Create content for each performer
            scenes_by_performer = {}
            for performer in performers:
                new_performer, studio, tags, scenes = await create_test_data(
                    stash_client,
                    prefix=f"performer_{performer.id}",  # Use performer ID instead of name to avoid prefix duplication
                )
                cleanup["performers"].append(
                    new_performer.id
                )  # Track the additional performer
                scenes_by_performer[performer.id] = scenes
                cleanup["studios"].append(studio.id)
                for tag in tags:
                    cleanup["tags"].append(tag.id)
                for scene in scenes:
                    cleanup["scenes"].append(scene.id)

            # Merge performers (manually since there's no direct merge API)
            main_performer = performers[0]

            # Update all scenes from both performers to use main performer
            for performer_id, scenes in scenes_by_performer.items():
                for scene in scenes:
                    scene.performers = [main_performer]
                    updated = await stash_client.update_scene(scene)
                    assert updated.performers[0]["id"] == main_performer.id

            # Verify merge
            all_scenes = await stash_client.find_scenes(
                scene_filter={
                    "performers": {
                        "value": [main_performer.id],
                        "modifier": "INCLUDES",
                    }
                }
            )
            # Should have all scenes from both performers
            total_scenes = sum(len(scenes) for scenes in scenes_by_performer.values())
            assert all_scenes.count == total_scenes

    except (ConnectionError, TimeoutError) as e:
        pytest.skip(
            f"Connection error - test requires running Stash instance: {str(e)}"
        )
    except Exception as e:
        # Re-raise other exceptions that aren't connection-related
        raise e


@pytest.mark.asyncio
async def test_studio_hierarchy_workflow(
    stash_client: StashClient, stash_cleanup_tracker, enable_scene_creation
) -> None:
    """Test studio hierarchy workflow.

    This test:
    1. Creates studio hierarchy
    2. Creates content at different levels
    3. Updates hierarchy
    4. Verifies inheritance
    5. Cleans up
    """
    try:
        async with stash_cleanup_tracker(stash_client) as cleanup:
            # Create parent level content and studio
            timestamp = datetime.now().timestamp()
            performer, parent_studio, tags, parent_scenes = await create_test_data(
                stash_client,
                prefix="parent_studio",
            )
            cleanup["performers"].append(performer.id)
            cleanup["studios"].append(parent_studio.id)
            for tag in tags:
                cleanup["tags"].append(tag.id)
            for scene in parent_scenes:
                cleanup["scenes"].append(scene.id)

            # Create child studios and their content
            child_studios = []
            for i in range(2):
                # First create child studio and content
                child_perf, child_studio, child_tags, child_scenes = (
                    await create_test_data(
                        stash_client,
                        prefix=f"child_studio_{i}_{timestamp}",
                    )
                )

                # Set up parent relationship
                child_studio.parent_studio = {"id": parent_studio.id}
                child_studio = await stash_client.update_studio(child_studio)
                child_studios.append(child_studio)

                # Track all resources
                cleanup["performers"].append(child_perf.id)
                cleanup["studios"].append(child_studio.id)
                for tag in child_tags:
                    cleanup["tags"].append(tag.id)
                for scene in child_scenes:
                    cleanup["scenes"].append(scene.id)

                # Verify parent was set correctly
                created_studio = await stash_client.find_studio(child_studio.id)
                print(f"Created Studio {i}: {created_studio}")  # Debug output
                assert created_studio.parent_studio is not None
                assert created_studio.parent_studio["id"] == parent_studio.id

            # Verify hierarchy
            scenes_by_studio = {}
            for studio in [parent_studio] + child_studios:
                scenes = await stash_client.find_scenes(
                    scene_filter={
                        "studios": {"value": [studio.id], "modifier": "INCLUDES"}
                    }
                )
                scenes_by_studio[studio.id] = scenes.scenes

            print(f"Scenes by Studio: \n{pformat(scenes_by_studio)}")
            print(f"Parent Studio: \n{pformat(parent_studio)}")
            print(f"Child Studios: \n{pformat(child_studios)}")
            # Verify hierarchy
            for studio_id, scenes in scenes_by_studio.items():
                for scene in scenes:
                    updated = await stash_client.find_scene(scene["id"])
                    if studio_id != parent_studio.id:
                        # Get full studio details since find_scene only returns studio ID
                        scene_studio = await stash_client.find_studio(
                            updated.studio["id"]
                        )
                        assert (
                            scene_studio.parent_studio
                            and scene_studio.parent_studio["id"] == parent_studio.id
                        )

            # Move child studio content to parent
            for studio in child_studios:
                scenes = scenes_by_studio[studio.id]
                for scene in scenes:
                    scene = Scene(**scene)
                    scene.studio = parent_studio
                    updated = await stash_client.update_scene(scene)
                    assert updated.studio["id"] == parent_studio.id

            # Verify all content moved
            parent_scenes = await stash_client.find_scenes(
                scene_filter={
                    "studios": {
                        "value": [parent_studio.id],
                        "modifier": "INCLUDES",
                    }
                }
            )
            total_scenes = sum(len(scenes) for scenes in scenes_by_studio.values())
            assert parent_scenes.count == total_scenes

    except (ConnectionError, TimeoutError) as e:
        pytest.skip(
            f"Connection error - test requires running Stash instance: {str(e)}"
        )
    except Exception as e:
        # Re-raise other exceptions that aren't connection-related
        raise e


@pytest.mark.asyncio
async def test_duplicate_management_workflow(
    stash_client: StashClient, stash_cleanup_tracker, enable_scene_creation
) -> None:
    """Test duplicate content management workflow.

    This test:
    1. Creates similar content
    2. Finds duplicates
    3. Merges duplicates
    4. Verifies cleanup
    """
    try:
        async with stash_cleanup_tracker(stash_client) as cleanup:
            # Create base content
            performer, studio, tags, _ = await create_test_data(
                stash_client,
                prefix="duplicate",
            )
            cleanup["performers"].append(performer.id)
            cleanup["studios"].append(studio.id)
            for tag in tags:
                cleanup["tags"].append(tag.id)

            # Create similar scenes with timestamps to ensure uniqueness
            timestamp = datetime.now().timestamp()
            scenes = []
            for i in range(2):  # Create only 2 initial scenes
                scene = Scene(
                    id="new",
                    title=f"duplicate_scene_{i}_{timestamp}",
                    details=f"Test scene {i}",
                    date=datetime.now().strftime("%Y-%m-%d"),
                    urls=[f"https://example.com/duplicate/scene_{i}"],
                    organized=True,
                    performers=[performer],
                    studio=studio,
                    tags=tags,
                )
                scene = await stash_client.create_scene(scene)
                scenes.append(scene)
                cleanup["scenes"].append(scene.id)  # Track original scenes

            # Create duplicate scenes with different titles
            duplicate_scenes = []
            for i in range(3):  # Create 3 duplicates
                scene = Scene(
                    id="new",
                    title=f"duplicate_scene_{i}",
                    details="Same content, different title",
                    date=datetime.now().strftime("%Y-%m-%d"),
                    urls=[f"https://example.com/duplicate/scene_{i}"],
                    organized=True,
                    performers=[performer],
                    studio=studio,
                    tags=tags,
                )
                scene = await stash_client.create_scene(scene)
                duplicate_scenes.append(scene)
                cleanup["scenes"].append(scene.id)  # Track duplicate scenes

            # Find duplicates
            duplicates = await stash_client.find_duplicate_scenes(
                distance=100,  # More lenient for testing
                duration_diff=1.0,
            )

            # Group duplicates
            if duplicates:
                for group in duplicates:
                    if len(group) > 1:
                        # Keep first scene, merge others into it
                        main_scene = group[0]
                        merge_scenes = group[1:]

                        # Update main scene
                        main_scene.title = "Merged Scene"
                        main_scene.details += "\nMerged from duplicates"
                        updated = await stash_client.update_scene(main_scene)
                        assert updated.title == "Merged Scene"

                        # Could delete merged scenes here if we had the API
                        # For now, just mark them
                        for scene in merge_scenes:
                            scene.title = f"DUPLICATE - {scene.title}"
                            scene.organized = False
                            await stash_client.update_scene(scene)

            # Verify results
            final_scenes = await stash_client.find_scenes(
                scene_filter={
                    "organized": True,
                    "studios": {"value": [studio.id], "modifier": "INCLUDES"},
                    "performers": {"value": [performer.id], "modifier": "INCLUDES"},
                    "title": {
                        "value": str(
                            timestamp
                        ),  # Use exact timestamp to ensure we only get scenes from this test run
                        "modifier": "INCLUDES",
                    },
                }
            )
            # Should only have 2 organized scenes after marking duplicates - the original timestamped ones
            assert final_scenes.count == 2

            # Verify all scenes still exist but some are marked unorganized
            all_scenes = await stash_client.find_scenes(
                scene_filter={
                    "performers": {"value": [performer.id], "modifier": "INCLUDES"},
                    "title": {"value": str(timestamp), "modifier": "INCLUDES"},
                }
            )
            assert all_scenes.count == 2  # Only the original timestamped scenes

    except (ConnectionError, TimeoutError) as e:
        pytest.skip(
            f"Connection error - test requires running Stash instance: {str(e)}"
        )
    except Exception as e:
        # Re-raise other exceptions that aren't connection-related
        raise e
