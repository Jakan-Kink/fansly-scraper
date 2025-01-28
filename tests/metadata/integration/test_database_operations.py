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

import asyncio
import sqlite3
from datetime import datetime, timezone
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import exc, select, text
from sqlalchemy.orm import Session

from metadata import Account, AccountMedia, Base, Media, Message, Post
from metadata.database import Database
from textio import print_error, print_info, print_warning


@pytest.fixture(autouse=True)
async def setup_database(database: Database):
    """Create database tables before each test."""
    try:
        async with database.get_async_session() as session:
            # Create all tables
            async with session.begin():
                await session.run_sync(Base.metadata.create_all, session.get_bind())
        yield
    finally:
        # Clean up after each test
        try:
            async with database.get_async_session() as session:
                # Drop all tables
                async with session.begin():
                    await session.run_sync(Base.metadata.drop_all, session.get_bind())
        except Exception as e:
            print_error(f"Error during cleanup: {e}")
            raise
        finally:
            # Ensure all connections are closed
            await database.close_async()


@pytest.mark.asyncio
async def test_complex_relationships(
    database: Database, session: Session, test_account: Account
):
    """Test complex relationships between multiple models."""
    async with database.get_async_session() as session:
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
        await session.flush()

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
        await session.commit()

        # Verify relationships
        result = await session.execute(select(Account))
        saved_account = result.scalar_one()
        assert saved_account.username == test_account.username
        assert len(await saved_account.awaitable_attrs.accountMedia) == 1

        result = await session.execute(select(Media))
        saved_media = result.scalar_one()
        assert saved_media.width == 1920
        assert saved_media.height == 1080
        assert saved_media.duration == 30.5


@pytest.mark.asyncio
async def test_cascade_operations(
    database: Database, session: Session, test_account: Account
):
    """Test cascade operations across relationships."""
    async with database.get_async_session() as session:
        # Create media and account media
        media = Media(id=1, accountId=test_account.id)
        session.add(media)
        await session.flush()

        account_media = AccountMedia(
            id=1,
            accountId=test_account.id,
            mediaId=media.id,
            createdAt=datetime.now(timezone.utc),
        )
        session.add(account_media)
        await session.commit()

        # Delete account and verify cascades
        result = await session.execute(select(Account).filter_by(id=test_account.id))
        account = result.scalar_one()
        session.delete(account)
        await session.commit()

        # Verify everything was deleted
        result = await session.execute(select(Account).filter_by(id=test_account.id))
        assert result.scalar_one_or_none() is None

        result = await session.execute(
            select(AccountMedia).filter_by(accountId=test_account.id)
        )
        assert result.scalar_one_or_none() is None

        # Media should still exist as it might be referenced by other accounts
        result = await session.execute(select(Media).filter_by(id=1))
        assert result.scalar_one_or_none() is not None


@pytest.mark.asyncio
async def test_database_constraints(
    database: Database, session: Session, test_account: Account
):
    """Test database constraints and integrity."""
    async with database.get_async_session() as session:
        # Create a Media object
        media = Media(
            id=100,
            accountId=test_account.id,
            createdAt=datetime.now(timezone.utc),
        )
        session.add(media)
        await session.flush()

        # Test 1: Create account media with non-existent account
        # Since foreign keys are disabled, this should succeed but validate at app level
        account_media = AccountMedia(
            id=100,
            accountId=999,  # Non-existent account
            mediaId=media.id,
            createdAt=datetime.now(timezone.utc),
        )
        session.add(account_media)
        await session.commit()

        # Verify the record exists but has an invalid foreign key
        result = await session.execute(select(AccountMedia).filter_by(id=100))
        invalid_account_media = result.scalar_one_or_none()
        assert invalid_account_media is not None

        # Verify the referenced account doesn't exist
        result = await session.execute(select(Account).filter_by(id=999))
        referenced_account = result.scalar_one_or_none()
        assert referenced_account is None

        # Test 2: Create account media with non-existent media
        account_media = AccountMedia(
            id=101,
            accountId=test_account.id,
            mediaId=999,  # Non-existent media
            createdAt=datetime.now(timezone.utc),
        )
        session.add(account_media)
        await session.commit()

        # Verify the record exists but has an invalid foreign key
        result = await session.execute(select(AccountMedia).filter_by(id=101))
        invalid_media_ref = result.scalar_one_or_none()
        assert invalid_media_ref is not None

        # Verify the referenced media doesn't exist
        result = await session.execute(select(Media).filter_by(id=999))
        referenced_media = result.scalar_one_or_none()
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
            await session.flush()
        await session.rollback()

        # Test 4: Create message with non-existent sender
        # Since foreign keys are disabled, this should succeed
        message = Message(
            id=1,
            senderId=999,  # Non-existent account
            content="Test",
            createdAt=datetime.now(timezone.utc),
        )
        session.add(message)
        await session.commit()

        # Verify the message exists but has an invalid sender
        result = await session.execute(select(Message).filter_by(id=1))
        invalid_message = result.scalar_one_or_none()
        assert invalid_message is not None

        # Verify the referenced sender doesn't exist
        result = await session.execute(select(Account).filter_by(id=999))
        referenced_sender = result.scalar_one_or_none()
        assert referenced_sender is None


