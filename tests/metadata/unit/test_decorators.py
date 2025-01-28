"""Tests for metadata decorators."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from config.decorators import with_database_session
from metadata.database import require_database_config


class MockConfig:
    """Mock config class for testing."""

    def __init__(self, has_database=True):
        """Initialize with optional database."""
        if has_database:
            self._database = MockDatabase()


class MockDatabase:
    """Mock database class for testing."""

    def __init__(self):
        """Initialize with session flags."""
        self.session_created = False
        self.async_session_created = False

    def sync_session(self):
        """Create a mock sync session."""
        self.session_created = True
        return MockSessionContext(Session())

    def async_session(self):
        """Create a mock async session."""
        self.async_session_created = True
        return MockAsyncSessionContext(AsyncSession())


class MockSessionContext:
    """Mock session context manager."""

    def __init__(self, session):
        """Initialize with session."""
        self.session = session
        self.entered = False
        self.exited = False

    def __enter__(self):
        """Enter context."""
        self.entered = True
        return self.session

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Exit context."""
        self.exited = True


class MockAsyncSessionContext:
    """Mock async session context manager."""

    def __init__(self, session):
        """Initialize with session."""
        self.session = session
        self.entered = False
        self.exited = False

    async def __aenter__(self):
        """Enter context."""
        self.entered = True
        return self.session

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Exit context."""
        self.exited = True


def test_require_database_config_sync():
    """Test require_database_config with sync function."""

    @require_database_config
    def sync_func(config):
        return config._database

    # Test with valid config
    config = MockConfig()
    assert sync_func(config) == config._database

    # Test with config missing database
    config_no_db = MockConfig(has_database=False)
    with pytest.raises(ValueError, match="Database configuration not found"):
        sync_func(config_no_db)

    # Test with no config
    with pytest.raises(ValueError, match="Database configuration not found"):
        sync_func(None)


@pytest.mark.asyncio
async def test_require_database_config_async():
    """Test require_database_config with async function."""

    @require_database_config
    async def async_func(config):
        return config._database

    # Test with valid config
    config = MockConfig()
    result = await async_func(config)
    assert result == config._database

    # Test with config missing database
    config_no_db = MockConfig(has_database=False)
    with pytest.raises(ValueError, match="Database configuration not found"):
        await async_func(config_no_db)

    # Test with no config
    with pytest.raises(ValueError, match="Database configuration not found"):
        await async_func(None)


def test_with_database_session_sync():
    """Test with_database_session with sync function."""
    config = MockConfig()

    @with_database_session()
    def sync_func(config, session=None):
        assert isinstance(session, Session)
        return session

    # Test without providing session
    result = sync_func(config)
    assert isinstance(result, Session)
    assert config._database.session_created

    # Test with provided session
    custom_session = Session()
    result = sync_func(config, session=custom_session)
    assert result == custom_session


@pytest.mark.asyncio
async def test_with_database_session_async():
    """Test with_database_session with async function."""
    config = MockConfig()

    @with_database_session(async_session=True)
    async def async_func(config, session=None):
        assert isinstance(session, AsyncSession)
        return session

    # Test without providing session
    result = await async_func(config)
    assert isinstance(result, AsyncSession)
    assert config._database.async_session_created

    # Test with provided session
    custom_session = AsyncSession()
    result = await async_func(config, session=custom_session)
    assert result == custom_session


def test_with_database_session_type_mismatch():
    """Test with_database_session type validation."""
    # Test async function with sync session
    with pytest.raises(ValueError, match="Session type mismatch"):

        @with_database_session(async_session=False)
        async def invalid_async_func(config, session=None):
            pass

    # Test sync function with async session
    with pytest.raises(ValueError, match="Session type mismatch"):

        @with_database_session(async_session=True)
        def invalid_sync_func(config, session=None):
            pass


@pytest.mark.asyncio
async def test_with_database_session_error_handling():
    """Test error handling in with_database_session."""
    config = MockConfig()

    @with_database_session(async_session=True)
    async def failing_async_func(config, session=None):
        raise ValueError("Test error")

    # Test error propagation
    with pytest.raises(ValueError, match="Test error"):
        await failing_async_func(config)

    # Verify session was cleaned up
    assert config._database.async_session_created


def test_with_database_session_missing_config():
    """Test with_database_session with missing config."""

    @with_database_session()
    def sync_func(config, session=None):
        pass

    with pytest.raises(ValueError, match="Database configuration not found"):
        sync_func(None)


@pytest.mark.asyncio
async def test_with_database_session_transaction():
    """Test transaction handling in with_database_session."""
    config = MockConfig()

    @with_database_session(async_session=True)
    async def async_func(config, session=None):
        assert session.in_transaction()
        return session

    result = await async_func(config)
    assert isinstance(result, AsyncSession)
