"""Integration tests for database migration patterns.

Tests migration behavior including:
- Forward/backward migration
- Data preservation
- Error recovery
- Performance monitoring
"""

from __future__ import annotations

import asyncio
import tempfile
import threading
import time
import warnings
from collections.abc import AsyncGenerator
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import exc as sa_exc
from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from alembic import command
from alembic.command import upgrade as alembic_upgrade
from alembic.config import Config as AlembicConfig
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from config import FanslyConfig
from metadata import Account, Message, Post
from metadata.database import Database
from tests.metadata.conftest import TestDatabase
from tests.metadata.integration.common import measure_time


async def get_current_revision(conn) -> str | None:
    """Get current database revision."""
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


@pytest.fixture(scope="function")
async def clean_database(config: FanslyConfig) -> AsyncGenerator[TestDatabase]:
    """Create a clean database without migrations for migration testing."""
    # Create temp file that will be automatically removed
    with tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False) as tmp:
        temp_path = Path(tmp.name)
        db = None
        config = FanslyConfig(program_version="test")
        config.metadata_db_file = temp_path
        db = TestDatabase(config, skip_migrations=True)
        yield db


def get_migration_revision(engine) -> str | None:
    """Get current migration revision with proper connection handling."""
    with engine.connect() as conn:
        with conn.begin():
            return MigrationContext.configure(conn).get_current_revision()


def run_migration(engine, target_revision: str):
    """Run migration with proper connection handling."""
    # First run the migration
    with engine.begin() as conn:
        cfg = AlembicConfig("alembic.ini")
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, target_revision)

    # Then get the revision with a new connection
    for _ in range(3):  # Retry a few times in case SQLite needs a moment
        try:
            with engine.begin() as conn:
                return MigrationContext.configure(conn).get_current_revision()
        except Exception:
            time.sleep(0.1)

    # Final attempt
    with engine.begin() as conn:
        return MigrationContext.configure(conn).get_current_revision()


@pytest.mark.asyncio
@measure_time
async def test_migration_performance(
    request,
    clean_database: Database,
    alembic_cfg: AlembicConfig,
):
    """Test migration performance with large dataset."""
    if clean_database is None or alembic_cfg is None:
        pytest.skip(
            "Required fixtures 'clean_database' and/or 'alembic_cfg' not available"
        )
    BATCH_SIZE = 1000

    # Initial setup - use separate connection
    run_migration(clean_database._sync_engine, "4416b99f028e")

    # Create large dataset
    start_time = time.time()
    async with clean_database.async_session_scope() as session:
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
            session.add_all(posts)
            await session.flush()

    data_creation_time = time.time() - start_time
    print(f"Large dataset creation time: {data_creation_time:.2f}s")

    # Perform migration with fresh connection
    start_time = time.time()
    run_migration(clean_database._sync_engine, "head")
    migration_time = time.time() - start_time
    print(f"Migration time for large dataset: {migration_time:.2f}s")

    # Verify data
    async with clean_database.async_session_scope() as session:
        result = await session.execute(text("SELECT COUNT(*) FROM posts"))
        post_count = result.scalar()
        assert post_count == 10 * BATCH_SIZE


@pytest.mark.asyncio
async def test_forward_migration(clean_database, alembic_cfg: AlembicConfig):
    """Test forward migration with data preservation."""
    # Initialize database and get initial revision
    run_migration(clean_database._sync_engine, "4416b99f028e")
    initial_rev = get_migration_revision(clean_database._sync_engine)
    assert initial_rev is not None

    # Create test data
    async with clean_database.async_session_scope() as session:
        await create_test_data(session)

    # Get all revisions
    revisions = get_all_revisions(alembic_cfg)
    assert len(revisions) > 0

    # Migrate forward through each revision with fresh connections
    start_time = time.time()
    for rev in revisions:
        if rev > initial_rev:
            run_migration(clean_database._sync_engine, rev)
            async with clean_database.async_session_scope() as session:
                await verify_test_data(session)

    duration = time.time() - start_time
    print(f"Forward migration time: {duration:.2f}s")