@pytest.mark.asyncio
async def test_transaction_isolation(database: Database):
    """Test transaction isolation levels."""
    # Create test data in first session
    async with database.get_async_session() as session1:
        account1 = Account(
            id=1,
            username="test_user_1",
            createdAt=datetime.now(timezone.utc),
        )
        session1.add(account1)

        # Start second transaction before committing first
        async with database.get_async_session() as session2:
            # Should not see uncommitted data from first session
            result = await session2.execute(select(Account).filter_by(id=1))
            assert result.scalar_one_or_none() is None

            # Add different account in second session
            account2 = Account(
                id=2,
                username="test_user_2",
                createdAt=datetime.now(timezone.utc),
            )
            session2.add(account2)
            await session2.commit()

        # First transaction should still succeed
        await session1.commit()

    # Verify final state
    async with database.get_async_session() as session:
        result = await session.execute(select(Account).order_by(Account.id))
        accounts = result.scalars().all()
        assert len(accounts) == 2
        assert accounts[0].id == 1
        assert accounts[1].id == 2


@pytest.mark.asyncio
async def test_concurrent_access(database: Database, test_account: Account):
    """Test concurrent database access patterns."""
    num_tasks = 5
    num_messages = 10

    async def add_messages(account_id: int, start_id: int) -> list[int]:
        """Add messages in separate task."""
        message_ids = []
        try:
            async with database.get_async_session() as session:
                # Disable foreign key checks
                await session.execute(text("PRAGMA foreign_keys = OFF"))
                for i in range(num_messages):
                    msg = Message(
                        id=start_id + i,
                        senderId=account_id,
                        content=f"Message {i} from task {id(asyncio.current_task())}",
                        createdAt=datetime.now(timezone.utc),
                    )
                    session.add(msg)
                    message_ids.append(msg.id)
                await session.commit()
            return message_ids
        except Exception as e:
            print_error(f"Error in add_messages task: {e}")
            raise

    # Create messages concurrently
    tasks = []
    try:
        for i in range(num_tasks):
            task = asyncio.create_task(
                add_messages(test_account.id, i * num_messages + 1)
            )
            tasks.append(task)

        # Wait for all tasks and collect results
        message_ids = []
        for task in tasks:
            message_ids.extend(await task)

        # Verify results
        async with database.get_async_session() as session:
            result = await session.execute(
                select(Message).filter(Message.id.in_(message_ids)).order_by(Message.id)
            )
            messages = result.scalars().all()

            assert len(messages) == num_tasks * num_messages
            assert all(msg.senderId == test_account.id for msg in messages)
    finally:
        # Cancel any remaining tasks
        for task in tasks:
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass


@pytest.mark.asyncio
async def test_query_performance(
    database: Database, session: Session, test_account: Account
):
    """Test query performance with indexes."""
    async with database.get_async_session() as session:
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
        await session.commit()

        # Create index on accountId
        await session.execute(
            text(
                "CREATE INDEX IF NOT EXISTS idx_account_media_accountid ON account_media (accountId)"
            )
        )
        await session.commit()

        # This should use the index on accountId
        result = await session.execute(
            text("EXPLAIN QUERY PLAN SELECT * FROM account_media WHERE accountId = 1")
        )
        plan = result.fetchall()
        # Verify index usage (plan should mention USING INDEX)
        assert any(
            "USING INDEX" in str(row) for row in plan
        ), "Query not using index for account_media.accountId"


