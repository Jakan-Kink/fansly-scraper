"""Unit tests for the database session decorator"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from config.decorators import with_database_session
from config.fanslyconfig import FanslyConfig


class TestDatabaseSessionDecorator:
    """Tests for the with_database_session decorator."""

    def test_sync_decorator_with_existing_session(self):
        """Test sync decorator with an existing session."""
        mock_session = MagicMock(spec=Session)
        mock_config = MagicMock(spec=FanslyConfig)

        @with_database_session()
        def test_func(config, session=None):
            return session

        result = test_func(config=mock_config, session=mock_session)
        assert result is mock_session
        # Verify we didn't attempt to create a new session
        mock_config._database.session_scope.assert_not_called()

    def test_sync_decorator_creates_session(self):
        """Test sync decorator creates a session when none provided."""
        mock_session = MagicMock(spec=Session)
        mock_context_manager = MagicMock()
        mock_context_manager.__enter__.return_value = mock_session
        mock_context_manager.__exit__.return_value = None

        mock_database = MagicMock()
        mock_database.session_scope.return_value = mock_context_manager

        mock_config = MagicMock(spec=FanslyConfig)
        mock_config._database = mock_database

        @with_database_session()
        def test_func(config, session=None):
            return session

        result = test_func(config=mock_config)
        assert result is mock_session
        # Verify we created a new session
        mock_config._database.session_scope.assert_called_once()

    def test_sync_decorator_finds_config_in_args(self):
        """Test sync decorator finds config in positional args."""
        mock_session = MagicMock(spec=Session)
        mock_context_manager = MagicMock()
        mock_context_manager.__enter__.return_value = mock_session
        mock_context_manager.__exit__.return_value = None

        mock_database = MagicMock()
        mock_database.session_scope.return_value = mock_context_manager

        mock_config = MagicMock(spec=FanslyConfig)
        mock_config._database = mock_database

        @with_database_session()
        def test_func(something_else, config, session=None):
            return session

        result = test_func("test", mock_config)
        assert result is mock_session
        # Verify we created a new session
        mock_config._database.session_scope.assert_called_once()

    def test_sync_decorator_missing_config(self):
        """Test sync decorator raises error when config not found."""

        @with_database_session()
        def test_func(something_else, session=None):
            return session

        with pytest.raises(ValueError, match="Database configuration not found"):
            test_func("test")

    @pytest.mark.asyncio
    async def test_async_decorator_with_existing_session(self):
        """Test async decorator with an existing session."""
        mock_session = AsyncMock(spec=AsyncSession)
        mock_config = MagicMock(spec=FanslyConfig)

        @with_database_session(async_session=True)
        async def test_func(config, session=None):
            return session

        result = await test_func(config=mock_config, session=mock_session)
        assert result is mock_session
        # Verify we didn't attempt to create a new session
        mock_config._database.async_session_scope.assert_not_called()

    @pytest.mark.asyncio
    async def test_async_decorator_creates_session(self):
        """Test async decorator creates a session when none provided."""
        mock_session = AsyncMock(spec=AsyncSession)
        mock_context_manager = AsyncMock()
        mock_context_manager.__aenter__.return_value = mock_session
        mock_context_manager.__aexit__.return_value = None

        mock_database = MagicMock()
        mock_database.async_session_scope.return_value = mock_context_manager

        mock_config = MagicMock(spec=FanslyConfig)
        mock_config._database = mock_database

        @with_database_session(async_session=True)
        async def test_func(config, session=None):
            return session

        result = await test_func(config=mock_config)
        assert result is mock_session
        # Verify we created a new session
        mock_config._database.async_session_scope.assert_called_once()

    @pytest.mark.asyncio
    async def test_async_decorator_finds_config_in_args(self):
        """Test async decorator finds config in positional args."""
        mock_session = AsyncMock(spec=AsyncSession)
        mock_context_manager = AsyncMock()
        mock_context_manager.__aenter__.return_value = mock_session
        mock_context_manager.__aexit__.return_value = None

        mock_database = MagicMock()
        mock_database.async_session_scope.return_value = mock_context_manager

        mock_config = MagicMock(spec=FanslyConfig)
        mock_config._database = mock_database

        @with_database_session(async_session=True)
        async def test_func(something_else, config, session=None):
            return session

        result = await test_func("test", mock_config)
        assert result is mock_session
        # Verify we created a new session
        mock_config._database.async_session_scope.assert_called_once()

    @pytest.mark.asyncio
    async def test_async_decorator_missing_config(self):
        """Test async decorator raises error when config not found."""

        @with_database_session(async_session=True)
        async def test_func(something_else, session=None):
            return session

        with pytest.raises(ValueError, match="Database configuration not found"):
            await test_func("test")

    def test_session_type_mismatch_sync_func_async_session(self):
        """Test error when sync function gets async session type."""
        with pytest.raises(ValueError, match="Session type mismatch"):

            @with_database_session(async_session=True)
            def test_func(config, session=None):
                return session

    def test_session_type_mismatch_async_func_sync_session(self):
        """Test error when async function gets sync session type."""
        with pytest.raises(ValueError, match="Session type mismatch"):

            @with_database_session(async_session=False)
            async def test_func(config, session=None):
                return session
