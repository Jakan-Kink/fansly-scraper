"""Integration tests for pinned posts FK violation fix (#51).

Verifies that process_account_data creates stub Post rows for pinned
post targets that don't exist yet, preventing ForeignKeyViolationError
on the pinned_posts junction table.
"""

import pytest

from metadata import (
    Account,
    Post,
    process_account_data,
)
from metadata.models import PinnedPost
from metadata.stub_tracker import get_stubs, is_stub
from tests.fixtures.utils.test_isolation import snowflake_id


def _make_account_data_with_pinned_posts(
    account_id: int,
    pinned_post_ids: list[int],
) -> dict:
    """Build minimal account API data with pinnedPosts.

    Mirrors the real Fansly API response shape for account data.
    """
    return {
        "id": account_id,
        "username": f"test_user_{account_id}",
        "pinnedPosts": [
            {
                "postId": str(pid),
                "accountId": str(account_id),
                "pos": i,
                "createdAt": 1721111467000 + i,
            }
            for i, pid in enumerate(pinned_post_ids)
        ],
    }


@pytest.mark.asyncio
async def test_pinned_posts_creates_stubs_for_missing_posts(entity_store, mock_config):
    """Pinned post targets that don't exist yet get stub Post rows."""
    account_id = snowflake_id()
    post_ids = [snowflake_id() for _ in range(3)]

    data = _make_account_data_with_pinned_posts(account_id, post_ids)
    await process_account_data(mock_config, data)

    # All three Posts should exist as stubs
    for pid in post_ids:
        post = await entity_store.get(Post, pid)
        assert post is not None, f"Stub Post {pid} was not created"
        assert post.accountId == account_id

    # Stubs should be registered in stub_tracker
    tracked = await get_stubs("posts")
    for pid in post_ids:
        assert pid in tracked, f"Post {pid} not in stub_tracker"


@pytest.mark.asyncio
async def test_pinned_posts_skips_existing_posts(entity_store, mock_config):
    """Posts that already exist should not be overwritten or re-stubbed."""
    account_id = snowflake_id()
    existing_post_id = snowflake_id()
    missing_post_id = snowflake_id()

    # Pre-create the account and one post with real content
    account = Account(id=account_id, username="pre_existing")
    await entity_store.save(account)
    real_post = Post(id=existing_post_id, accountId=account_id, content="real")
    await entity_store.save(real_post)

    data = _make_account_data_with_pinned_posts(
        account_id, [existing_post_id, missing_post_id]
    )
    await process_account_data(mock_config, data)

    # Existing post should retain its content (not overwritten by stub)
    post = await entity_store.get(Post, existing_post_id)
    assert post.content == "real"

    # Only the missing post should be a stub
    assert not await is_stub("posts", existing_post_id)
    assert await is_stub("posts", missing_post_id)


@pytest.mark.asyncio
async def test_pinned_posts_junction_rows_created(entity_store, mock_config):
    """The pinned_posts junction table should be populated after stubs."""
    account_id = snowflake_id()
    post_ids = [snowflake_id() for _ in range(2)]

    data = _make_account_data_with_pinned_posts(account_id, post_ids)
    await process_account_data(mock_config, data)

    # Verify junction rows exist in DB

    rows = await entity_store.find_records(PinnedPost, accountId=account_id)
    junction_post_ids = {r["postId"] for r in rows}
    assert junction_post_ids == set(post_ids)


@pytest.mark.asyncio
async def test_account_without_pinned_posts_works(entity_store, mock_config):
    """Accounts without pinnedPosts should save normally (no regression)."""
    account_id = snowflake_id()
    data = {"id": account_id, "username": "no_pins"}

    await process_account_data(mock_config, data)

    account = await entity_store.get(Account, account_id)
    assert account is not None
    assert account.username == "no_pins"


@pytest.mark.asyncio
async def test_pinned_posts_idempotent_on_reprocess(entity_store, mock_config):
    """Processing the same account data twice should not duplicate stubs."""
    account_id = snowflake_id()
    post_ids = [snowflake_id() for _ in range(2)]

    data = _make_account_data_with_pinned_posts(account_id, post_ids)

    # Process twice
    await process_account_data(mock_config, data)
    await process_account_data(mock_config, data)

    # Should still have exactly the same junction rows

    rows = await entity_store.find_records(PinnedPost, accountId=account_id)
    junction_post_ids = {r["postId"] for r in rows}
    assert junction_post_ids == set(post_ids)

    # Each post should exist exactly once
    for pid in post_ids:
        post = await entity_store.get(Post, pid)
        assert post is not None
