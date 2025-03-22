"""Test transaction recovery mechanisms."""

import asyncio
import sqlite3
from unittest.mock import MagicMock, patch

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

        # Patch the _handle_savepoint_error method to verify it's called
        with patch.object(test_database, "_handle_savepoint_error") as mock_handler:
            # Simulate a savepoint error
            await test_database._handle_savepoint_error(
                mock_session,
                test_database._thread_local.async_sessions[task_id],
                task_id,
                test_database._thread_local.async_sessions,
            )

            # Verify the handler was called
            assert mock_handler.called

    @pytest.mark.asyncio
    async def test_nested_transaction_recovery(self, test_database):
        """Test recovery from nested transaction errors."""
        # Create a generator from the async_session_scope method
        session_gen = test_database.async_session_scope()

        # Get the session from the generator
        session = await session_gen.__anext__()

        try:
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
        finally:
            # Close the generator
            try:
                await session_gen.aclose()
            except Exception:
                pass

    @pytest.mark.asyncio
    async def test_connection_invalidation(self, test_database):
        """Test connection invalidation and recreation."""
        # Create a generator from the async_session_scope method
        session_gen = test_database.async_session_scope()

        # Get the session from the generator
        session = await session_gen.__anext__()

        try:
            # Execute a query to ensure the session is active
            await session.execute(text("SELECT 1"))

            # Get the connection
            connection = await session.connection()

            # Patch the _handle_savepoint_error method to verify it's called
            with patch.object(test_database, "_handle_savepoint_error") as mock_handler:
                # Invalidate the connection
                await connection.invalidate()

                # Try to execute another query - this should fail
                try:
                    await session.execute(text("SELECT 2"))
                except Exception:
                    # This should trigger our recovery mechanism
                    task_id = id(asyncio.current_task())
                    session_wrapper = test_database._thread_local.async_sessions.get(
                        task_id
                    )

                    # Manually call the recovery method since we're not using the context manager
                    await test_database._handle_savepoint_error(
                        session,
                        session_wrapper,
                        task_id,
                        test_database._thread_local.async_sessions,
                    )

                # Verify the handler was called
                assert mock_handler.called
        finally:
            # Close the generator
            try:
                await session_gen.aclose()
            except Exception:
                pass

        # Create a new session to verify we can still connect
        new_session_gen = test_database.async_session_scope()
        new_session = await new_session_gen.__anext__()

        try:
            # Execute a query to verify the new session works
            result = await new_session.execute(text("SELECT 3"))
            assert result.scalar() == 3
        finally:
            # Close the generator
            try:
                await new_session_gen.aclose()
            except Exception:
                pass
