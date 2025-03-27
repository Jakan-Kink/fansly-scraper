"""Integration tests for database migration patterns.

Tests migration behavior including:
- Forward/backward migration
- Data preservation
- Error recovery
- Performance monitoring
"""

from __future__ import annotations

import asyncio
import threading
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from alembic import command
from alembic.config import Config as AlembicConfig
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from metadata import Account, Message, Post

if TYPE_CHECKING:
    from metadata.database import Database


async def get_current_revision(engine: Engine) -> str | None:
    """Get current database revision."""
    conn = engine.connect()
    context = MigrationContext.configure(conn)
    return context.get_current_revision()


def get_all_revisions(alembic_cfg: AlembicConfig) -> list[str]:
    """Get all available migration revisions."""
    script = ScriptDirectory.from_config(alembic_cfg)
    return [sc.revision for sc in script.walk_revisions()]


async def create_test_data(session: AsyncSession) -> None:
    """Create test data for migration testing."""
    # Create test account
    account = Account(
        id=1,
        username="test_migration_user",
        createdAt=datetime.now(timezone.utc),
    )
    session.add(account)
    await session.flush()

    # Create test posts
    for i in range(5):
        post = Post(
            id=i + 1,
            accountId=account.id,
            content=f"Test post {i}",
            createdAt=datetime.now(timezone.utc),
        )
        session.add(post)

    # Create test messages
    for i in range(5):
        message = Message(
            id=i + 1,
            senderId=account.id,
            content=f"Test message {i}",
            createdAt=datetime.now(timezone.utc),
        )
        session.add(message)

    await session.commit()


async def verify_test_data(session: AsyncSession) -> None:
    """Verify test data after migration."""
    # Verify account
    result = await session.execute(
        text("SELECT * FROM accounts WHERE username = :username"),
        {"username": "test_migration_user"},
    )
    account = result.fetchone()
    assert account is not None

    # Verify posts
    result = await session.execute(
        text("SELECT * FROM posts WHERE accountId = :account_id"),
        {"account_id": account.id},
    )
    posts = result.fetchall()
    assert len(posts) == 5
    assert all("Test post" in post.content for post in posts)

    # Verify messages
    result = await session.execute(
        text("SELECT * FROM messages WHERE senderId = :sender_id"),
        {"sender_id": account.id},
    )
    messages = result.fetchall()
    assert len(messages) == 5
    assert all("Test message" in message.content for message in messages)


@pytest.fixture(scope="function")
async def alembic_cfg(test_database: Database) -> AlembicConfig:
    """Create Alembic config for testing."""
    cfg = AlembicConfig("alembic.ini")
    # Wait for the test_database fixture to be ready
    await asyncio.sleep(0)  # Give control back to event loop
    cfg.attributes["connection"] = test_database._sync_engine.connect()
    return cfg


async def test_forward_migration(test_database, alembic_cfg: AlembicConfig):
    """Test forward migration with data preservation."""
    # Get initial revision
    initial_rev = await get_current_revision(test_database._sync_engine)
    assert initial_rev is not None

    # Create test data
    async with test_database.async_session_scope() as session:
        await create_test_data(session)

    # Get all revisions
    revisions = get_all_revisions(alembic_cfg)
    assert len(revisions) > 0

    # Migrate forward through each revision
    start_time = time.time()
    for rev in revisions:
        if rev > initial_rev:
            command.upgrade(alembic_cfg, rev)

            # Verify data after each migration
            async with test_database.async_session_scope() as session:
                await verify_test_data(session)

    duration = time.time() - start_time
    print(f"Forward migration time: {duration:.2f}s")


async def test_backward_migration(test_database, alembic_cfg: AlembicConfig):
    """Test backward migration with data preservation."""
    # Get current revision
    current_rev = await get_current_revision(test_database._sync_engine)
    assert current_rev is not None

    # Create test data
    async with test_database.async_session_scope() as session:
        await create_test_data(session)

    # Get all revisions
    revisions = get_all_revisions(alembic_cfg)
    assert len(revisions) > 0

    # Migrate backward through each revision
    start_time = time.time()
    for rev in reversed(revisions):
        if rev < current_rev:
            command.downgrade(alembic_cfg, rev)

            # Verify data after each migration
            async with test_database.async_session_scope() as session:
                await verify_test_data(session)

    duration = time.time() - start_time
    print(f"Backward migration time: {duration:.2f}s")


