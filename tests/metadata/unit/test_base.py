"""Unit tests for metadata.base module."""

import asyncio

import pytest
import pytest_asyncio
from sqlalchemy import create_engine, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import Session, sessionmaker

from metadata.base import Base
from tests.metadata.unit.models import SampleModel, test_metadata


@pytest.fixture
def sync_engine():
    """Create a sync SQLite engine for testing."""
    engine = create_engine("sqlite:///:memory:")
    test_metadata.create_all(engine)
    yield engine
    test_metadata.drop_all(engine)


@pytest.fixture
def sync_session(sync_engine):
    """Create a sync session for testing."""
    Session = sessionmaker(bind=sync_engine)
    session = Session()
    yield session
    session.close()


@pytest_asyncio.fixture
def async_engine():
    """Create an async SQLite engine for testing."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    return engine


@pytest_asyncio.fixture
async def async_session(async_engine):
    """Create an async session for testing."""
    async with async_engine.begin() as conn:
        await conn.run_sync(test_metadata.create_all)

    session = AsyncSession(
        bind=async_engine,
        expire_on_commit=False,
    )

    try:
        yield session
    finally:
        await session.close()

        async with async_engine.begin() as conn:
            await conn.run_sync(test_metadata.drop_all)


def test_model_creation(sync_session: Session):
    """Test creating and querying a model."""
    model = SampleModel(id=1, name="test")
    sync_session.add(model)
    sync_session.commit()

    # Query the model
    result = sync_session.execute(
        select(SampleModel).where(SampleModel.id == 1)
    ).scalar_one()
    assert result.name == "test"
    assert result.sync_method() == "Sync test"


def test_model_update(sync_session: Session):
    """Test updating a model."""
    # Create model
    model = SampleModel(id=1, name="test")
    sync_session.add(model)
    sync_session.commit()

    # Update model
    model.name = "updated"
    sync_session.commit()

    # Query updated model
    result = sync_session.execute(
        select(SampleModel).where(SampleModel.id == 1)
    ).scalar_one()
    assert result.name == "updated"
    assert result.sync_method() == "Sync updated"


@pytest.mark.asyncio
async def test_async_model_creation(async_session):
    """Test creating and querying a model asynchronously."""
    model = SampleModel(id=1, name="test")
    async_session.add(model)
    await async_session.commit()

    # Query the model
    result = await async_session.execute(select(SampleModel).where(SampleModel.id == 1))
    result = result.scalar_one()
    assert result.name == "test"
    assert await result.async_method() == "Async test"


@pytest.mark.asyncio
async def test_async_model_update(async_session):
    """Test updating a model asynchronously."""
    # Create model
    model = SampleModel(id=1, name="test")
    async_session.add(model)
    await async_session.commit()

    # Update model
    model.name = "updated"
    await async_session.commit()

    # Query updated model
    result = await async_session.execute(select(SampleModel).where(SampleModel.id == 1))
    result = result.scalar_one()
    assert result.name == "updated"
    assert await result.async_method() == "Async updated"
