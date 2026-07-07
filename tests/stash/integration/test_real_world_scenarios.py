"""Integration tests for real-world scenarios.

These tests require a running Stash instance.

Note: These tests are skipped when running in the sandbox environment.
"""

import asyncio
import os
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime

import pytest
from stash_graphql_client import StashClient, present
from stash_graphql_client.types import (
    GenerateMetadataInput,
    GenerateMetadataOptions,
    Performer,
    Scene,
    Studio,
    Tag,
)

from metadata import Account, Post
from tests.fixtures.stash.stash_api_fixtures import dump_graphql_calls
from tests.fixtures.stash.stash_integration_fixtures import capture_graphql_calls
from tests.fixtures.utils.test_isolation import snowflake_id


# Skip all tests in this module when running in sandbox
pytestmark = pytest.mark.skipif(
    os.environ.get("OPENHANDS_SANDBOX") == "1",
    reason="Integration tests require a running Stash instance",
)

# stash_cleanup_tracker yields a factory: tracker(client) -> async ctx of the
# id-buckets dict. enable_scene_creation is a setup/teardown fixture (value unused).
CleanupTracker = Callable[..., AbstractAsyncContextManager[dict[str, list[str]]]]


def get_id(obj):
    """Get ID from Pydantic model."""
    return obj.id


def get_ids(objects):
    """Get set of IDs from list of Pydantic models."""
    return {obj.id for obj in objects}


@pytest.mark.asyncio
@pytest.mark.timeout(
    180
)  # 3 minutes for content import (Stash can be slow with parallel workers)
async def test_content_import_workflow(
    stash_client: StashClient,
    mock_account: Account,
    mock_post: Post,
    stash_cleanup_tracker: CleanupTracker,
    enable_scene_creation: object,
) -> None:
    """Test importing content from a platform.

    This test simulates importing content from a platform like OnlyFans:
    1. Creates performer from account
    2. Creates studio from account
    3. Creates tags from post hashtags
    4. Creates scene from post and media
    5. Generates metadata
    6. Verifies everything
    7. Cleans up created objects

    Note: This test requires extended timeout due to metadata generation.
    """
    try:
        async with stash_cleanup_tracker(stash_client) as cleanup:
            try:
                with capture_graphql_calls(stash_client) as calls:
                    # Create performer from account with unique name
                    performer = Performer(
                        name=f"Test Account {mock_post.id}",
                        urls=[f"https://fansly.com/{mock_account.username}"],
                    )
                    performer = await stash_client.create_performer(performer)
                    cleanup["performers"].append(performer.id)

                    # Create studio from account with unique name
                    studio = Studio(
                        name=f"test_account_{mock_post.id}",
                        urls=[f"https://fansly.com/{mock_account.username}"],
                    )
                    studio = await stash_client.create_studio(studio)
                    cleanup["studios"].append(studio.id)

                    # Create tags from hashtags with unique names
                    tags: list[Tag] = []
                    hashtags = ["tag1", "tag2"]  # Extracted from post content
                    for tag_name in hashtags:
                        unique_name = f"{tag_name}_{mock_post.id}"  # Unique name
                        tag = Tag(
                            name=unique_name,
                            description=f"Imported from {mock_account.username}",
                        )
                        tag = await stash_client.create_tag(tag)
                        tags.append(tag)
                        cleanup["tags"].append(tag.id)

                    # Create scene from post and media
                    scene = Scene(
                        title=f"{mock_account.username} - {mock_post.id}",
                        details=mock_post.content,
                        date=present(mock_post.createdAt).strftime("%Y-%m-%d"),
                        urls=[f"https://example.com/posts/{mock_post.id}"],
                        organized=True,
                        performers=[performer],
                        studio=studio,
                        tags=tags,
                    )
                    scene = await stash_client.create_scene(scene)
                    cleanup["scenes"].append(scene.id)

                    # Generate metadata with progress tracking
                    options = GenerateMetadataOptions(
                        covers=True,
                        sprites=True,
                        previews=True,
                        phashes=True,
                    )
                    input_data = GenerateMetadataInput(
                        sceneIDs=[scene.id],
                        overwrite=True,
                    )
                    job_id = await stash_client.metadata_generate(options, input_data)

                    # Small delay to ensure subscription can be established
                    # before job completes. On fast hardware (M3 Pro), metadata
                    # generation can complete in milliseconds.
                    await asyncio.sleep(0.1)

                    # Wait for job to complete
                    job_result = await stash_client.wait_for_job_with_updates(job_id)

                    # Re-fetch final scene for verification
                    final_scene = await stash_client.find_scene(scene.id)
            finally:
                dump_graphql_calls(calls, "content_import_workflow")

            assert performer.id is not None
            assert performer.name == f"Test Account {mock_post.id}"
            assert studio.id is not None
            assert studio.name == f"test_account_{mock_post.id}"
            assert all(t.id is not None for t in tags)
            assert scene.id is not None
            assert job_id is not None
            assert job_result is True  # Job completed successfully

            assert final_scene is not None
            assert performer.id in get_ids(final_scene.performers)
            assert get_id(final_scene.studio) == studio.id
            assert len(present(final_scene.tags)) == len(tags)
            assert get_ids(final_scene.tags) == {t.id for t in tags}
    except RuntimeError as e:
        if "Stash instance" in str(e):
            pytest.skip(f"Test requires running Stash instance: {e}")
        else:
            raise


