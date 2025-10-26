"""Tests for database integration with main application."""

import asyncio
import os
import threading
import uuid
from urllib.parse import quote_plus

import pytest
from sqlalchemy import create_engine, text

from config import FanslyConfig
from metadata.database import Database


# Removed deprecated SQLite-specific fixtures and test classes:
# - config_sqlite fixture
# - TestDatabaseCleanup (cleanup now tested in TestDatabaseThreading)
# - TestPerCreatorDatabase (PostgreSQL uses schema-based isolation)
# - TestDatabaseMigrations (PostgreSQL migration tests covered elsewhere)


class TestDatabaseThreading:
    """Test database thread safety."""

    @pytest.fixture
    def thread_test_database(self):
        """Create a temporary database for threading tests using PostgreSQL with UUID isolation."""
        # Generate unique database name
        test_db_name = f"test_{uuid.uuid4().hex[:16]}"

        # Connect to postgres database to create test database
        admin_config = FanslyConfig(program_version="test")
        admin_config.pg_host = os.getenv("FANSLY_PG_HOST", "localhost")
        admin_config.pg_port = int(os.getenv("FANSLY_PG_PORT", "5432"))
        admin_config.pg_database = "postgres"  # Connect to default postgres DB
        admin_config.pg_user = os.getenv("FANSLY_PG_USER", "fansly_user")
        admin_config.pg_password = os.getenv("FANSLY_PG_PASSWORD", "")

        # Build admin connection URL
        password = admin_config.pg_password or ""
        password_encoded = quote_plus(password)
        admin_url = f"postgresql://{admin_config.pg_user}:{password_encoded}@{admin_config.pg_host}:{admin_config.pg_port}/postgres"

        # Create the test database
        admin_engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
        with admin_engine.connect() as conn:
            conn.execute(text(f'CREATE DATABASE "{test_db_name}"'))
        admin_engine.dispose()

        # Now create config pointing to our test database
        config = FanslyConfig(program_version="test")
        config.pg_host = admin_config.pg_host
        config.pg_port = admin_config.pg_port
        config.pg_database = test_db_name
        config.pg_user = admin_config.pg_user
        config.pg_password = admin_config.pg_password
        config.metadata_db_file = None

        # Create the database instance (with migrations)
        database = Database(config, skip_migrations=False)

        yield database

        # Clean up - close connections and drop database
        try:
            database.close_sync()

            # Reconnect to postgres database to drop test database
            admin_engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
            with admin_engine.connect() as conn:
                # Terminate any remaining connections
                conn.execute(
                    text(
                        f"""
                    SELECT pg_terminate_backend(pg_stat_activity.pid)
                    FROM pg_stat_activity
                    WHERE pg_stat_activity.datname = '{test_db_name}'
                    AND pid <> pg_backend_pid()
                """
                    )
                )
                conn.execute(text(f'DROP DATABASE IF EXISTS "{test_db_name}"'))
            admin_engine.dispose()
        except Exception as e:
            print(f"Error during database cleanup: {e}")

    @pytest.mark.asyncio
    async def test_concurrent_access(self, thread_test_database):
        """Test concurrent database access."""

        async def worker(i: int) -> None:
            async with thread_test_database.async_session_scope() as session:
                await session.execute(
                    text("CREATE TABLE IF NOT EXISTS test (id INTEGER PRIMARY KEY)")
                )
                # Fix: Separate the SQL text and parameters
                await session.execute(text("INSERT INTO test VALUES (:id)"), {"id": i})

        # Run concurrent workers
        workers = [worker(i) for i in range(5)]
        await asyncio.gather(*workers)

        # Verify data
        async with thread_test_database.async_session_scope() as session:
            result = await session.execute(text("SELECT COUNT(*) FROM test"))
            count = result.scalar()
            assert count == 5

    def test_thread_local_connections(self, thread_test_database):
        """Test thread-local connection management.

        PostgreSQL has robust threading support, so this test is now enabled.
        """

        results = []
        errors = []

        # Create table once before threading to avoid race condition
        with thread_test_database.session_scope() as session:
            session.execute(
                text("CREATE TABLE IF NOT EXISTS test (id INTEGER PRIMARY KEY)")
            )

        def worker(worker_id):
            try:
                with thread_test_database.session_scope() as session:
                    # PostgreSQL: Use parameterized queries
                    session.execute(
                        text("INSERT INTO test (id) VALUES (:id)"), {"id": worker_id}
                    )

                    # Successfully completed
                    results.append(True)
            except Exception as e:
                # Capture any errors that occur
                errors.append(f"Worker {worker_id} error: {e}")

        # Create and start threads with unique IDs
        threads = [threading.Thread(target=worker, args=(i,)) for i in range(3)]
        for t in threads:
            t.start()

        # Join with timeout to prevent test from hanging
        for t in threads:
            t.join(timeout=5)

        # Report any errors that occurred
        if errors:
            pytest.fail(f"Thread errors occurred: {errors}")

        # Check results - should have 3 successful completions
        assert len(results) == 3, (
            f"Expected 3 successful threads, got {len(results)}: {results}, errors: {errors}"
        )

        # Verify the data in the database
        with thread_test_database.session_scope() as session:
            count = session.execute(text("SELECT COUNT(*) FROM test")).scalar()
            assert count == 3, f"Expected 3 records in the database, got {count}"
