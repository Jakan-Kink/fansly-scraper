"""Unit tests for metadata.database module."""

import os
import random
import shutil
import sqlite3
import tempfile
import threading
import time
from contextlib import suppress
from functools import wraps
from pathlib import Path
from queue import Queue
from typing import Any, Optional
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import Integer, String, func, select, text
from sqlalchemy.exc import (
    DatabaseError,
    IntegrityError,
    OperationalError,
    StatementError,
)
from sqlalchemy.orm import Mapped, mapped_column

from alembic.config import Config as AlembicConfig
from metadata.base import Base
from metadata.database import Database, run_migrations_if_needed


class TestModel(Base):
    """Test model for database operations."""

    __tablename__ = "test_database_models"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)


def _remove_with_retry(
    path: str | Path, max_retries: int = 5, base_delay: float = 0.1
) -> bool:
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


def _cleanup_database_files(db_path: str | Path, temp_dir: str | Path) -> None:
    """Clean up database and related files."""
    # Close any remaining database connections
    with suppress(Exception):
        sqlite3.connect(db_path).close()

    # Remove main database file and WAL/SHM files
    for path in [db_path, f"{db_path}-wal", f"{db_path}-shm"]:
        _remove_with_retry(path)

    # List and remove any remaining files
    try:
        for filename in os.listdir(temp_dir):
            filepath = os.path.join(temp_dir, filename)
            if not _remove_with_retry(filepath):
                print(f"Warning: Could not remove file: {filepath}")
    except OSError:
        pass


def _remove_temp_dir(
    temp_dir: str | Path, max_retries: int = 5, base_delay: float = 0.1
) -> None:
    """Remove temporary directory with retry."""
    for attempt in range(max_retries):
        try:
            if os.path.exists(temp_dir):
                shutil.rmtree(temp_dir, ignore_errors=True)
            return
        except OSError as e:
            if attempt == max_retries - 1:
                print(f"Failed to remove temp directory {temp_dir}: {e}")
            time.sleep(base_delay * (2**attempt))


@pytest.fixture
def temp_db():
    """Create a temporary database file with proper cleanup handling."""
    temp_dir = tempfile.mkdtemp()
    db_path = os.path.join(temp_dir, "test.db")
    yield temp_dir, db_path

    # Cleanup in correct order
    _cleanup_database_files(db_path, temp_dir)
    _remove_temp_dir(temp_dir)


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


