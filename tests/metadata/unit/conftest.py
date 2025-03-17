"""Common test fixtures and utilities for metadata unit tests."""

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from functools import wraps

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from alembic import command
from alembic.config import Config as AlembicConfig
from metadata.base import Base
from metadata.post import Post


def run_async(func):
    """Decorator to run async functions in sync tests."""

    @wraps(func)
    def wrapper(*args, **kwargs):
        return asyncio.run(func(*args, **kwargs))

    return wrapper


@pytest.fixture
def safe_name(request) -> str:
    """Generate a safe name for the test database based on the test name."""
    # Get the full test name and replace invalid characters
    test_name = request.node.name.replace("[", "_").replace("]", "_")
    test_name = test_name.replace(".", "_").replace("::", "_")
    return test_name


@pytest.fixture
async def test_engine() -> AsyncGenerator[AsyncEngine]:
    """Create a test database engine.

    Uses SQLite in-memory database with a static pool to ensure each test gets
    its own isolated database instance.
    """
    # Create sync engine for migrations
    sync_engine = create_engine(
        "sqlite://",  # Creates a new in-memory database
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,  # Ensures single connection pool
        echo=False,
    )

    @event.listens_for(sync_engine, "connect")
    def do_connect(dbapi_connection, connection_record):
        # Disable foreign key checking during table creation/deletion
        dbapi_connection.execute("PRAGMA foreign_keys=OFF")
        # Enable WAL mode for better concurrency
        dbapi_connection.execute("PRAGMA journal_mode=WAL")

    # Create database schema using Alembic migrations
    alembic_cfg = AlembicConfig("alembic.ini")
    alembic_cfg.attributes["connection"] = sync_engine.connect()
    command.upgrade(alembic_cfg, "head")

    # Create async engine
    engine = create_async_engine(
        "sqlite+aiosqlite://",  # Creates a new in-memory database
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,  # Ensures single connection pool
        echo=False,
    )

    yield engine

    # Close all connections
    await engine.dispose()
    sync_engine.dispose()


@pytest.fixture
async def test_session(test_engine) -> AsyncGenerator[AsyncSession]:
    """Create a test database session."""
    async_session_factory = async_sessionmaker(
        bind=test_engine,
        expire_on_commit=False,
        autoflush=False,
        class_=AsyncSession,
    )
    session = async_session_factory()
    await session.begin()
    try:
        yield session
    finally:
        await session.rollback()
        await session.close()


@pytest.fixture
@asynccontextmanager
async def test_async_session(test_engine) -> AsyncGenerator[AsyncSession]:
    """Create a test async database session."""
    engine = await anext(test_engine)
    async_session_factory = async_sessionmaker(
        bind=engine,
        expire_on_commit=False,
        autoflush=False,
        class_=AsyncSession,
    )
    session = async_session_factory()
    await session.begin()
    try:
        yield session
    finally:
        await session.rollback()
        await session.close()
