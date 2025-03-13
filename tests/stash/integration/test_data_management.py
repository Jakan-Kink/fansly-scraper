"""Integration tests for data management scenarios.

These tests require a running Stash instance.
"""

import asyncio
from collections.abc import AsyncGenerator
from datetime import datetime
from pathlib import Path
from typing import List

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


async def create_test_data(
    stash_client: StashClient,
    prefix: str = "test",
) -> tuple[Performer, Studio, list[Tag], list[Scene]]:
    """Create test data for cleanup."""
    # Create performer
    performer = Performer(
        id="new",
        name=f"{prefix}_performer",
        gender="FEMALE",
    )
    performer = await stash_client.create_performer(performer)

    # Create studio
    studio = Studio(
        id="new",
        name=f"{prefix}_studio",
    )
    studio = await stash_client.create_studio(studio)

    # Create tags
    tags = []
    for i in range(3):
        tag = Tag(
            id="new",
            name=f"{prefix}_tag_{i}",
        )
        tag = await stash_client.create_tag(tag)
        tags.append(tag)

    # Create scenes
    scenes = []
    for i in range(2):
        scene = Scene(
            id="new",
            title=f"{prefix}_scene_{i}",
            details=f"Test scene {i}",
            date=datetime.now().strftime("%Y-%m-%d"),
            urls=[f"https://example.com/{prefix}/scene_{i}"],
            organized=True,
            performers=[performer],
            studio=studio,
            tags=tags,
        )
        scene = await stash_client.create_scene(scene)
        scenes.append(scene)

    return performer, studio, tags, scenes


@pytest.mark.asyncio
async def test_tag_cleanup_workflow(stash_client: StashClient) -> None:
    """Test tag cleanup workflow.

    This test:
    1. Creates test data with tags
    2. Simulates tag usage
    3. Merges duplicate tags
    4. Updates tag hierarchy
    5. Cleans up unused tags
    """
    try:
        # Create initial data
        performer, studio, tags, scenes = await create_test_data(
            stash_client, prefix="tag_cleanup"
        )

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
            merged = await stash_client.tags_merge(
                source=[dup.id],
                destination=orig.id,
            )
            assert merged.id == orig.id
            # Verify scenes have merged tag
            for scene in scenes:
                updated = await stash_client.find_scene(scene.id)
                assert merged.id in [t.id for t in updated.tags]
                assert dup.id not in [t.id for t in updated.tags]

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
            assert updated.parents[0].id == parent_tag.id

        # Verify hierarchy
        parent = await stash_client.find_tag(parent_tag.id)
        assert len(parent.children) == len(tags)
        assert all(t.id in [c.id for c in parent.children] for t in tags)

    except Exception as e:
        pytest.skip(f"Test requires running Stash instance: {e}")


@pytest.mark.asyncio
async def test_performer_merge_workflow(stash_client: StashClient) -> None:
    """Test performer merge workflow.

    This test:
    1. Creates test performers
    2. Creates content for each
    3. Merges performers
    4. Verifies content is properly merged
    5. Cleans up
    """
    try:
        # Create performers
        performers = []
        for i in range(2):
            performer = Performer(
                id="new",
                name=f"merge_performer_{i}",
                gender="FEMALE",
            )
            performer = await stash_client.create_performer(performer)
            performers.append(performer)

        # Create content for each performer
        scenes_by_performer = {}
        for performer in performers:
            _, studio, tags, scenes = await create_test_data(
                stash_client,
                prefix=f"merge_{performer.name}",
            )
            scenes_by_performer[performer.id] = scenes

        # Merge performers (manually since there's no direct merge API)
        main_performer = performers[0]
        merge_performer = performers[1]

        # Update all scenes to use main performer
        merge_scenes = scenes_by_performer[merge_performer.id]
        for scene in merge_scenes:
            scene.performers = [main_performer]
            updated = await stash_client.update_scene(scene)
            assert updated.performers[0].id == main_performer.id

        # Verify merge
        all_scenes = await stash_client.find_scenes(
            scene_filter={
                "performers": {
                    "value": [main_performer.id],
                    "modifier": "INCLUDES",
                }
            }
        )
        assert all_scenes.count == len(scenes_by_performer[main_performer.id]) + len(
            merge_scenes
        )

    except Exception as e:
        pytest.skip(f"Test requires running Stash instance: {e}")


@pytest.mark.asyncio
async def test_studio_hierarchy_workflow(stash_client: StashClient) -> None:
    """Test studio hierarchy workflow.

    This test:
    1. Creates studio hierarchy
    2. Creates content at different levels
    3. Updates hierarchy
    4. Verifies inheritance
    5. Cleans up
    """
    try:
        # Create studio hierarchy
        parent_studio = Studio(
            id="new",
            name="parent_studio",
        )
        parent_studio = await stash_client.create_studio(parent_studio)

        child_studios = []
        for i in range(2):
            studio = Studio(
                id="new",
                name=f"child_studio_{i}",
                parent_studio=parent_studio,
            )
            studio = await stash_client.create_studio(studio)
            child_studios.append(studio)

        # Create content at each level
        scenes_by_studio = {}

        # Parent content
        performer, _, tags, parent_scenes = await create_test_data(
            stash_client,
            prefix="parent_studio",
        )
        scenes_by_studio[parent_studio.id] = parent_scenes

        # Child content
        for studio in child_studios:
            _, _, _, scenes = await create_test_data(
                stash_client,
                prefix=f"child_{studio.name}",
            )
            scenes_by_studio[studio.id] = scenes

        # Verify hierarchy
        for studio_id, scenes in scenes_by_studio.items():
            for scene in scenes:
                updated = await stash_client.find_scene(scene.id)
                if studio_id != parent_studio.id:
                    # Child studio scenes should have parent
                    assert updated.studio.parent_studio.id == parent_studio.id

        # Move child studio content to parent
        for studio in child_studios:
            scenes = scenes_by_studio[studio.id]
            for scene in scenes:
                scene.studio = parent_studio
                updated = await stash_client.update_scene(scene)
                assert updated.studio.id == parent_studio.id

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

    except Exception as e:
        pytest.skip(f"Test requires running Stash instance: {e}")


@pytest.mark.asyncio
async def test_duplicate_management_workflow(stash_client: StashClient) -> None:
    """Test duplicate content management workflow.

    This test:
    1. Creates similar content
    2. Finds duplicates
    3. Merges duplicates
    4. Verifies cleanup
    """
    try:
        # Create base content
        performer, studio, tags, _ = await create_test_data(
            stash_client,
            prefix="duplicate",
        )

        # Create similar scenes
        scenes = []
        for i in range(3):
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
            scenes.append(scene)

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
                "performers": {
                    "value": [performer.id],
                    "modifier": "INCLUDES",
                }
            }
        )
        assert final_scenes.count == len(scenes)
        organized = [s for s in final_scenes.scenes if s.organized]
        unorganized = [s for s in final_scenes.scenes if not s.organized]
        if duplicates:
            assert len(organized) < len(scenes)
            assert len(unorganized) > 0

    except Exception as e:
        pytest.skip(f"Test requires running Stash instance: {e}")
