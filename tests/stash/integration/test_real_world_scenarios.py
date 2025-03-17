"""Integration tests for real-world scenarios.

These tests require a running Stash instance.

Note: These tests are skipped when running in the sandbox environment.
"""

import asyncio
import os
from datetime import datetime, timezone
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

# Skip all tests in this module when running in sandbox
pytestmark = pytest.mark.skipif(
    os.environ.get("OPENHANDS_SANDBOX") == "1",
    reason="Integration tests require a running Stash instance",
)


def get_id(obj):
    """Get ID from either a dict or an object with id attribute."""
    return obj["id"] if isinstance(obj, dict) else obj.id


def get_ids(objects):
    """Get set of IDs from list of dicts or objects."""
    return {get_id(obj) for obj in objects}


@pytest.fixture
def mock_account() -> Account:
    """Create a mock account for testing."""
    account = Account(
        id=123,
        username="test_account",
        displayName="Test Account",
        about="Test account bio",
        location="US",
        createdAt=datetime.now(timezone.utc),
    )
    # Initialize relationships
    account.posts = []
    account.sent_messages = []
    account.received_messages = []
    account.accountMedia = set()
    account.accountMediaBundles = set()
    account.stories = set()
    account.pinnedPosts = set()
    account.walls = set()
    return account


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
    stash_cleanup_tracker,
    enable_scene_creation,
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
    """
    try:
        async with stash_cleanup_tracker(stash_client) as cleanup:
            # Create performer from account with unique name
            performer = Performer.from_account(mock_account)
            performer.name = f"Test Account {mock_post.id}"  # Make name unique
            await performer.save(stash_client)
            assert performer.id is not None
            assert performer.name == f"Test Account {mock_post.id}"
            cleanup["performers"].append(performer.id)

            # Create studio from account with unique name
            studio = await Studio.from_account(mock_account)
            studio.name = f"test_account_{mock_post.id}"  # Make name unique
            await studio.save(stash_client)
            assert studio.id is not None
            assert studio.name == f"test_account_{mock_post.id}"
            cleanup["studios"].append(studio.id)

            # Create tags from hashtags with unique names
            tags: list[Tag] = []
            hashtags = ["tag1", "tag2"]  # Extracted from post content
            for tag_name in hashtags:
                unique_name = f"{tag_name}_{mock_post.id}"  # Make name unique
                tag = Tag(
                    id="new",
                    name=unique_name,
                    description=f"Imported from {mock_account.username}",
                )
                tag = await stash_client.create_tag(tag)
                assert tag.id is not None
                tags.append(tag)
                cleanup["tags"].append(tag.id)

            # Create scene from post and media
            scene = Scene(
                id="new",
                title=f"{mock_account.username} - {mock_post.id}",
                details=mock_post.content,
                date=mock_post.createdAt.strftime("%Y-%m-%d"),
                urls=[f"https://example.com/posts/{mock_post.id}"],
                organized=True,
                performers=[performer],
                studio=studio,
                tags=tags,
            )
            scene = await stash_client.create_scene(scene)
            assert scene.id is not None
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
            assert job_id is not None

            # Wait for job to complete
            result = await stash_client.wait_for_job_with_updates(job_id)
            assert result is True  # Job completed successfully

            # Verify final scene
            scene = await stash_client.find_scene(scene.id)
            assert scene is not None
            assert performer.id in get_ids(scene.performers)
            assert get_id(scene.studio) == studio.id
            assert len(scene.tags) == len(tags)
            assert get_ids(scene.tags) == {t.id for t in tags}
    except RuntimeError as e:
        if "Stash instance" in str(e):
            pytest.skip("Test requires running Stash instance: {e}")
        else:
            raise e


@pytest.mark.asyncio
async def test_batch_import_workflow(
    stash_client: StashClient,
    mock_account: Account,
    stash_cleanup_tracker,
    enable_scene_creation,
) -> None:
    """Test batch importing content.

    This test simulates batch importing multiple posts:
    1. Creates base performer/studio
    2. Processes posts in batches
    3. Handles rate limiting
    4. Tracks overall progress
    5. Verifies everything
    6. Cleans up created objects
    """
    try:
        async with stash_cleanup_tracker(stash_client) as cleanup:
            # Create base performer/studio with unique names
            performer = Performer.from_account(mock_account)
            performer.name = (
                f"Test Account Batch {datetime.now().timestamp()}"  # Make name unique
            )
            await performer.save(stash_client)
            assert performer.id is not None
            cleanup["performers"].append(performer.id)

            studio = await Studio.from_account(mock_account)
            studio.name = (
                f"test_account_batch_{datetime.now().timestamp()}"  # Make name unique
            )
            await studio.save(stash_client)
            assert studio.id is not None
            cleanup["studios"].append(studio.id)

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
                        id="new",
                        title=f"{mock_account.username} - {post.id}",
                        details=post.content,
                        date=post.createdAt.strftime("%Y-%m-%d"),
                        urls=[f"https://example.com/posts/{post.id}"],
                        organized=True,
                        performers=[performer],
                        studio=studio,
                    )
                    scene = await stash_client.create_scene(scene)
                    cleanup["scenes"].append(scene.id)  # Track for cleanup
                    return scene

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
                    sceneIDs=[s.id for s in scenes],
                    overwrite=True,
                )
                job_id = await stash_client.metadata_generate(options, input_data)
                assert job_id is not None

                # Wait for job to complete
                result = await stash_client.wait_for_job_with_updates(job_id)

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

    except RuntimeError as e:
        if "Stash instance" in str(e):
            pytest.skip("Test requires running Stash instance: {e}")
        else:
            raise e


@pytest.mark.asyncio
async def test_incremental_update_workflow(
    stash_client: StashClient,
    mock_account: Account,
    stash_cleanup_tracker,
    enable_scene_creation,
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
            # Create initial content with unique names
            performer = Performer.from_account(mock_account)
            performer.name = (
                f"Test Account Incr {datetime.now().timestamp()}"  # Make name unique
            )
            await performer.save(stash_client)
            assert performer.id is not None
            cleanup["performers"].append(performer.id)

            studio = await Studio.from_account(mock_account)
            studio.name = (
                f"test_account_incr_{datetime.now().timestamp()}"  # Make name unique
            )
            await studio.save(stash_client)
            assert studio.id is not None
            cleanup["studios"].append(studio.id)

            # Create initial scene
            scene = Scene(
                id="new",
                title=f"{mock_account.username} - Initial",
                details="Initial content",
                date=datetime.now().strftime("%Y-%m-%d"),
                urls=["https://example.com/posts/initial"],
                organized=True,
                performers=[performer],
                studio=studio,
            )
            scene = await stash_client.create_scene(scene)
            assert scene.id is not None
            cleanup["scenes"].append(scene.id)

            # Simulate new content
            new_scenes = []
            for i in range(3):
                new_scene = Scene(
                    id="new",
                    title=f"{mock_account.username} - New {i}",
                    details=f"New content {i}",
                    date=datetime.now().strftime("%Y-%m-%d"),
                    urls=[f"https://example.com/posts/new_{i}"],
                    organized=True,
                    performers=[performer],
                    studio=studio,
                )
                try:
                    new_scene = await stash_client.create_scene(new_scene)
                    assert new_scene.id is not None
                    new_scenes.append(new_scene)
                    cleanup["scenes"].append(new_scene.id)  # Track for cleanup
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

    except RuntimeError as e:
        if "Stash instance" in str(e):
            pytest.skip("Test requires running Stash instance: {e}")
        else:
            raise e