@pytest.mark.asyncio
async def test_backward_migration(clean_database, alembic_cfg: AlembicConfig):
    """Test backward migration with data preservation."""
    # Get current revision with fresh connection
    run_migration(clean_database._sync_engine, "head")
    with clean_database._sync_engine.connect() as conn:
        current_rev = MigrationContext.configure(conn).get_current_revision()
    assert current_rev is not None

    # Create test data
    async with clean_database.async_session_scope() as session:
        await create_test_data(session)

    # Get all revisions
    revisions = get_all_revisions(alembic_cfg)
    assert len(revisions) > 0

    # Migrate backward through each revision with fresh connections
    start_time = time.time()
    for rev in reversed(revisions):
        if rev < current_rev:
            run_migration(clean_database._sync_engine, rev)
            async with clean_database.async_session_scope() as session:
                await verify_test_data(session)

    duration = time.time() - start_time
    print(f"Backward migration time: {duration:.2f}s")


@pytest.mark.asyncio
async def test_migration_error_recovery(
    clean_database: Database,
    alembic_cfg: AlembicConfig,
):
    """Test recovery from migration errors."""
    # First run migration to head to establish initial state
    run_migration(clean_database._sync_engine, "head")

    # Get current revision with fresh connection
    with clean_database._sync_engine.connect() as conn:
        current_rev = MigrationContext.configure(conn).get_current_revision()
    assert current_rev is not None

    # Create test data and simulate error by ensuring table is dropped
    async with clean_database.async_session_scope() as session:
        await create_test_data(session)
        await session.execute(text("DROP TABLE IF EXISTS alembic_version"))
        await session.commit()

    # Attempt migration (should fail)
    with pytest.raises(OperationalError):
        run_migration(clean_database._sync_engine, "head")

    # Recover by recreating alembic_version, ensuring it doesn't exist first
    async with clean_database.async_session_scope() as session:
        await session.execute(text("DROP TABLE IF EXISTS alembic_version"))
        await session.commit()

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
        await session.commit()

    # Verify recovery with fresh connection
    with clean_database._sync_engine.connect() as conn:
        recovered_rev = MigrationContext.configure(conn).get_current_revision()
    assert recovered_rev == current_rev

    # Verify data survived
    async with clean_database.async_session_scope() as session:
        await verify_test_data(session)


@pytest.mark.asyncio
async def test_concurrent_migrations(clean_database: Database):
    """Test handling of concurrent migration attempts."""

    async def attempt_migration():
        try:
            run_migration(clean_database._sync_engine, "head")
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
    with clean_database._sync_engine.connect() as conn:
        current_rev = await get_current_revision(conn)
        assert current_rev is not None


@pytest.mark.asyncio
async def test_index_recreation(clean_database, alembic_cfg: AlembicConfig):
    """Test index handling during migrations."""

    def get_supported_indexes(inspector, table):
        """Get indexes, filtering out unsupported ones."""
        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore",
                category=sa_exc.SAWarning,
                message=r"Skipped unsupported reflection of expression-based index.*",
            )
            return inspector.get_indexes(table)

    # Get initial indexes using a fresh connection
    with clean_database._sync_engine.connect() as conn:
        inspector = inspect(conn)
        initial_indexes = {
            table: get_supported_indexes(inspector, table)
            for table in inspector.get_table_names()
        }

    # Perform migration with its own connection
    run_migration(clean_database._sync_engine, "head")

    # Get final indexes with another fresh connection
    with clean_database._sync_engine.connect() as conn:
        inspector = inspect(conn)
        final_indexes = {
            table: get_supported_indexes(inspector, table)
            for table in inspector.get_table_names()
        }

    # Verify indexes were preserved or updated as expected
    for table in initial_indexes:
        if table in final_indexes:
            initial_names = {idx["name"] for idx in initial_indexes[table]}
            final_names = {idx["name"] for idx in final_indexes[table]}
            # Filter out expression-based indexes from the comparison
            initial_names = {
                name for name in initial_names if not name.endswith("_lower")
            }
            final_names = {name for name in final_names if not name.endswith("_lower")}
            assert final_names, f"No indexes found for table {table}"
            assert len(final_names) >= len(
                initial_names
            ), f"Lost indexes on table {table}"
