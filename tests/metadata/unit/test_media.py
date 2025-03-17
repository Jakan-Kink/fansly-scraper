"""Unit tests for metadata.media module."""

import json
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from metadata.base import Base
from metadata.media import Media, MediaLocation, process_media_item_dict


@pytest.mark.asyncio
class TestMedia:
    """Test cases for Media class and related functions."""

    @pytest.fixture(autouse=True)
    async def setup(self, test_database):
        """Set up test database and session."""
        # Use the test_database fixture from conftest.py
        async with test_database.async_session_scope() as session:
            self.session = session
            yield
            await session.close()

    async def test_media_creation(self):
        """Test creating a Media object with basic attributes."""
        media = Media(
            id=1,
            accountId=123,
            mimetype="video/mp4",
            width=1920,
            height=1080,
            duration=30.5,
        )
        self.session.add(media)
        await self.session.commit()

        saved_media = (await self.session.execute(select(Media))).scalar_one_or_none()
        assert saved_media.id == 1
        assert saved_media.accountId == 123
        assert saved_media.mimetype == "video/mp4"
        assert saved_media.width == 1920
        assert saved_media.height == 1080
        assert saved_media.duration == 30.5

    async def test_process_video_metadata(self):
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

        await process_media_item_dict(config_mock, media_item, session=self.session)

        saved_media = (await self.session.execute(select(Media))).scalar_one_or_none()
        assert saved_media.width == 1920
        assert saved_media.height == 1080
        assert saved_media.duration == 30.5

    async def test_media_location(self):
        """Test creating and associating MediaLocation with Media."""
        media = Media(id=1, accountId=123)
        location = MediaLocation(
            mediaId=1, locationId="loc1", location="https://example.com/video.mp4"
        )
        self.session.add(media)
        self.session.add(location)
        await self.session.commit()

        saved_media = (await self.session.execute(select(Media))).scalar_one_or_none()
        assert len(saved_media.locations) == 1
        assert saved_media.locations["loc1"].location == "https://example.com/video.mp4"

    async def test_invalid_metadata(self):
        """Test handling invalid metadata JSON."""
        config_mock = MagicMock()
        media_item = {
            "id": 1,
            "accountId": 123,
            "mimetype": "video/mp4",
            "metadata": "invalid json",
        }

        await process_media_item_dict(config_mock, media_item, session=self.session)

        saved_media = (await self.session.execute(select(Media))).scalar_one_or_none()
        assert saved_media.meta_info == "invalid json"
        assert saved_media.duration is None
        assert saved_media.width is None
        assert saved_media.height is None
