"""Integration tests for media processing functionality."""

import json
import os
from unittest import TestCase

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from config import FanslyConfig
from metadata.account import Account, AccountMedia, AccountMediaBundle
from metadata.base import Base
from metadata.database import Database
from metadata.media import Media, process_media_info


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
        self.config._database.session_scope = self.Session

        # Create tables
        Base.metadata.create_all(self.engine)

        # Create test account
        self.account = self.session.query(Account).filter_by(id=1).first()
        if not self.account:
            self.account = Account(id=1, username="test_user")
            self.session.add(self.account)
            self.session.commit()

    def tearDown(self):
        """Clean up after each test."""
        self.session.close()

    def test_process_video_from_timeline(self):
        """Test processing a video from timeline data."""
        try:
            # Find a video in the test data
            media_data = None
            for media in self.timeline_data["response"]["accountMedia"]:
                if media.get("media", {}).get("mimetype", "").startswith("video/"):
                    media_data = media
                    break

            if not media_data:
                self.skipTest("No video found in test data")

            # Ensure we have all required fields
            self.assertIn("mediaId", media_data, "Missing mediaId in media_data")
            self.assertIn("media", media_data, "Missing media object in media_data")
            self.assertIn(
                "mimetype", media_data["media"], "Missing mimetype in media object"
            )

            # Process the media
            process_media_info(self.config, media_data)

            # Verify the results
            with self.config._database.session_scope() as session:
                # Test 1: Basic media record creation
                media = session.query(Media).filter_by(id=media_data["mediaId"]).first()
                self.assertIsNotNone(media, f"Media {media_data['mediaId']} not found")
                self.assertEqual(
                    media.mimetype,
                    media_data["media"]["mimetype"],
                    "Mimetype mismatch",
                )

                # Test 2: Metadata handling
                if "metadata" in media_data["media"]:
                    try:
                        metadata = json.loads(media_data["media"]["metadata"])
                    except json.JSONDecodeError as e:
                        self.fail(f"Invalid JSON in metadata: {e}")

                    # Test 2.1: Duration
                    if "duration" in metadata:
                        try:
                            expected_duration = float(metadata["duration"])
                            self.assertEqual(
                                media.duration,
                                expected_duration,
                                f"Duration mismatch: got {media.duration}, expected {expected_duration}",
                            )
                        except (ValueError, TypeError) as e:
                            self.fail(f"Invalid duration value: {e}")

                    # Test 2.2: Dimensions
                    if "original" in metadata:
                        original = metadata["original"]
                        # Width
                        if "width" in original:
                            try:
                                expected_width = int(original["width"])
                                self.assertEqual(
                                    media.width,
                                    expected_width,
                                    f"Width mismatch: got {media.width}, expected {expected_width}",
                                )
                            except (ValueError, TypeError) as e:
                                self.fail(f"Invalid width value: {e}")

                        # Height
                        if "height" in original:
                            try:
                                expected_height = int(original["height"])
                                self.assertEqual(
                                    media.height,
                                    expected_height,
                                    f"Height mismatch: got {media.height}, expected {expected_height}",
                                )
                            except (ValueError, TypeError) as e:
                                self.fail(f"Invalid height value: {e}")

                # Test 3: Process same media again to test unique constraint handling
                process_media_info(self.config, media_data)
                # Verify only one record exists
                count = session.query(Media).filter_by(id=media_data["mediaId"]).count()
                self.assertEqual(count, 1, "Duplicate media record created")

        finally:
            # Cleanup
            with self.config._database.session_scope() as session:
                session.query(Media).delete()
                session.commit()

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
        with self.config._database.session_scope() as session:
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
