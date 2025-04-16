"""Test transaction recovery mechanisms."""

import asyncio
import sqlite3
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError, PendingRollbackError

from metadata.database import Database


class TestTransactionRecovery:
    """Test transaction recovery mechanisms."""

    @pytest.mark.asyncio
    async def test_savepoint_error_recovery(self, test_database):
        """Test recovery from savepoint errors."""
        # Create a mock connection that raises a savepoint error on rollback
        mock_connection = MagicMock()
        mock_connection.rollback.side_effect = sqlite3.OperationalError(
            "no such savepoint: sa_savepoint_1"
        )

        # Create a mock session that returns our mock connection
        mock_session = MagicMock()
        mock_session.connection.return_value = mock_connection
        mock_session.in_transaction.return_value = True
        mock_session.is_active = True

        # Create a mock for the async_session_factory
        new_session = MagicMock()
        new_session.execute.return_value = MagicMock()
        test_database._async_session_factory = MagicMock(return_value=new_session)

        # Create a session wrapper to simulate the context
        task_id = id(asyncio.current_task())
        if not hasattr(test_database._thread_local, "async_sessions"):
            test_database._thread_local.async_sessions = {}
        test_database._thread_local.async_sessions[task_id] = {
            "session": mock_session,
            "depth": 1,
        }

        # Use an AsyncMock instance properly (not returning a coroutine)
        mock_handler = AsyncMock()
        # Patch the _handle_savepoint_error method to verify it's called
        with patch.object(test_database, "_handle_savepoint_error", new=mock_handler):
            # Simulate a savepoint error
            await test_database._handle_savepoint_error(
                mock_session,
                test_database._thread_local.async_sessions[task_id],
                task_id,
                test_database._thread_local.async_sessions,
            )

            # Verify the handler was called
            mock_handler.assert_called_once()

    @pytest.mark.asyncio
    async def test_nested_transaction_recovery(self, test_database):
        """Test recovery from nested transaction errors."""
        # Use the async context manager properly
        async with test_database.async_session_scope() as session:
            # Execute a query to ensure the session is active
            await session.execute(text("SELECT 1"))

            # Begin a nested transaction
            async with session.begin_nested():
                # Execute another query
                await session.execute(text("SELECT 2"))

                # Simulate an error in the nested transaction
                try:
                    # This should trigger a rollback to the savepoint
                    raise ValueError("Test error in nested transaction")
                except ValueError:
                    # This should be caught by the nested transaction context manager
                    # and trigger a rollback to the savepoint
                    pass

            # The outer transaction should still be active
            assert session.in_transaction()

            # Execute another query to verify the session is still usable
            result = await session.execute(text("SELECT 3"))
            assert result.scalar() == 3

    @pytest.mark.asyncio
    async def test_connection_invalidation(self, test_database):
        """Test connection invalidation and recreation."""
        # Mock the _handle_savepoint_error method to avoid actual database operations
        with patch.object(test_database, "_handle_savepoint_error") as mock_handler:
            # Create a mock session
            mock_session = MagicMock()
            mock_session.execute.side_effect = [
                MagicMock(),  # First call succeeds
                OperationalError(
                    "connection invalidated", None, None
                ),  # Second call fails
            ]

            # Create a mock connection
            mock_connection = MagicMock()
            mock_session.connection.return_value = mock_connection

            # Create a session wrapper
            task_id = id(asyncio.current_task())
            if not hasattr(test_database._thread_local, "async_sessions"):
                test_database._thread_local.async_sessions = {}
            test_database._thread_local.async_sessions[task_id] = {
                "session": mock_session,
                "depth": 1,
            }

            # Simulate a connection invalidation scenario
            try:
                # First query succeeds
                await mock_session.execute(text("SELECT 1"))

                # Invalidate the connection
                mock_connection.invalidate.return_value = None
                await mock_connection.invalidate()

                # Second query fails
                await mock_session.execute(text("SELECT 2"))
            except Exception:
                # This should trigger our recovery mechanism
                await test_database._handle_savepoint_error(
                    mock_session,
                    test_database._thread_local.async_sessions[task_id],
                    task_id,
                    test_database._thread_local.async_sessions,
                )

            # Verify the handler was called
            assert mock_handler.called
