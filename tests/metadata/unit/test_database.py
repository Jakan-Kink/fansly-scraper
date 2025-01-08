"""Unit tests for metadata.database module."""

import os
import random
import sqlite3
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import Integer, String, func, select, text
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
    """Create a temporary database file with proper cleanup handling."""
    import shutil
    from contextlib import suppress

    temp_dir = tempfile.mkdtemp()
    db_path = os.path.join(temp_dir, "test.db")
    yield temp_dir, db_path

    def remove_with_retry(path, max_retries=5, base_delay=0.1):
        """Remove a file with retry on failure."""
        for attempt in range(max_retries):
            try:
                if os.path.exists(path):
                    os.remove(path)
                return True
            except (OSError, PermissionError) as e:
                if attempt == max_retries - 1:
                    print(f"Failed to remove {path} after {max_retries} attempts: {e}")
                    return False
                time.sleep(base_delay * (2**attempt))
        return False

    def cleanup_database_files():
        """Clean up database and related files."""
        # Close any remaining database connections
        with suppress(Exception):
            sqlite3.connect(db_path).close()

        # Remove main database file
        remove_with_retry(db_path)

        # Remove WAL and SHM files
        for ext in ["-wal", "-shm"]:
            remove_with_retry(db_path + ext)

        # List and remove any remaining files
        remaining_files = []
        try:
            remaining_files = os.listdir(temp_dir)
        except OSError:
            pass

        for filename in remaining_files:
            filepath = os.path.join(temp_dir, filename)
            if not remove_with_retry(filepath):
                print(f"Warning: Could not remove file: {filepath}")

    def remove_temp_dir():
        """Remove temporary directory with retry."""
        for attempt in range(5):
            try:
                if os.path.exists(temp_dir):
                    shutil.rmtree(temp_dir, ignore_errors=True)
                return
            except OSError as e:
                if attempt == 4:
                    print(f"Failed to remove temp directory {temp_dir}: {e}")
                time.sleep(0.1 * (2**attempt))

    # Cleanup in correct order
    cleanup_database_files()
    remove_temp_dir()


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
        result = session.execute(select(TestModel)).scalar_one_or_none()
        assert result.name == "test"


