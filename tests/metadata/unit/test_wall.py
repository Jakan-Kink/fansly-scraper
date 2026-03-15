"""Unit tests for metadata.wall module."""

from datetime import UTC, datetime

import pytest

from download.downloadstate import DownloadState
from metadata import Account, Post, Wall, process_account_walls, process_wall_posts
from tests.fixtures.utils.test_isolation import snowflake_id


@pytest.mark.asyncio
async def test_wall_creation(entity_store):
    """Test creating a wall with basic attributes."""
    store = entity_store

    account_id = snowflake_id()
    wall_id = snowflake_id()

    account = Account(id=account_id, username="test_user")
    await store.save(account)

    wall = Wall(
        id=wall_id,
        accountId=account_id,
        pos=1,
        name="Test Wall",
        description="Test Description",
    )
    await store.save(wall)

    saved = await store.get(Wall, wall_id)
    assert saved is not None
    assert saved.name == "Test Wall"
    assert saved.description == "Test Description"
    assert saved.pos == 1
    assert saved.accountId == account_id


@pytest.mark.asyncio
async def test_wall_post_association(entity_store):
    """Test associating posts with a wall."""
    store = entity_store

    account_id = snowflake_id()
    wall_id = snowflake_id()
    post_ids = [snowflake_id() for _ in range(3)]

    account = Account(id=account_id, username="test_user")
    await store.save(account)

    wall = Wall(id=wall_id, accountId=account_id, name="Test Wall")
    await store.save(wall)

    # Create posts
    for i, pid in enumerate(post_ids):
        post = Post(id=pid, accountId=account_id, content=f"Post {i + 1}")
        await store.save(post)

    # Associate posts with wall
    wall.posts = [await store.get(Post, pid) for pid in post_ids]
    await store.save(wall)

    saved = await store.get(Wall, wall_id)
    assert saved is not None
    assert len(saved.posts) == 3
    assert sorted(p.content for p in saved.posts) == ["Post 1", "Post 2", "Post 3"]


@pytest.mark.asyncio
async def test_process_account_walls(entity_store, config):
    """Test processing walls data for an account.

    process_account_walls uses get_store() internally, wired via entity_store.
    """
    store = entity_store

    account_id = snowflake_id()
    wall_id1 = snowflake_id()
    wall_id2 = snowflake_id()

    account = Account(id=account_id, username="test_user")
    await store.save(account)

    walls_data = [
        {"id": wall_id1, "pos": 1, "name": "Wall 1", "description": "Description 1"},
        {"id": wall_id2, "pos": 2, "name": "Wall 2", "description": "Description 2"},
    ]

    await process_account_walls(config=config, account=account, walls_data=walls_data)

    walls = await store.find(Wall, accountId=account_id)
    assert len(walls) == 2
    walls_sorted = sorted(walls, key=lambda w: w.pos)
    assert walls_sorted[0].name == "Wall 1"
    assert walls_sorted[1].name == "Wall 2"
    assert walls_sorted[0].pos == 1
    assert walls_sorted[1].pos == 2


@pytest.mark.asyncio
async def test_wall_cleanup(entity_store, config):
    """Test cleanup of removed walls.

    process_account_walls deletes walls that are no longer in the data.
    """
    store = entity_store

    account_id = snowflake_id()
    wall_id1 = snowflake_id()
    wall_id2 = snowflake_id()
    wall_id3 = snowflake_id()

    account = Account(id=account_id, username="test_user")
    await store.save(account)

    # Create initial walls
    for wid, i in zip([wall_id1, wall_id2, wall_id3], range(1, 4), strict=True):
        wall = Wall(id=wid, accountId=account_id, name=f"Wall {i}", pos=i)
        await store.save(wall)

    # Process with only walls 1 and 3
    new_walls_data = [
        {"id": wall_id1, "pos": 1, "name": "Wall 1", "description": "Description 1"},
        {"id": wall_id3, "pos": 2, "name": "Wall 3", "description": "Description 3"},
    ]

    await process_account_walls(config, account, new_walls_data)

    # Verify wall 2 was removed
    remaining = await store.find(Wall, accountId=account_id)
    remaining_ids = sorted([w.id for w in remaining])
    assert remaining_ids == sorted([wall_id1, wall_id3])


@pytest.mark.asyncio
async def test_process_wall_posts(entity_store, config):
    """Test processing posts for a wall.

    process_wall_posts uses get_store() internally, wired via entity_store.
    """
    store = entity_store

    account_id = snowflake_id()
    wall_id = snowflake_id()
    post_id1 = snowflake_id()
    post_id2 = snowflake_id()

    account = Account(id=account_id, username="test_user")
    await store.save(account)

    wall = Wall(id=wall_id, accountId=account_id, name="Test Wall")
    await store.save(wall)

    state = DownloadState()
    state.creator_id = account_id

    posts_data = {
        "posts": [
            {
                "id": post_id1,
                "accountId": account_id,
                "content": "Post 1",
                "createdAt": int(datetime.now(UTC).timestamp()),
            },
            {
                "id": post_id2,
                "accountId": account_id,
                "content": "Post 2",
                "createdAt": int(datetime.now(UTC).timestamp()),
            },
        ],
        "accounts": [{"id": account_id, "username": "test_user"}],
        "accountMedia": [],
    }

    await process_wall_posts(config, state, str(wall_id), posts_data)

    # Verify posts were created
    post1 = await store.get(Post, post_id1)
    post2 = await store.get(Post, post_id2)
    assert post1 is not None
    assert post2 is not None

    # Verify wall-post association via raw query
    pool = await store._get_pool()
    rows = await pool.fetch(
        'SELECT "postId" FROM wall_posts WHERE "wallId" = $1 ORDER BY "postId"', wall_id
    )
    post_ids_saved = [row["postId"] for row in rows]
    assert post_ids_saved == sorted([post_id1, post_id2])