async def test_migration_error_recovery(test_database, alembic_cfg: AlembicConfig):
    """Test recovery from migration errors."""
    # Get current revision
    current_rev = await get_current_revision(test_database._sync_engine)
    assert current_rev is not None

    # Create test data
    async with test_database.async_session_scope() as session:
        await create_test_data(session)

    # Simulate migration error by corrupting the database
    async with test_database.async_session_scope() as session:
        await session.execute(text("DROP TABLE IF EXISTS alembic_version"))

    # Attempt migration
    with pytest.raises(OperationalError):
        command.upgrade(alembic_cfg, "head")

    # Recover by recreating alembic_version
    async with test_database.async_session_scope() as session:
        await session.execute(
            text(
                """
                CREATE TABLE alembic_version (
                    version_num VARCHAR(32) NOT NULL,
                    CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num)
                )
                """
            )
        )
        await session.execute(
            text("INSERT INTO alembic_version (version_num) VALUES (:version)"),
            {"version": current_rev},
        )

    # Verify recovery
    assert await get_current_revision(test_database._sync_engine) == current_rev

    # Verify data survived
    async with test_database.async_session_scope() as session:
        await verify_test_data(session)


async def test_migration_performance(test_database, alembic_cfg: AlembicConfig):
    """Test migration performance with large dataset."""
    BATCH_SIZE = 1000

    # Create large dataset
    start_time = time.time()
    async with test_database.async_session_scope() as session:
        # Create base account
        account = Account(
            id=1,
            username="test_perf_user",
            createdAt=datetime.now(timezone.utc),
        )
        session.add(account)
        await session.flush()

        # Create posts in batches
        for batch in range(10):
            posts = []
            for i in range(BATCH_SIZE):
                post = Post(
                    id=batch * BATCH_SIZE + i + 1,
                    accountId=account.id,
                    content=f"Performance test post {i}",
                    createdAt=datetime.now(timezone.utc),
                )
                posts.append(post)
            session.add_all(posts)  # Use add_all instead of bulk_save_objects for async
            await session.flush()

    data_creation_time = time.time() - start_time
    print(f"Large dataset creation time: {data_creation_time:.2f}s")

    # Perform migration
    start_time = time.time()
    command.upgrade(alembic_cfg, "head")
    migration_time = time.time() - start_time
    print(f"Migration time for large dataset: {migration_time:.2f}s")

    # Verify data
    async with test_database.async_session_scope() as session:
        result = await session.execute(text("SELECT COUNT(*) FROM posts"))
        post_count = result.scalar()
        assert post_count == 10 * BATCH_SIZE


async def test_concurrent_migrations(test_database, alembic_cfg: AlembicConfig):
    """Test handling of concurrent migration attempts."""

    async def attempt_migration():
        try:
            command.upgrade(alembic_cfg, "head")
        except OperationalError as e:
            # Expected - database should be locked
            assert "database is locked" in str(e).lower()

    # Start first migration
    task1 = asyncio.create_task(attempt_migration())

    # Attempt concurrent migration
    await asyncio.sleep(0.1)  # Give first migration a chance to start
    task2 = asyncio.create_task(attempt_migration())

    # Wait for both to complete
    await asyncio.gather(task1, task2)

    # Verify database is in consistent state
    current_rev = await get_current_revision(test_database._sync_engine)
    assert current_rev is not None


async def test_index_recreation(test_database, alembic_cfg: AlembicConfig):
    """Test index handling during migrations."""
    # Get initial indexes
    inspector = inspect(test_database._sync_engine)
    initial_indexes = {
        table: inspector.get_indexes(table) for table in inspector.get_table_names()
    }

    # Perform migration
    command.upgrade(alembic_cfg, "head")

    # Get final indexes
    inspector = inspect(test_database._sync_engine)
    final_indexes = {
        table: inspector.get_indexes(table) for table in inspector.get_table_names()
    }

    # Verify indexes were preserved or updated as expected
    for table in initial_indexes:
        if table in final_indexes:
            # Compare index sets
            initial_names = {idx["name"] for idx in initial_indexes[table]}
            final_names = {idx["name"] for idx in final_indexes[table]}
            # Either indexes should be preserved or there should be new ones
            assert final_names, f"No indexes found for table {table}"
            # Ensure we haven't lost any critical indexes
            assert len(final_names) >= len(
                initial_names
            ), f"Lost indexes on table {table}"
