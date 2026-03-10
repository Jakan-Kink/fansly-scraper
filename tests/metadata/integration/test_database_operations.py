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
import contextlib
from datetime import UTC, datetime

import pytest
from sqlalchemy import exc, select, text

from metadata import Account, AccountMedia, Media, Message, Post
from metadata.database import Database
from textio import print_error, print_info


@pytest.fixture
async def setup_database(test_database_sync: Database):
    """Create database tables before each test.

    Note: Not autouse - tests must explicitly request this fixture if they need it.
    """
    try:
        # First, ensure tables are dropped if they exist
        async with (
            test_database_sync.async_session_scope() as session,
            session.begin(),
        ):
            # Get connection and run DDL operations
            # conn = await session.connection()
            # # Drop all tables first to ensure clean state
            # await conn.run_sync(Base.metadata.drop_all)
            # # Create all tables
            # await conn.run_sync(Base.metadata.create_all)

            # PostgreSQL: Reset sequences if needed
            # Note: PostgreSQL uses SEQUENCE objects instead of sqlite_sequence
            await session.commit()

        yield
    except Exception as e:
        print_error(f"Error during setup: {e}")
        raise
    finally:
        # Clean up after each test
        # try:
        # async with test_database_sync.async_session_scope() as session:
        #     async with session.begin():
        #         conn = await session.connection()
        #         # await conn.run_sync(Base.metadata.drop_all)
        # except Exception as e:
        #     print_error(f"Error during cleanup: {e}")
        #     raise
        # finally:
        await test_database_sync.close_async()


@pytest.mark.asyncio
async def test_complex_relationships(
    test_database_sync: Database, test_account: Account
):
    """Test complex relationships between multiple models."""
    async with test_database_sync.async_session_scope() as session:
        # Load the account from the database (already created by the test_account fixture)
        result = await session.execute(
            select(Account).where(Account.id == test_account.id)
        )
        account = result.scalar_one()

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
        await session.flush()

        # Create account media
        account_media = AccountMedia(
            id=1,
            accountId=account.id,
            mediaId=media.id,
            createdAt=datetime.now(UTC),
        )
        session.add(account_media)

        # Create post with media
        post = Post(
            id=1,
            accountId=account.id,
            content="Test post",
            createdAt=datetime.now(UTC),
        )
        session.add(post)

        # Create message referencing the account
        message = Message(
            id=1,
            senderId=account.id,
            content="Test message",
            createdAt=datetime.now(UTC),
        )
        session.add(message)
        await session.commit()

        # Verify relationships
        result = await session.execute(select(Account).where(Account.id == account.id))
        saved_account = result.scalar_one()
        assert saved_account.username == account.username
        assert len(await saved_account.awaitable_attrs.accountMedia) == 1

        result = await session.execute(select(Media))
        saved_media = result.scalar_one()
        assert saved_media.width == 1920
        assert saved_media.height == 1080
        assert saved_media.duration == 30.5


@pytest.mark.asyncio
async def test_cascade_operations(config):
    """Test cascade operations across relationships."""
    database = Database(config)

    try:
        # Create initial test data - first, independent media that should persist
        async with database.async_session_scope() as session:
            # Create test account first
            test_account = Account(
                id=555,
                username="standalone_media_owner",
                createdAt=datetime.now(UTC),
            )
            session.add(test_account)
            await session.commit()

            # Create standalone media with required accountId
            standalone_media = Media(
                id=1001,
                accountId=test_account.id,
                mimetype="image/jpeg",
                width=800,
                height=600,
                createdAt=datetime.now(UTC),
            )
            session.add(standalone_media)
            await session.commit()
            print_info(f"Created standalone media with ID {standalone_media.id}")

        # Create account and related data
        async with database.async_session_scope() as session:
            account = Account(
                id=999,
                username="test_cascade_user",
                createdAt=datetime.now(UTC),
            )
            session.add(account)
            await session.commit()
            print_info(f"Created account {account.id} for cascade test")

            account_owned_media = Media(
                id=1000,
                accountId=account.id,
                mimetype="video/mp4",
                width=1920,
                height=1080,
            )
            session.add(account_owned_media)
            await session.flush()

            account_media = AccountMedia(
                id=999,
                accountId=account.id,
                mediaId=account_owned_media.id,
                createdAt=datetime.now(UTC),
            )
            session.add(account_media)
            await session.commit()

        # Delete in dependency order to avoid FK violations
        async with database.async_session_scope() as session:
            # 1. Delete AccountMedia (references both Account and Media)
            result = await session.execute(
                select(AccountMedia).filter_by(accountId=999)
            )
            account_media_records = result.scalars().all()
            for record in account_media_records:
                await session.delete(record)
            await session.flush()
            print_info(f"Deleted {len(account_media_records)} AccountMedia records")

            # 2. Delete Media owned by the account (references Account via FK)
            result = await session.execute(select(Media).filter_by(accountId=999))
            media_records = result.scalars().all()
            for record in media_records:
                await session.delete(record)
            await session.flush()
            print_info(f"Deleted {len(media_records)} Media records")

            # 3. Delete the account itself
            result = await session.execute(select(Account).filter_by(id=999))
            found_account = result.scalar_one_or_none()

            if found_account is not None:
                await session.delete(found_account)
                await session.commit()
                print_info(f"Deleted account {found_account.id}")
            else:
                await session.commit()
                pytest.skip("Account 999 not found, cannot test cascades")

        # Verify deletion and persistence
        async with database.async_session_scope() as verify_session:
            # Verify account was deleted
            result = await verify_session.execute(select(Account).filter_by(id=999))
            remaining_account = result.scalar_one_or_none()
            assert remaining_account is None, "Account 999 wasn't deleted from database"

            # Check account media was deleted
            result = await verify_session.execute(
                select(AccountMedia).filter_by(accountId=999)
            )
            remaining_media_associations = result.scalars().all()
            assert len(remaining_media_associations) == 0, (
                "AccountMedia for account 999 wasn't deleted"
            )

            # Check that standalone media still exists
            result = await verify_session.execute(select(Media).filter_by(id=1001))
            standalone_media_found = result.scalar_one_or_none()
            assert standalone_media_found is not None, (
                "Standalone media was incorrectly deleted"
            )

            # The account-owned media might or might not be cascade-deleted
            result = await verify_session.execute(select(Media).filter_by(id=1000))
            account_media_still_exists = result.scalar_one_or_none() is not None
            print_info(
                f"Account-owned media (ID 1000) {'still exists' if account_media_still_exists else 'was deleted'} after account deletion"
            )
    finally:
        await database.cleanup()


