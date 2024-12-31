"""Integration tests for the metadata package."""

from datetime import datetime, timezone
from unittest import TestCase

import pytest
from sqlalchemy import text

from metadata import (
    Account,
    AccountMedia,
    Media,
    Message,
    Post,
    TimelineStats,
    Wall,
    process_account_data,
    process_media_info,
)


@pytest.mark.integration
class TestMetadataPackage(TestCase):
    """Integration tests for the metadata package as a whole."""

    @pytest.fixture(autouse=True)
    def setup_database(self, temp_db_path, database, timeline_data):
        from tests.metadata.conftest import temp_db_path

        """Set up test environment with database."""
        self.db_path = temp_db_path
        self.database = database
        self.timeline_data = timeline_data
        self.config = database.config  # Get config from database fixture
        self.config._database = database  # Set the database on the config

    def test_full_content_processing(self):
        """Test processing a complete set of content."""
        # Process account data from timeline
        account_data = self.timeline_data["response"]["accounts"][0]
        process_account_data(self.config, account_data)

        # Process media
        if "accountMedia" in self.timeline_data["response"]:
            for media_data in self.timeline_data["response"]["accountMedia"]:
                process_media_info(self.config, media_data)

        # Verify data through database queries
        with self.database.get_sync_session() as session:
            # Check account
            account = session.query(Account).first()
            self.assertIsNotNone(account)
            self.assertEqual(account.username, account_data["username"])

            # Check timeline stats
            if "timelineStats" in account_data:
                stats = account.timelineStats
                self.assertIsNotNone(stats)
                self.assertEqual(
                    stats.imageCount, account_data["timelineStats"].get("imageCount", 0)
                )
                self.assertEqual(
                    stats.videoCount, account_data["timelineStats"].get("videoCount", 0)
                )
                self.assertIsInstance(stats, TimelineStats)

            # Check media
            media_count = session.query(Media).count()
            self.assertGreater(media_count, 0)

            # Check account media
            account_media_count = session.query(AccountMedia).count()
            self.assertGreater(account_media_count, 0)

    def test_relationship_integrity(self):
        """Test integrity of relationships between models."""
        from tests.metadata.utils import (
            create_test_data_set,
            verify_relationship_integrity,
        )

        with self.database.get_sync_session() as session:
            # Create a complete set of test data
            data = create_test_data_set(
                session,
                num_accounts=2,
                num_media_per_account=3,
                num_posts_per_account=2,
                num_walls_per_account=2,
            )

            # Verify relationships for each account
            for account in data["accounts"]:
                # Explicitly query for accountMedia relationship
                account_media = (
                    session.query(AccountMedia).filter_by(accountId=account.id).all()
                )
                self.assertGreater(len(account_media), 0)
                self.assertTrue(
                    verify_relationship_integrity(
                        session, account, "accountMedia", expected_count=3
                    )
                )

                # Verify each AccountMedia -> Media relationship
                account_media_items = (
                    session.query(AccountMedia).filter_by(accountId=account.id).all()
                )
                for account_media in account_media_items:
                    media = (
                        session.query(Media).filter_by(id=account_media.mediaId).first()
                    )
                    self.assertIsNotNone(media)
                    self.assertEqual(media.accountId, account.id)

                # Account -> Walls relationship
                self.assertTrue(
                    verify_relationship_integrity(
                        session, account, "walls", expected_count=2
                    )
                )

                # Create walls for the account
                for i in range(2):
                    wall = Wall(
                        id=len(data["walls"]) + 1,
                        accountId=account.id,
                        createdAt=datetime.now(timezone.utc),
                    )
                    session.add(wall)
                    data["walls"].append(wall)
                session.commit()

                # Verify walls were created
                self.assertEqual(len(account.walls), 2)

            # Verify wall -> posts relationships
            for wall in data["walls"]:
                # Use explicit query to efficiently fetch matching posts
                matching_posts = (
                    session.query(Post).filter(Post.accountId == wall.accountId).all()
                )
                wall.posts = matching_posts
            # Single commit after the loop
            session.commit()

    def test_database_constraints(self):
        """Test database constraints and referential integrity."""
        with self.database.get_sync_session() as session:
            # Try to create media without account (should fail)
            media = Media(id=1)  # Missing required accountId
            session.add(media)
            with self.assertRaises(Exception):
                session.commit()
            session.rollback()

            # Try to create wall without account (should fail)
            wall = Wall(id=1, name="Test")  # Missing required accountId
            session.add(wall)
            with self.assertRaises(Exception):
                session.commit()
            session.rollback()

            # Try to create message without sender (should fail)
            message = Message(
                id=1, content="Test", createdAt=datetime.now(timezone.utc)
            )  # Missing required senderId
            session.add(message)
            with self.assertRaises(Exception):
                session.commit()
            session.rollback()

    def test_database_indexes(self):
        """Test that important queries use indexes."""
        with self.database.get_sync_session() as session:
            # Create test account
            account = Account(id=1, username="test_user")
            session.add(account)
            session.commit()

            # Check username index
            result = session.execute(
                text(
                    "EXPLAIN QUERY PLAN SELECT * FROM accounts WHERE username = 'test_user'"
                )
            )
            plan = result.fetchall()
            self.assertTrue(
                any("USING INDEX" in str(row) for row in plan),
                "Query not using index for accounts.username",
            )

            # Check foreign key indexes
            result = session.execute(
                text("EXPLAIN QUERY PLAN SELECT * FROM walls WHERE accountId = 1")
            )
            plan = result.fetchall()
            self.assertTrue(
                any("USING INDEX" in str(row) for row in plan),
                "Query not using index for walls.accountId",
            )