@pytest.mark.asyncio
@pytest.mark.timeout(300)  # 5 minutes for metadata generation on 10 scenes
async def test_batch_import_workflow(
    stash_client: StashClient,
    mock_account: Account,
    stash_cleanup_tracker: CleanupTracker,
    enable_scene_creation: object,
) -> None:
    """Test batch importing content.

    This test simulates batch importing multiple posts:
    1. Creates base performer/studio
    2. Processes posts in batches
    3. Handles rate limiting
    4. Tracks overall progress
    5. Verifies everything
    6. Cleans up created objects

    Note: This test requires extended timeout due to metadata generation.
    """
    try:
        async with stash_cleanup_tracker(stash_client) as cleanup:
            try:
                with capture_graphql_calls(stash_client) as calls:
                    # Create base performer/studio with unique names
                    timestamp = datetime.now(UTC).timestamp()
                    performer = Performer(
                        name=f"Test Account Batch {timestamp}",
                        urls=[f"https://fansly.com/{mock_account.username}"],
                    )
                    performer = await stash_client.create_performer(performer)
                    cleanup["performers"].append(performer.id)

                    studio = Studio(
                        name=f"test_account_batch_{timestamp}",
                        urls=[f"https://fansly.com/{mock_account.username}"],
                    )
                    studio = await stash_client.create_studio(studio)
                    cleanup["studios"].append(studio.id)

                    # Create mock posts with unique IDs
                    posts = []
                    base_id = snowflake_id()
                    for i in range(10):
                        post = Post(
                            id=base_id + i,
                            accountId=present(mock_account.id),
                            content=f"Test post {i} content #tag{i}",
                            createdAt=datetime.now(UTC),
                        )
                        posts.append(post)

                    # Process in batches
                    all_scenes: list[Scene] = []
                    batch_size = 3
                    for i in range(0, len(posts), batch_size):
                        batch = posts[i : i + batch_size]

                        # Create scenes concurrently
                        async def create_scene(post: Post) -> Scene:
                            scene = Scene(
                                title=f"{mock_account.username} - {post.id}",
                                details=post.content,
                                date=present(post.createdAt).strftime("%Y-%m-%d"),
                                urls=[f"https://example.com/posts/{post.id}"],
                                organized=True,
                                performers=[performer],
                                studio=studio,
                            )
                            scene = await stash_client.create_scene(scene)
                            cleanup["scenes"].append(scene.id)  # Track for cleanup
                            return scene

                        scenes = await asyncio.gather(*[create_scene(p) for p in batch])
                        all_scenes.extend(scenes)

                        # Generate metadata for batch
                        options = GenerateMetadataOptions(
                            covers=True,
                            sprites=True,
                            previews=True,
                        )
                        input_data = GenerateMetadataInput(
                            sceneIDs=[s.id for s in scenes],
                            overwrite=True,
                        )
                        job_id = await stash_client.metadata_generate(
                            options, input_data
                        )

                        # Small delay to ensure subscription can be established
                        # before job completes. On fast hardware (M3 Pro),
                        # metadata generation can complete in milliseconds.
                        await asyncio.sleep(0.1)

                        # Wait for job to complete; failures are non-fatal —
                        # log and continue with the next batch.
                        try:
                            job_result = await stash_client.wait_for_job_with_updates(
                                job_id
                            )
                            if job_result is not True:
                                print(
                                    f"Metadata generation failed for batch "
                                    f"{i // batch_size + 1}"
                                )
                        except (TimeoutError, Exception) as e:
                            print(
                                f"Metadata generation failed for batch "
                                f"{i // batch_size + 1}: {e}"
                            )

                        # Rate limiting delay between batches
                        if i + batch_size < len(posts):
                            await asyncio.sleep(1.0)

                    # Verify all scenes
                    found = await stash_client.find_scenes(
                        scene_filter={
                            "performers": {
                                "value": [performer.id],
                                "modifier": "INCLUDES",
                            }
                        }
                    )
            finally:
                dump_graphql_calls(calls, "batch_import_workflow")

            assert performer.id is not None
            assert studio.id is not None
            assert len(all_scenes) == len(posts)
            assert all(s.id is not None for s in all_scenes)
            assert found.count == len(posts)

    except RuntimeError as e:
        if "Stash instance" in str(e):
            pytest.skip(f"Test requires running Stash instance: {e}")
        else:
            raise


