"""Unit tests for metadata.media module."""

import json
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import create_engine, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import sessionmaker

from metadata.base import Base
from metadata.database import Database
from metadata.media import Media, MediaLocation, process_media_item_dict


@pytest_asyncio.fixture
async def media_session(test_engine):
    """Set up test database and session."""
    # Create session factory
    async_session_factory = async_sessionmaker(
        bind=test_engine,
        expire_on_commit=False,
        class_=AsyncSession,
    )

    # Create session
    async with async_session_factory() as session:
        # Create tables
        async with session.begin():
            await session.execute(text("PRAGMA foreign_keys=OFF"))
            await session.execute(text("PRAGMA journal_mode=WAL"))
        yield session


@pytest.mark.asyncio
async def test_media_creation(media_session):
    """Test creating a Media object with basic attributes."""
    media = Media(
        id=1,
        accountId=123,
        mimetype="video/mp4",
        width=1920,
        height=1080,
        duration=30.5,
    )
    media_session.add(media)
    await media_session.commit()

    saved_media = (await media_session.execute(select(Media))).scalar_one_or_none()
    assert saved_media.id == 1
    assert saved_media.accountId == 123
    assert saved_media.mimetype == "video/mp4"
    assert saved_media.width == 1920
    assert saved_media.height == 1080
    assert saved_media.duration == 30.5


@pytest.mark.asyncio
async def test_process_video_metadata(media_session):
    """Test processing video metadata with duration and dimensions."""
    config_mock = MagicMock()
    media_item = {
        "id": 1,
        "accountId": 123,
        "mimetype": "video/mp4",
        "metadata": json.dumps(
            {"original": {"width": 1920, "height": 1080}, "duration": 30.5}
        ),
    }

    await process_media_item_dict(config_mock, media_item, session=media_session)

    saved_media = (await media_session.execute(select(Media))).scalar_one_or_none()
    assert saved_media.width == 1920
    assert saved_media.height == 1080
    assert saved_media.duration == 30.5


@pytest.mark.asyncio
async def test_media_location(media_session):
    """Test creating and associating MediaLocation with Media."""
    media = Media(id=1, accountId=123)
    location = MediaLocation(
        mediaId=1, locationId="loc1", location="https://example.com/video.mp4"
    )
    media_session.add(media)
    media_session.add(location)
    await media_session.commit()

    saved_media = (await media_session.execute(select(Media))).scalar_one_or_none()
    assert len(saved_media.locations) == 1
    assert saved_media.locations["loc1"].location == "https://example.com/video.mp4"


@pytest.mark.asyncio
async def test_invalid_metadata(media_session):
    """Test handling invalid metadata JSON."""
    config_mock = MagicMock()
    media_item = {
        "id": 1,
        "accountId": 123,
        "mimetype": "video/mp4",
        "metadata": "invalid json",
    }

    await process_media_item_dict(config_mock, media_item, session=media_session)

    saved_media = (await media_session.execute(select(Media))).scalar_one_or_none()
    assert saved_media.meta_info == "invalid json"
    assert saved_media.duration is None
    assert saved_media.width is None
    assert saved_media.height is None
