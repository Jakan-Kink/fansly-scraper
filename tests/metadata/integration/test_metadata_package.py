"""Integration tests for the metadata package."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from api.fansly import FanslyApi
from metadata import (
    Account,
    AccountMedia,
    Media,
    Post,
    TimelineStats,
    Wall,
    process_account_data,
    process_media_info,
)
from tests.fixtures.utils.test_isolation import snowflake_id


@pytest.mark.integration
@pytest.mark.asyncio
async def test_full_content_processing(entity_store, mock_config, timeline_data):
    """Test processing a complete set of content."""
    # Convert string IDs to int (mimics what API layer does)
    response = FanslyApi.convert_ids_to_int(timeline_data["response"])

    account_data = response["accounts"][0]

    # Process account data via production code
    await process_account_data(mock_config, account_data)

    # Verify account was created
    account_id = account_data["id"]
    account = await entity_store.get(Account, account_id)
    assert account is not None
    assert account.username == account_data["username"]

    # Verify timeline stats
    if "timelineStats" in account_data:
        stats = await entity_store.get(TimelineStats, account_id)
        assert stats is not None
        assert stats.imageCount == account_data["timelineStats"]["imageCount"]
        assert stats.videoCount == account_data["timelineStats"]["videoCount"]

    # Process media
    if "accountMedia" in response:
        for media_data in response["accountMedia"]:
            await process_media_info(mock_config, media_data)

    # Verify media was created — check at least one exists
    if response.get("accountMedia"):
        first_am = response["accountMedia"][0]
        if "media" in first_am:
            media_id = first_am["media"]["id"]
            media = await entity_store.get(Media, media_id)
            assert media is not None

        # Verify AccountMedia was created
        am = await entity_store.get(AccountMedia, first_am["id"])
        assert am is not None


@pytest.mark.integration
@pytest.mark.asyncio
async def test_relationship_integrity(entity_store, mock_config):
    """Test integrity of relationships between models."""
    # Create accounts with media, posts, and walls
    for i in range(2):
        account_id = snowflake_id()
        account = Account(id=account_id, username=f"integrity_user_{i}")
        await entity_store.save(account)

        # Create media for this account
        for _j in range(3):
            media_id = snowflake_id()
            media = Media(id=media_id, accountId=account_id, mimetype="video/mp4")
            await entity_store.save(media)

            am = AccountMedia(
                id=snowflake_id(),
                accountId=account_id,
                mediaId=media_id,
                createdAt=datetime.now(UTC),
            )
            await entity_store.save(am)

        # Create posts
        for j in range(2):
            post = Post(
                id=snowflake_id(),
                accountId=account_id,
                content=f"Post {j} for account {account_id}",
                createdAt=datetime.now(UTC),
            )
            await entity_store.save(post)

        # Create walls
        for j in range(2):
            wall = Wall(
                id=snowflake_id(),
                accountId=account_id,
                name=f"Wall {j}",
                pos=j,
            )
            await entity_store.save(wall)

    # Verify via entity_store queries
    accounts = await entity_store.find(Account)
    assert len(accounts) == 2

    for account in accounts:
        # Check media
        media_list = await entity_store.find(Media, accountId=account.id)
        assert len(media_list) == 3

        # Check posts
        posts = await entity_store.find(Post, accountId=account.id)
        assert len(posts) == 2

        # Check walls
        walls = await entity_store.find(Wall, accountId=account.id)
        assert len(walls) == 2


@pytest.mark.integration
@pytest.mark.asyncio
async def test_database_constraints(entity_store):
    """Test that Pydantic models enforce required fields."""
    # Media requires accountId
    with pytest.raises(ValidationError, match="accountId"):
        Media(id=snowflake_id())

    # Wall requires accountId
    with pytest.raises(ValidationError, match="accountId"):
        Wall(id=snowflake_id(), name="Test")

    # Message requires senderId
    from metadata import Message

    with pytest.raises(ValidationError, match="senderId"):
        Message(id=snowflake_id(), content="Test", createdAt=datetime.now(UTC))


@pytest.mark.integration
@pytest.mark.asyncio
async def test_database_indexes(entity_store):
    """Test that important indexes exist (PostgreSQL)."""
    # Create test data to query against
    account_id = snowflake_id()
    account = Account(id=account_id, username="index_test_user")
    await entity_store.save(account)

    wall = Wall(id=snowflake_id(), accountId=account_id, name="Test Wall")
    await entity_store.save(wall)

    # Use raw SQL via the store's pool to check EXPLAIN plans
    pool = entity_store.pool
    async with pool.acquire() as conn:
        # Check username index
        plan = await conn.fetch(
            "EXPLAIN SELECT * FROM accounts WHERE username = 'index_test_user'"
        )
        plan_str = "\n".join(str(row) for row in plan)
        assert "Index" in plan_str, (
            f"Query not using index for accounts.username. Plan: {plan_str}"
        )

        # Check foreign key index on walls.accountId
        plan = await conn.fetch(
            f'EXPLAIN SELECT * FROM walls WHERE "accountId" = {account_id}'
        )
        plan_str = "\n".join(str(row) for row in plan)
        assert "Index" in plan_str, (
            f"Query not using index for walls.accountId. Plan: {plan_str}"
        )
