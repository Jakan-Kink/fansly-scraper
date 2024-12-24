"""Unit tests for metadata.database module."""

import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import Integer, String, text
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
    os.rmdir(temp_dir)


@pytest.fixture
def mock_config(temp_db):
    """Create a mock configuration."""
    _, db_path = temp_db
    config_mock = MagicMock()
    config_mock.metadata_db_file = db_path
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
        assert result == 1

        # Check journal mode
        result = conn.execute(text("PRAGMA journal_mode")).scalar()
        assert result.upper() == "WAL"

        # Check cache size (100MB)
        result = conn.execute(text("PRAGMA cache_size")).scalar()
        assert result == -102400

        # Check page size
        result = conn.execute(text("PRAGMA page_size")).scalar()
        assert result == 4096

        # Check memory map size (1GB)
        result = conn.execute(text("PRAGMA mmap_size")).scalar()
        assert result == 1073741824


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

    def worker(thread_id):
        with database.get_sync_session() as session:
            model = TestModel(id=thread_id, name=f"test_{thread_id}")
            session.add(model)
            session.commit()

    # Create multiple threads
    threads = []
    for i in range(5):
        thread = threading.Thread(target=worker, args=(i,))
        threads.append(thread)
        thread.start()

    # Wait for all threads to complete
    for thread in threads:
        thread.join()

    # Verify all records were saved
    with database.get_sync_session() as session:
        count = session.query(TestModel).count()
        assert count == 5


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

    # Create database instance (this should work as it just stores the path)
    db = Database(mock_config)

    try:
        # Try to use the database (this should fail)
        with pytest.raises(Exception) as exc_info:  # noqa: F841
            with db.get_sync_session() as session:
                session.execute(text("SELECT 1"))
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
    # Write data
    with database.get_sync_session() as session:
        model = TestModel(id=1, name="test")
        session.add(model)
        session.commit()

    # Verify data is immediately available in a new connection
    db2 = Database(database.config)
    try:
        with db2.get_sync_session() as session:
            result = session.query(TestModel).first()
            assert result is not None
            assert result.name == "test"
    finally:
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
