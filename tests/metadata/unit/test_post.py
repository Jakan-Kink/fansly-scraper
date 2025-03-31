"""Unit tests for post metadata functionality."""

from datetime import datetime, timezone
from unittest.mock import patch

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from metadata.account import Account
from metadata.attachment import Attachment, ContentType
from metadata.post import Post, pinned_posts, post_mentions, process_pinned_posts


@pytest_asyncio.fixture
async def test_account(session):
    """Create a test account."""
    account = Account(id=1, username="test_user")
    session.add(account)
    await session.commit()
    await session.refresh(account)
    return account


@pytest_asyncio.fixture
async def session(test_engine):
    """Create a test database session."""
    # Create session factory
    async_session_factory = async_sessionmaker(
        bind=test_engine,
        expire_on_commit=False,
        class_=AsyncSession,
    )

    # Create session
    async with async_session_factory() as session:
        # Configure session
        await session.execute(text("PRAGMA foreign_keys=OFF"))
        await session.execute(text("PRAGMA journal_mode=WAL"))
        yield session


@pytest.mark.asyncio
async def test_post_model_basic(session, test_account):
    """Test basic Post model functionality."""
    # Create a test post
    post = Post(
        id=1,
        accountId=test_account.id,
        content="Test post content",
        fypFlag=0,
        createdAt=datetime.now(timezone.utc),
    )
    session.add(post)
    await session.commit()

    # Query and verify
    result = await session.execute(select(Post).where(Post.id == 1))
    queried_post = result.unique().scalar_one_or_none()
    assert queried_post is not None
    assert queried_post.content == "Test post content"
    assert queried_post.accountId == test_account.id


@pytest.mark.asyncio
async def test_post_with_attachments(session, test_account):
    """Test Post with attachments relationship."""
    post = Post(
        id=1,
        accountId=test_account.id,
        content="Post with attachments",
        createdAt=datetime.now(timezone.utc),
    )
    session.add(post)

    # Add attachments
    attachments = [
        Attachment(
            id=i,
            postId=post.id,
            contentId=f"content_{i}",
            contentType=ContentType.ACCOUNT_MEDIA,
            pos=i,
        )
        for i in range(3)
    ]
    post.attachments.extend(attachments)
    await session.commit()

    # Verify attachments
    result = await session.execute(select(Post).where(Post.id == 1))
    queried_post = result.unique().scalar_one_or_none()
    assert len(queried_post.attachments) == 3
    assert all(isinstance(a, Attachment) for a in queried_post.attachments)
    assert [a.pos for a in queried_post.attachments] == [0, 1, 2]


@pytest.mark.asyncio
async def test_post_mentions(session, test_account):
    """Test post mentions relationship."""
    post = Post(
        id=1,
        accountId=test_account.id,
        content="Post with mentions",
        createdAt=datetime.now(timezone.utc),
    )
    session.add(post)
    await session.commit()

    # Add mention
    await session.execute(
        post_mentions.insert().values(
            postId=post.id,
            accountId=test_account.id,
            handle="test_handle",
        )
    )
    await session.commit()

    # Verify mention
    result = await session.execute(
        select(Post)
        .options(selectinload(Post.accountMentions))
        .where(Post.id == 1)
        .execution_options(populate_existing=True)
    )
    queried_post = result.unique().scalar_one_or_none()
    # Refresh object to ensure accountMentions are loaded in a greenlet context
    await session.run_sync(
        lambda s: s.refresh(queried_post, attribute_names=["accountMentions"])
    )
    assert len(queried_post.accountMentions) == 1
    assert queried_post.accountMentions[0].id == test_account.id


@pytest.mark.asyncio
async def test_process_pinned_posts(session, test_account, config):
    """Test processing pinned posts."""
    # Create a test post first
    post = Post(
        id=1,
        accountId=test_account.id,
        content="Test pinned post",
        createdAt=datetime.now(timezone.utc),
    )
    session.add(post)
    await session.commit()

    # Test data for pinned posts
    pinned_data = [
        {
            "postId": 1,
            "pos": 0,
            "createdAt": int(datetime.now(timezone.utc).timestamp() * 1000),
        }
    ]

    # Process pinned posts
    await process_pinned_posts(config, test_account, pinned_data, session=session)

    # Verify pinned post
    result = await session.execute(
        select(pinned_posts).where(
            pinned_posts.c.postId == 1,
            pinned_posts.c.accountId == test_account.id,
        )
    )
    result_row = result.mappings().first()  # Get the row as a mapping
    assert result_row is not None
    assert result_row["pos"] == 0