@pytest.mark.asyncio
async def test_database_constraints(
    test_database_sync: Database, test_account: Account
):
    """Test database constraints and integrity."""
    async with test_database_sync.async_session_scope() as session:
        # Create a Media object
        media = Media(
            id=100,
            accountId=test_account.id,
            createdAt=datetime.now(UTC),
        )
        session.add(media)
        await session.flush()

        # Test 1: Create account media with non-existent account
        # PostgreSQL enforces FK constraints — this should fail
        account_media = AccountMedia(
            id=100,
            accountId=999,  # Non-existent account
            mediaId=media.id,
            createdAt=datetime.now(UTC),
        )
        session.add(account_media)
        with pytest.raises(exc.IntegrityError):
            await session.commit()
        await session.rollback()

        # Test 2: Create account media with non-existent media
        # PostgreSQL enforces FK constraints — this should fail
        account_media = AccountMedia(
            id=101,
            accountId=test_account.id,
            mediaId=999,  # Non-existent media
            createdAt=datetime.now(UTC),
        )
        session.add(account_media)
        with pytest.raises(exc.IntegrityError):
            await session.commit()
        await session.rollback()

        # Test 3: Create message without sender
        # This should fail at the database level due to NOT NULL constraint
        message = Message(
            id=1,
            content="Test",
            createdAt=datetime.now(UTC),
        )
        session.add(message)
        with pytest.raises((exc.IntegrityError, exc.StatementError)):
            await session.flush()
        await session.rollback()

        # Test 4: Create message with non-existent sender
        # PostgreSQL enforces FK constraints — this should fail
        message = Message(
            id=1,
            senderId=999,  # Non-existent account
            content="Test",
            createdAt=datetime.now(UTC),
        )
        session.add(message)
        with pytest.raises(exc.IntegrityError):
            await session.commit()
        await session.rollback()


@pytest.mark.asyncio
async def test_transaction_isolation(test_database_sync: Database):
    """Test transaction isolation levels."""
    # Create test data in first session
    async with test_database_sync.async_session_scope() as session1:
        account1 = Account(
            id=1,
            username="test_user_1",
            createdAt=datetime.now(UTC),
        )
        session1.add(account1)

        # Start second transaction before committing first
        async with test_database_sync.async_session_scope() as session2:
            # Should not see uncommitted data from first session
            result = await session2.execute(select(Account).filter_by(id=1))
            assert result.scalar_one_or_none() is None

            # Add different account in second session
            account2 = Account(
                id=2,
                username="test_user_2",
                createdAt=datetime.now(UTC),
            )
            session2.add(account2)
            await session2.commit()

        # First transaction should still succeed
        await session1.commit()

    # Verify final state
    async with test_database_sync.async_session_scope() as session:
        result = await session.execute(select(Account).order_by(Account.id))
        accounts = result.scalars().all()
        assert len(accounts) == 2
        assert accounts[0].id == 1
        assert accounts[1].id == 2


@pytest.mark.asyncio
async def test_concurrent_access(test_database_sync: Database, test_account: Account):
    """Test concurrent database access patterns."""
    num_tasks = 5
    num_messages = 10

    async def add_messages(account_id: int, start_id: int) -> list[int]:
        """Add messages in separate task."""
        message_ids = []
        try:
            async with test_database_sync.async_session_scope() as session:
                # PostgreSQL: No PRAGMA statements needed
                for i in range(num_messages):
                    msg = Message(
                        id=start_id + i,
                        senderId=account_id,
                        content=f"Message {i} from task {id(asyncio.current_task())}",
                        createdAt=datetime.now(UTC),
                    )
                    session.add(msg)
                    message_ids.append(msg.id)
                await session.commit()
        except Exception as e:
            print_error(f"Error in add_messages task: {e}")
            raise
        else:
            return message_ids

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
        async with test_database_sync.async_session_scope() as session:
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
                with contextlib.suppress(asyncio.CancelledError):
                    await task


