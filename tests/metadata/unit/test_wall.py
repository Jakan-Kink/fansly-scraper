"""Unit tests for metadata.wall module."""

from datetime import datetime, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from download.downloadstate import DownloadState
from metadata import Account, Post, Wall, process_account_walls, process_wall_posts
from metadata.wall import wall_posts
from tests.fixtures import AccountFactory, PostFactory


@pytest.mark.asyncio
async def test_wall_creation(session: AsyncSession, session_sync):
    """Test creating a wall with basic attributes.

    Uses AccountFactory to create test account.
    factory_session is autouse=True so it's automatically applied.
    """
    # Create account with factory - only use the ID
    account = AccountFactory(id=1, username="test_user")
    account_id = account.id  # Get ID before session expires
    session.expire_all()

    wall = Wall(
        id=1,
        accountId=account_id,
        pos=1,
        name="Test Wall",
        description="Test Description",
    )
    session.add(wall)
    await session.commit()

    session.expire_all()
    result = await session.execute(
        select(Wall).execution_options(populate_existing=True)
    )
    saved_wall = result.unique().scalar_one_or_none()
    assert saved_wall.name == "Test Wall"
    assert saved_wall.description == "Test Description"
    assert saved_wall.pos == 1
    assert saved_wall.accountId == account_id


@pytest.mark.asyncio
async def test_wall_post_association(
    session: AsyncSession,
    session_sync,
):
    """Test associating posts with a wall.

    Uses AccountFactory and PostFactory for creating test data.
    factory_session is autouse=True so it's automatically applied.
    """
    # Create account with factory - get ID immediately
    account = AccountFactory(id=1, username="test_user")
    account_id = account.id
    session.expire_all()

    # Create wall in async session
    wall = Wall(id=1, accountId=account_id, name="Test Wall")
    session.add(wall)
    await session.commit()

    # Create posts using factory - get IDs immediately
    factory_posts = [
        PostFactory(id=i, accountId=account_id, content=f"Post {i}")
        for i in range(1, 4)
    ]
    post_ids = [p.id for p in factory_posts]
    session_sync.commit()

    # Query posts in async session
    session.expire_all()
    result = await session.execute(select(Post).where(Post.id.in_(post_ids)))
    posts = result.unique().scalars().all()

    # Refresh wall.posts in a greenlet context
    await session.run_sync(lambda s: s.refresh(wall, attribute_names=["posts"]))

    # Associate posts with wall inside a synchronous context
    await session.run_sync(lambda s: wall.posts.extend(posts))
    await session.commit()

    # Verify associations with eager loading
    session.expire_all()
    result = await session.execute(
        select(Wall)
        .where(Wall.id == 1)
        .options(selectinload(Wall.posts))
        .execution_options(populate_existing=True)
    )
    saved_wall = result.unique().scalar_one_or_none()
    assert len(saved_wall.posts) == 3
    assert sorted(p.content for p in saved_wall.posts) == ["Post 1", "Post 2", "Post 3"]


@pytest.mark.asyncio
async def test_process_account_walls(
    config,
    session: AsyncSession,
    session_sync,
):
    """Test processing walls data for an account.

    Uses AccountFactory and centralized config/session fixtures.
    factory_session is autouse=True so it's automatically applied.
    """
    # Create account with factory - get ID immediately
    account = AccountFactory(id=1, username="test_user")
    account_id = account.id
    session.expire_all()

    # Query account in async session
    result = await session.execute(select(Account).where(Account.id == account_id))
    account = result.scalar_one()

    # Test wall data matching test_wall fixture
    walls_data = [
        {"id": 1, "pos": 1, "name": "Wall 1", "description": "Description 1"},
        {"id": 2, "pos": 2, "name": "Wall 2", "description": "Description 2"},
    ]

    await process_account_walls(
        config=config,
        account=account,
        walls_data=walls_data,
        session=session,
    )

    session.expire_all()
    result = await session.execute(select(Wall).where(Wall.accountId == account_id))
    walls = result.scalars().all()
    assert len(walls) == 2
    assert walls[0].name == "Wall 1"
    assert walls[1].name == "Wall 2"
    assert walls[0].pos == 1
    assert walls[1].pos == 2


