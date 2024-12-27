"""Integration tests for database operations."""

import os
import tempfile
from datetime import datetime, timezone
from unittest import TestCase

from sqlalchemy import exc, text

from config import FanslyConfig
from metadata.account import Account, AccountMedia
from metadata.base import Base
from metadata.database import Database
from metadata.media import Media
from metadata.messages import Message
from metadata.post import Post


class TestDatabaseOperations(TestCase):
    """Integration tests for database operations across multiple models."""

    def setUp(self):
        """Set up test database with all tables."""
        # Create temporary database file
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test.db")

        # Create config and database
        self.config = FanslyConfig(program_version="0.10.0")
        self.config.metadata_db_file = self.db_path
        self.database = Database(self.config)

        # Create all tables
        Base.metadata.create_all(self.database.sync_engine)

    def tearDown(self):
        """Clean up test database."""
        try:
            # Clean up data
            with self.database.get_sync_session() as session:
                for table in reversed(Base.metadata.sorted_tables):
                    session.execute(table.delete())
                session.commit()
        except Exception:
            pass  # Ignore cleanup errors

        try:
            # Close database
            self.database.close()
        except Exception:
            pass  # Ignore close errors

        try:
            # Remove temporary files
            if os.path.exists(self.db_path):
                os.remove(self.db_path)
            if os.path.exists(self.temp_dir):
                import shutil

                shutil.rmtree(self.temp_dir)  # Remove directory and all its contents
        except Exception:
            pass  # Ignore cleanup errors

    def test_complex_relationships(self):
        """Test complex relationships between multiple models."""
        with self.database.get_sync_session() as session:
            # Create account
            account = Account(id=1, username="test_user")
            session.add(account)
            session.flush()

            # Create media
            media = Media(
                id=1,
                accountId=account.id,
                mimetype="video/mp4",
                width=1920,
                height=1080,
                duration=30.5,
            )
            session.add(media)
            session.flush()

            # Create account media
            account_media = AccountMedia(
                id=1,
                accountId=account.id,
                mediaId=media.id,
                createdAt=datetime.now(timezone.utc),
            )
            session.add(account_media)

            # Create post with media
            post = Post(
                id=1,
                accountId=account.id,
                content="Test post",
                createdAt=datetime.now(timezone.utc),
            )
            session.add(post)

            # Create message referencing the account
            message = Message(
                id=1,
                senderId=account.id,
                content="Test message",
                createdAt=datetime.now(timezone.utc),
            )
            session.add(message)
            session.commit()

            # Verify relationships
            saved_account = session.query(Account).first()
            self.assertEqual(saved_account.username, "test_user")
            self.assertEqual(len(saved_account.accountMedia), 1)

            saved_media = session.query(Media).first()
            self.assertEqual(saved_media.width, 1920)
            self.assertEqual(saved_media.height, 1080)
            self.assertEqual(saved_media.duration, 30.5)

    def test_cascade_operations(self):
        """Test cascade operations across relationships."""
        with self.database.get_sync_session() as session:
            # Create account with related entities
            account = Account(id=1, username="test_user")
            session.add(account)
            session.flush()

            # Create media and account media
            media = Media(id=1, accountId=account.id)
            session.add(media)
            session.flush()

            account_media = AccountMedia(
                id=1,
                accountId=account.id,
                mediaId=media.id,
                createdAt=datetime.now(timezone.utc),
            )
            session.add(account_media)
            session.commit()

            # Delete account and verify cascades
            session.delete(account)
            session.commit()

            # Verify everything was deleted
            self.assertIsNone(session.query(Account).first())
            self.assertIsNone(session.query(AccountMedia).first())
            # Media should still exist as it might be referenced by other accounts
            self.assertIsNotNone(session.query(Media).first())

    def test_database_constraints(self):
        """Test database constraints and integrity."""
        with self.database.get_sync_session() as session:
            # Create an Account first
            account = Account(id=100, username="test_user")
            session.add(account)
            session.flush()

            # Create a Media object
            media = Media(
                id=100,
                accountId=account.id,
                createdAt=datetime.now(timezone.utc),
            )
            session.add(media)
            session.flush()

            # Try to create account media with non-existent account (should fail)
            try:
                account_media = AccountMedia(
                    id=100,
                    accountId=999,  # Non-existent account
                    mediaId=media.id,
                    createdAt=datetime.now(timezone.utc),
                )
                session.add(account_media)
                session.commit()  # Use commit to catch the error
                self.fail("IntegrityError not raised")
            except exc.IntegrityError:
                session.rollback()

            # Try to create account media with non-existent media (should fail)
            try:
                account_media = AccountMedia(
                    id=101,
                    accountId=account.id,
                    mediaId=999,  # Non-existent media
                    createdAt=datetime.now(timezone.utc),
                )
                session.add(account_media)
                session.commit()  # Use commit to catch the error
                self.fail("IntegrityError not raised")
            except exc.IntegrityError:
                session.rollback()

            # Try to create message without sender (should fail)
            message = Message(
                id=1,
                content="Test",
                createdAt=datetime.now(timezone.utc),
            )
            session.add(message)
            with self.assertRaises(exc.IntegrityError):
                session.flush()  # Use flush instead of commit to catch the error
            session.rollback()

            # Try to create message with non-existent sender (should fail)
            message = Message(
                id=1,
                senderId=999,  # Non-existent account
                content="Test",
                createdAt=datetime.now(timezone.utc),
            )
            session.add(message)
            with self.assertRaises(Exception):
                session.commit()

    def test_query_performance(self):
        """Test query performance with indexes."""
        with self.database.get_sync_session() as session:
            # Create test data
            account = Account(id=1, username="test_user")
            session.add(account)
            session.flush()

            # Create multiple media items
            for i in range(100):
                media = Media(id=i + 1, accountId=account.id)
                session.add(media)
                account_media = AccountMedia(
                    id=i + 1,
                    accountId=account.id,
                    mediaId=media.id,
                    createdAt=datetime.now(timezone.utc),
                )
                session.add(account_media)
            session.commit()

            # Create index on accountId
            session.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS idx_account_media_accountid ON account_media (accountId)"
                )
            )
            session.commit()

            # This should use the index on accountId
            result = session.execute(
                text(
                    "EXPLAIN QUERY PLAN SELECT * FROM account_media WHERE accountId = 1"
                )
            )
            plan = result.fetchall()
            # Verify index usage (plan should mention USING INDEX)
            self.assertTrue(
                any("USING INDEX" in str(row) for row in plan),
                "Query not using index for account_media.accountId",
            )

    def test_write_through_cache_integration(self):
        """Test write-through caching in a multi-table scenario."""
        # Create initial data
        with self.database.get_sync_session() as session:
            account = Account(id=1, username="test_user")
            session.add(account)
            session.commit()

        # Create a second database connection
        db2 = Database(self.config)
        try:
            # Verify data is immediately visible
            with db2.get_sync_session() as session:
                saved_account = session.query(Account).first()
                self.assertEqual(saved_account.username, "test_user")

                # Add more data through second connection
                media = Media(id=1, accountId=account.id)
                session.add(media)
                session.commit()
        finally:
            db2.close()

        # Verify new data is visible in original connection
        with self.database.get_sync_session() as session:
            saved_media = session.query(Media).first()
            self.assertIsNotNone(saved_media)
            self.assertEqual(saved_media.accountId, 1)

    def test_database_cleanup_integration(self):
        """Test database cleanup in integration scenario."""
        # Create some data
        with self.database.get_sync_session() as session:
            account = Account(id=1, username="test_user")
            session.add(account)
            session.commit()

        # Close the database
        self.database.close()

        # Verify we can't use the database after closing
        with self.assertRaises(Exception):
            with self.database.get_sync_session() as session:
                session.query(Account).first()

        # Create a new connection to verify data persists
        db2 = Database(self.config)
        try:
            with db2.get_sync_session() as session:
                saved_account = session.query(Account).first()
                self.assertEqual(saved_account.username, "test_user")
        finally:
            db2.close()
