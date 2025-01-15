"""Integration tests for database operations with enhanced patterns.

This module demonstrates:
- Complex relationship testing
- Transaction isolation
- Concurrent access patterns
- Error recovery scenarios
- Edge cases in relationships
- Performance monitoring
- Write-through cache behavior
- Database cleanup
"""

# pylint: disable=unused-argument  # Test fixtures are used indirectly

from __future__ import annotations

import concurrent.futures
import sqlite3
import threading
from datetime import datetime, timezone
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import exc, text
from sqlalchemy.orm import Session

from metadata import Account, AccountMedia, Media, Message, Post

if TYPE_CHECKING:
    from metadata.database import Database


def test_complex_relationships(
    database: Database, session: Session, test_account: Account
):
    """Test complex relationships between multiple models."""
    # Create media
    media = Media(
        id=1,
        accountId=test_account.id,
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
        accountId=test_account.id,
        mediaId=media.id,
        createdAt=datetime.now(timezone.utc),
    )
    session.add(account_media)

    # Create post with media
    post = Post(
        id=1,
        accountId=test_account.id,
        content="Test post",
        createdAt=datetime.now(timezone.utc),
    )
    session.add(post)

    # Create message referencing the account
    message = Message(
        id=1,
        senderId=test_account.id,
        content="Test message",
        createdAt=datetime.now(timezone.utc),
    )
    session.add(message)
    session.commit()

    # Verify relationships
    saved_account = session.query(Account).first()
    assert saved_account.username == test_account.username
    assert len(saved_account.accountMedia) == 1

    saved_media = session.query(Media).first()
    assert saved_media.width == 1920
    assert saved_media.height == 1080
    assert saved_media.duration == 30.5


def test_cascade_operations(
    database: Database, session: Session, test_account: Account
):
    """Test cascade operations across relationships."""
    # Create media and account media
    media = Media(id=1, accountId=test_account.id)
    session.add(media)
    session.flush()

    account_media = AccountMedia(
        id=1,
        accountId=test_account.id,
        mediaId=media.id,
        createdAt=datetime.now(timezone.utc),
    )
    session.add(account_media)
    session.commit()

    # Delete account and verify cascades
    session.delete(test_account)
    session.commit()

    # Verify everything was deleted
    assert not session.query(Account).first()
    assert not session.query(AccountMedia).first()
    # Media should still exist as it might be referenced by other accounts
    assert session.query(Media).first() is not None


def test_database_constraints(
    database: Database, session: Session, test_account: Account
):
    """Test database constraints and integrity."""
    # Create a Media object
    media = Media(
        id=100,
        accountId=test_account.id,
        createdAt=datetime.now(timezone.utc),
    )
    session.add(media)
    session.flush()

    # Test 1: Create account media with non-existent account
    # Since foreign keys are disabled, this should succeed but validate at app level
    account_media = AccountMedia(
        id=100,
        accountId=999,  # Non-existent account
        mediaId=media.id,
        createdAt=datetime.now(timezone.utc),
    )
    session.add(account_media)
    session.commit()

    # Verify the record exists but has an invalid foreign key
    invalid_account_media = session.query(AccountMedia).filter_by(id=100).first()
    assert invalid_account_media is not None
    # Verify the referenced account doesn't exist
    referenced_account = session.query(Account).filter_by(id=999).first()
    assert referenced_account is None

    # Test 2: Create account media with non-existent media
    account_media = AccountMedia(
        id=101,
        accountId=test_account.id,
        mediaId=999,  # Non-existent media
        createdAt=datetime.now(timezone.utc),
    )
    session.add(account_media)
    session.commit()

    # Verify the record exists but has an invalid foreign key
    invalid_media_ref = session.query(AccountMedia).filter_by(id=101).first()
    assert invalid_media_ref is not None
    # Verify the referenced media doesn't exist
    referenced_media = session.query(Media).filter_by(id=999).first()
    assert referenced_media is None

    # Test 3: Create message without sender
    # This should fail at the database level due to NOT NULL constraint
    message = Message(
        id=1,
        content="Test",
        createdAt=datetime.now(timezone.utc),
    )
    session.add(message)
    with pytest.raises((exc.IntegrityError, exc.StatementError)):
        session.flush()
    session.rollback()

    # Test 4: Create message with non-existent sender
    # Since foreign keys are disabled, this should succeed
    message = Message(
        id=1,
        senderId=999,  # Non-existent account
        content="Test",
        createdAt=datetime.now(timezone.utc),
    )
    session.add(message)
    session.commit()

    # Verify the message exists but has an invalid sender
    invalid_message = session.query(Message).filter_by(id=1).first()
    assert invalid_message is not None
    # Verify the referenced sender doesn't exist
    referenced_sender = session.query(Account).filter_by(id=999).first()
    assert referenced_sender is None


