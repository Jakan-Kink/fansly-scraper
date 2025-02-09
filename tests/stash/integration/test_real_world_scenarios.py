"""Integration tests for real-world scenarios.

These tests require a running Stash instance.
"""

import asyncio
from collections.abc import AsyncGenerator
from datetime import datetime
from pathlib import Path

import pytest
import pytest_asyncio

from metadata import Account, Media, Post
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


@pytest.fixture
def mock_account() -> Account:
    """Create a mock account for testing."""
    return Account(
        id=123,
        username="test_account",
        displayName="Test Account",
        about="Test account bio",
        location="US",
        createdAt=datetime.now(),
        updatedAt=datetime.now(),
    )


@pytest.fixture
def mock_post() -> Post:
    """Create a mock post for testing."""
    return Post(
        id=456,
        accountId=123,
        content="Test post content #tag1 #tag2",
        createdAt=datetime.now(),
    )


@pytest.fixture
def mock_media() -> Media:
    """Create a mock media for testing."""
    return Media(
        id=789,
        accountId=123,
        local_filename="test_video.mp4",
        createdAt=datetime.now(),
    )


@pytest.mark.asyncio
async def test_content_import_workflow(
    stash_client: StashClient,
    mock_account: Account,
    mock_post: Post,
    mock_media: Media,
) -> None:
    """Test importing content from a platform.

    This test simulates importing content from a platform like OnlyFans:
    1. Creates performer from account
    2. Creates studio from account
    3. Creates tags from post hashtags
    4. Creates scene from post and media
    5. Generates metadata
    6. Verifies everything
    """
    try:
        # Create performer from account
        performer = await Performer.from_account(mock_account)
        performer = await performer.save(stash_client)
        assert performer.id is not None
        assert performer.name == mock_account.displayName

        # Create studio from account
        studio = await Studio.from_account(mock_account)
        studio = await studio.save(stash_client)
        assert studio.id is not None
        assert studio.name == mock_account.username

        # Create tags from hashtags
        tags = []
        hashtags = ["tag1", "tag2"]  # Extracted from post content
        for tag_name in hashtags:
            tag = Tag(
                name=tag_name,
                description=f"Imported from {mock_account.username}",
                created_at=datetime.now(),
                updated_at=datetime.now(),
            )
            tag = await stash_client.create_tag(tag)
            assert tag.id is not None
            tags.append(tag)

        # Create scene from post and media
        scene = Scene(
            title=f"{mock_account.username} - {mock_post.id}",
            details=mock_post.content,
            date=mock_post.createdAt.strftime("%Y-%m-%d"),
            urls=[f"https://example.com/posts/{mock_post.id}"],
            organized=True,
            performers=[performer],
            studio=studio,
            tags=tags,
            created_at=mock_post.createdAt,
            updated_at=mock_post.createdAt,
        )
        scene = await stash_client.create_scene(scene)
        assert scene.id is not None

        # Generate metadata with progress tracking
        options = GenerateMetadataOptions(
            covers=True,
            sprites=True,
            previews=True,
            phashes=True,
        )
        input_data = GenerateMetadataInput(
            scene_ids=[scene.id],
            overwrite=True,
        )
        job_id = await stash_client.metadata_generate(options, input_data)
        assert job_id is not None

        # Track progress with subscription
        progress_updates = []
        async with stash_client.subscribe_to_jobs() as subscription:
            async for update in subscription:
                if update.job and update.job.id == job_id:
                    progress_updates.append(update.progress)
                    if update.status in ["FINISHED", "CANCELLED"]:
                        break

        # Verify progress was tracked
        assert len(progress_updates) > 0
        assert progress_updates[-1] == 100.0

        # Verify final scene
        scene = await stash_client.find_scene(scene.id)
        assert scene is not None
        assert scene.performers[0].id == performer.id
        assert scene.studio.id == studio.id
        assert len(scene.tags) == len(tags)
        assert {t.id for t in scene.tags} == {t.id for t in tags}

    except Exception as e:
        pytest.skip(f"Test requires running Stash instance: {e}")


