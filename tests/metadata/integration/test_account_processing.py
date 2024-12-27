"""Integration tests for account processing functionality."""

import json
import os
from datetime import datetime, timezone
from unittest import TestCase

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from config import FanslyConfig
from metadata.account import (
    Account,
    AccountMedia,
    AccountMediaBundle,
    TimelineStats,
    process_account_data,
)
from metadata.base import Base
from metadata.database import Database
from metadata.media import Media


class TestAccountProcessing(TestCase):
    """Integration tests for account data processing."""

    @classmethod
    def setUpClass(cls):
        """Load test data."""
        # Load test data
        cls.test_data_dir = os.path.join(os.path.dirname(__file__), "..", "..", "json")
        with open(os.path.join(cls.test_data_dir, "timeline-sample-account.json")) as f:
            cls.timeline_data = json.load(f)

    def setUp(self):
        """Set up fresh database and session for each test."""
        # Create test database
        self.engine: Engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.Session: sessionmaker = sessionmaker(bind=self.engine)
        self.session: Session = self.Session()

        # Create config with test database
        self.config = FanslyConfig(program_version="0.10.0")
        self.config.metadata_db_file = ":memory:"
        self.config._database = Database(self.config)
        self.config._database.sync_engine = self.engine
        self.config._database.sync_session = self.Session

    def tearDown(self):
        """Clean up after each test."""
        try:
            # Clean up data
            for table in reversed(Base.metadata.sorted_tables):
                self.session.execute(table.delete())
            self.session.commit()
        except Exception:
            self.session.rollback()
        finally:
            self.session.close()
            self.engine.dispose()

    def test_process_account_from_timeline(self):
        """Test processing account data from timeline response."""
        # Get first account from timeline data
        account_data = self.timeline_data["response"]["accounts"][0]

        # Process the account
        process_account_data(self.config, account_data)

        # Verify account was created
        with self.config._database.sync_session() as session:
            account = session.query(Account).filter_by(id=account_data["id"]).first()
            self.assertIsNotNone(account)
            self.assertEqual(account.username, account_data["username"])

            # Verify timeline stats if present
            if "timelineStats" in account_data:
                stats = (
                    session.query(TimelineStats).filter_by(accountId=account.id).first()
                )
                self.assertIsNotNone(stats)
                self.assertEqual(
                    stats.imageCount, account_data["timelineStats"]["imageCount"]
                )
                self.assertEqual(
                    stats.videoCount, account_data["timelineStats"]["videoCount"]
                )

            # Verify avatar if present
            if "avatar" in account_data:
                self.assertIsNotNone(account.avatar)
                avatar_media = (
                    session.query(Media)
                    .filter_by(id=account_data["avatar"]["id"])
                    .first()
                )
                self.assertIsNotNone(avatar_media)

            # Verify banner if present
            if "banner" in account_data:
                self.assertIsNotNone(account.banner)
                banner_media = (
                    session.query(Media)
                    .filter_by(id=account_data["banner"]["id"])
                    .first()
                )
                self.assertIsNotNone(banner_media)

    def test_update_optimization_integration(self):
        """Integration test for update optimization."""
        # Create initial account with timeline stats
        account_data = {
            "id": 999999,
            "username": "test_optimization",
            "displayName": "Test User",
            "timelineStats": {
                "imageCount": 10,
                "videoCount": 5,
                "fetchedAt": int(datetime.now(timezone.utc).timestamp() * 1000),
            },
        }

        # Process initial data
        process_account_data(self.config, account_data)

        # Get initial update time of the account and stats
        with self.config._database.sync_session() as session:
            account = session.query(Account).get(account_data["id"])
            stats = session.query(TimelineStats).get(account_data["id"])
            initial_account_updated = getattr(account, "_sa_instance_state").modified
            initial_stats_updated = getattr(stats, "_sa_instance_state").modified

        # Process same data again
        process_account_data(self.config, account_data)

        # Check that nothing was updated
        with self.config._database.sync_session() as session:
            account = session.query(Account).get(account_data["id"])
            stats = session.query(TimelineStats).get(account_data["id"])
            self.assertEqual(
                getattr(account, "_sa_instance_state").modified,
                initial_account_updated,
                "Account should not be marked as modified when no values changed",
            )
            self.assertEqual(
                getattr(stats, "_sa_instance_state").modified,
                initial_stats_updated,
                "TimelineStats should not be marked as modified when no values changed",
            )

        # Update some values
        account_data["displayName"] = "Updated Name"
        account_data["timelineStats"]["imageCount"] = 15
        process_account_data(self.config, account_data)

        # Check that only changed values were updated
        with self.config._database.sync_session() as session:
            account = session.query(Account).get(account_data["id"])
            stats = session.query(TimelineStats).get(account_data["id"])
            self.assertEqual(account.displayName, "Updated Name")
            self.assertEqual(stats.imageCount, 15)
            self.assertEqual(stats.videoCount, 5)  # Should remain unchanged

    def test_process_account_media_bundles(self):
        """Test processing account media bundles from timeline response."""
        if "accountMediaBundles" not in self.timeline_data["response"]:
            self.skipTest("No bundles found in test data")

        # Get first account and its bundles
        account_data = self.timeline_data["response"]["accounts"][0]
        bundles_data = self.timeline_data["response"]["accountMediaBundles"]

        # Create the account first
        process_account_data(self.config, account_data)

        # Process each bundle's media
        with self.config._database.sync_session() as session:
            for bundle in bundles_data:
                # Create necessary AccountMedia records
                for content in bundle.get("bundleContent", []):
                    media = AccountMedia(
                        id=content["accountMediaId"],
                        accountId=account_data["id"],
                        mediaId=content["accountMediaId"],
                    )
                    session.add(media)
            session.commit()

        # Process the bundles
        from metadata.account import process_media_bundles

        process_media_bundles(self.config, account_data["id"], bundles_data)

        # Verify bundles were created with correct ordering
        with self.config._database.sync_session() as session:
            for bundle_data in bundles_data:
                bundle = (
                    session.query(AccountMediaBundle)
                    .filter_by(id=bundle_data["id"])
                    .first()
                )
                self.assertIsNotNone(bundle)

                # Verify media count
                self.assertEqual(
                    len(bundle.accountMediaIds), len(bundle_data["bundleContent"])
                )

                # Verify order
                media_ids = [m.id for m in bundle.accountMediaIds]
                expected_order = [
                    c["accountMediaId"]
                    for c in sorted(
                        bundle_data["bundleContent"], key=lambda x: x["pos"]
                    )
                ]
                self.assertEqual(media_ids, expected_order)
