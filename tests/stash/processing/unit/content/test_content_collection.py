"""Tests for collecting media from attachments and processing items with gallery.

This test module uses real database fixtures and factories instead of mocks
to provide more reliable integration testing while maintaining test isolation.
"""

from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from metadata import Account, AccountMedia
from metadata.attachment import Attachment, ContentType
from metadata.post import Post
from tests.fixtures import (
    AccountFactory,
    AccountMediaFactory,
    AttachmentFactory,
    MediaFactory,
    PostFactory,
)


@pytest.mark.asyncio
async def test_collect_media_from_attachments_empty(content_mixin):
    """Test _collect_media_from_attachments with empty attachments."""
    attachments = []
    result = await content_mixin._collect_media_from_attachments(attachments)
    assert result == []


@pytest.mark.asyncio
async def test_collect_media_from_attachments_no_media(
    factory_async_session, session, content_mixin
):
    """Test _collect_media_from_attachments with attachments that have no media."""
    # Create attachments with contentType but contentId pointing to non-existent media
    # This simulates attachments that don't have associated media loaded
    attachment1 = AttachmentFactory(
        id=60001,
        contentType=ContentType.ACCOUNT_MEDIA,
        contentId=99999,  # Non-existent AccountMedia
        pos=0,
    )
    attachment2 = AttachmentFactory(
        id=60002,
        contentType=ContentType.ACCOUNT_MEDIA,
        contentId=99998,  # Non-existent AccountMedia
        pos=1,
    )

    # Query fresh from async session
    result = await session.execute(
        select(Attachment).where(Attachment.id.in_([60001, 60002]))
    )
    attachments = list(result.scalars().all())

    result = await content_mixin._collect_media_from_attachments(attachments)
    assert result == []


@pytest.mark.asyncio
async def test_collect_media_from_attachments_with_media(
    factory_async_session, session, content_mixin
):
    """Test _collect_media_from_attachments with attachments that have media."""
    # Create account with factory
    account = AccountFactory(
        id=12345,
        username="test_user",
        displayName="Test User",
    )

    # Create media objects using factory
    media1 = MediaFactory(
        id=123,
        accountId=12345,
        mimetype="image/jpeg",
        location="https://example.com/media_123.jpg",
        width=800,
        height=600,
    )
    media2 = MediaFactory(
        id=456,
        accountId=12345,
        mimetype="video/mp4",
        location="https://example.com/media_456.mp4",
        width=1280,
        height=720,
    )

    # Create AccountMedia to link attachments to media using factory
    account_media1 = AccountMediaFactory(id=123, accountId=12345, mediaId=123)
    account_media2 = AccountMediaFactory(id=456, accountId=12345, mediaId=456)

    # Create attachments with media - use contentId not accountMediaId
    attachment1 = AttachmentFactory(
        id=60003,
        contentType=ContentType.ACCOUNT_MEDIA,
        contentId=123,  # Points to AccountMedia id
        pos=0,
    )
    attachment2 = AttachmentFactory(
        id=60004,
        contentType=ContentType.ACCOUNT_MEDIA,
        contentId=456,  # Points to AccountMedia id
        pos=1,
    )

    # Query fresh attachments from async session with eager loading
    result = await session.execute(
        select(Attachment)
        .where(Attachment.id.in_([60003, 60004]))
        .options(selectinload(Attachment.media).selectinload(AccountMedia.media))
    )
    attachments = list(result.scalars().all())

    result = await content_mixin._collect_media_from_attachments(attachments)

    # Verify we got media objects back
    assert len(result) == 2
    media_ids = [m.id for m in result]
    assert 123 in media_ids
    assert 456 in media_ids


@pytest.mark.asyncio
async def test_process_items_with_gallery_empty(
    factory_async_session, session, content_mixin, mock_performer, mock_studio
):
    """Test _process_items_with_gallery with empty items list."""
    # Create a real account using factory
    account = AccountFactory(
        id=12345,
        username="test_user",
        displayName="Test User",
    )

    # Query fresh from async session
    result = await session.execute(select(Account).where(Account.id == 12345))
    account = result.scalar_one()

    await content_mixin._process_items_with_gallery(
        account=account,
        performer=mock_performer,
        studio=mock_studio,
        item_type="post",
        items=[],
        url_pattern_func=lambda x: f"https://example.com/post/{x.id}",
        session=session,
    )

    # Verify _process_item_gallery was not called (mocked method)
    assert content_mixin._process_item_gallery.call_count == 0


