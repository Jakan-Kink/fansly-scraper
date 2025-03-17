"""Unit tests for metadata.wall module."""

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from metadata.account import Account
from metadata.post import Post
from metadata.wall import Wall, process_account_walls, process_wall_posts


@pytest.fixture
def test_account():
    """Create a test account."""
    return Account(id=1, username="test_user")


@pytest.mark.asyncio
async def test_wall_creation(test_async_session, test_account: Account):
    """Test creating a wall with basic attributes."""
    async with test_async_session as session:
        session.add(test_account)
        await session.commit()
        wall = Wall(
            id=1,
            accountId=test_account.id,
            pos=1,
            name="Test Wall",
            description="Test Description",
        )
        session.add(wall)
        await session.commit()

        result = await session.execute(select(Wall))
        saved_wall = result.scalar_one_or_none()
        assert saved_wall.name == "Test Wall"
        assert saved_wall.description == "Test Description"
        assert saved_wall.pos == 1
        assert saved_wall.account == test_account


@pytest.mark.asyncio
async def test_wall_post_association(test_async_session, test_account):
    """Test associating posts with a wall."""
    async with test_async_session as session:
        session.add(test_account)
        await session.commit()
        # Create wall
        wall = Wall(id=1, accountId=test_account.id, name="Test Wall")
        session.add(wall)

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
        for post in posts:
            session.add(post)
        await session.flush()

        # Associate posts with wall
        wall.posts = posts
        await session.commit()

        # Verify associations
        result = await session.execute(select(Wall))
        saved_wall = result.scalar_one_or_none()
        assert len(saved_wall.posts) == 3
        assert [p.content for p in saved_wall.posts] == ["Post 1", "Post 2", "Post 3"]


@pytest.mark.asyncio
async def test_process_account_walls(test_async_session, test_account):
    """Test processing walls data for an account."""
    async with test_async_session as session:
        session.add(test_account)
        await session.commit()
        config_mock = MagicMock()
        config_mock._database = MagicMock()
        config_mock._database.async_session = lambda: session
        walls_data = [
            {"id": 1, "pos": 1, "name": "Wall 1", "description": "Description 1"},
            {"id": 2, "pos": 2, "name": "Wall 2", "description": "Description 2"},
        ]

        await process_account_walls(config_mock, test_account, walls_data)

        # Verify walls were created
        result = await session.execute(select(Wall).order_by(Wall.pos))
        walls = result.scalars().all()
        assert len(walls) == 2
        assert walls[0].name == "Wall 1"
        assert walls[1].name == "Wall 2"
        assert walls[0].pos == 1
        assert walls[1].pos == 2


@pytest.mark.asyncio
async def test_wall_cleanup(test_async_session, test_account):
    """Test cleanup of removed walls."""
    async with test_async_session as session:
        session.add(test_account)
        await session.commit()
        # Create initial walls
        walls = [
            Wall(id=i, accountId=test_account.id, name=f"Wall {i}", pos=i)
            for i in range(1, 4)
        ]
        for wall in walls:
            session.add(wall)
        await session.commit()

        # Process new walls data (missing one wall)
        config_mock = MagicMock()
        config_mock._database = MagicMock()
        config_mock._database.async_session = lambda: session
        new_walls_data = [
            {"id": 1, "pos": 1, "name": "Wall 1", "description": "Description 1"},
            {"id": 3, "pos": 2, "name": "Wall 3", "description": "Description 3"},
        ]

        await process_account_walls(config_mock, test_account, new_walls_data)

        # Verify wall 2 was removed
        result = await session.execute(select(Wall).order_by(Wall.pos))
        remaining_walls = result.scalars().all()
        assert len(remaining_walls) == 2
        assert [w.id for w in remaining_walls] == [1, 3]


@pytest.mark.asyncio
async def test_process_wall_posts(test_async_session, test_account):
    """Test processing posts for a wall."""
    async with test_async_session as session:
        session.add(test_account)
        await session.commit()
        # Create wall
        wall = Wall(id=1, accountId=test_account.id, name="Test Wall")
        session.add(wall)
        await session.commit()

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

        config_mock = MagicMock()
        config_mock._database = MagicMock()
        config_mock._database.async_session = lambda: session
        await process_wall_posts(config_mock, None, wall.id, posts_data)

        # Verify posts were associated with wall
        result = await session.execute(select(Wall))
        saved_wall = result.scalar_one_or_none()
        assert len(saved_wall.posts) == 2
        assert sorted(p.id for p in saved_wall.posts) == [1, 2]
