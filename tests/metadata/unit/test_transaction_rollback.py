"""Tests for SQLAlchemy nested transaction rollback handling."""

import asyncio
import os
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from config import FanslyConfig
from metadata.database import Database


@pytest.fixture
def config(tmp_path: Path) -> FanslyConfig:
    """Create test configuration."""
    config = FanslyConfig(program_version="0.10.0")
    config.metadata_db_file = tmp_path / "test_transaction.db"
    return config


@pytest.fixture(autouse=True)
def cleanup_test_table(database: Database):
    """Clean up test table before and after each test."""
    # Drop table if it exists before test
    with database.session_scope() as session:
        session.execute(text("DROP TABLE IF EXISTS test"))
        session.commit()
    yield
    # Drop table after test
    with database.session_scope() as session:
        session.execute(text("DROP TABLE IF EXISTS test"))
        session.commit()


@pytest.fixture
def database(config: FanslyConfig) -> Database:
    """Create test database for transaction tests."""
    db = Database(config)
    yield db
    db.close_sync()


class TestNestedTransactionRollback:
    """Test nested transaction rollback handling."""

    def test_successful_nested_transactions(self, database: Database):
        """Test scenario where nested transactions succeed."""
        # Create test table
        with database.session_scope() as session:
            session.execute(
                text("CREATE TABLE test (id INTEGER PRIMARY KEY, value TEXT)")
            )
            session.commit()

        # Test nested transactions
        with database.session_scope() as session:
            with session.begin():
                # Insert in outer transaction
                session.execute(
                    text("INSERT INTO test (id, value) VALUES (1, 'outer')")
                )

                # First nested transaction
                with session.begin_nested():
                    session.execute(
                        text("INSERT INTO test (id, value) VALUES (2, 'nested1')")
                    )

                # Second nested transaction
                with session.begin_nested():
                    session.execute(
                        text("INSERT INTO test (id, value) VALUES (3, 'nested2')")
                    )

        # Check results
        with database.session_scope() as session:
            result = session.execute(text("SELECT * FROM test ORDER BY id"))
            rows = result.fetchall()
            assert len(rows) == 3
            assert rows[0][0] == 1 and rows[0][1] == "outer"
            assert rows[1][0] == 2 and rows[1][1] == "nested1"
            assert rows[2][0] == 3 and rows[2][1] == "nested2"

    def test_nested_transaction_error_recovery(self, database: Database):
        """Test scenario where a nested transaction fails but outer transaction continues."""
        # Create test table
        with database.session_scope() as session:
            session.execute(
                text("CREATE TABLE test (id INTEGER PRIMARY KEY, value TEXT)")
            )
            session.commit()

        # Test nested transactions with error
        with database.session_scope() as session:
            with session.begin():
                # Insert in outer transaction
                session.execute(
                    text("INSERT INTO test (id, value) VALUES (1, 'outer')")
                )

                # First nested transaction
                with session.begin_nested():
                    session.execute(
                        text("INSERT INTO test (id, value) VALUES (2, 'nested1')")
                    )

                # Second nested transaction - will fail
                try:
                    with session.begin_nested():
                        # This will fail - duplicate primary key
                        session.execute(
                            text("INSERT INTO test (id, value) VALUES (1, 'duplicate')")
                        )
                except Exception:
                    # Expected error, continue with outer transaction
                    pass

                # Third nested transaction - should still work
                with session.begin_nested():
                    session.execute(
                        text("INSERT INTO test (id, value) VALUES (3, 'nested3')")
                    )

        # Check results
        with database.session_scope() as session:
            result = session.execute(text("SELECT * FROM test ORDER BY id"))
            rows = result.fetchall()
            assert len(rows) == 3
            assert rows[0][0] == 1 and rows[0][1] == "outer"
            assert rows[1][0] == 2 and rows[1][1] == "nested1"
            assert rows[2][0] == 3 and rows[2][1] == "nested3"

    def test_connection_failure_recovery(self, database: Database, monkeypatch):
        """Test recovery from connection failures during nested transactions."""
        # Create test table
        with database.session_scope() as session:
            session.execute(
                text("CREATE TABLE test (id INTEGER PRIMARY KEY, value TEXT)")
            )
            session.commit()

        # Instead of actually closing the connection, we'll mock the execute method
        # to simulate a connection error during a nested transaction
        from sqlalchemy.orm import Session

        original_execute = Session.execute

        def mock_execute(self, statement, *args, **kwargs):
            # Only raise an error for a specific query to simulate a connection issue
            if "nested2" in str(statement):
                raise Exception("Connection lost during transaction")
            return original_execute(self, statement, *args, **kwargs)

        # Apply the mock
        monkeypatch.setattr(Session, "execute", mock_execute)

        # Test transaction with simulated connection error
        try:
            with database.session_scope() as session:
                with session.begin():
                    # Insert in outer transaction
                    session.execute(
                        text("INSERT INTO test (id, value) VALUES (1, 'outer')")
                    )

                    # First nested transaction
                    with session.begin_nested():
                        session.execute(
                            text("INSERT INTO test (id, value) VALUES (2, 'nested1')")
                        )

                    # Second nested transaction - will have connection issue
                    try:
                        with session.begin_nested():
                            # This will trigger our mocked error
                            session.execute(
                                text(
                                    "INSERT INTO test (id, value) VALUES (3, 'nested2')"
                                )
                            )
                    except Exception:
                        # Expected error, but this should propagate to the outer transaction
                        raise
        except Exception:
            # Expected error in outer transaction
            pass

        # Restore the original execute method
        monkeypatch.setattr(Session, "execute", original_execute)

        # Try to create a new session after the error
        with database.session_scope() as session:
            # Try to insert new data - this should work if our recovery mechanism is functioning
            session.execute(
                text("INSERT INTO test (id, value) VALUES (4, 'after_error')")
            )
            session.commit()

            # Verify the modification worked
            result = session.execute(text("SELECT * FROM test WHERE id = 4"))
            row = result.fetchone()
            assert row is not None
            assert row[0] == 4 and row[1] == "after_error"

    def test_multiple_savepoint_errors(self, database: Database, monkeypatch):
        """Test handling of multiple savepoint errors in sequence."""
        # Create test table
        with database.session_scope() as session:
            session.execute(
                text("CREATE TABLE test (id INTEGER PRIMARY KEY, value TEXT)")
            )
            session.commit()

        # Mock the rollback method to simulate multiple savepoint errors
        from sqlalchemy.orm import Session

        original_rollback = Session.rollback
        rollback_attempts = [0]  # Use a list to allow modification in nested function

        def mock_rollback(self):
            rollback_attempts[0] += 1

            # First two attempts fail with savepoint error
            if rollback_attempts[0] <= 2:
                raise Exception(
                    "Can't reconnect until invalid savepoint transaction is rolled back"
                )

            # Third attempt succeeds
            return original_rollback(self)

        # Apply the mock
        monkeypatch.setattr(Session, "rollback", mock_rollback)

        # Test transaction with mocked rollback errors
        try:
            with database.session_scope() as session:
                session.execute(text("INSERT INTO test (id, value) VALUES (1, 'test')"))
                # Force an error to trigger rollback
                session.execute(
                    text("INSERT INTO test (id, value) VALUES (1, 'duplicate')")
                )
        except Exception:
            # Expected error
            pass

        # Verify we had multiple rollback attempts
        assert rollback_attempts[0] > 0

        # Try a new session to make sure we can still use the database
        with database.session_scope() as session:
            session.execute(
                text("INSERT INTO test (id, value) VALUES (5, 'after_mock_error')")
            )
            session.commit()

            # Verify the modification worked
            result = session.execute(text("SELECT * FROM test WHERE id = 5"))
            row = result.fetchone()
            assert row is not None
            assert row[0] == 5 and row[1] == "after_mock_error"
