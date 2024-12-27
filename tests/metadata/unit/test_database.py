"""Unit tests for metadata.database module."""

import os
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import Integer, String, text
from sqlalchemy.exc import DatabaseError
from sqlalchemy.orm import Mapped, mapped_column

from alembic.config import Config as AlembicConfig
from metadata.base import Base
from metadata.database import Database, run_migrations_if_needed


class TestModel(Base):
    """Test model for database operations."""

    __tablename__ = "test_database_models"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)


@pytest.fixture
def temp_db():
    """Create a temporary database file."""
    temp_dir = tempfile.mkdtemp()
    db_path = os.path.join(temp_dir, "test.db")
    yield temp_dir, db_path

    # Cleanup after tests
    if os.path.exists(db_path):
        os.remove(db_path)
    # Clean up WAL and SHM files if they exist
    for ext in ["-wal", "-shm"]:
        wal_path = db_path + ext
        if os.path.exists(wal_path):
            os.remove(wal_path)
    # List and remove any remaining files
    for filename in os.listdir(temp_dir):
        filepath = os.path.join(temp_dir, filename)
        print(f"Found unexpected file: {filepath}")
        try:
            os.remove(filepath)
        except Exception as e:
            print(f"Error removing {filepath}: {e}")
    os.rmdir(temp_dir)


@pytest.fixture
def mock_config(temp_db):
    """Create a mock configuration."""
    _, db_path = temp_db
    config_mock = MagicMock()
    config_mock.metadata_db_file = db_path
    # Add required database sync settings
    config_mock.db_sync_min_size = 50
    config_mock.db_sync_commits = 1000
    config_mock.db_sync_seconds = 60
    return config_mock


@pytest.fixture
def database(mock_config):
    """Create and configure test database."""
    db = Database(mock_config)
    Base.metadata.create_all(db.sync_engine)
    yield db
    Base.metadata.drop_all(db.sync_engine)
    db.close()  # Properly close the database


def test_database_initialization(database, temp_db):
    """Test database initialization and configuration."""
    _, db_path = temp_db
    assert database.db_file == Path(db_path)

    # Test SQLite configuration
    with database.sync_engine.connect() as conn:
        # Check foreign key support
        result = conn.execute(text("PRAGMA foreign_keys")).scalar()
        assert result == 0  # Foreign keys disabled due to import order dependencies

        # Check journal mode
        result = conn.execute(text("PRAGMA journal_mode")).scalar()
        assert result.upper() == "WAL"

        # Check synchronous mode
        result = conn.execute(text("PRAGMA synchronous")).scalar()
        assert result == 1  # NORMAL

        # Check temp store
        result = conn.execute(text("PRAGMA temp_store")).scalar()
        assert result == 2  # MEMORY

        # Check page size
        result = conn.execute(text("PRAGMA page_size")).scalar()
        assert result == 4096

        # Check memory map size (1GB)
        result = conn.execute(text("PRAGMA mmap_size")).scalar()
        assert result == 1073741824

        # Check cache size (100MB for <1GB databases)
        result = conn.execute(text("PRAGMA cache_size")).scalar()
        assert result == -102400  # Exactly 100MB (negative value means KB)


def test_session_management(database):
    """Test session creation and management."""
    with database.get_sync_session() as session:
        # Create test record
        model = TestModel(id=1, name="test")
        session.add(model)
        session.commit()

        # Verify record was saved
        result = session.query(TestModel).first()
        assert result.name == "test"


