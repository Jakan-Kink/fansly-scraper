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
            try:
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
                    # Test 1: Account -> AccountMedia relationship
                    account_media = (
                        session.query(AccountMedia)
                        .filter_by(accountId=account.id)
                        .order_by(AccountMedia.id)
                        .all()
                    )
                    self.assertEqual(
                        len(account_media), 3
                    )  # Expect exactly 3 media items
                    self.assertTrue(
                        verify_relationship_integrity(
                            session, account, "accountMedia", expected_count=3
                        )
                    )

                    # Test 2: AccountMedia -> Media relationship
                    for account_media in account_media:
                        media = (
                            session.query(Media)
                            .filter_by(id=account_media.mediaId)
                            .first()
                        )
                        self.assertIsNotNone(
                            media,
                            f"Media {account_media.mediaId} not found for AccountMedia {account_media.id}",
                        )
                        self.assertEqual(
                            media.accountId,
                            account.id,
                            f"Media {media.id} has wrong accountId {media.accountId}, expected {account.id}",
                        )

                    # Test 3: Account -> Walls relationship (existing walls)
                    existing_walls = (
                        session.query(Wall)
                        .filter_by(accountId=account.id)
                        .order_by(Wall.id)
                        .all()
                    )
                    self.assertEqual(
                        len(existing_walls),
                        2,
                        f"Expected 2 walls for account {account.id}, found {len(existing_walls)}",
                    )
                    self.assertTrue(
                        verify_relationship_integrity(
                            session, account, "walls", expected_count=2
                        )
                    )

                    # Test 4: Wall -> Posts relationship
                    for wall in existing_walls:
                        # Get posts for this wall's account
                        account_posts = (
                            session.query(Post)
                            .filter_by(accountId=wall.accountId)
                            .order_by(Post.id)
                            .all()
                        )
                        self.assertEqual(
                            len(account_posts),
                            2,
                            f"Expected 2 posts for account {wall.accountId}, found {len(account_posts)}",
                        )

                        # Verify each post belongs to the correct account
                        for post in account_posts:
                            self.assertEqual(
                                post.accountId,
                                wall.accountId,
                                f"Post {post.id} has wrong accountId {post.accountId}, expected {wall.accountId}",
                            )

                # Test 5: Verify no orphaned records
                # Count total records
                total_accounts = session.query(Account).count()
                total_media = session.query(Media).count()
                total_account_media = session.query(AccountMedia).count()
                total_walls = session.query(Wall).count()
                total_posts = session.query(Post).count()

                # Verify expected counts
                self.assertEqual(total_accounts, 2)  # num_accounts
                self.assertEqual(total_media, 6)  # num_accounts * num_media_per_account
                self.assertEqual(total_account_media, 6)  # Same as media count
                self.assertEqual(total_walls, 4)  # num_accounts * num_walls_per_account
                self.assertEqual(total_posts, 4)  # num_accounts * num_posts_per_account

            finally:
                # Cleanup in reverse order of dependencies
                session.query(Post).delete()
                session.query(Wall).delete()
                session.query(AccountMedia).delete()
                session.query(Media).delete()
                session.query(Account).delete()
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