@pytest.mark.asyncio
async def test_wall_cleanup(
    config,
    session: AsyncSession,
    session_sync,
):
    """Test cleanup of removed walls.

    Uses AccountFactory and centralized config/session fixtures.
    factory_session is autouse=True so it's automatically applied.
    """
    # Create account with factory - get ID immediately
    account = AccountFactory(id=1, username="test_user")
    account_id = account.id
    session.expire_all()

    # Query account in async session
    result = await session.execute(select(Account).where(Account.id == account_id))
    account = result.scalar_one()

    # Create initial walls
    walls = [
        Wall(id=i, accountId=account_id, name=f"Wall {i}", pos=i) for i in range(1, 4)
    ]
    for wall in walls:
        session.add(wall)
    await session.commit()

    new_walls_data = [
        {"id": 1, "pos": 1, "name": "Wall 1", "description": "Description 1"},
        {"id": 3, "pos": 2, "name": "Wall 3", "description": "Description 3"},
    ]

    await process_account_walls(
        config,
        account,
        new_walls_data,
        session=session,  # Pass the session explicitly
    )

    # Verify wall 2 was removed
    session.expire_all()
    result = await session.execute(
        select(Wall).order_by(Wall.pos).execution_options(populate_existing=True)
    )
    remaining_walls = result.unique().scalars().all()
    assert len(remaining_walls) == 2
    assert [w.id for w in remaining_walls] == [1, 3]


@pytest.mark.asyncio
async def test_process_wall_posts(
    config,
    session: AsyncSession,
    session_sync,
):
    """Test processing posts for a wall.

    Uses AccountFactory and centralized config/session fixtures.
    factory_session is autouse=True so it's automatically applied.
    """
    # Create account with factory - get ID immediately
    account = AccountFactory(id=1, username="test_user")
    account_id = account.id
    session.expire_all()

    # Create wall
    wall = Wall(id=1, accountId=account_id, name="Test Wall")
    session.add(wall)
    await session.commit()

    # Extract wall ID before session operations
    wall_id = wall.id

    # Make sure wall.posts is properly initialized in the greenlet context
    await session.run_sync(lambda s: s.refresh(wall, attribute_names=["posts"]))

    # Create DownloadState for processing
    state = DownloadState()
    state.creator_id = account_id

    # Create posts data
    posts_data = {
        "posts": [
            {
                "id": 1,
                "accountId": account_id,
                "content": "Post 1",
                "createdAt": int(datetime.now(timezone.utc).timestamp()),
            },
            {
                "id": 2,
                "accountId": account_id,
                "content": "Post 2",
                "createdAt": int(datetime.now(timezone.utc).timestamp()),
            },
        ],
        "accounts": [{"id": account_id, "username": "test_user"}],
        "accountMedia": [],
    }

    await process_wall_posts(
        config,
        state,
        wall_id,
        posts_data,
        session=session,
    )

    # Verify posts were created by checking count and IDs
    session.expire_all()
    result = await session.execute(select(Post.id).order_by(Post.id))
    post_ids = [row[0] for row in result.fetchall()]
    assert len(post_ids) == 2
    assert post_ids == [1, 2]

    # Now verify the relationship from wall to posts using ORM query
    stmt = (
        select(Post.id)
        .select_from(wall_posts)
        .join(Post, Post.id == wall_posts.c.postId)
        .where(wall_posts.c.wallId == wall_id)
        .order_by(Post.id)
    )
    result = await session.execute(stmt)
    wall_post_ids = [row[0] for row in result.fetchall()]
    assert len(wall_post_ids) == 2
    assert sorted(wall_post_ids) == [1, 2]