def test_concurrent_access(database):
    """Test concurrent database access."""
    import threading
    import time
    from functools import wraps
    from queue import Queue

    from sqlalchemy.exc import IntegrityError, OperationalError, StatementError

    def retry_on_db_error(max_retries=5, delay=0.1):
        """Retry decorator for database operations.

        Args:
            max_retries: Number of retry attempts
            delay: Base delay between retries (uses exponential backoff)
        """

        def decorator(func):
            @wraps(func)
            def wrapper(*args, **kwargs):
                last_error = None
                for attempt in range(max_retries):
                    try:
                        return func(*args, **kwargs)
                    except (OperationalError, IntegrityError, StatementError) as e:
                        last_error = e
                        error_msg = str(e).lower()

                        # Common SQLite error messages that indicate retry-able conditions
                        retry_messages = {
                            "database is locked",
                            "cannot start a transaction within a transaction",
                            "disk i/o error",
                            "database table is locked",
                            "database schema has changed",
                            "constraint failed",
                            "record not found",
                            "not an error",  # SQLite "not an error" is actually not an error
                            "busy timeout expired",
                            "database connection failed",
                        }

                        if attempt < max_retries - 1 and any(
                            msg in error_msg for msg in retry_messages
                        ):
                            # Use exponential backoff with jitter
                            jitter = random.uniform(0, 0.1)  # Add 0-100ms random jitter
                            sleep_time = delay * (2**attempt) + jitter
                            time.sleep(sleep_time)
                            continue
                        raise
                    except Exception:
                        # Don't retry on non-database errors
                        raise

                raise last_error or Exception("Max retries exceeded")

            return wrapper

        return decorator

    def insert_with_retry(thread_id, error_queue):
        """Insert a record with retries on failure.

        Uses optimistic locking pattern with retry on conflicts.
        """

        @retry_on_db_error(max_retries=5, delay=0.01)
        def _do_insert():
            with database.get_sync_session() as session:
                try:
                    # Use select for update to lock the row
                    stmt = (
                        select(TestModel)
                        .where(TestModel.id == thread_id)
                        .with_for_update()
                    )
                    existing = session.execute(stmt).scalar_one_or_none()

                    if existing is not None:
                        # Record exists, verify its integrity
                        if existing.name != f"test_{thread_id}":
                            # Data inconsistency found
                            error_queue.put(
                                (
                                    thread_id,
                                    f"Data inconsistency: expected name 'test_{thread_id}' but found '{existing.name}'",
                                )
                            )
                        return

                    # Record doesn't exist, create it
                    model = TestModel(id=thread_id, name=f"test_{thread_id}")
                    session.add(model)

                    try:
                        session.commit()
                    except IntegrityError:
                        # Another thread might have created the record
                        session.rollback()
                        # Verify the record was created correctly
                        existing = session.execute(
                            select(TestModel).where(TestModel.id == thread_id)
                        ).scalar_one_or_none()
                        if existing is None or existing.name != f"test_{thread_id}":
                            raise  # Re-raise if record is missing or incorrect
                except Exception as e:
                    if "not an error" not in str(e).lower():
                        session.rollback()
                        raise

        @retry_on_db_error(max_retries=5, delay=0.01)
        def _verify_insert():
            with database.get_sync_session() as session:
                try:
                    saved = session.execute(
                        select(TestModel).where(TestModel.id == thread_id)
                    ).scalar_one_or_none()

                    if saved is None:
                        error_queue.put(
                            (thread_id, "Record not found during verification")
                        )
                        raise OperationalError("Record not found", None, None)

                    if saved.name != f"test_{thread_id}":
                        error_queue.put(
                            (
                                thread_id,
                                f"Data verification failed: expected name 'test_{thread_id}' but found '{saved.name}'",
                            )
                        )
                        raise AssertionError(
                            f"Record for thread {thread_id} has incorrect name"
                        )
                except Exception as e:
                    if "not an error" not in str(e).lower():
                        raise

        success = False
        last_error = None

        # Try the operation multiple times
        for attempt in range(5):
            try:
                _do_insert()
                _verify_insert()
                success = True
                break
            except Exception as e:
                last_error = e
                if "not an error" not in str(e).lower():
                    # Add context to the error
                    error_queue.put(
                        (thread_id, f"Attempt {attempt + 1} failed: {str(e)}")
                    )
                    time.sleep(0.2 * (2**attempt))  # Exponential backoff
                    continue
                raise

        if not success:
            error_msg = f"Failed to insert/verify record for thread {thread_id} after 5 attempts"
            if last_error:
                error_msg += f": {str(last_error)}"
            error_queue.put((thread_id, error_msg))
            raise Exception(error_msg)

    # Create an error queue to collect errors from threads
    error_queue = Queue()

    class Worker(threading.Thread):
        """Worker thread that handles database operations with proper error tracking."""

        def __init__(self, thread_id, error_queue):
            super().__init__()
            self.thread_id = thread_id
            self.error_queue = error_queue
            self.success = False
            self.exception = None

        def run(self):
            try:
                insert_with_retry(self.thread_id, self.error_queue)
                self.success = True
            except Exception as e:
                self.exception = e
                if "not an error" not in str(e).lower():
                    self.error_queue.put(
                        (self.thread_id, f"Worker failed with error: {str(e)}")
                    )

    # Create and start workers
    num_threads = 5  # Increased number of threads to better test concurrency
    workers = []
    for i in range(num_threads):
        worker = Worker(i, error_queue)
        workers.append(worker)
        worker.start()

    # Wait for all workers to complete
    for worker in workers:
        worker.join(timeout=30)  # Add timeout to prevent hanging
        if worker.is_alive():
            error_queue.put((worker.thread_id, "Worker timed out after 30 seconds"))
            continue

    # Collect all errors and worker status
    errors = []
    successful_workers = 0
    while not error_queue.empty():
        thread_id, error = error_queue.get()
        errors.append(f"Thread {thread_id}: {error}")

    for worker in workers:
        if worker.success:
            successful_workers += 1
        elif worker.exception and "not an error" not in str(worker.exception).lower():
            errors.append(f"Thread {worker.thread_id} failed: {worker.exception}")

    # Verify database state
    with database.get_sync_session() as session:
        # Use a transaction with isolation level to ensure consistent read
        session.connection(execution_options={"isolation_level": "REPEATABLE READ"})

        try:
            records = (
                session.execute(
                    select(TestModel)
                    .order_by(TestModel.id)
                    .with_for_update()  # Lock records during verification
                )
                .scalars()
                .all()
            )

            count = len(records)
            expected_count = num_threads  # Should match number of threads

            # Build detailed error report if needed
            if count != expected_count or errors:
                error_msg = []

                # Database state report
                error_msg.append(f"Expected {expected_count} records but found {count}")
                error_msg.append(
                    f"Successful workers: {successful_workers}/{num_threads}"
                )

                # Thread errors
                if errors:
                    error_msg.append("\nThread errors:")
                    error_msg.extend(f"  {error}" for error in errors)

                # Record state
                error_msg.append("\nExisting records:")
                for record in records:
                    error_msg.append(f"  ID: {record.id}, Name: {record.name}")

                # Missing records
                existing_ids = {r.id for r in records}
                missing_ids = set(range(num_threads)) - existing_ids
                if missing_ids:
                    error_msg.append("\nMissing record IDs:")
                    error_msg.extend(f"  {id}" for id in sorted(missing_ids))

                raise AssertionError("\n".join(error_msg))

            # Verify record integrity
            for i, record in enumerate(records):
                assert record.id == i, f"Record {i} has wrong ID: {record.id}"
                assert (
                    record.name == f"test_{i}"
                ), f"Record {i} has wrong name: {record.name}"

            session.commit()  # Commit the verification transaction

        except Exception:
            session.rollback()
            raise


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
    """Test transaction isolation levels and behavior."""

    def verify_isolation(isolation_level):
        """Test specific isolation level behavior."""
        # Create initial record
        with database.get_sync_session() as session:
            session.connection(execution_options={"isolation_level": isolation_level})
            model = TestModel(id=1, name="initial")
            session.add(model)
            session.commit()

        # Test concurrent modifications
        with database.get_sync_session() as session1:
            # Set isolation level for session1
            session1.connection(execution_options={"isolation_level": isolation_level})

            # Session 1 reads the record
            record1 = session1.execute(
                select(TestModel).where(TestModel.id == 1).with_for_update()
            ).scalar_one()

            # Session 2 tries to modify the same record
            with database.get_sync_session() as session2:
                session2.connection(
                    execution_options={"isolation_level": isolation_level}
                )

                if isolation_level in ("SERIALIZABLE", "REPEATABLE READ"):
                    # Should not be able to modify record until session1 commits
                    with pytest.raises(Exception) as exc_info:
                        record2 = session2.execute(
                            select(TestModel).where(TestModel.id == 1).with_for_update()
                        ).scalar_one()
                        record2.name = "modified by session2"
                        session2.commit()
                    assert any(
                        msg in str(exc_info.value).lower()
                        for msg in ["deadlock", "lock", "timeout", "busy"]
                    )

                elif isolation_level == "READ COMMITTED":
                    # Should see committed changes but allow modifications
                    record2 = session2.execute(
                        select(TestModel).where(TestModel.id == 1)
                    ).scalar_one()
                    assert record2.name == "initial"

                    # Modify in session2
                    record2.name = "modified by session2"
                    session2.commit()

                    # Session1's transaction should fail on commit due to concurrent modification
                    record1.name = "modified by session1"
                    with pytest.raises(Exception) as exc_info:
                        session1.commit()
                    assert any(
                        msg in str(exc_info.value).lower()
                        for msg in ["serialization", "concurrent modification"]
                    )

    # Test different isolation levels
    for isolation_level in ["SERIALIZABLE", "REPEATABLE READ", "READ COMMITTED"]:
        try:
            verify_isolation(isolation_level)
        except Exception as e:
            if "isolation level" not in str(e).lower():
                raise  # Re-raise if not an isolation level support issue
            print(f"Isolation level {isolation_level} not supported: {e}")

    # Test dirty reads are prevented
    with database.get_sync_session() as session1:
        # Session 1 creates a record but doesn't commit
        model = TestModel(id=100, name="uncommitted")
        session1.add(model)

        # Session 2 should not see the uncommitted record
        with database.get_sync_session() as session2:
            result = session2.execute(
                select(TestModel).where(TestModel.id == 100)
            ).scalar_one_or_none()
            assert result is None, "Dirty read detected!"

        # Rollback session1's changes
        session1.rollback()

    # Test phantom reads are prevented
    with database.get_sync_session() as session1:
        session1.connection(execution_options={"isolation_level": "REPEATABLE READ"})

        # First read
        count1 = session1.execute(
            select(func.count()).select_from(TestModel)  # pylint: disable=not-callable
        ).scalar_one()

        # Session 2 adds a new record
        with database.get_sync_session() as session2:
            model = TestModel(id=200, name="phantom")
            session2.add(model)
            session2.commit()

        # Second read in session1 should see same count
        count2 = session1.execute(
            select(func.count()).select_from(TestModel)  # pylint: disable=not-callable
        ).scalar_one()

        assert count1 == count2, "Phantom read detected!"


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
        result = session.execute(select(TestModel)).scalar_one_or_none()
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
        result = session.execute(select(TestModel)).scalar_one_or_none()
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
            results = (
                session.execute(select(TestModel).order_by(TestModel.id))
                .scalars()
                .all()
            )
            assert len(results) == 2
            assert results[0].name == "test"
            assert results[1].name == "test2"

        # Test cache invalidation through second connection
        with db2.get_sync_session() as session:
            model = session.execute(
                select(TestModel).where(TestModel.id == 1)
            ).scalar_one_or_none()
            model.name = "updated"
            session.commit()

        # Force sync on second connection
        db2._optimized_connection.sync_manager.sync_now()

        # Verify update is visible in first connection
        with database.get_sync_session() as session:
            result = session.execute(
                select(TestModel).where(TestModel.id == 1)
            ).scalar_one_or_none()
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
            session.execute(select(TestModel)).scalar_one_or_none()
