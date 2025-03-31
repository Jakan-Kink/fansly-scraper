"""Integration tests for wall operations."""

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, selectinload, sessionmaker
from sqlalchemy.sql import text

from config import FanslyConfig
from metadata.account import Account
from metadata.base import Base
from metadata.database import Database
from metadata.post import Post
from metadata.wall import Wall, process_account_walls, process_wall_posts
from metadata.wall import wall_posts as wall_posts_table


@pytest.fixture(autouse=True)
async def setup_account(test_database, request):
    """Set up test account."""
    # Generate unique ID based on test name
    test_name = request.node.name
    import hashlib

    unique_id = (
        int(
            hashlib.sha1(f"TestWallOperations_{test_name}".encode()).hexdigest()[:8],
            16,
        )
        % 1000000
    )

    async with test_database.async_session_scope() as session:
        # Create test account with unique ID
        account = Account(id=unique_id, username=f"test_user_{unique_id}")
        session.add(account)
        await session.commit()
        return account


@pytest.mark.asyncio
async def test_wall_post_integration(test_database, setup_account):
    """Test full wall and post integration."""
    async with test_database.async_session_scope() as session:
        # Create walls
        walls = [
            Wall(
                id=i,
                accountId=setup_account.id,
                name=f"Wall {i}",
                pos=i,
                description=f"Description {i}",
            )
            for i in range(1, 3)
        ]
        for wall in walls:
            session.add(wall)

        # Create posts
        posts = [
            Post(
                id=i,
                accountId=setup_account.id,
                content=f"Post {i}",
                createdAt=datetime.now(timezone.utc),
            )
            for i in range(1, 5)
        ]
        for post in posts:
            session.add(post)
        await session.flush()

        # Associate posts with walls using the association table directly
        for post in posts[:2]:
            await session.execute(
                wall_posts_table.insert().values(wallId=walls[0].id, postId=post.id)
            )

        for post in posts[2:]:
            await session.execute(
                wall_posts_table.insert().values(wallId=walls[1].id, postId=post.id)
            )

        await session.commit()

        # Verify through ORM queries
        # Check first wall
        wall1 = await session.scalar(select(Wall).where(Wall.id == 1))
        assert wall1 is not None, "Wall 1 should exist"
        wall1_posts = sorted(wall1.posts, key=lambda p: p.content)
        assert len(wall1_posts) == 2
        assert [p.content for p in wall1_posts] == ["Post 1", "Post 2"]

        # Check second wall
        wall2 = await session.scalar(select(Wall).where(Wall.id == 2))
        assert wall2 is not None, "Wall 2 should exist"
        wall2_posts = sorted(wall2.posts, key=lambda p: p.content)
        assert len(wall2_posts) == 2
        assert [p.content for p in wall2_posts] == ["Post 3", "Post 4"]


@pytest.mark.asyncio
async def test_wall_updates_with_posts(test_database, config, setup_account):
    """Test updating walls while maintaining post associations."""
    async with test_database.async_session_scope() as session:
        # Create initial wall with posts
        wall = Wall(id=1, accountId=setup_account.id, name="Original Wall", pos=1)
        session.add(wall)

        posts = [
            Post(
                id=i,
                accountId=setup_account.id,
                content=f"Post {i}",
                createdAt=datetime.now(timezone.utc),
            )
            for i in range(1, 3)
        ]
        for post in posts:
            session.add(post)
        wall.posts = posts
        await session.commit()

        # Update wall through process_account_walls
        new_wall_data = [
            {
                "id": 1,
                "pos": 2,  # Changed position
                "name": "Updated Wall",  # Changed name
                "description": "New description",
            }
        ]

        await process_account_walls(
            config, setup_account, new_wall_data, session=session
        )

        # Verify updates
        result = await session.execute(text("SELECT * FROM walls WHERE id = 1"))
        updated_wall = result.fetchone()
        assert updated_wall.name == "Updated Wall"
        assert updated_wall.pos == 2
        assert updated_wall.description == "New description"

        # Verify posts are still associated
        result = await session.execute(
            text(
                "SELECT * FROM posts JOIN wall_posts ON posts.id = wall_posts.postId WHERE wall_posts.wallId = 1 ORDER BY posts.content"
            )
        )
        wall_posts = result.fetchall()
        assert len(wall_posts) == 2
        assert [p.content for p in wall_posts] == ["Post 1", "Post 2"]


@pytest.mark.asyncio
async def test_wall_post_processing(test_database, config, setup_account):
    """Test processing wall posts from timeline-style data."""
    async with test_database.async_session_scope() as session:
        # Create wall
        wall = Wall(id=1, accountId=setup_account.id, name="Test Wall")
        session.add(wall)
        await session.commit()

        # Explicitly load the wall with its posts to avoid lazy loading
        stmt = select(Wall).options(selectinload(Wall.posts)).where(Wall.id == 1)
        result = await session.execute(stmt)
        wall = result.scalar_one()

        # Create posts data in timeline format
        posts_data = {
            "posts": [
                {
                    "id": i,
                    "accountId": setup_account.id,
                    "content": f"Post {i}",
                    "createdAt": int(datetime.now(timezone.utc).timestamp()),
                }
                for i in range(1, 4)
            ],
            "accounts": [{"id": setup_account.id, "username": setup_account.username}],
            "accountMedia": [],  # Empty list to avoid KeyError
        }

        # Process posts - use the eager-loaded wall object
        await process_wall_posts(config, None, wall.id, posts_data, session=session)

        # Verify
        result = await session.execute(text("SELECT * FROM walls WHERE id = 1"))
        wall = result.fetchone()
        result = await session.execute(
            text(
                "SELECT * FROM posts JOIN wall_posts ON posts.id = wall_posts.postId WHERE wall_posts.wallId = 1 ORDER BY posts.content"
            )
        )
        wall_posts = result.fetchall()
        assert len(wall_posts) == 3

        # Verify post content
        post_contents = [p.content for p in wall_posts]
        assert post_contents == ["Post 1", "Post 2", "Post 3"]

        # Verify post-wall relationships
        for post in wall_posts:
            result = await session.execute(
                text(
                    "SELECT * FROM wall_posts WHERE postId = :post_id AND wallId = :wall_id"
                ),
                {"post_id": post.id, "wall_id": wall.id},
            )
            assert result.fetchone() is not None
