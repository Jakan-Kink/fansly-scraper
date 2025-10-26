"""Unit tests for post metadata functionality."""

from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from metadata.attachment import Attachment, ContentType
from metadata.post import Post, pinned_posts, post_mentions, process_pinned_posts
from tests.fixtures import AccountFactory, AttachmentFactory, PostFactory


@pytest.mark.asyncio
async def test_post_model_basic(session: AsyncSession, factory_session):
    """Test basic Post model functionality.

    Uses AccountFactory and PostFactory.
    factory_session configures factories with the database session.
    """
    # Create account and post using factories
    account = AccountFactory(id=1, username="test_user")
    account_id = account.id
    post = PostFactory(
        id=1,
        accountId=account_id,
        content="Test post content",
        fypFlag=0,
    )
    session.expire_all()

    # Query and verify
    result = await session.execute(select(Post).where(Post.id == 1))
    queried_post = result.unique().scalar_one_or_none()
    assert queried_post is not None
    assert queried_post.content == "Test post content"
    assert queried_post.accountId == account_id


@pytest.mark.asyncio
async def test_post_with_attachments(session: AsyncSession, factory_session):
    """Test Post with attachments relationship.

    Uses AccountFactory and PostFactory.
    factory_session configures factories with the database session.
    """
    # Create account and post using factories
    account = AccountFactory(id=1, username="test_user")
    account_id = account.id
    post = PostFactory(
        id=1,
        accountId=account_id,
        content="Post with attachments",
    )
    post_id = post.id

    for i in range(3):
        AttachmentFactory(
            id=i,
            postId=post_id,
            contentId=i + 1000,
            contentType=ContentType.ACCOUNT_MEDIA,
            pos=i,
        )
    factory_session.commit()

    # Verify attachments - expire sessions to ensure fresh data
    session.expire_all()
    result = await session.execute(
        select(Post).options(selectinload(Post.attachments)).where(Post.id == post_id)
    )
    queried_post = result.unique().scalar_one_or_none()
    assert queried_post is not None
    assert len(queried_post.attachments) == 3
    assert all(isinstance(a, Attachment) for a in queried_post.attachments)
    assert sorted([a.pos for a in queried_post.attachments]) == [0, 1, 2]


@pytest.mark.asyncio
async def test_post_mentions(session: AsyncSession, factory_session):
    """Test post mentions relationship.

    Uses AccountFactory and PostFactory.
    factory_session configures factories with the database session.
    """
    # Create account and post using factories
    account = AccountFactory(id=1, username="test_user")
    account_id = account.id
    post = PostFactory(
        id=1,
        accountId=account_id,
        content="Post with mentions",
    )
    post_id = post.id
    session.expire_all()

    # Add mention
    await session.execute(
        post_mentions.insert().values(
            postId=post_id,
            accountId=account_id,
            handle="test_handle",
        )
    )
    await session.commit()

    # Verify mention
    session.expire_all()
    result = await session.execute(
        select(Post)
        .options(selectinload(Post.accountMentions))
        .where(Post.id == post_id)
        .execution_options(populate_existing=True)
    )
    queried_post = result.unique().scalar_one_or_none()
    # Refresh object to ensure accountMentions are loaded in a greenlet context
    await session.run_sync(
        lambda s: s.refresh(queried_post, attribute_names=["accountMentions"])
    )
    assert queried_post is not None
    assert len(queried_post.accountMentions) == 1
    assert queried_post.accountMentions[0].id == account_id


@pytest.mark.asyncio
async def test_process_pinned_posts(session: AsyncSession, factory_session, config):
    """Test processing pinned posts.

    Uses AccountFactory and PostFactory.
    factory_session is autouse=True so it's automatically applied.
    """
    # Create account and post using factories
    account = AccountFactory(id=1, username="test_user")
    account_id = account.id
    post = PostFactory(
        id=1,
        accountId=account_id,
        content="Test pinned post",
    )
    session.expire_all()

    # Query account in async session
    from metadata.account import Account

    result = await session.execute(select(Account).where(Account.id == account_id))
    account = result.scalar_one()

    # Test data for pinned posts
    pinned_data = [
        {
            "postId": 1,
            "pos": 0,
            "createdAt": int(datetime.now(timezone.utc).timestamp() * 1000),
        }
    ]

    # Process pinned posts
    await process_pinned_posts(config, account, pinned_data, session=session)

    # Verify pinned post
    session.expire_all()
    result = await session.execute(
        select(pinned_posts).where(
            pinned_posts.c.postId == 1,
            pinned_posts.c.accountId == account_id,
        )
    )
    result_row = result.mappings().first()
    assert result_row is not None
    assert result_row["pos"] == 0


@pytest.mark.asyncio
async def test_process_pinned_posts_nonexistent(
    session: AsyncSession, factory_session, config
):
    """Test processing pinned posts with nonexistent post.

    Uses AccountFactory.
    factory_session is autouse=True so it's automatically applied.
    """
    # Create account using factory
    account = AccountFactory(id=1, username="test_user")
    account_id = account.id
    session.expire_all()

    # Query account in async session
    from metadata.account import Account

    result = await session.execute(select(Account).where(Account.id == account_id))
    account = result.scalar_one()

    with patch("metadata.post.json_output") as mock_json_output:
        pinned_data = [
            {
                "postId": 999,  # Nonexistent post
                "pos": 0,
                "createdAt": int(datetime.now(timezone.utc).timestamp() * 1000),
            }
        ]

        await process_pinned_posts(config, account, pinned_data, session=session)

        # Verify logging
        mock_json_output.assert_any_call(
            1,
            "meta/post - p_p_p - skipping_missing_post",
            {
                "postId": 999,
                "accountId": account_id,
                "reason": "Post does not exist in database",
            },
        )


