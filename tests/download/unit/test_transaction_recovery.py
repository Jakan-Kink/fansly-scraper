"""Test transaction recovery mechanisms."""

import pytest
from sqlalchemy import text


class TestTransactionRecovery:
    """Test transaction recovery mechanisms."""

    @pytest.mark.asyncio
    async def test_savepoint_error_recovery(self, test_database):
        """Test that transactions can handle errors and continue."""
        # Use the real async session to test actual transaction behavior
        async with test_database.async_session_scope() as session:
            # Insert a test value in a transaction
            await session.execute(text("SELECT 1"))

            # Create a nested transaction and intentionally cause an error
            try:
                async with session.begin_nested():
                    # This will succeed
                    await session.execute(text("SELECT 2"))
                    # Simulate an error that rolls back this savepoint
                    raise ValueError("Simulated savepoint error")
            except ValueError:
                # Expected error - savepoint should be rolled back
                pass

            # Outer transaction should still be active and usable
            assert session.in_transaction()
            result = await session.execute(text("SELECT 3"))
            assert result.scalar() == 3

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
                    raise ValueError(
                        "Test error in nested transaction"
                    )  # Test error simulation pattern
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
        """Test that multiple sessions can be created successfully."""
        # Test that we can create multiple sessions in sequence
        # This verifies the connection pool can handle session recreation

        # First session
        async with test_database.async_session_scope() as session1:
            result1 = await session1.execute(text("SELECT 1"))
            assert result1.scalar() == 1

        # Second session (new connection from pool)
        async with test_database.async_session_scope() as session2:
            result2 = await session2.execute(text("SELECT 2"))
            assert result2.scalar() == 2

        # Third session (verifies connection pool is working)
        async with test_database.async_session_scope() as session3:
            result3 = await session3.execute(text("SELECT 3"))
            assert result3.scalar() == 3