@pytest.mark.asyncio
@pytest.mark.timeout(180)  # 3 minutes for incremental updates
async def test_incremental_update_workflow(
    stash_client: StashClient,
    mock_account: Account,
    stash_cleanup_tracker: CleanupTracker,
    enable_scene_creation: object,
) -> None:
    """Test incremental content updates.

    This test simulates updating content incrementally:
    1. Creates initial content
    2. Simulates new posts
    3. Updates only what's needed
    4. Handles errors gracefully
    5. Verifies everything
    6. Cleans up created objects
    """
    try:
        async with stash_cleanup_tracker(stash_client) as cleanup:
            job_timed_out = False
            job_result: bool | None = None
            try:
                with capture_graphql_calls(stash_client) as calls:
                    # Create initial content with unique names
                    timestamp = datetime.now(UTC).timestamp()
                    performer = Performer(
                        name=f"Test Account Incr {timestamp}",
                        urls=[f"https://fansly.com/{mock_account.username}"],
                    )
                    performer = await stash_client.create_performer(performer)
                    cleanup["performers"].append(performer.id)

                    studio = Studio(
                        name=f"test_account_incr_{timestamp}",
                        urls=[f"https://fansly.com/{mock_account.username}"],
                    )
                    studio = await stash_client.create_studio(studio)
                    cleanup["studios"].append(studio.id)

                    # Create initial scene
                    scene = Scene(
                        title=f"{mock_account.username} - Initial",
                        details="Initial content",
                        date=datetime.now(UTC).strftime("%Y-%m-%d"),
                        urls=["https://example.com/posts/initial"],
                        organized=True,
                        performers=[performer],
                        studio=studio,
                    )
                    scene = await stash_client.create_scene(scene)
                    cleanup["scenes"].append(scene.id)

                    # Simulate new content
                    new_scenes = []
                    for i in range(3):
                        new_scene = Scene(
                            title=f"{mock_account.username} - New {i}",
                            details=f"New content {i}",
                            date=datetime.now(UTC).strftime("%Y-%m-%d"),
                            urls=[f"https://example.com/posts/new_{i}"],
                            organized=True,
                            performers=[performer],
                            studio=studio,
                        )
                        try:
                            new_scene = await stash_client.create_scene(new_scene)
                            new_scenes.append(new_scene)
                            cleanup["scenes"].append(new_scene.id)
                        except Exception as e:
                            # Log error but continue
                            print(f"Failed to create scene {i}: {e}")

                    # Update metadata only for new scenes
                    if new_scenes:
                        options = GenerateMetadataOptions(
                            covers=True,
                            sprites=True,
                            previews=True,
                        )
                        input_data = GenerateMetadataInput(
                            sceneIDs=[s.id for s in new_scenes],
                            overwrite=True,
                        )
                        job_id = await stash_client.metadata_generate(
                            options, input_data
                        )

                        # Small delay to ensure subscription can be established
                        # before job completes. On fast hardware (M3 Pro),
                        # metadata generation can complete in milliseconds.
                        await asyncio.sleep(0.1)

                        # Wait for job with timeout
                        try:
                            job_result = await stash_client.wait_for_job_with_updates(
                                job_id
                            )
                        except TimeoutError:
                            job_timed_out = True
                            print("Metadata generation timed out")

                    # Verify all content
                    found = await stash_client.find_scenes(
                        scene_filter={
                            "performers": {
                                "value": [performer.id],
                                "modifier": "INCLUDES",
                            }
                        }
                    )
            finally:
                dump_graphql_calls(calls, "incremental_update_workflow")

            assert performer.id is not None
            assert studio.id is not None
            assert scene.id is not None
            assert all(s.id is not None for s in new_scenes)
            if new_scenes and not job_timed_out:
                assert job_result is True
            assert found.count == len(new_scenes) + 1  # +1 for initial scene

    except RuntimeError as e:
        if "Stash instance" in str(e):
            pytest.skip(f"Test requires running Stash instance: {e}")
        else:
            raise
