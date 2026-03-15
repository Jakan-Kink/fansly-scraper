"""Integration tests for wall operations."""

from datetime import UTC, datetime

import pytest

from download.downloadstate import DownloadState
from metadata import Account, Post, Wall, process_account_walls, process_wall_posts
from tests.fixtures.utils.test_isolation import snowflake_id


@pytest.mark.asyncio
async def test_wall_post_integration(entity_store, mock_config):
    """Test full wall and post integration via entity_store."""
    account_id = snowflake_id()
    account = Account(id=account_id, username=f"test_user_{account_id}")
    await entity_store.save(account)

    # Create walls
    wall_ids = [snowflake_id(), snowflake_id()]
    for i, wid in enumerate(wall_ids):
        wall = Wall(
            id=wid,
            accountId=account_id,
            name=f"Wall {i + 1}",
            pos=i,
        )
        await entity_store.save(wall)

    # Create posts
    post_ids = [snowflake_id() for _ in range(4)]
    for i, pid in enumerate(post_ids):
        post = Post(
            id=pid,
            accountId=account_id,
            content=f"Post {i + 1}",
            createdAt=datetime.now(UTC),
        )
        await entity_store.save(post)

    # Associate posts with walls
    wall1 = await entity_store.get(Wall, wall_ids[0])
    wall1.posts = [
        await entity_store.get(Post, post_ids[0]),
        await entity_store.get(Post, post_ids[1]),
    ]
    await entity_store.save(wall1)

    wall2 = await entity_store.get(Wall, wall_ids[1])
    wall2.posts = [
        await entity_store.get(Post, post_ids[2]),
        await entity_store.get(Post, post_ids[3]),
    ]
    await entity_store.save(wall2)

    # Verify through entity_store
    wall1_refreshed = await entity_store.get(Wall, wall_ids[0])
    assert len(wall1_refreshed.posts) == 2

    wall2_refreshed = await entity_store.get(Wall, wall_ids[1])
    assert len(wall2_refreshed.posts) == 2


@pytest.mark.asyncio
async def test_wall_updates_with_posts(entity_store, mock_config):
    """Test updating walls while maintaining post associations."""
    account_id = snowflake_id()
    account = Account(id=account_id, username="wall_update_test")
    await entity_store.save(account)

    wall_id = snowflake_id()
    wall = Wall(id=wall_id, accountId=account_id, name="Original Wall", pos=1)
    await entity_store.save(wall)

    # Create posts and associate
    post_ids = [snowflake_id(), snowflake_id()]
    for i, pid in enumerate(post_ids):
        post = Post(
            id=pid,
            accountId=account_id,
            content=f"Post {i + 1}",
            createdAt=datetime.now(UTC),
        )
        await entity_store.save(post)

    wall.posts = [
        await entity_store.get(Post, post_ids[0]),
        await entity_store.get(Post, post_ids[1]),
    ]
    await entity_store.save(wall)

    # Update wall through production code
    new_wall_data = [
        {
            "id": wall_id,
            "pos": 2,
            "name": "Updated Wall",
            "description": "New description",
        }
    ]

    await process_account_walls(mock_config, account, new_wall_data)

    # Verify updates
    updated_wall = await entity_store.get(Wall, wall_id)
    assert updated_wall.name == "Updated Wall"
    assert updated_wall.pos == 2
    assert updated_wall.description == "New description"


@pytest.mark.asyncio
async def test_wall_post_processing(entity_store, mock_config):
    """Test processing wall posts from timeline-style data."""
    account_id = snowflake_id()
    account = Account(id=account_id, username="wall_post_test")
    await entity_store.save(account)

    wall_id = snowflake_id()
    wall = Wall(id=wall_id, accountId=account_id, name="Test Wall")
    await entity_store.save(wall)

    # Create posts data in timeline format
    post_ids = [snowflake_id() for _ in range(3)]
    posts_data = {
        "posts": [
            {
                "id": pid,
                "accountId": account_id,
                "content": f"Post {i + 1}",
                "createdAt": int(datetime.now(UTC).timestamp()),
            }
            for i, pid in enumerate(post_ids)
        ],
        "accounts": [{"id": account_id, "username": account.username}],
        "accountMedia": [],
    }

    # Process wall posts via production code
    state = DownloadState(creator_id=account_id)
    await process_wall_posts(mock_config, state, wall_id, posts_data)

    # Verify posts were created and associated with wall
    wall_refreshed = await entity_store.get(Wall, wall_id)
    assert len(wall_refreshed.posts) == 3

    for pid in post_ids:
        post = await entity_store.get(Post, pid)
        assert post is not None