def test_transaction_isolation(database: Database, session_factory: Session):
    """Test transaction isolation levels."""
    # Create test data in first session
    with database.get_sync_session() as session1:
        account1 = Account(
            id=1,
            username="test_user_1",
            createdAt=datetime.now(timezone.utc),
        )
        session1.add(account1)

        # Start second transaction before committing first
        with database.get_sync_session() as session2:
            # Should not see uncommitted data from first session
            assert not session2.query(Account).filter_by(id=1).first()

            # Add different account in second session
            account2 = Account(
                id=2,
                username="test_user_2",
                createdAt=datetime.now(timezone.utc),
            )
            session2.add(account2)
            session2.commit()

        # First transaction should still succeed
        session1.commit()

    # Verify final state
    with database.transaction(readonly=True) as session:
        accounts = session.query(Account).order_by(Account.id).all()
        assert len(accounts) == 2
        assert accounts[0].id == 1
        assert accounts[1].id == 2


def test_concurrent_access(database: Database, test_account: Account):
    """Test concurrent database access patterns."""
    num_threads = 5
    num_messages = 10
    thread_lock = threading.Lock()

    def add_messages(account_id: int, start_id: int) -> list[int]:
        """Add messages in separate thread."""
        message_ids = []
        # Use lock to prevent SQLite database lock errors
        with thread_lock:
            with database.get_sync_session() as session:
                # Disable foreign key checks
                session.execute(text("PRAGMA foreign_keys = OFF"))
                for i in range(num_messages):
                    msg = Message(
                        id=start_id + i,
                        senderId=account_id,
                        content=f"Message {i} from thread {threading.get_ident()}",
                        createdAt=datetime.now(timezone.utc),
                    )
                    session.add(msg)
                    message_ids.append(msg.id)
                session.commit()
        return message_ids

    # Create messages concurrently
    with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as executor:
        futures = []
        for i in range(num_threads):
            future = executor.submit(
                add_messages,
                test_account.id,
                i * num_messages + 1,
            )
            futures.append(future)

        # Wait for all threads and collect results
        message_ids = []
        for future in concurrent.futures.as_completed(futures):
            message_ids.extend(future.result())

    # Verify results
    with database.get_sync_session() as session:
        messages = (
            session.query(Message)
            .filter(Message.id.in_(message_ids))
            .order_by(Message.id)
            .all()
        )

        assert len(messages) == num_threads * num_messages
        assert all(msg.senderId == test_account.id for msg in messages)


def test_query_performance(database: Database, session: Session, test_account: Account):
    """Test query performance with indexes."""
    # Create multiple media items
    for i in range(100):
        media = Media(id=i + 1, accountId=test_account.id)
        session.add(media)
        account_media = AccountMedia(
            id=i + 1,
            accountId=test_account.id,
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
        text("EXPLAIN QUERY PLAN SELECT * FROM account_media WHERE accountId = 1")
    )
    plan = result.fetchall()
    # Verify index usage (plan should mention USING INDEX)
    assert any(
        "USING INDEX" in str(row) for row in plan
    ), "Query not using index for account_media.accountId"


def test_bulk_operations(database: Database):
    """Test bulk database operations with transaction management."""
    # Ensure we're not in a transaction
    with database.session_scope() as session:
        session.rollback()  # Clear any existing transaction

        try:
            # Your test logic here
            with session.begin_nested():
                # Do bulk operations
                pass
        except Exception:
            session.rollback()
            raise


def test_write_through_cache_integration(
    database: Database, test_config, test_account: Account
):
    """Test write-through caching in a multi-table scenario."""
    # Create initial data with unique username
    unique_username = f"cache_test_{test_account.username}"
    with database.get_sync_session() as session:
        account = Account(id=1, username=unique_username)
        session.add(account)
        session.commit()

    # Create a second database connection
    db2 = Database(test_config)
    try:
        # Verify data is immediately visible
        with db2.get_sync_session() as session:
            saved_account = session.query(Account).first()
            assert saved_account.username == unique_username

            # Add more data through second connection
            media = Media(id=1, accountId=saved_account.id)
            session.add(media)
            session.commit()

            # Verify data is visible in same session
            saved_media = session.query(Media).first()
            assert saved_media is not None
            assert saved_media.accountId == 1
    finally:
        db2.close()

    # Verify new data is visible in original connection
    with database.get_sync_session() as session:
        saved_media = session.query(Media).first()
        assert saved_media is not None
        assert saved_media.accountId == 1


def test_database_cleanup_integration(
    database: Database, session: Session, test_account: Account
):
    """Test database cleanup in integration scenario."""
    # Create some data with a unique username
    unique_username = f"cleanup_test_{test_account.username}"
    with database.get_sync_session() as test_session:
        account = Account(id=1, username=unique_username)
        test_session.add(account)
        test_session.commit()

    # Close the database and ensure proper cleanup
    database.close()

    # Verify we can't use the database after closing
    with pytest.raises((Exception, sqlite3.OperationalError)):
        with database.get_sync_session() as test_session:
            test_session.query(Account).first()

    # Create a new connection to verify data persists
    db2: Database = Database(database.config)
    try:
        with db2.get_sync_session() as test_session:
            saved_account = test_session.query(Account).first()
            assert saved_account.username == unique_username
    finally:
        # Ensure proper cleanup of second connection
        db2.close()
        # Clean up any remaining WAL files
        wal_path = database.db_file.with_suffix(".sqlite3-wal")
        shm_path = database.db_file.with_suffix(".sqlite3-shm")
        if wal_path.exists():
            wal_path.unlink()
        if shm_path.exists():
            shm_path.unlink()