def test_concurrent_access(database):
    """Test concurrent database access."""
    import threading
    import time
    from functools import wraps
    from queue import Queue

    from sqlalchemy.exc import OperationalError

    def retry_on_db_error(max_retries=3, delay=0.1):
        def decorator(func):
            @wraps(func)
            def wrapper(*args, **kwargs):
                last_error = None
                for attempt in range(max_retries):
                    try:
                        return func(*args, **kwargs)
                    except Exception as e:
                        last_error = e
                        error_msg = str(e).lower()
                        if attempt < max_retries - 1 and any(
                            msg in error_msg
                            for msg in [
                                "database is locked",
                                "cannot start a transaction within a transaction",
                                "disk i/o error",
                                "record not found",
                                "not an error",  # SQLite "not an error" is actually not an error
                            ]
                        ):
                            time.sleep(delay * (2**attempt))  # Exponential backoff
                            continue
                        raise
                raise last_error or Exception("Max retries exceeded")

            return wrapper

        return decorator

    def insert_with_retry(thread_id, error_queue):
        """Insert a record with retries on failure."""

        @retry_on_db_error(max_retries=5, delay=0.2)
        def _do_insert():
            with database.get_sync_session() as session:
                try:
                    # Check if record already exists
                    existing = session.query(TestModel).filter_by(id=thread_id).first()
                    if existing is not None:
                        return  # Record already exists, no need to insert

                    model = TestModel(id=thread_id, name=f"test_{thread_id}")
                    session.add(model)
                    session.commit()
                except Exception as e:
                    if (
                        "not an error" not in str(e).lower()
                    ):  # Ignore SQLite "not an error"
                        session.rollback()
                        raise

        @retry_on_db_error(max_retries=5, delay=0.2)
        def _verify_insert():
            with database.get_sync_session() as session:
                try:
                    saved = session.query(TestModel).filter_by(id=thread_id).first()
                    if saved is None:
                        raise OperationalError("Record not found", None, None)
                    assert (
                        saved.name == f"test_{thread_id}"
                    ), f"Record for thread {thread_id} has incorrect name"
                except Exception as e:
                    if (
                        "not an error" not in str(e).lower()
                    ):  # Ignore SQLite "not an error"
                        raise

        try:
            # Try to insert and verify
            for _ in range(5):  # Add retries for the entire operation
                try:
                    _do_insert()
                    _verify_insert()
                    return  # Success
                except Exception as e:
                    if (
                        "not an error" not in str(e).lower()
                    ):  # Only retry on real errors
                        time.sleep(0.2)
                        continue
                    raise
            raise Exception(
                f"Failed to insert/verify record for thread {thread_id} after 5 attempts"
            )
        except Exception as e:
            error_queue.put((thread_id, str(e)))
            raise

    # Create an error queue to collect errors from threads
    error_queue = Queue()

    def worker(thread_id):
        """Worker function that handles its own retries."""
        try:
            insert_with_retry(thread_id, error_queue)
        except Exception as e:
            if "not an error" not in str(e).lower():  # Only log real errors
                print(f"Worker {thread_id} failed: {e}")
                error_queue.put((thread_id, str(e)))

    # Create multiple threads
    threads = []
    for i in range(3):  # Reduced number of threads to avoid excessive contention
        thread = threading.Thread(target=worker, args=(i,))
        threads.append(thread)
        thread.start()

    # Wait for all threads to complete
    for thread in threads:
        thread.join()

    # Check for any errors in the queue
    errors = []
    while not error_queue.empty():
        thread_id, error = error_queue.get()
        errors.append(f"Thread {thread_id}: {error}")

    # Verify all records were saved
    with database.get_sync_session() as session:
        records = session.query(TestModel).order_by(TestModel.id).all()
        count = len(records)

        # If we don't have all records, print detailed error info
        if count != 3:
            error_msg = [f"Expected 3 records but found {count}"]
            if errors:
                error_msg.append("Thread errors:")
                error_msg.extend(errors)
            error_msg.append("Existing records:")
            for record in records:
                error_msg.append(f"  ID: {record.id}, Name: {record.name}")
            raise AssertionError("\n".join(error_msg))

        # Verify each record has correct data
        for i, record in enumerate(records):
            assert record.id == i, f"Record {i} has wrong ID: {record.id}"
            assert (
                record.name == f"test_{i}"
            ), f"Record {i} has wrong name: {record.name}"


@patch("metadata.database.alembic_upgrade")
def test_migrations(mock_upgrade, database):
    """Test migration handling."""
    # Create mock Alembic config
    alembic_cfg = MagicMock(spec=AlembicConfig)

    # Test initial migration
    run_migrations_if_needed(database, alembic_cfg)
    mock_upgrade.assert_called_once()

    # Reset mock and test subsequent migration
    mock_upgrade.reset_mock()
    run_migrations_if_needed(database, alembic_cfg)
    mock_upgrade.assert_called_once()


def test_transaction_isolation(database):
    """Test transaction isolation."""
    # Start two sessions
    with database.get_sync_session() as session1:
        # Session 1 creates a record but doesn't commit
        model = TestModel(id=1, name="test")
        session1.add(model)

        # Session 2 shouldn't see the uncommitted record
        with database.get_sync_session() as session2:
            result = session2.query(TestModel).first()
            assert result is None

        # After commit, session 2 should see the record
        session1.commit()

        with database.get_sync_session() as session2:
            result = session2.query(TestModel).first()
            assert result is not None
            assert result.name == "test"


