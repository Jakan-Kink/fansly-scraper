"""Integration tests for database migration patterns.

Tests migration behavior including:
- Forward/backward migration
- Data preservation
- Error recovery
- Performance monitoring
"""

import time
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

import pytest
from sqlalchemy import MetaData, Table, create_engine, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from alembic import command
from alembic.config import Config as AlembicConfig
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from metadata import Account, Base, Message, Post
from metadata.database import Database


def get_current_revision(engine: Engine) -> str | None:
    """Get current database revision."""
    conn = engine.connect()
    context = MigrationContext.configure(conn)
    return context.get_current_revision()


def get_all_revisions(alembic_cfg: AlembicConfig) -> list[str]:
    """Get all available migration revisions."""
    script = ScriptDirectory.from_config(alembic_cfg)
    return [sc.revision for sc in script.walk_revisions()]


def create_test_data(session: Session) -> None:
    """Create test data for migration testing."""
    # Create test account
    account = Account(
        id=1,
        username="test_migration_user",
        createdAt=datetime.now(timezone.utc),
    )
    session.add(account)
    session.flush()

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

    session.commit()


def verify_test_data(session: Session) -> None:
    """Verify test data after migration."""
    # Verify account
    account = session.query(Account).filter_by(username="test_migration_user").first()
    assert account is not None

    # Verify posts
    posts = session.query(Post).filter_by(accountId=account.id).all()
    assert len(posts) == 5
    assert all("Test post" in post.content for post in posts)

    # Verify messages
    messages = session.query(Message).filter_by(senderId=account.id).all()
    assert len(messages) == 5
    assert all("Test message" in message.content for message in messages)


class TestMigrationPatterns:
    """Test suite for database migration patterns."""

    @pytest.fixture(scope="function")
    def database(self, test_database: Database) -> Database:
        """Get test database."""
        return test_database

    @pytest.fixture(scope="function")
    def alembic_cfg(self, database: Database) -> AlembicConfig:
        """Create Alembic config for testing."""
        cfg = AlembicConfig("alembic.ini")
        cfg.attributes["connection"] = database.sync_engine.connect()
        return cfg

    def test_forward_migration(self, database: Database, alembic_cfg: AlembicConfig):
        """Test forward migration with data preservation."""
        # Get initial revision
        initial_rev = get_current_revision(database.sync_engine)
        assert initial_rev is not None

        # Create test data
        with database.get_sync_session() as session:
            create_test_data(session)

        # Get all revisions
        revisions = get_all_revisions(alembic_cfg)
        assert len(revisions) > 0

        # Migrate forward through each revision
        start_time = time.time()
        for rev in revisions:
            if rev > initial_rev:
                command.upgrade(alembic_cfg, rev)

                # Verify data after each migration
                with database.get_sync_session() as session:
                    verify_test_data(session)

        duration = time.time() - start_time
        print(f"Forward migration time: {duration:.2f}s")

    def test_backward_migration(self, database: Database, alembic_cfg: AlembicConfig):
        """Test backward migration with data preservation."""
        # Get current revision
        current_rev = get_current_revision(database.sync_engine)
        assert current_rev is not None

        # Create test data
        with database.get_sync_session() as session:
            create_test_data(session)

        # Get all revisions
        revisions = get_all_revisions(alembic_cfg)
        assert len(revisions) > 0

        # Migrate backward through each revision
        start_time = time.time()
        for rev in reversed(revisions):
            if rev < current_rev:
                command.downgrade(alembic_cfg, rev)

                # Verify data after each migration
                with database.get_sync_session() as session:
                    verify_test_data(session)

        duration = time.time() - start_time
        print(f"Backward migration time: {duration:.2f}s")

    def test_migration_error_recovery(
        self, database: Database, alembic_cfg: AlembicConfig
    ):
        """Test recovery from migration errors."""
        # Get current revision
        current_rev = get_current_revision(database.sync_engine)
        assert current_rev is not None

        # Create test data
        with database.get_sync_session() as session:
            create_test_data(session)

        # Simulate migration error by corrupting the database
        with database.get_sync_session() as session:
            session.execute(text("DROP TABLE IF EXISTS alembic_version"))

        # Attempt migration
        with pytest.raises(OperationalError):
            command.upgrade(alembic_cfg, "head")

        # Recover by recreating alembic_version
        with database.get_sync_session() as session:
            session.execute(
                text(
                    """
                    CREATE TABLE alembic_version (
                        version_num VARCHAR(32) NOT NULL,
                        CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num)
                    )
                    """
                )
            )
            session.execute(
                text("INSERT INTO alembic_version (version_num) VALUES (:version)"),
                {"version": current_rev},
            )

        # Verify recovery
        assert get_current_revision(database.sync_engine) == current_rev

        # Verify data survived
        with database.get_sync_session() as session:
            verify_test_data(session)

    def test_migration_performance(
        self, database: Database, alembic_cfg: AlembicConfig
    ):
        """Test migration performance with large dataset."""
        BATCH_SIZE = 1000

        # Create large dataset
        start_time = time.time()
        with database.get_sync_session() as session:
            # Create base account
            account = Account(
                id=1,
                username="test_perf_user",
                createdAt=datetime.now(timezone.utc),
            )
            session.add(account)
            session.flush()

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
                session.bulk_save_objects(posts)
                session.flush()

        data_creation_time = time.time() - start_time
        print(f"Large dataset creation time: {data_creation_time:.2f}s")

        # Perform migration
        start_time = time.time()
        command.upgrade(alembic_cfg, "head")
        migration_time = time.time() - start_time
        print(f"Migration time for large dataset: {migration_time:.2f}s")

        # Verify data
        with database.get_sync_session() as session:
            post_count = session.query(Post).count()
            assert post_count == 10 * BATCH_SIZE

    def test_concurrent_migrations(
        self, database: Database, alembic_cfg: AlembicConfig
    ):
        """Test handling of concurrent migration attempts."""
        import threading
        import time

        def attempt_migration():
            try:
                command.upgrade(alembic_cfg, "head")
            except OperationalError as e:
                # Expected - database should be locked
                assert "database is locked" in str(e).lower()

        # Start first migration
        thread1 = threading.Thread(target=attempt_migration)
        thread1.start()

        # Attempt concurrent migration
        time.sleep(0.1)  # Give first migration a chance to start
        thread2 = threading.Thread(target=attempt_migration)
        thread2.start()

        # Wait for both to complete
        thread1.join()
        thread2.join()

        # Verify database is in consistent state
        current_rev = get_current_revision(database.sync_engine)
        assert current_rev is not None

    def test_index_recreation(self, database: Database, alembic_cfg: AlembicConfig):
        """Test index handling during migrations."""
        # Get initial indexes
        inspector = inspect(database.sync_engine)
        initial_indexes = {
            table: inspector.get_indexes(table) for table in inspector.get_table_names()
        }

        # Perform migration
        command.upgrade(alembic_cfg, "head")

        # Get final indexes
        inspector = inspect(database.sync_engine)
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