@pytest.mark.asyncio
async def test_bulk_operations(database: Database):
    """Test bulk database operations with transaction management."""
    async with database.get_async_session() as session:
        await session.rollback()  # Clear any existing transaction

        try:
            # Start a nested transaction
            async with session.begin():
                # Create multiple records
                for i in range(10):
                    account = Account(
                        id=i + 1,
                        username=f"bulk_user_{i}",
                        createdAt=datetime.now(timezone.utc),
                    )
                    session.add(account)
                await session.commit()

            # Verify records were created
            result = await session.execute(select(Account))
            accounts = result.scalars().all()
            assert len(accounts) == 10

        except Exception:
            await session.rollback()
            raise


@pytest.mark.asyncio
async def test_write_through_cache_integration(
    database: Database, test_config, test_account: Account
):
    """Test write-through caching in a multi-table scenario."""
    # Create initial data with unique username
    unique_username = f"cache_test_{test_account.username}"
    async with database.get_async_session() as session:
        account = Account(id=1, username=unique_username)
        session.add(account)
        await session.commit()

    # Create a second database connection
    db2: Database | None = None
    try:
        db2 = Database(test_config)
        # Verify data is immediately visible
        async with db2.get_async_session() as session:
            result = await session.execute(select(Account))
            saved_account = result.scalar_one()
            assert saved_account.username == unique_username

            # Add more data through second connection
            media = Media(id=1, accountId=saved_account.id)
            session.add(media)
            await session.commit()

            # Verify data is visible in same session
            result = await session.execute(select(Media))
            saved_media = result.scalar_one()
            assert saved_media is not None
            assert saved_media.accountId == 1
    finally:
        if db2 is not None:
            try:
                async with asyncio.timeout(5):
                    await db2.close_async()
            except TimeoutError:
                print_error("Timeout while closing second database")
                raise
            except Exception as e:
                print_error(f"Error closing second database: {e}")
                raise

    # Verify new data is visible in original connection
    async with database.get_async_session() as session:
        result = await session.execute(select(Media))
        saved_media = result.scalar_one()
        assert saved_media is not None
        assert saved_media.accountId == 1


@pytest.mark.asyncio
@pytest.mark.timeout(30)  # Add timeout to prevent hanging
async def test_database_cleanup_integration(
    database: Database, session: Session, test_account: Account
):
    """Test database cleanup in integration scenario."""
    print_info("Starting database cleanup integration test")

    # Create some data with a unique username
    unique_username = f"cleanup_test_{test_account.username}"
    print_info("Creating test account")

    # Use async session for consistency
    async with database.get_async_session() as test_session:
        account = Account(id=1, username=unique_username)
        test_session.add(account)
        await test_session.commit()
        print_info("Test account created successfully")

    print_info("Closing database")
    # Close the database with timeout
    try:
        async with asyncio.timeout(5):
            await database.close_async()
            print_info("Database closed successfully")
    except TimeoutError:
        print_error("Timeout while closing database")
        raise
    except Exception as e:
        print_error(f"Error closing database: {e}")
        raise

    print_info("Verifying database is closed")
    # Verify database is properly closed
    assert not hasattr(database, "async_engine"), "Async engine should be disposed"
    assert not hasattr(database, "sync_engine"), "Sync engine should be disposed"
    print_info("Database verified as closed")

    print_info("Creating new database connection")
    # Create a new connection to verify data persists
    db2: Database | None = None
    try:
        db2 = Database(database.config)
        print_info("Verifying data persistence")
        # Use async session for consistency
        async with db2.get_async_session() as test_session:
            result = await test_session.execute(
                select(Account).filter_by(username=unique_username)
            )
            saved_account = result.scalar_one()
            assert saved_account.username == unique_username
            print_info("Data persistence verified")
    finally:
        print_info("Cleaning up second database connection")
        # Close second database with timeout
        if db2 is not None:
            try:
                async with asyncio.timeout(5):
                    await db2.close_async()
                    print_info("Second database closed successfully")
            except TimeoutError:
                print_error("Timeout while closing second database")
                raise
            except Exception as e:
                print_error(f"Error closing second database: {e}")
                raise
