"""Integration tests for the metadata package."""

from datetime import datetime, timezone

import pytest
from sqlalchemy import select, text

from metadata import (
    Account,
    AccountMedia,
    Media,
    Message,
    TimelineStats,
    Wall,
    process_account_data,
    process_media_info,
)
from tests.metadata.helpers.utils import (
    create_test_data_set,
    verify_relationship_integrity,
)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_full_content_processing(test_database, config, timeline_data):
    """Test processing a complete set of content."""
    # Process account data from timeline
    account_data = timeline_data["response"]["accounts"][0]
    async with test_database.async_session_scope() as session:
        await process_account_data(config, account_data, session=session)

        # Process media
        if "accountMedia" in timeline_data["response"]:
            for media_data in timeline_data["response"]["accountMedia"]:
                await process_media_info(config, media_data, session=session)

    # Verify data through database queries
    async with test_database.async_session_scope() as session:
        # Check account
        result = await session.execute(text("SELECT * FROM accounts"))
        account = result.fetchone()
        assert account is not None
        assert account.username == account_data["username"]

        # Check timeline stats
        if "timelineStats" in account_data:
            result = await session.execute(
                text("SELECT * FROM timeline_stats WHERE accountId = :account_id"),
                {"account_id": account.id},
            )
            stats = result.fetchone()
            assert stats is not None
            assert stats.imageCount == account_data["timelineStats"].get(
                "imageCount", 0
            )
            assert stats.videoCount == account_data["timelineStats"].get(
                "videoCount", 0
            )
            assert isinstance(stats, TimelineStats)

        # Check media
        result = await session.execute(text("SELECT COUNT(*) FROM media"))
        media_count = result.scalar()
        assert media_count > 0

        # Check account media
        result = await session.execute(text("SELECT COUNT(*) FROM account_media"))
        account_media_count = result.scalar()
        assert account_media_count > 0


@pytest.mark.integration
@pytest.mark.asyncio
async def test_relationship_integrity(test_database):
    """Test integrity of relationships between models."""

    async with test_database.async_session_scope() as session:
        try:
            print("\nCreating test data set...")
            data = create_test_data_set(
                session,
                num_accounts=2,
                num_media_per_account=3,
                num_posts_per_account=2,
                num_walls_per_account=2,
            )
            await session.commit()
            print(f"Created {len(data['accounts'])} accounts")
            print(f"Created {len(data['media'])} media items")
            print(f"Created {len(data['account_media'])} account_media associations")

            # Verify relationships for each account
            for account in data["accounts"]:
                print(f"\nVerifying relationships for account {account.id}")
                # Test 1: Account -> AccountMedia relationship using ORM
                account_media = await session.scalars(
                    select(AccountMedia).filter_by(accountId=account.id)
                )
                assert len(list(account_media)) == 3

                # Verify relationship count using async-safe method
                integrity_check = await verify_relationship_integrity(
                    session, account, "accountMedia", expected_count=3
                )
                assert integrity_check, "Relationship integrity check failed"

                # Test 2: AccountMedia -> Media relationship using ORM
                for account_media in account_media:
                    media = account_media.media
                    assert media is not None, (
                        f"Media {account_media.mediaId} not found for AccountMedia {account_media.id}"
                    )
                    assert media.accountId == account.id, (
                        f"Media {media.id} has wrong accountId {media.accountId}, expected {account.id}"
                    )

                # Test 3: Account -> Walls relationship (existing walls) using ORM
                existing_walls = await session.scalars(
                    select(Wall).filter_by(accountId=account.id)
                )
                assert len(list(existing_walls)) == 2, (
                    f"Expected 2 walls for account {account.id}, found {len(list(existing_walls))}"
                )
                assert await verify_relationship_integrity(
                    session, account, "walls", expected_count=2
                )

                # Test 4: Wall -> Posts relationship using ORM
                for wall in existing_walls:
                    account_posts = wall.posts
                    assert len(account_posts) == 2, (
                        f"Expected 2 posts for account {wall.accountId}, found {len(account_posts)}"
                    )

                    # Verify each post belongs to the correct account
                    for post in account_posts:
                        assert post.accountId == wall.accountId, (
                            f"Post {post.id} has wrong accountId {post.accountId}, expected {wall.accountId}"
                        )

            # Test 5: Verify no orphaned records
            # Count total records
            result = await session.execute(text("SELECT COUNT(*) FROM accounts"))
            total_accounts = result.scalar()
            result = await session.execute(text("SELECT COUNT(*) FROM media"))
            total_media = result.scalar()
            result = await session.execute(text("SELECT COUNT(*) FROM account_media"))
            total_account_media = result.scalar()
            result = await session.execute(text("SELECT COUNT(*) FROM walls"))
            total_walls = result.scalar()
            result = await session.execute(text("SELECT COUNT(*) FROM posts"))
            total_posts = result.scalar()

            # Verify expected counts
            assert total_accounts == 2  # num_accounts
            assert total_media == 6  # num_accounts * num_media_per_account
            assert total_account_media == 6  # Same as media count
            assert total_walls == 4  # num_accounts * num_walls_per_account
            assert total_posts == 4  # num_accounts * num_posts_per_account

        finally:
            # Cleanup in reverse order of dependencies
            await session.execute(text("DELETE FROM posts"))
            await session.execute(text("DELETE FROM walls"))
            await session.execute(text("DELETE FROM account_media"))
            await session.execute(text("DELETE FROM media"))
            await session.execute(text("DELETE FROM accounts"))
            await session.commit()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_database_constraints(test_database):
    """Test database constraints and referential integrity."""
    async with test_database.async_session_scope() as session:
        # Try to create media without account (should fail)
        media = Media(id=1)  # Missing required accountId
        session.add(media)
        with pytest.raises(Exception):
            await session.commit()
        await session.rollback()

        # Try to create wall without account (should fail)
        wall = Wall(id=1, name="Test")  # Missing required accountId
        session.add(wall)
        with pytest.raises(Exception):
            await session.commit()
        await session.rollback()

        # Try to create message without sender (should fail)
        message = Message(
            id=1, content="Test", createdAt=datetime.now(timezone.utc)
        )  # Missing required senderId
        session.add(message)
        with pytest.raises(Exception):
            await session.commit()
        await session.rollback()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_database_indexes(test_database):
    """Test that important queries use indexes."""
    async with test_database.async_session_scope() as session:
        # Create test account
        account = Account(id=1, username="test_user")
        session.add(account)
        await session.commit()

        # Check username index
        result = await session.execute(
            text(
                "EXPLAIN QUERY PLAN SELECT * FROM accounts WHERE username = 'test_user'"
            )
        )
        plan = result.fetchall()
        assert any("USING INDEX" in str(row) for row in plan), (
            "Query not using index for accounts.username"
        )

        # Check foreign key indexes
        result = await session.execute(
            text("EXPLAIN QUERY PLAN SELECT * FROM walls WHERE accountId = 1")
        )
        plan = result.fetchall()
        assert any("USING INDEX" in str(row) for row in plan), (
            "Query not using index for walls.accountId"
        )
