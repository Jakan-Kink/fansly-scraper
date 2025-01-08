"""Unit tests for metadata.base module."""

import asyncio
from unittest import IsolatedAsyncioTestCase, TestCase

from sqlalchemy import Integer, String, create_engine, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import Mapped, Session, mapped_column, sessionmaker

from metadata.base import Base


class TestModel(Base):
    """Test model for verifying Base functionality."""

    __tablename__ = "test_models"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)

    async def async_method(self) -> str:
        """Test async method."""
        return f"Async {self.name}"

    def sync_method(self) -> str:
        """Test sync method."""
        return f"Sync {self.name}"


class TestBase(TestCase):
    """Test cases for synchronous Base functionality."""

    def setUp(self):
        """Set up test database and session."""
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.session = self.Session()

    def tearDown(self):
        """Clean up test database."""
        self.session.close()
        Base.metadata.drop_all(self.engine)

    def test_model_creation(self):
        """Test creating and querying a model."""
        model = TestModel(id=1, name="test")
        self.session.add(model)
        self.session.commit()

        saved_model = self.session.execute(select(TestModel)).scalar_one_or_none()
        self.assertEqual(saved_model.name, "test")
        self.assertEqual(saved_model.sync_method(), "Sync test")

    def test_model_update(self):
        """Test updating a model."""
        model = TestModel(id=1, name="test")
        self.session.add(model)
        self.session.commit()

        model.name = "updated"
        self.session.commit()

        saved_model = self.session.execute(select(TestModel)).scalar_one_or_none()
        self.assertEqual(saved_model.name, "updated")


class TestBaseAsync(IsolatedAsyncioTestCase):
    """Test cases for asynchronous Base functionality."""

    async def asyncSetUp(self):
        """Set up async test database and session."""
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        self.async_session = sessionmaker(
            self.engine, class_=AsyncSession, expire_on_commit=False
        )

    async def asyncTearDown(self):
        """Clean up async test database."""
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await self.engine.dispose()

    async def test_async_model_creation(self):
        """Test creating and querying a model asynchronously."""
        async with self.async_session() as session:
            model = TestModel(id=1, name="test")
            session.add(model)
            await session.commit()

            result = await session.get(TestModel, 1)
            self.assertEqual(result.name, "test")
            self.assertEqual(await result.async_method(), "Async test")

    async def test_async_model_update(self):
        """Test updating a model asynchronously."""
        async with self.async_session() as session:
            model = TestModel(id=1, name="test")
            session.add(model)
            await session.commit()

            model.name = "updated"
            await session.commit()

            result = await session.get(TestModel, 1)
            self.assertEqual(result.name, "updated")