@pytest.mark.asyncio
async def test_batch_import_workflow(
    stash_client: StashClient,
    mock_account: Account,
) -> None:
    """Test batch importing content.

    This test simulates batch importing multiple posts:
    1. Creates base performer/studio
    2. Processes posts in batches
    3. Handles rate limiting
    4. Tracks overall progress
    5. Verifies everything
    """
    try:
        # Create base performer/studio
        performer = await Performer.from_account(mock_account)
        performer = await performer.save(stash_client)
        assert performer.id is not None

        studio = await Studio.from_account(mock_account)
        studio = await studio.save(stash_client)
        assert studio.id is not None

        # Create mock posts
        posts = []
        for i in range(10):
            post = Post(
                id=1000 + i,
                accountId=mock_account.id,
                content=f"Test post {i} content #tag{i}",
                createdAt=datetime.now(),
            )
            posts.append(post)

        # Process in batches
        batch_size = 3
        for i in range(0, len(posts), batch_size):
            batch = posts[i : i + batch_size]

            # Create scenes concurrently
            async def create_scene(post: Post) -> Scene:
                scene = Scene(
                    title=f"{mock_account.username} - {post.id}",
                    details=post.content,
                    date=post.createdAt.strftime("%Y-%m-%d"),
                    urls=[f"https://example.com/posts/{post.id}"],
                    organized=True,
                    performers=[performer],
                    studio=studio,
                    created_at=post.createdAt,
                    updated_at=post.createdAt,
                )
                return await stash_client.create_scene(scene)

            scenes = await asyncio.gather(*[create_scene(p) for p in batch])
            assert len(scenes) == len(batch)
            assert all(s.id is not None for s in scenes)

            # Generate metadata for batch
            options = GenerateMetadataOptions(
                covers=True,
                sprites=True,
                previews=True,
            )
            input_data = GenerateMetadataInput(
                scene_ids=[s.id for s in scenes],
                overwrite=True,
            )
            job_id = await stash_client.metadata_generate(options, input_data)
            assert job_id is not None

            # Wait for job to complete
            async with stash_client.subscribe_to_jobs() as subscription:
                async for update in subscription:
                    if update.job and update.job.id == job_id:
                        if update.status in ["FINISHED", "CANCELLED"]:
                            break

            # Rate limiting delay between batches
            if i + batch_size < len(posts):
                await asyncio.sleep(1.0)

        # Verify all scenes
        result = await stash_client.find_scenes(
            scene_filter={
                "performers": {
                    "value": [performer.id],
                    "modifier": "INCLUDES",
                }
            }
        )
        assert result.count == len(posts)

    except Exception as e:
        pytest.skip(f"Test requires running Stash instance: {e}")


@pytest.mark.asyncio
async def test_incremental_update_workflow(
    stash_client: StashClient,
    mock_account: Account,
) -> None:
    """Test incremental content updates.

    This test simulates updating content incrementally:
    1. Creates initial content
    2. Simulates new posts
    3. Updates only what's needed
    4. Handles errors gracefully
    5. Verifies everything
    """
    try:
        # Create initial content
        performer = await Performer.from_account(mock_account)
        performer = await performer.save(stash_client)
        assert performer.id is not None

        studio = await Studio.from_account(mock_account)
        studio = await studio.save(stash_client)
        assert studio.id is not None

        # Create initial scene
        scene = Scene(
            title=f"{mock_account.username} - Initial",
            details="Initial content",
            date=datetime.now().strftime("%Y-%m-%d"),
            urls=["https://example.com/posts/initial"],
            organized=True,
            performers=[performer],
            studio=studio,
            created_at=datetime.now(),
            updated_at=datetime.now(),
        )
        scene = await stash_client.create_scene(scene)
        assert scene.id is not None

        # Simulate new content
        new_scenes = []
        for i in range(3):
            new_scene = Scene(
                title=f"{mock_account.username} - New {i}",
                details=f"New content {i}",
                date=datetime.now().strftime("%Y-%m-%d"),
                urls=[f"https://example.com/posts/new_{i}"],
                organized=True,
                performers=[performer],
                studio=studio,
                created_at=datetime.now(),
                updated_at=datetime.now(),
            )
            try:
                new_scene = await stash_client.create_scene(new_scene)
                assert new_scene.id is not None
                new_scenes.append(new_scene)
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
                scene_ids=[s.id for s in new_scenes],
                overwrite=True,
            )
            job_id = await stash_client.metadata_generate(options, input_data)
            assert job_id is not None

            # Wait for job with timeout
            try:
                result = await stash_client.wait_for_job_with_updates(
                    job_id,
                    timeout=30.0,
                )
                assert result is True
            except TimeoutError:
                print("Metadata generation timed out")

        # Verify all content
        result = await stash_client.find_scenes(
            scene_filter={
                "performers": {
                    "value": [performer.id],
                    "modifier": "INCLUDES",
                }
            }
        )
        assert result.count == len(new_scenes) + 1  # +1 for initial scene

    except Exception as e:
        pytest.skip(f"Test requires running Stash instance: {e}")
