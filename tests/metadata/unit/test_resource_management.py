"""Unit tests for database resource management."""

import asyncio
import threading
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from metadata.resource_management import (
    AsyncConnections,
    ConnectionManager,
    ThreadLocalConnections,
)


class TestThreadLocalConnections:
    """Test thread-local connection management."""

    def test_get_thread_ids(self):
        """Test getting thread IDs with connections."""
        manager = ThreadLocalConnections()
        # Add some test connections
        manager.set_connection("123", MagicMock())
        manager.set_connection("456", MagicMock())
        manager.set_connection("not_a_thread", MagicMock())

        thread_ids = manager._get_thread_ids()
        assert sorted(thread_ids) == ["123", "456"]

    def test_connection_lifecycle(self):
        """Test setting, getting, and removing connections."""
        manager = ThreadLocalConnections()
        conn = MagicMock()

        # Set and get
        manager.set_connection("123", conn)
        assert manager.get_connection("123") == conn

        # Remove
        manager.remove_connection("123")
        assert manager.get_connection("123") is None

    @pytest.mark.asyncio
    async def test_cleanup(self):
        """Test cleaning up thread-local connections."""
        manager = ThreadLocalConnections()
        conn1 = MagicMock()
        conn2 = MagicMock()

        manager.set_connection("123", conn1)
        manager.set_connection("456", conn2)

        await manager.cleanup()

        conn1.close.assert_called_once()
        conn2.close.assert_called_once()
        assert manager.get_connection("123") is None
        assert manager.get_connection("456") is None


class TestAsyncConnections:
    """Test async connection management."""

    @pytest.mark.asyncio
    async def test_session_lifecycle(self):
        """Test adding, getting, and removing sessions."""
        manager = AsyncConnections()
        session = AsyncMock(spec=AsyncSession)
        session.is_active = True

        # Add and get
        await manager.add_session(123, session)
        result = await manager.get_session(123)
        assert result == (session, 1)

        # Remove
        await manager.remove_session(123)
        result = await manager.get_session(123)
        assert result is None
        session.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_reference_counting(self):
        """Test session reference counting."""
        manager = AsyncConnections()
        session = AsyncMock(spec=AsyncSession)
        session.is_active = True

        # Initial add
        await manager.add_session(123, session)
        result = await manager.get_session(123)
        assert result == (session, 1)

        # Increment
        await manager.increment_ref_count(123)
        result = await manager.get_session(123)
        assert result == (session, 2)

        # Decrement once - should keep session
        await manager.decrement_ref_count(123)
        result = await manager.get_session(123)
        assert result == (session, 1)

        # Decrement again - should remove session
        await manager.decrement_ref_count(123)
        result = await manager.get_session(123)
        assert result is None
        session.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_cleanup(self):
        """Test cleaning up async connections."""
        manager = AsyncConnections()
        session1 = AsyncMock(spec=AsyncSession)
        session2 = AsyncMock(spec=AsyncSession)
        session1.is_active = True
        session2.is_active = True

        await manager.add_session(123, session1)
        await manager.add_session(456, session2)

        await manager.cleanup()

        session1.close.assert_awaited_once()
        session2.close.assert_awaited_once()
        assert await manager.get_session(123) is None
        assert await manager.get_session(456) is None


class TestConnectionManager:
    """Test unified connection management."""

    @pytest.mark.asyncio
    async def test_cleanup_coordination(self):
        """Test coordinated cleanup of all connections."""
        manager = ConnectionManager()

        # Add some test connections
        thread_conn = MagicMock()
        async_session = AsyncMock(spec=AsyncSession)
        async_session.is_active = True

        manager.thread_connections.set_connection("123", thread_conn)
        await manager.async_connections.add_session(456, async_session)

        # Test cleanup
        await manager.cleanup()

        thread_conn.close.assert_called_once()
        async_session.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_cleanup_timeout(self):
        """Test cleanup timeout handling."""
        manager = ConnectionManager()

        # Mock a slow cleanup
        async def slow_cleanup():
            await asyncio.sleep(2)  # Longer than timeout

        with patch.object(
            manager.thread_connections, "cleanup", side_effect=slow_cleanup
        ):
            await manager.cleanup()
            # Should not raise TimeoutError, but log error instead

    @pytest.mark.asyncio
    async def test_connection_access(self):
        """Test accessing connections through manager."""
        manager = ConnectionManager()

        # Test thread connection
        thread_conn = MagicMock()
        manager.thread_connections.set_connection("123", thread_conn)
        assert manager.get_thread_connection("123") == thread_conn

        # Test async session
        session = AsyncMock(spec=AsyncSession)
        await manager.async_connections.add_session(456, session)
        result = await manager.get_async_session(456)
        assert result == (session, 1)