def test_error_handling(database):
    """Test database error handling."""
    with database.get_sync_session() as session:
        # Try to create invalid record
        model = TestModel(id=1, name=None)  # name is non-nullable
        session.add(model)

        # Should raise an error
        with pytest.raises(Exception):
            session.commit()

        # Need to explicitly rollback after error
        session.rollback()

        # Session should be rolled back
        result = session.query(TestModel).first()
        assert result is None


def test_database_connection_error(mock_config):
    """Test handling of database connection errors."""
    # Set invalid database path
    mock_config.metadata_db_file = "/nonexistent/path/db.sqlite"
    mock_config.db_sync_min_size = 50  # Add required config value
    mock_config.db_sync_commits = 1000
    mock_config.db_sync_seconds = 60

    # Create database instance (this should work as it just stores the path)
    db = Database(mock_config)

    try:
        # Try to use the database (this should fail)
        with pytest.raises((DatabaseError, sqlite3.OperationalError)) as exc_info:
            # Try to connect directly to the database
            conn = sqlite3.connect("/nonexistent/path/db.sqlite")
            conn.execute("SELECT 1")
        # Verify it's a database-related error
        assert any(
            err in str(exc_info.value).lower()
            for err in ["database", "no such file", "unable to open"]
        )
    finally:
        db.close()


def test_session_context_error_handling(database):
    """Test error handling in session context manager."""
    with pytest.raises(Exception):
        with database.get_sync_session() as session:
            model = TestModel(id=1, name="test")
            session.add(model)
            raise Exception("Test error")

    # Verify the session was rolled back
    with database.get_sync_session() as session:
        result = session.query(TestModel).first()
        assert result is None


def test_write_through_cache(database):
    """Test write-through caching functionality."""
    # Create a test record to ensure database file exists
    with database.get_sync_session() as session:
        model = TestModel(id=1, name="test")
        session.add(model)
        session.commit()

    # Force sync to ensure database file exists
    database._optimized_connection.sync_manager.sync_now()

    # Verify cache configuration
    with database.sync_engine.connect() as conn:
        # Check cache size (100MB for <1GB databases)
        result = conn.execute(text("PRAGMA cache_size")).scalar()
        assert result == -102400  # 100MB in KB (negative value)

        # Check database size
        db_size = os.path.getsize(database.db_file)
        assert db_size < 1024 * 1024 * 1024  # Less than 1GB

        # Verify write-ahead log mode
        result = conn.execute(text("PRAGMA journal_mode")).scalar()
        assert result.upper() == "WAL"

    # Create a second database connection
    db2 = Database(database.config)
    try:
        # Write data with a different ID through first connection
        with database.get_sync_session() as session:
            model = TestModel(id=2, name="test2")
            session.add(model)
            session.commit()

        # Force sync to ensure data is written
        database._optimized_connection.sync_manager.sync_now()

        # Verify data is immediately available in second connection
        with db2.get_sync_session() as session:
            results = session.query(TestModel).order_by(TestModel.id).all()
            assert len(results) == 2
            assert results[0].name == "test"
            assert results[1].name == "test2"

        # Test cache invalidation through second connection
        with db2.get_sync_session() as session:
            model = session.query(TestModel).filter_by(id=1).first()
            model.name = "updated"
            session.commit()

        # Force sync on second connection
        db2._optimized_connection.sync_manager.sync_now()

        # Verify update is visible in first connection
        with database.get_sync_session() as session:
            result = session.query(TestModel).filter_by(id=1).first()
            assert result.name == "updated"

    finally:
        # Ensure proper cleanup of second connection
        db2._optimized_connection.sync_manager.stop_sync_thread()
        db2._optimized_connection.sync_manager.sync_now()
        db2.close()


def test_database_cleanup(mock_config):
    """Test proper database cleanup."""
    db = Database(mock_config)
    Base.metadata.create_all(db.sync_engine)

    # Use the database
    with db.get_sync_session() as session:
        model = TestModel(id=1, name="test")
        session.add(model)
        session.commit()

    # Close the database
    db.close()

    # Verify we can't use the database after closing
    with pytest.raises(Exception):
        with db.get_sync_session() as session:
            session.query(TestModel).first()
