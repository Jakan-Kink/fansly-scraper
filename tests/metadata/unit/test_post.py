"""Unit tests for post metadata functionality."""

from datetime import UTC, datetime
from unittest.mock import patch

import pytest

from metadata import Account, Attachment, ContentType, Post, process_pinned_posts
from metadata.models import PostMention
from tests.fixtures.utils.test_isolation import snowflake_id


@pytest.mark.asyncio
async def test_post_model_basic(entity_store):
    """Test basic Post model functionality."""
    store = entity_store

    account_id = snowflake_id()
    post_id = snowflake_id()

    account = Account(id=account_id, username="test_user")
    await store.save(account)

    post = Post(
        id=post_id,
        accountId=account_id,
        content="Test post content",
        fypFlag=0,
    )
    await store.save(post)

    saved = await store.get(Post, post_id)
    assert saved is not None
    assert saved.content == "Test post content"
    assert saved.accountId == account_id


@pytest.mark.asyncio
async def test_post_with_attachments(entity_store):
    """Test Post with attachments relationship.

    Attachments are saved via _sync_associations when the Post
    has the attachments list populated before save.
    """
    store = entity_store

    account_id = snowflake_id()
    post_id = snowflake_id()

    account = Account(id=account_id, username="test_user")
    await store.save(account)

    # Create post first (FK requirement for attachments)
    post = Post(id=post_id, accountId=account_id, content="Post with attachments")
    await store.save(post)

    # Add attachments and re-save (triggers _sync_associations)
    post.attachments = [
        Attachment(
            id=snowflake_id(),
            postId=post_id,
            contentId=snowflake_id(),
            contentType=ContentType.ACCOUNT_MEDIA,
            pos=i,
        )
        for i in range(3)
    ]
    await store.save(post)

    saved = await store.get(Post, post_id)
    assert saved is not None
    assert len(saved.attachments) == 3
    assert sorted([a.pos for a in saved.attachments]) == [0, 1, 2]


@pytest.mark.asyncio
async def test_post_mentions(entity_store):
    """Test post mentions via PostMention entities.

    Mentions are a reverse_fk relationship: Post.mentions → PostMention.postId.
    """
    store = entity_store

    account_id = snowflake_id()
    post_id = snowflake_id()

    account = Account(id=account_id, username="test_user")
    await store.save(account)

    post = Post(id=post_id, accountId=account_id, content="Post with mentions")
    await store.save(post)

    # Add mention and re-save
    mention = PostMention(postId=post_id, accountId=account_id, handle="test_handle")
    post.mentions = [mention]
    await store.save(post)

    saved = await store.get(Post, post_id)
    assert saved is not None
    assert len(saved.mentions) == 1
    assert saved.mentions[0].handle == "test_handle"


@pytest.mark.asyncio
async def test_process_pinned_posts(entity_store, config):
    """Test processing pinned posts.

    process_pinned_posts uses get_store() internally, wired via entity_store.
    """
    store = entity_store

    account_id = snowflake_id()
    post_id = snowflake_id()

    account = Account(id=account_id, username="test_user")
    await store.save(account)

    post = Post(id=post_id, accountId=account_id, content="Test pinned post")
    await store.save(post)

    pinned_data = [
        {
            "postId": post_id,
            "pos": 0,
            "createdAt": int(datetime.now(UTC).timestamp() * 1000),
        }
    ]

    await process_pinned_posts(config, account, pinned_data)

    # Verify via raw query on pinned_posts junction
    pool = await store._get_pool()
    row = await pool.fetchrow(
        'SELECT * FROM pinned_posts WHERE "postId" = $1 AND "accountId" = $2',
        post_id,
        account_id,
    )
    assert row is not None
    assert row["pos"] == 0


@pytest.mark.asyncio
async def test_process_pinned_posts_nonexistent(entity_store, config):
    """Test processing pinned posts with nonexistent post.

    Should log a warning and skip the post.
    """
    store = entity_store

    account_id = snowflake_id()
    nonexistent_post_id = snowflake_id()

    account = Account(id=account_id, username="test_user")
    await store.save(account)

    with patch("metadata.post.json_output") as mock_json_output:
        pinned_data = [
            {
                "postId": nonexistent_post_id,  # Nonexistent post
                "pos": 0,
                "createdAt": int(datetime.now(UTC).timestamp() * 1000),
            }
        ]

        await process_pinned_posts(config, account, pinned_data)

        mock_json_output.assert_any_call(
            1,
            "meta/post - p_p_p - skipping_missing_post",
            {"postId": nonexistent_post_id, "accountId": account_id},
        )