@pytest.mark.asyncio
async def test_process_items_with_gallery_no_attachments(
    factory_async_session, session, content_mixin, mock_performer, mock_studio
):
    """Test _process_items_with_gallery with items that have no attachments."""
    # Create a real account with factory
    account = AccountFactory(
        id=12345,
        username="test_user",
        displayName="Test User",
    )

    # Create a real post using factory with no attachments
    post = PostFactory(
        id=123,
        accountId=12345,
        content="Test content",
        createdAt=datetime(2024, 5, 1, 12, 0, 0, tzinfo=UTC),
    )

    # Query fresh from async session
    result = await session.execute(select(Account).where(Account.id == 12345))
    account = result.scalar_one()

    result = await session.execute(select(Post).where(Post.id == 123))
    post = result.unique().scalar_one()

    await content_mixin._process_items_with_gallery(
        account=account,
        performer=mock_performer,
        studio=mock_studio,
        item_type="post",
        items=[post],
        url_pattern_func=lambda x: f"https://example.com/post/{x.id}",
        session=session,
    )

    # Verify _process_item_gallery was called with the item (mocked method)
    content_mixin._process_item_gallery.assert_called_once()
    call_args = content_mixin._process_item_gallery.call_args
    assert call_args[1]["item"].id == post.id
    assert call_args[1]["account"].id == account.id
    assert call_args[1]["performer"] == mock_performer
    assert call_args[1]["studio"] == mock_studio
    assert call_args[1]["item_type"] == "post"
    assert call_args[1]["url_pattern"] == f"https://example.com/post/{post.id}"
    assert call_args[1]["session"] == session


@pytest.mark.asyncio
async def test_process_items_with_gallery_with_multiple_items(
    factory_async_session, session, content_mixin, mock_performer, mock_studio
):
    """Test _process_items_with_gallery with multiple items."""
    # Create a real account with factory
    account = AccountFactory(
        id=12345,
        username="test_user",
        displayName="Test User",
    )

    # Create multiple real posts using factory
    post1 = PostFactory(
        id=123,
        accountId=12345,
        content="Test post 1",
        createdAt=datetime(2024, 5, 1, 12, 0, 0, tzinfo=UTC),
    )
    post2 = PostFactory(
        id=456,
        accountId=12345,
        content="Test post 2",
        createdAt=datetime(2024, 5, 2, 12, 0, 0, tzinfo=UTC),
    )

    # Query fresh from async session
    result = await session.execute(select(Account).where(Account.id == 12345))
    account = result.scalar_one()

    result = await session.execute(
        select(Post).where(Post.id.in_([123, 456])).order_by(Post.id)
    )
    posts = list(result.unique().scalars().all())
    post1, post2 = posts[0], posts[1]

    await content_mixin._process_items_with_gallery(
        account=account,
        performer=mock_performer,
        studio=mock_studio,
        item_type="post",
        items=[post1, post2],
        url_pattern_func=lambda x: f"https://example.com/post/{x.id}",
        session=session,
    )

    # Verify _process_item_gallery was called twice, once for each item
    assert content_mixin._process_item_gallery.call_count == 2
    calls = content_mixin._process_item_gallery.call_args_list
    assert calls[0][1]["item"].id == post1.id
    assert calls[1][1]["item"].id == post2.id


@pytest.mark.asyncio
async def test_process_creator_posts_no_posts(
    factory_async_session, session, content_mixin, mock_performer, mock_studio
):
    """Test process_creator_posts with no posts."""
    # Create a real account with factory
    account = AccountFactory(
        id=12345,
        username="test_user",
        displayName="Test User",
    )

    # Query fresh from async session
    result = await session.execute(select(Account).where(Account.id == 12345))
    account = result.scalar_one()

    # Mock the worker pool methods that are already mocked in content_mixin
    await content_mixin.process_creator_posts(
        account=account,
        performer=mock_performer,
        studio=mock_studio,
        session=session,
    )

    # Verify session operations
    # The account should be in the session
    stmt = select(Account).where(Account.id == account.id)
    result = await session.execute(stmt)
    found_account = result.scalar_one_or_none()
    assert found_account is not None
    assert found_account.id == account.id

    # Verify worker pool setup was called
    content_mixin._setup_worker_pool.assert_called_once()
    setup_call_args = content_mixin._setup_worker_pool.call_args
    # Check that "post" is in the arguments (should be in the first positional arg)
    assert "post" in str(setup_call_args)

    # Verify worker pool execution with empty items
    content_mixin._run_worker_pool.assert_called_once()
    batch_args = content_mixin._run_worker_pool.call_args[1]
    assert batch_args["items"] == []


@pytest.mark.asyncio
async def test_process_creator_messages_no_messages(
    factory_async_session, session, content_mixin, mock_performer, mock_studio
):
    """Test process_creator_messages with no messages."""
    # Create a real account with factory
    account = AccountFactory(
        id=12345,
        username="test_user",
        displayName="Test User",
    )

    # Query fresh from async session
    result = await session.execute(select(Account).where(Account.id == 12345))
    account = result.scalar_one()

    # Mock the worker pool methods that are already mocked in content_mixin
    await content_mixin.process_creator_messages(
        account=account,
        performer=mock_performer,
        studio=mock_studio,
        session=session,
    )

    # Verify session operations
    # The account should be in the session
    stmt = select(Account).where(Account.id == account.id)
    result = await session.execute(stmt)
    found_account = result.scalar_one_or_none()
    assert found_account is not None
    assert found_account.id == account.id

    # Verify worker pool setup was called
    content_mixin._setup_worker_pool.assert_called_once()
    setup_call_args = content_mixin._setup_worker_pool.call_args
    # Check that "message" is in the arguments
    assert "message" in str(setup_call_args)

    # Verify worker pool execution with empty items
    content_mixin._run_worker_pool.assert_called_once()
    batch_args = content_mixin._run_worker_pool.call_args[1]
    assert batch_args["items"] == []
