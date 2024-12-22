"""Unit tests for metadata.database module."""

import os
import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import MagicMock, patch

from sqlalchemy import Column, Integer, String, text
from sqlalchemy.orm import Mapped, mapped_column

from alembic.config import Config as AlembicConfig
from metadata.base import Base
from metadata.database import Database, run_migrations_if_needed


class TestModel(Base):
    """Test model for database operations."""

    __tablename__ = "test_models"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)


class TestDatabase(TestCase):
    """Test cases for Database class and related functionality."""

    def setUp(self):
        """Set up test database and configuration."""
        # Create temporary database file
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test.db")

        # Mock FanslyConfig
        self.config_mock = MagicMock()
        self.config_mock.metadata_db_file = self.db_path

        # Create database instance
        self.database = Database(self.config_mock)

        # Create tables
        Base.metadata.create_all(self.database.sync_engine)

    def tearDown(self):
        """Clean up test database."""
        Base.metadata.drop_all(self.database.sync_engine)
        self.database.sync_engine.dispose()

        # Remove temporary files
        if os.path.exists(self.db_path):
            os.remove(self.db_path)
        os.rmdir(self.temp_dir)

    def test_database_initialization(self):
        """Test database initialization and configuration."""
        self.assertEqual(self.database.db_file, Path(self.db_path))
        self.assertEqual(self.database.config, self.config_mock)

        # Test SQLite configuration
        with self.database.sync_engine.connect() as conn:
            # Check foreign key support
            result = conn.execute(text("PRAGMA foreign_keys")).scalar()
            self.assertEqual(result, 1)

            # Check journal mode
            result = conn.execute(text("PRAGMA journal_mode")).scalar()
            self.assertEqual(result.upper(), "WAL")

    def test_session_management(self):
        """Test session creation and management."""
        # Test synchronous session
        with self.database.get_sync_session() as session:
            # Create test record
            model = TestModel(id=1, name="test")
            session.add(model)
            session.commit()

            # Verify record was saved
            result = session.query(TestModel).first()
            self.assertEqual(result.name, "test")

    def test_concurrent_access(self):
        """Test concurrent database access."""
        import threading

        def worker():
            with self.database.get_sync_session() as session:
                model = TestModel(
                    id=threading.get_ident(), name=f"test_{threading.get_ident()}"
                )
                session.add(model)
                session.commit()

        # Create multiple threads
        threads = []
        for _ in range(5):
            thread = threading.Thread(target=worker)
            threads.append(thread)
            thread.start()

        # Wait for all threads to complete
        for thread in threads:
            thread.join()

        # Verify all records were saved
        with self.database.get_sync_session() as session:
            count = session.query(TestModel).count()
            self.assertEqual(count, 5)

    @patch("metadata.database.alembic_upgrade")
    def test_migrations(self, mock_upgrade):
        """Test migration handling."""
        # Create mock Alembic config
        alembic_cfg = MagicMock(spec=AlembicConfig)

        # Test initial migration
        run_migrations_if_needed(self.database, alembic_cfg)
        mock_upgrade.assert_called_once()

        # Reset mock and test subsequent migration
        mock_upgrade.reset_mock()
        run_migrations_if_needed(self.database, alembic_cfg)
        mock_upgrade.assert_called_once()

    def test_transaction_isolation(self):
        """Test transaction isolation."""
        # Start two sessions
        session1 = self.database.sync_session()
        session2 = self.database.sync_session()

        try:
            # Session 1 creates a record but doesn't commit
            model = TestModel(id=1, name="test")
            session1.add(model)

            # Session 2 shouldn't see the uncommitted record
            result = session2.query(TestModel).first()
            self.assertIsNone(result)

            # After commit, session 2 should see the record
            session1.commit()
            result = session2.query(TestModel).first()
            self.assertIsNotNone(result)
            self.assertEqual(result.name, "test")

        finally:
            session1.close()
            session2.close()

    def test_error_handling(self):
        """Test database error handling."""
        with self.database.get_sync_session() as session:
            # Try to create invalid record
            model = TestModel(id=1, name=None)  # name is non-nullable
            session.add(model)

            # Should raise an error
            with self.assertRaises(Exception):
                session.commit()

            # Session should be rolled back
            result = session.query(TestModel).first()
            self.assertIsNone(result)