@pytest.mark.asyncio
async def test_process_pinned_posts_nonexistent(session, test_account, config):
    """Test processing pinned posts with nonexistent post."""
    with patch("metadata.post.json_output") as mock_json_output:
        pinned_data = [
            {
                "postId": 999,  # Nonexistent post
                "pos": 0,
                "createdAt": int(datetime.now(timezone.utc).timestamp() * 1000),
            }
        ]

        await process_pinned_posts(config, test_account, pinned_data, session=session)

        # Verify logging
        mock_json_output.assert_any_call(
            1,
            "meta/post - p_p_p - skipping_missing_post",
            {
                "postId": 999,
                "accountId": test_account.id,
                "reason": "Post does not exist in database",
            },
        )


@pytest.mark.asyncio
async def test_process_pinned_posts_update(session, test_account, config):
    """Test updating existing pinned post."""
    # Create a test post
    post = Post(
        id=1,
        accountId=test_account.id,
        content="Test pinned post",
        createdAt=datetime.now(timezone.utc),
    )
    session.add(post)
    await session.commit()

    # Initial pinned post data
    initial_data = [
        {
            "postId": 1,
            "pos": 0,
            "createdAt": int(datetime.now(timezone.utc).timestamp() * 1000),
        }
    ]
    await process_pinned_posts(config, test_account, initial_data, session=session)

    # Update with new position
    updated_data = [
        {
            "postId": 1,
            "pos": 1,  # Changed position
            "createdAt": int(datetime.now(timezone.utc).timestamp() * 1000),
        }
    ]
    await process_pinned_posts(config, test_account, updated_data, session=session)

    # Verify update - use mappings().first() to get a row as a dict/mapping
    result = await session.execute(
        select(pinned_posts).where(
            pinned_posts.c.postId == 1,
            pinned_posts.c.accountId == test_account.id,
        )
    )
    result_row = result.mappings().first()  # Get row as a mapping
    assert result_row is not None
    assert result_row["pos"] == 1  # Access by key rather than attribute


@pytest.mark.asyncio
async def test_post_reply_fields(session, test_account):
    """Test post reply-related fields."""
    # Create parent post
    parent_post = Post(
        id=1,
        accountId=test_account.id,
        content="Parent post",
        createdAt=datetime.now(timezone.utc),
    )
    session.add(parent_post)

    # Create reply post
    reply_post = Post(
        id=2,
        accountId=test_account.id,
        content="Reply post",
        inReplyTo=parent_post.id,
        inReplyToRoot=parent_post.id,
        createdAt=datetime.now(timezone.utc),
    )
    session.add(reply_post)
    await session.commit()

    # Verify reply relationships
    result = await session.execute(select(Post).where(Post.id == 2))
    queried_reply = result.unique().scalar_one_or_none()
    assert queried_reply.inReplyTo == parent_post.id
    assert queried_reply.inReplyToRoot == parent_post.id


@pytest.mark.parametrize(
    "expires_at",
    [
        datetime.now(timezone.utc),  # With expiration
        None,  # Without expiration
    ],
)
@pytest.mark.asyncio
async def test_post_expiration(session, test_account, expires_at):
    """Test post expiration field."""
    post = Post(
        id=1,
        accountId=test_account.id,
        content="Test post",
        createdAt=datetime.now(timezone.utc),
        expiresAt=expires_at,
    )
    session.add(post)
    await session.commit()

    result = await session.execute(select(Post).where(Post.id == 1))
    queried_post = result.unique().scalar_one_or_none()
    # Compare timestamps in UTC
    if expires_at is not None:
        assert queried_post.expiresAt.replace(tzinfo=timezone.utc) == expires_at
    else:
        assert queried_post.expiresAt is None


@pytest.mark.asyncio
async def test_post_cascade_delete(session, test_account):
    """Test cascade deletion of post relationships."""
    # Create post with attachments and mentions
    post = Post(
        id=1,
        accountId=test_account.id,
        content="Test post",
        createdAt=datetime.now(timezone.utc),
    )
    session.add(post)
    await session.flush()

    # Add attachment
    attachment = Attachment(
        id=1,
        postId=post.id,
        contentId="content_1",
        contentType=ContentType.ACCOUNT_MEDIA,
        pos=0,
    )
    session.add(attachment)
    await session.flush()

    # Add mention
    await session.execute(
        post_mentions.insert().values(
            postId=post.id,
            accountId=test_account.id,
            handle="test_handle",
        )
    )
    await session.commit()

    # Delete post
    await session.delete(post)
    await session.commit()

    # Verify cascade deletion
    result = await session.execute(select(Post).where(Post.id == 1))
    assert result.unique().scalar_one_or_none() is None

    result = await session.execute(select(Attachment).where(Attachment.postId == 1))
    assert result.unique().scalar_one_or_none() is None

    result = await session.execute(
        select(post_mentions).where(post_mentions.c.postId == 1)
    )
    assert result.unique().first() is None
