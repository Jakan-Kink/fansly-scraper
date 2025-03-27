"""Unit tests for metadata.attachment module."""

from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from metadata.account import Account
from metadata.attachment import Attachment, ContentType
from metadata.messages import Message
from metadata.post import Post


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


@pytest_asyncio.fixture
async def test_account(session):
    """Create a test account."""
    account = Account(id=1, username="test_user")
    session.add(account)
    await session.commit()
    await session.refresh(account)
    return account


@pytest.mark.asyncio
async def test_post_attachment_ordering(session, test_account):
    """Test that post attachments are ordered by position."""
    # Create post
    post = Post(
        id=1,
        accountId=test_account.id,
        content="Test post",
        createdAt=datetime.now(timezone.utc),
    )
    session.add(post)
    await session.flush()

    # Create attachments with different positions
    attachments = [
        Attachment(
            postId=1, contentId=i, pos=pos, contentType=ContentType.ACCOUNT_MEDIA
        )
        for i, pos in [(1, 3), (2, 1), (3, 2)]  # Out of order positions
    ]
    session.add_all(attachments)
    await session.commit()

    # Verify order
    result = await session.execute(select(Post))
    saved_post = result.unique().scalar_one_or_none()
    attachment_positions = [a.pos for a in saved_post.attachments]
    assert attachment_positions == [1, 2, 3]  # Should be ordered
    attachment_content_ids = [a.contentId for a in saved_post.attachments]
    assert attachment_content_ids == [2, 3, 1]  # Should match position order


@pytest.mark.asyncio
async def test_message_attachment_ordering(session, test_account):
    """Test that message attachments are ordered by position."""
    # Create message
    message = Message(
        id=1,
        senderId=test_account.id,
        content="Test message",
        createdAt=datetime.now(timezone.utc),
    )
    session.add(message)
    await session.commit()

    # Create attachments with different positions
    attachments = [
        Attachment(
            messageId=1, contentId=i, pos=pos, contentType=ContentType.ACCOUNT_MEDIA
        )
        for i, pos in [(1, 2), (2, 3), (3, 1)]  # Out of order positions
    ]
    session.add_all(attachments)
    await session.commit()

    # Verify order
    result = await session.execute(
        select(Message).options(selectinload(Message.attachments))
    )
    saved_message = result.unique().scalar_one_or_none()
    attachment_positions = [a.pos for a in saved_message.attachments]
    # Refresh the message to eagerly load attachments in a synchronous context
    await session.run_sync(
        lambda s: s.refresh(saved_message, attribute_names=["attachments"])
    )
    assert attachment_positions == [1, 2, 3]  # Should be ordered
    attachment_content_ids = [a.contentId for a in saved_message.attachments]
    assert attachment_content_ids == [3, 1, 2]  # Should match position order


@pytest.mark.asyncio
async def test_attachment_content_resolution(session, test_account):
    """Test resolving different types of attachment content."""
    # Create post with different types of attachments
    post = Post(
        id=1,
        accountId=test_account.id,
        content="Test post",
        createdAt=datetime.now(timezone.utc),
    )
    session.add(post)
    await session.flush()

    # Create attachments with different content types
    attachments = [
        Attachment(postId=1, contentId=1, pos=1, contentType=ContentType.ACCOUNT_MEDIA),
        Attachment(
            postId=1,
            contentId=2,
            pos=2,
            contentType=ContentType.ACCOUNT_MEDIA_BUNDLE,
        ),
        Attachment(postId=1, contentId=3, pos=3, contentType=ContentType.STORY),
    ]
    session.add_all(attachments)
    await session.commit()

    # Verify content type properties
    result = await session.execute(select(Post))
    saved_post = result.unique().scalar_one_or_none()
    assert saved_post.attachments[0].is_account_media is True
    assert saved_post.attachments[1].is_account_media_bundle is True
    assert saved_post.attachments[2].is_account_media is False
    assert saved_post.attachments[2].is_account_media_bundle is False


@pytest.mark.asyncio
async def test_attachment_exclusivity(session, test_account):
    """Test that attachments can't belong to both post and message."""
    attachment = Attachment(
        contentId=1,
        pos=1,
        contentType=ContentType.ACCOUNT_MEDIA,
        postId=1,
        messageId=1,  # This should violate the constraint
    )

    # Create post and message
    post = Post(id=1, accountId=test_account.id, createdAt=datetime.now(timezone.utc))
    message = Message(
        id=1,
        senderId=test_account.id,
        content="Test",
        createdAt=datetime.now(timezone.utc),
    )
    session.add_all([post, message])
    await session.flush()

    # Adding the attachment should fail
    with pytest.raises(Exception):  # Should raise due to check constraint
        session.add(attachment)
        await session.commit()
