"""Unit tests for metadata.wall module."""

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from metadata import Account, Post, Wall, process_account_walls, process_wall_posts


@pytest_asyncio.fixture
async def test_config():
    """Create a mock configuration."""
    config = MagicMock()
    config._database = MagicMock()
    return config


@pytest_asyncio.fixture
async def test_account(test_async_session):
    """Create a test account."""
    from datetime import datetime, timezone

    from sqlalchemy import select

    # Check if account already exists
    result = await test_async_session.execute(select(Account).filter_by(id=1))
    account = result.scalar_one_or_none()

    if account is None:
        # Create the account
        account = Account(
            id=1, username="test_user", createdAt=datetime.now(timezone.utc)
        )
        test_async_session.add(account)
        await test_async_session.commit()
        await test_async_session.refresh(account)

    return account


@pytest.mark.asyncio
async def test_wall_creation(test_async_session: AsyncSession, test_account: Account):
    """Test creating a wall with basic attributes."""
    wall = Wall(
        id=1,
        accountId=test_account.id,
        pos=1,
        name="Test Wall",
        description="Test Description",
    )
    test_async_session.add(wall)
    await test_async_session.commit()

    result = await test_async_session.execute(
        select(Wall).execution_options(populate_existing=True)
    )
    saved_wall = result.unique().scalar_one_or_none()
    assert saved_wall.name == "Test Wall"
    assert saved_wall.description == "Test Description"
    assert saved_wall.pos == 1
    assert saved_wall.account == test_account


@pytest.mark.asyncio
async def test_wall_post_association(
    test_async_session: AsyncSession,
    test_account: Account,
):
    """Test associating posts with a wall."""
    # Create wall
    wall = Wall(id=1, accountId=test_account.id, name="Test Wall")
    test_async_session.add(wall)
    await test_async_session.commit()

    # Create posts
    posts = [
        Post(
            id=i,
            accountId=test_account.id,
            content=f"Post {i}",
            createdAt=datetime.now(timezone.utc),
        )
        for i in range(1, 4)
    ]
    test_async_session.add_all(posts)
    await test_async_session.commit()

    # Refresh wall.posts in a greenlet context
    await test_async_session.run_sync(
        lambda s: s.refresh(wall, attribute_names=["posts"])
    )

    # Associate posts with wall inside a synchronous context
    await test_async_session.run_sync(lambda s: wall.posts.extend(posts))
    await test_async_session.commit()

    # Verify associations
    result = await test_async_session.execute(
        select(Wall).where(Wall.id == 1).execution_options(populate_existing=True)
    )
    saved_wall = result.unique().scalar_one_or_none()
    assert len(saved_wall.posts) == 3
    assert sorted(p.content for p in saved_wall.posts) == ["Post 1", "Post 2", "Post 3"]


@pytest.mark.asyncio
async def test_process_account_walls(
    test_config,
    test_async_session: AsyncSession,
    test_account: Account,
):
    """Test processing walls data for an account."""
    # Test wall data matching test_wall fixture
    walls_data = [
        {"id": 1, "pos": 1, "name": "Wall 1", "description": "Description 1"},
        {"id": 2, "pos": 2, "name": "Wall 2", "description": "Description 2"},
    ]

    await process_account_walls(
        config=test_config,
        account=test_account,
        walls_data=walls_data,
        session=test_async_session,
    )

    result = await test_async_session.execute(
        select(Wall).where(Wall.accountId == test_account.id)
    )
    walls = result.scalars().all()
    assert len(walls) == 2
    assert walls[0].name == "Wall 1"
    assert walls[1].name == "Wall 2"
    assert walls[0].pos == 1
    assert walls[1].pos == 2


@pytest.mark.asyncio
async def test_wall_cleanup(
    test_config,
    test_async_session: AsyncSession,
    test_account: Account,
):
    """Test cleanup of removed walls."""
    # Create initial walls
    walls = [
        Wall(id=i, accountId=test_account.id, name=f"Wall {i}", pos=i)
        for i in range(1, 4)
    ]
    for wall in walls:
        test_async_session.add(wall)
    await test_async_session.commit()

    new_walls_data = [
        {"id": 1, "pos": 1, "name": "Wall 1", "description": "Description 1"},
        {"id": 3, "pos": 2, "name": "Wall 3", "description": "Description 3"},
    ]

    await process_account_walls(
        test_config,
        test_account,
        new_walls_data,
        session=test_async_session,  # Pass the session explicitly
    )

    # Verify wall 2 was removed
    result = await test_async_session.execute(
        select(Wall).order_by(Wall.pos).execution_options(populate_existing=True)
    )
    remaining_walls = result.unique().scalars().all()
    assert len(remaining_walls) == 2
    assert [w.id for w in remaining_walls] == [1, 3]


@pytest.mark.asyncio
async def test_process_wall_posts(
    test_config,
    test_async_session: AsyncSession,
    test_account: Account,
):
    """Test processing posts for a wall."""
    # Create wall
    wall = Wall(id=1, accountId=test_account.id, name="Test Wall")
    test_async_session.add(wall)
    await test_async_session.commit()

    # Make sure wall.posts is properly initialized in the greenlet context
    await test_async_session.run_sync(
        lambda s: s.refresh(wall, attribute_names=["posts"])
    )

    # Create posts data
    posts_data = {
        "posts": [
            {
                "id": 1,
                "accountId": test_account.id,
                "content": "Post 1",
                "createdAt": int(datetime.now(timezone.utc).timestamp()),
            },
            {
                "id": 2,
                "accountId": test_account.id,
                "content": "Post 2",
                "createdAt": int(datetime.now(timezone.utc).timestamp()),
            },
        ],
        "accounts": [{"id": test_account.id, "username": "test_user"}],
        "accountMedia": [],
    }

    await process_wall_posts(
        test_config,
        None,
        wall.id,
        posts_data,
        session=test_async_session,
    )

    # Verify posts were created by checking count and IDs
    result = await test_async_session.execute(text("SELECT id FROM posts ORDER BY id"))
    post_ids = [row[0] for row in result.fetchall()]
    assert len(post_ids) == 2
    assert post_ids == [1, 2]

    # Now verify the relationship from wall to posts using a direct SQL query
    result = await test_async_session.execute(
        text(
            "SELECT p.id "
            "FROM posts p "
            "JOIN wall_posts wp ON p.id = wp.postId "
            "WHERE wp.wallId = :wall_id "
            "ORDER BY p.id"
        ),
        {"wall_id": wall.id},
    )

    # Get the post IDs directly from the SQL query result
    wall_post_ids = [row[0] for row in result.fetchall()]
    assert len(wall_post_ids) == 2
    assert sorted(wall_post_ids) == [1, 2]