@pytest.mark.asyncio
async def test_process_pinned_posts_update(
    session: AsyncSession, factory_session, config
):
    """Test updating existing pinned post.

    Uses AccountFactory and PostFactory.
    factory_session is autouse=True so it's automatically applied.
    """
    # Create account and post using factories
    account = AccountFactory(id=1, username="test_user")
    account_id = account.id
    post = PostFactory(
        id=1,
        accountId=account_id,
        content="Test pinned post",
    )
    session.expire_all()

    # Query account in async session
    from metadata.account import Account

    result = await session.execute(select(Account).where(Account.id == account_id))
    account = result.scalar_one()

    # Initial pinned post data
    initial_data = [
        {
            "postId": 1,
            "pos": 0,
            "createdAt": int(datetime.now(timezone.utc).timestamp() * 1000),
        }
    ]
    await process_pinned_posts(config, account, initial_data, session=session)

    # Update with new position
    updated_data = [
        {
            "postId": 1,
            "pos": 1,  # Changed position
            "createdAt": int(datetime.now(timezone.utc).timestamp() * 1000),
        }
    ]
    await process_pinned_posts(config, account, updated_data, session=session)

    # Verify update
    session.expire_all()
    result = await session.execute(
        select(pinned_posts).where(
            pinned_posts.c.postId == 1,
            pinned_posts.c.accountId == account_id,
        )
    )
    result_row = result.mappings().first()
    assert result_row is not None
    assert result_row["pos"] == 1


@pytest.mark.asyncio
async def test_post_reply_fields(session: AsyncSession, factory_session):
    """Test post reply-related fields.

    Uses AccountFactory and PostFactory.
    factory_session is autouse=True so it's automatically applied.
    """
    # Create account and posts using factories
    account = AccountFactory(id=1, username="test_user")
    account_id = account.id

    # Create parent post
    parent_post = PostFactory(
        id=1,
        accountId=account_id,
        content="Parent post",
    )
    parent_id = parent_post.id

    # Create reply post
    reply_post = PostFactory(
        id=2,
        accountId=account_id,
        content="Reply post",
        inReplyTo=parent_id,
        inReplyToRoot=parent_id,
    )
    session.expire_all()

    # Verify reply relationships
    result = await session.execute(select(Post).where(Post.id == 2))
    queried_reply = result.unique().scalar_one_or_none()
    assert queried_reply is not None
    assert queried_reply.inReplyTo == parent_id
    assert queried_reply.inReplyToRoot == parent_id


@pytest.mark.parametrize(
    "expires_at",
    [
        datetime.now(timezone.utc),  # With expiration
        None,  # Without expiration
    ],
)
@pytest.mark.asyncio
async def test_post_expiration(session: AsyncSession, factory_session, expires_at):
    """Test post expiration field.

    Uses AccountFactory and PostFactory.
    factory_session is autouse=True so it's automatically applied.
    """
    # Create account and post using factories
    account = AccountFactory(id=1, username="test_user")
    account_id = account.id
    post = PostFactory(
        id=1,
        accountId=account_id,
        content="Test post",
        expiresAt=expires_at,
    )
    session.expire_all()

    result = await session.execute(select(Post).where(Post.id == 1))
    queried_post = result.unique().scalar_one_or_none()
    # Compare timestamps in UTC
    assert queried_post is not None
    if expires_at is not None:
        assert queried_post.expiresAt is not None
        assert queried_post.expiresAt.replace(tzinfo=timezone.utc) == expires_at
    else:
        assert queried_post.expiresAt is None


@pytest.mark.asyncio
async def test_post_cascade_delete(session: AsyncSession, factory_session):
    """Test cascade deletion of post relationships.

    Uses AccountFactory, PostFactory, and AttachmentFactory.
    factory_session is autouse=True so it's automatically applied.
    """
    # Create account and post using factories
    account = AccountFactory(id=1, username="test_user")
    account_id = account.id
    post = PostFactory(
        id=1,
        accountId=account_id,
        content="Test post",
    )
    post_id = post.id

    AttachmentFactory(
        id=1,
        postId=post_id,
        contentId=1001,
        contentType=ContentType.ACCOUNT_MEDIA,
        pos=0,
    )
    factory_session.commit()
    session.expire_all()

    # Add mention in async session
    await session.execute(
        post_mentions.insert().values(
            postId=post_id,
            accountId=account_id,
            handle="test_handle",
        )
    )
    await session.commit()

    # Query and delete post in async session
    result = await session.execute(select(Post).where(Post.id == post_id))
    post = result.unique().scalar_one()
    await session.delete(post)
    await session.commit()

    # Verify cascade deletion
    session.expire_all()
    result = await session.execute(select(Post).where(Post.id == post_id))
    assert result.unique().scalar_one_or_none() is None

    result = await session.execute(
        select(Attachment).where(Attachment.postId == post_id)
    )
    assert result.unique().scalar_one_or_none() is None

    result = await session.execute(
        select(post_mentions).where(post_mentions.c.postId == post_id)
    )
    assert result.unique().first() is None