@pytest.mark.asyncio
async def test_process_pinned_posts_update(entity_store, config):
    """Test updating existing pinned post position.

    sync_junction does DELETE + re-INSERT, so updating pos should work.
    """
    store = entity_store

    account_id = snowflake_id()
    post_id = snowflake_id()

    account = Account(id=account_id, username="test_user")
    await store.save(account)

    post = Post(id=post_id, accountId=account_id, content="Test pinned post")
    await store.save(post)

    # Initial pinned post
    initial_data = [
        {
            "postId": post_id,
            "pos": 0,
            "createdAt": int(datetime.now(UTC).timestamp() * 1000),
        }
    ]
    await process_pinned_posts(config, account, initial_data)

    # Update with new position
    updated_data = [
        {
            "postId": post_id,
            "pos": 1,
            "createdAt": int(datetime.now(UTC).timestamp() * 1000),
        }
    ]
    await process_pinned_posts(config, account, updated_data)

    # Verify update
    pool = await store._get_pool()
    row = await pool.fetchrow(
        'SELECT * FROM pinned_posts WHERE "postId" = $1 AND "accountId" = $2',
        post_id,
        account_id,
    )
    assert row is not None
    assert row["pos"] == 1


@pytest.mark.asyncio
async def test_post_reply_fields(entity_store):
    """Test post reply-related fields."""
    store = entity_store

    account_id = snowflake_id()
    parent_id = snowflake_id()
    reply_id = snowflake_id()

    account = Account(id=account_id, username="test_user")
    await store.save(account)

    # Create parent post
    parent = Post(id=parent_id, accountId=account_id, content="Parent post")
    await store.save(parent)

    # Create reply post
    reply = Post(
        id=reply_id,
        accountId=account_id,
        content="Reply post",
        inReplyTo=parent_id,
        inReplyToRoot=parent_id,
    )
    await store.save(reply)

    saved = await store.get(Post, reply_id)
    assert saved is not None
    assert saved.inReplyTo == parent_id
    assert saved.inReplyToRoot == parent_id


@pytest.mark.parametrize(
    "expires_at",
    [
        datetime.now(UTC),
        None,
    ],
)
@pytest.mark.asyncio
async def test_post_expiration(entity_store, expires_at):
    """Test post expiration field."""
    store = entity_store

    account_id = snowflake_id()
    post_id = snowflake_id()

    account = Account(id=account_id, username="test_user")
    await store.save(account)

    post = Post(
        id=post_id, accountId=account_id, content="Test post", expiresAt=expires_at
    )
    await store.save(post)

    saved = await store.get(Post, post_id)
    assert saved is not None
    if expires_at is not None:
        assert saved.expiresAt is not None
    else:
        assert saved.expiresAt is None


@pytest.mark.asyncio
async def test_post_cascade_delete(entity_store):
    """Test deleting a post also removes associated data.

    EntityStore delete is a simple DELETE by PK. Cascade is handled
    by PostgreSQL ON DELETE CASCADE on the FK constraints.
    """
    store = entity_store

    account_id = snowflake_id()
    post_id = snowflake_id()
    att_id = snowflake_id()
    content_id = snowflake_id()

    account = Account(id=account_id, username="test_user")
    await store.save(account)

    post = Post(id=post_id, accountId=account_id, content="Test post")
    await store.save(post)

    # Add attachment
    post.attachments = [
        Attachment(
            id=att_id,
            postId=post_id,
            contentId=content_id,
            contentType=ContentType.ACCOUNT_MEDIA,
            pos=0,
        )
    ]
    await store.save(post)

    # Add mention
    mention = PostMention(postId=post_id, accountId=account_id, handle="test_handle")
    post.mentions = [mention]
    await store.save(post)

    # Delete the post
    await store.delete(post)

    # Verify post is gone
    assert await store.get(Post, post_id) is None

    # Verify cascade removed attachments (FK ON DELETE CASCADE)
    pool = await store._get_pool()
    att_row = await pool.fetchrow(
        'SELECT * FROM attachments WHERE "postId" = $1', post_id
    )
    assert att_row is None

    mention_row = await pool.fetchrow(
        'SELECT * FROM post_mentions WHERE "postId" = $1', post_id
    )
    assert mention_row is None