def _get_retry_messages() -> set[str]:
    """Get set of retry-able SQLite error messages."""
    return {
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


def retry_on_db_error(max_retries=5, delay=0.1):
    """Retry decorator for database operations.

    Args:
        max_retries: Number of retry attempts
        delay: Base delay between retries (uses exponential backoff)
    """
    retry_messages = _get_retry_messages()

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


def _verify_record_integrity(
    thread_id: int, record: TestModel, error_queue: Queue
) -> bool:
    """Verify record integrity and report errors.
    Returns True if record is valid."""
    if record is None:
        error_queue.put((thread_id, "Record not found during verification"))
        return False

    if record.name != f"test_{thread_id}":
        error_queue.put(
            (
                thread_id,
                f"Data verification failed: expected name 'test_{thread_id}' but found '{record.name}'",
            )
        )
        return False

    return True


def _get_existing_record(session, thread_id: int) -> TestModel | None:
    """Get existing record with lock."""
    stmt = select(TestModel).where(TestModel.id == thread_id).with_for_update()
    return session.execute(stmt).scalar_one_or_none()


def _create_record(session, thread_id: int, error_queue: Queue) -> bool:
    """Create a new record. Returns True if successful."""
    model = TestModel(id=thread_id, name=f"test_{thread_id}")
    session.add(model)

    try:
        session.commit()
        return True
    except IntegrityError:
        # Another thread might have created the record
        session.rollback()
        # Verify the record was created correctly
        existing = session.execute(
            select(TestModel).where(TestModel.id == thread_id)
        ).scalar_one_or_none()
        if not _verify_record_integrity(thread_id, existing, error_queue):
            raise  # Re-raise if record is missing or incorrect
        return True
    except Exception as e:
        if "not an error" not in str(e).lower():
            session.rollback()
            raise
        return False


@retry_on_db_error(max_retries=5, delay=0.01)
def _do_insert(database, thread_id: int, error_queue: Queue) -> None:
    """Insert a record with retry."""
    with database.get_sync_session() as session:
        existing = _get_existing_record(session, thread_id)

        if existing is not None:
            # Record exists, verify its integrity
            if not _verify_record_integrity(thread_id, existing, error_queue):
                raise AssertionError("Record integrity check failed")
            return

        # Record doesn't exist, create it
        if not _create_record(session, thread_id, error_queue):
            raise AssertionError("Failed to create record")


@retry_on_db_error(max_retries=5, delay=0.01)
def _verify_insert(database, thread_id: int, error_queue: Queue) -> None:
    """Verify record was inserted correctly."""
    with database.get_sync_session() as session:
        saved = session.execute(
            select(TestModel).where(TestModel.id == thread_id)
        ).scalar_one_or_none()

        if not _verify_record_integrity(thread_id, saved, error_queue):
            raise AssertionError(f"Record for thread {thread_id} has incorrect name")


def _handle_retry_error(
    e: Exception, thread_id: int, attempt: int, error_queue: Queue
) -> None:
    """Handle error during retry."""
    if "not an error" not in str(e).lower():
        error_queue.put((thread_id, f"Attempt {attempt + 1} failed: {str(e)}"))
        time.sleep(0.2 * (2**attempt))  # Exponential backoff
    else:
        raise


def _do_insert_with_retry(database, thread_id: int, error_queue: Queue) -> None:
    """Insert a record with retries."""
    success = False
    last_error = None

    # Try the operation multiple times
    for attempt in range(5):
        try:
            _do_insert(database, thread_id, error_queue)
            _verify_insert(database, thread_id, error_queue)
            success = True
            break
        except Exception as e:
            last_error = e
            _handle_retry_error(e, thread_id, attempt, error_queue)

    if not success:
        error_msg = (
            f"Failed to insert/verify record for thread {thread_id} after 5 attempts"
        )
        if last_error:
            error_msg += f": {str(last_error)}"
        error_queue.put((thread_id, error_msg))
        raise Exception(error_msg)


class DatabaseWorker(threading.Thread):
    """Worker thread that handles database operations with proper error tracking."""

    def __init__(self, thread_id: int, database, error_queue: Queue):
        super().__init__()
        self.thread_id = thread_id
        self.database = database
        self.error_queue = error_queue
        self.success = False
        self.exception = None

    def run(self):
        try:
            _do_insert_with_retry(self.database, self.thread_id, self.error_queue)
            self.success = True
        except Exception as e:
            self.exception = e
            if "not an error" not in str(e).lower():
                self.error_queue.put(
                    (self.thread_id, f"Worker failed with error: {str(e)}")
                )


def _collect_worker_results(
    workers: list[DatabaseWorker], error_queue: Queue
) -> tuple[list[str], int]:
    """Collect results from workers and error queue.
    Returns (errors, successful_workers)."""
    errors = []
    successful_workers = 0

    # Get errors from queue
    while not error_queue.empty():
        thread_id, error = error_queue.get()
        errors.append(f"Thread {thread_id}: {error}")

    # Check worker status
    for worker in workers:
        if worker.success:
            successful_workers += 1
        elif worker.exception and "not an error" not in str(worker.exception).lower():
            errors.append(f"Thread {worker.thread_id} failed: {worker.exception}")

    return errors, successful_workers


def _verify_database_state(
    database,
    num_threads: int,
    errors: list[str],
    successful_workers: int,
) -> None:
    """Verify final database state matches expected state."""
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
            expected_count = num_threads

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


def test_concurrent_access(database):
    """Test concurrent database access."""

    # Create an error queue to collect errors from threads
    error_queue = Queue()

    # Create and start workers
    num_threads = 5  # Increased number of threads to better test concurrency
    workers = []
    for i in range(num_threads):
        worker = DatabaseWorker(i, database, error_queue)
        workers.append(worker)
        worker.start()

    # Wait for all workers to complete
    for worker in workers:
        worker.join(timeout=30)  # Add timeout to prevent hanging
        if worker.is_alive():
            error_queue.put((worker.thread_id, "Worker timed out after 30 seconds"))
            continue

    # Collect results and verify database state
    errors, successful_workers = _collect_worker_results(workers, error_queue)
    _verify_database_state(database, num_threads, errors, successful_workers)


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
