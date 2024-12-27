"""Integration tests for media processing functionality."""

import json
import os
from datetime import datetime, timezone
from unittest import TestCase

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from config import FanslyConfig
from metadata.account import Account, AccountMedia, AccountMediaBundle
from metadata.base import Base
from metadata.database import Database
from metadata.media import Media, MediaLocation, process_media_info


class TestMediaProcessing(TestCase):
    """Integration tests for media processing."""

    @classmethod
    def setUpClass(cls):
        """Set up test database and load test data."""
        # Create test database
        cls.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(cls.engine)
        cls.Session = sessionmaker(bind=cls.engine)

        # Load test data
        cls.test_data_dir = os.path.join(os.path.dirname(__file__), "..", "..", "json")
        with open(os.path.join(cls.test_data_dir, "timeline-sample-account.json")) as f:
            cls.timeline_data = json.load(f)

    def setUp(self):
        """Set up fresh session and config for each test."""
        self.session = self.Session()
        self.config = FanslyConfig(program_version="0.10.0")
        self.config.metadata_db_file = ":memory:"
        self.config._database = Database(self.config)
        self.config._database.sync_engine = self.engine
        self.config._database.sync_session = self.Session

        # Create tables
        Base.metadata.create_all(self.engine)

        # Create test account
        self.account = Account(id=1, username="test_user")
        self.session.add(self.account)
        self.session.commit()

    def tearDown(self):
        """Clean up after each test."""
        self.session.close()

    def test_process_video_from_timeline(self):
        """Test processing a video from timeline data."""
        # Find a video in the test data
        media_data = None
        for media in self.timeline_data["response"]["accountMedia"]:
            if media.get("media", {}).get("mimetype", "").startswith("video/"):
                media_data = media
                break

        if not media_data:
            self.skipTest("No video found in test data")

        # Process the media
        process_media_info(self.config, media_data)

        # Verify the results
        with self.config._database.sync_session() as session:
            media = session.query(Media).filter_by(id=media_data["mediaId"]).first()
            self.assertIsNotNone(media)
            self.assertEqual(media.mimetype, media_data["media"]["mimetype"])
            if "metadata" in media_data["media"]:
                metadata = json.loads(media_data["media"]["metadata"])
                if "duration" in metadata:
                    self.assertEqual(media.duration, float(metadata["duration"]))
                if "original" in metadata:
                    self.assertEqual(media.width, metadata["original"].get("width"))
                    self.assertEqual(media.height, metadata["original"].get("height"))

    def test_process_media_bundle_from_timeline(self):
        """Test processing a media bundle from timeline data."""
        if "accountMediaBundles" not in self.timeline_data["response"]:
            self.skipTest("No bundles found in test data")

        bundle_data = self.timeline_data["response"]["accountMediaBundles"][0]

        # Create necessary AccountMedia records
        for content in bundle_data.get("bundleContent", []):
            media = AccountMedia(
                id=content["accountMediaId"],
                accountId=1,
                mediaId=content["accountMediaId"],
            )
            self.session.add(media)
        self.session.commit()

        # Process the bundle
        from metadata.account import process_media_bundles

        process_media_bundles(self.config, 1, [bundle_data])

        # Verify the results
        with self.config._database.sync_session() as session:
            bundle = (
                session.query(AccountMediaBundle)
                .filter_by(id=bundle_data["id"])
                .first()
            )
            self.assertIsNotNone(bundle)
            self.assertEqual(
                len(bundle.accountMediaIds), len(bundle_data["bundleContent"])
            )

            # Verify order is preserved
            media_ids = [m.id for m in bundle.accountMediaIds]
            expected_order = [
                c["accountMediaId"]
                for c in sorted(bundle_data["bundleContent"], key=lambda x: x["pos"])
            ]
            self.assertEqual(media_ids, expected_order)
