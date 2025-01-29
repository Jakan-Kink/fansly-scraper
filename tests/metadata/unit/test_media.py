"""Unit tests for metadata.media module."""

import json
from datetime import datetime, timezone
from unittest import TestCase
from unittest.mock import MagicMock, patch

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from metadata.base import Base
from metadata.media import Media, MediaLocation, _process_media_item_dict_inner


class TestMedia(TestCase):
    """Test cases for Media class and related functions."""

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

    def test_media_creation(self):
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
        self.session.commit()

        saved_media = self.session.execute(select(Media)).scalar_one_or_none()
        self.assertEqual(saved_media.id, 1)
        self.assertEqual(saved_media.accountId, 123)
        self.assertEqual(saved_media.mimetype, "video/mp4")
        self.assertEqual(saved_media.width, 1920)
        self.assertEqual(saved_media.height, 1080)
        self.assertEqual(saved_media.duration, 30.5)

    def test_process_video_metadata(self):
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

        _process_media_item_dict_inner(config_mock, media_item, session=self.session)

        saved_media = self.session.execute(select(Media)).scalar_one_or_none()
        self.assertEqual(saved_media.width, 1920)
        self.assertEqual(saved_media.height, 1080)
        self.assertEqual(saved_media.duration, 30.5)

    def test_media_location(self):
        """Test creating and associating MediaLocation with Media."""
        media = Media(id=1, accountId=123)
        location = MediaLocation(
            mediaId=1, locationId="loc1", location="https://example.com/video.mp4"
        )
        self.session.add(media)
        self.session.add(location)
        self.session.commit()

        saved_media = self.session.execute(select(Media)).scalar_one_or_none()
        self.assertEqual(len(saved_media.locations), 1)
        self.assertEqual(
            saved_media.locations["loc1"].location, "https://example.com/video.mp4"
        )

    def test_invalid_metadata(self):
        """Test handling invalid metadata JSON."""
        config_mock = MagicMock()
        media_item = {
            "id": 1,
            "accountId": 123,
            "mimetype": "video/mp4",
            "metadata": "invalid json",
        }

        _process_media_item_dict_inner(config_mock, media_item, session=self.session)

        saved_media = self.session.execute(select(Media)).scalar_one_or_none()
        self.assertEqual(saved_media.meta_info, "invalid json")
        self.assertIsNone(saved_media.duration)
        self.assertIsNone(saved_media.width)
        self.assertIsNone(saved_media.height)