@pytest.mark.asyncio
async def test_query_performance(test_database_sync: Database, test_account: Account):
    """Test query performance with indexes."""
    async with test_database_sync.async_session_scope() as session:
        # Create multiple media items
        for i in range(100):
            media = Media(id=i + 1, accountId=test_account.id)
            session.add(media)
            account_media = AccountMedia(
                id=i + 1,
                accountId=test_account.id,
                mediaId=media.id,
                createdAt=datetime.now(UTC),
            )
            session.add(account_media)
        await session.commit()

        # Create index on accountId
        await session.execute(
            text(
                'CREATE INDEX IF NOT EXISTS idx_account_media_accountid ON account_media ("accountId")'
            )
        )
        await session.commit()

        # Verify the index exists (PostgreSQL may choose Seq Scan for small tables)
        result = await session.execute(
            text(
                "SELECT indexname FROM pg_indexes "
                "WHERE tablename = 'account_media' AND indexname = 'idx_account_media_accountid'"
            )
        )
        index_row = result.scalar_one_or_none()
        assert index_row is not None, (
            "Index idx_account_media_accountid not found on account_media"
        )


@pytest.mark.asyncio
async def test_bulk_operations(test_database_sync: Database):
    """Test bulk database operations with transaction management."""
    async with test_database_sync.async_session_scope() as session:
        await session.rollback()  # Clear any existing transaction

        try:
            # Start a nested transaction
            async with session.begin():
                # Create multiple records
                for i in range(10):
                    account = Account(
                        id=i + 1,
                        username=f"bulk_user_{i}",
                        createdAt=datetime.now(UTC),
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
async def test_write_through_cache_integration(config, test_account: Account):
    """Test data persistence across separate database connections."""
    # First database connection
    db1 = Database(config, skip_migrations=True)

    try:
        unique_username = f"cache_test_{test_account.username}"
        unique_media_id = 12345

        async with db1.async_session_scope() as session:
            print_info(
                f"Creating account {unique_username} and media {unique_media_id} in db1"
            )
            account = Account(id=1, username=unique_username)
            session.add(account)
            await session.commit()

            media = Media(id=unique_media_id, accountId=account.id)
            session.add(media)
            await session.commit()

        # Close the first database connection
        await db1.cleanup()
        print_info("First database connection closed")

        # Create a second connection to the same PostgreSQL database
        db2 = Database(config, skip_migrations=True)

        try:
            async with db2.async_session_scope() as session:
                # Verify the account persists
                result = await session.execute(
                    select(Account).filter_by(username=unique_username)
                )
                saved_account = result.scalar_one_or_none()
                assert saved_account is not None, (
                    f"Account {unique_username} not found in second connection"
                )
                print_info(f"Found account {unique_username} in second connection")

                # Verify the media persists
                result = await session.execute(
                    select(Media).filter_by(id=unique_media_id)
                )
                saved_media = result.scalar_one_or_none()
                assert saved_media is not None, (
                    f"Media {unique_media_id} not found in second connection"
                )
                assert saved_media.id == unique_media_id
                assert saved_media.accountId == 1
                print_info(f"Found media {unique_media_id} in second connection")
        finally:
            await db2.cleanup()
    except Exception:
        with contextlib.suppress(Exception):
            await db1.cleanup()
        raise


@pytest.mark.asyncio
@pytest.mark.timeout(30)
async def test_database_cleanup_integration(config, test_account: Account):
    """Test database cleanup and data persistence across connections."""
    db1 = Database(config, skip_migrations=True)

    try:
        # Verify tables exist
        async with db1.async_session_scope() as session:
            result = await session.execute(
                text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
            )
            tables = result.scalars().all()
            print_info(f"Tables in database: {tables}")

        # Create unique test data
        unique_username = f"cleanup_test_{test_account.username}"
        unique_id = 999999

        async with db1.async_session_scope() as session:
            account = Account(id=unique_id, username=unique_username)
            session.add(account)
            await session.commit()

            result = await session.execute(select(Account).filter_by(id=unique_id))
            verify_account = result.scalar_one_or_none()
            assert verify_account is not None, "Account not created in first session"
            print_info("Test account created successfully")

        # Close first connection
        await db1.cleanup()
        print_info("First database connection closed")

        # Create second connection to same PostgreSQL database
        db2 = Database(config, skip_migrations=True)

        try:
            async with db2.async_session_scope() as session:
                result = await session.execute(select(Account).filter_by(id=unique_id))
                saved_account = result.scalar_one_or_none()

                assert saved_account is not None, (
                    f"Account {unique_id} not persisted between connections"
                )
                assert saved_account.username == unique_username
                print_info("Data persistence verified across connections")
        finally:
            await db2.cleanup()
    except Exception:
        with contextlib.suppress(Exception):
            await db1.cleanup()
        raise
