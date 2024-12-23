"""Unit tests for metadata.account module."""

from datetime import datetime, timezone
from unittest import TestCase
from unittest.mock import MagicMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from metadata.account import (
    Account,
    AccountMedia,
    AccountMediaBundle,
    TimelineStats,
    account_media_bundle_media,
    process_media_bundles,
)
from metadata.base import Base


class TestAccount(TestCase):
    """Test cases for Account class and related functions."""

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

    def test_account_media_bundle_creation(self):
        """Test creating an AccountMediaBundle with ordered content."""
        # Create account
        account = Account(id=1, username="test_user")
        self.session.add(account)

        # Create media items
        media1 = AccountMedia(
            id=1, accountId=1, mediaId=101, createdAt=datetime.now(timezone.utc)
        )
        media2 = AccountMedia(
            id=2, accountId=1, mediaId=102, createdAt=datetime.now(timezone.utc)
        )
        self.session.add_all([media1, media2])

        # Create bundle
        bundle = AccountMediaBundle(
            id=1, accountId=1, createdAt=datetime.now(timezone.utc)
        )
        self.session.add(bundle)
        self.session.flush()

        # Add media to bundle with positions
        self.session.execute(
            account_media_bundle_media.insert().values(
                [
                    {"bundle_id": 1, "media_id": 1, "pos": 2},
                    {"bundle_id": 1, "media_id": 2, "pos": 1},
                ]
            )
        )
        self.session.commit()

        # Verify bundle content order
        saved_bundle = self.session.query(AccountMediaBundle).first()
        media_ids = sorted(
            [m.id for m in saved_bundle.accountMediaIds], key=lambda x: x
        )
        self.assertEqual(media_ids, [1, 2])  # Should be ordered by id

    def test_update_optimization(self):
        """Test that attributes are only updated when values actually change."""
        # Create initial account
        account = Account(id=1, username="test_user", displayName="Test User")
        self.session.add(account)
        self.session.commit()

        # Mock SQLAlchemy's Session.execute to track calls
        original_execute = Session.execute
        execute_calls = []

        def mock_execute(self, *args, **kwargs):
            execute_calls.append(args[0])
            return original_execute(self, *args, **kwargs)

        with patch("sqlalchemy.orm.session.Session.execute", mock_execute):
            # Update with same values
            data = {"id": 1, "username": "test_user", "displayName": "Test User"}
            from metadata.account import process_account_data

            mock_config = MagicMock()
            mock_config._database = MagicMock()
            mock_config._database.sync_session = self.Session
            process_account_data(mock_config, data)

            # Check that no UPDATE statements were executed
            update_calls = [
                call for call in execute_calls if str(call).upper().startswith("UPDATE")
            ]
            self.assertEqual(
                len(update_calls),
                0,
                "No updates should be performed when values haven't changed",
            )

            # Update with different values
            data["displayName"] = "New Name"
            mock_config = MagicMock()
            mock_config._database = MagicMock()
            mock_config._database.sync_session = self.Session
            process_account_data(mock_config, data)

            # Check that UPDATE was executed only for changed value
            update_calls = [
                call for call in execute_calls if str(call).upper().startswith("UPDATE")
            ]
            self.assertEqual(
                len(update_calls), 1, "Update should be performed when value changes"
            )

    def test_timeline_stats_optimization(self):
        """Test that timeline stats are only updated when values change."""
        from metadata.account import process_timeline_stats

        # Create initial timeline stats
        stats = TimelineStats(
            accountId=1,
            imageCount=10,
            videoCount=5,
            fetchedAt=datetime.now(timezone.utc),
        )
        self.session.add(stats)
        self.session.commit()

        # Mock execute to track calls
        original_execute = Session.execute
        execute_calls = []

        def mock_execute(self, *args, **kwargs):
            execute_calls.append(args[0])
            return original_execute(self, *args, **kwargs)

        with patch("sqlalchemy.orm.session.Session.execute", mock_execute):
            # Update with same values
            data = {
                "id": 1,
                "timelineStats": {
                    "imageCount": 10,
                    "videoCount": 5,
                    "fetchedAt": int(stats.fetchedAt.timestamp() * 1000),
                },
            }
            process_timeline_stats(self.session, data)

            # Check that no UPDATE statements were executed
            update_calls = [
                call for call in execute_calls if str(call).upper().startswith("UPDATE")
            ]
            self.assertEqual(
                len(update_calls),
                0,
                "No updates should be performed when values haven't changed",
            )

            # Update with different values
            data["timelineStats"]["imageCount"] = 15
            process_timeline_stats(self.session, data)

            # Check that UPDATE was executed
            update_calls = [
                call for call in execute_calls if str(call).upper().startswith("UPDATE")
            ]
            self.assertEqual(
                len(update_calls), 1, "Update should be performed when value changes"
            )

    def test_process_media_bundles(self):
        """Test processing media bundles from API response."""
        mock_config = MagicMock()
        mock_config._database = MagicMock()
        mock_config._database.sync_session = self.Session
        bundles_data = [
            {
                "id": 1,
                "accountId": 1,
                "createdAt": int(datetime.now(timezone.utc).timestamp()),
                "bundleContent": [
                    {"accountMediaId": 101, "pos": 2},
                    {"accountMediaId": 102, "pos": 1},
                ],
            }
        ]

        # Create account and media
        account = Account(id=1, username="test_user")
        media1 = AccountMedia(
            id=101, accountId=1, mediaId=1001, createdAt=datetime.now(timezone.utc)
        )
        media2 = AccountMedia(
            id=102, accountId=1, mediaId=1002, createdAt=datetime.now(timezone.utc)
        )
        self.session.add_all([account, media1, media2])
        self.session.commit()

        process_media_bundles(mock_config, 1, bundles_data)

        # Verify bundle was created with correct order
        bundle = self.session.query(AccountMediaBundle).first()
        self.assertIsNotNone(bundle)
        media_ids = [m.id for m in bundle.accountMediaIds]
        self.assertEqual(media_ids, [102, 101])  # Should be ordered by pos
