"""Tests for collecting media from attachments and processing items with gallery."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from metadata import Media
from metadata.account import AccountMedia
from metadata.attachment import Attachment, ContentType
from tests.stash.processing.unit.media_mixin.async_mock_helper import (
    AccessibleAsyncMock,
    make_asyncmock_awaitable,
)


@pytest.mark.asyncio
async def test_collect_media_from_attachments_empty(mixin, mock_session):
    """Test _collect_media_from_attachments with empty attachments."""
    attachments = []
    result = await mixin._collect_media_from_attachments(attachments, mock_session)
    assert result == {}
    assert mock_session.execute.call_count == 0


@pytest.mark.asyncio
async def test_collect_media_from_attachments_no_media(mixin, mock_session):
    """Test _collect_media_from_attachments with attachments that have no media."""
    # Create attachments with no media
    attachment1 = AccessibleAsyncMock()
    attachment1._awaitable_attrs = MagicMock()
    attachment1._awaitable_attrs.media = AsyncMock(return_value=None)
    make_asyncmock_awaitable(attachment1._awaitable_attrs.media)

    attachment2 = AccessibleAsyncMock()
    attachment2._awaitable_attrs = MagicMock()
    attachment2._awaitable_attrs.media = AsyncMock(return_value=None)
    make_asyncmock_awaitable(attachment2._awaitable_attrs.media)

    attachments = [attachment1, attachment2]

    result = await mixin._collect_media_from_attachments(attachments, mock_session)
    assert result == {}
    assert mock_session.execute.call_count == 0


@pytest.mark.asyncio
async def test_collect_media_from_attachments_with_media(mixin, mock_session):
    """Test _collect_media_from_attachments with attachments that have media."""
    # Create media object
    media1 = AccessibleAsyncMock()
    media1.id = "media_123"
    media1.media = AccessibleAsyncMock()
    media1.media.id = "media_123"
    media1.media.is_audible = False
    media1.media.is_downloadable = True
    media1.media.is_playable = False
    media1.media.is_visible = True
    media1.media.mime = "image/jpeg"
    media1.media.src = "https://example.com/media_123.jpg"
    media1.media.width = 800
    media1.media.height = 600

    media2 = AccessibleAsyncMock()
    media2.id = "media_456"
    media2.media = AccessibleAsyncMock()
    media2.media.id = "media_456"
    media2.media.is_audible = True
    media2.media.is_downloadable = True
    media2.media.is_playable = True
    media2.media.is_visible = True
    media2.media.mime = "video/mp4"
    media2.media.src = "https://example.com/media_456.mp4"
    media2.media.width = 1280
    media2.media.height = 720

    # Create attachments with media
    attachment1 = AccessibleAsyncMock()
    attachment1._awaitable_attrs = MagicMock()
    attachment1._awaitable_attrs.media = AsyncMock(return_value=media1)
    make_asyncmock_awaitable(attachment1._awaitable_attrs.media)

    attachment2 = AccessibleAsyncMock()
    attachment2._awaitable_attrs = MagicMock()
    attachment2._awaitable_attrs.media = AsyncMock(return_value=media2)
    make_asyncmock_awaitable(attachment2._awaitable_attrs.media)

    # Mock database media record lookup
    db_media1 = MagicMock(spec=Media)
    db_media1.id = "media_123"
    db_media2 = MagicMock(spec=Media)
    db_media2.id = "media_456"

    mock_result = AccessibleAsyncMock()
    mock_result.scalars = MagicMock()
    mock_result.scalars.return_value = AccessibleAsyncMock()
    mock_result.scalars.return_value.all = AsyncMock(
        return_value=[db_media1, db_media2]
    )
    make_asyncmock_awaitable(mock_result.scalars.return_value.all)

    mock_session.execute.return_value = mock_result
    make_asyncmock_awaitable(mock_session.execute)

    attachments = [attachment1, attachment2]
    result = await mixin._collect_media_from_attachments(attachments, mock_session)

    assert len(result) == 2
    assert result["media_123"] == db_media1
    assert result["media_456"] == db_media2
    assert mock_session.execute.call_count == 1


@pytest.mark.asyncio
async def test_process_items_with_gallery_empty(
    mixin, mock_session, sample_account, mock_performer, mock_studio
):
    """Test _process_items_with_gallery with empty items list."""
    await mixin._process_items_with_gallery(
        account=sample_account,
        performer=mock_performer,
        studio=mock_studio,
        item_type="post",
        items=[],
        url_pattern_func=lambda x: f"https://example.com/post/{x.id}",
        session=mock_session,
    )

    # Verify _process_item_gallery was not called
    assert mixin._process_item_gallery.call_count == 0


@pytest.mark.asyncio
async def test_process_items_with_gallery_no_attachments(
    mixin, mock_session, sample_account, mock_performer, mock_studio
):
    """Test _process_items_with_gallery with items that have no attachments."""
    # Create item with no attachments
    post = AccessibleAsyncMock()
    post.id = "post_123"
    post.title = "Test Post"
    post.content = "Test content"
    post.created_at = "2024-05-01T12:00:00Z"
    post._awaitable_attrs = MagicMock()
    post._awaitable_attrs.attachments = AsyncMock(return_value=[])
    make_asyncmock_awaitable(post._awaitable_attrs.attachments)
    post._awaitable_attrs.hashtags = AsyncMock(return_value=[])
    make_asyncmock_awaitable(post._awaitable_attrs.hashtags)
    post._awaitable_attrs.accountMentions = AsyncMock(return_value=[])
    make_asyncmock_awaitable(post._awaitable_attrs.accountMentions)

    await mixin._process_items_with_gallery(
        account=sample_account,
        performer=mock_performer,
        studio=mock_studio,
        item_type="post",
        items=[post],
        url_pattern_func=lambda x: f"https://example.com/post/{x.id}",
        session=mock_session,
    )

    # Verify _process_item_gallery was called with the item
    mixin._process_item_gallery.assert_called_once()
    call_args = mixin._process_item_gallery.call_args
    assert call_args[1]["item"] == post
    assert call_args[1]["account"] == sample_account
    assert call_args[1]["performer"] == mock_performer
    assert call_args[1]["studio"] == mock_studio
    assert call_args[1]["item_type"] == "post"
    assert call_args[1]["url_pattern"] == f"https://example.com/post/{post.id}"
    assert call_args[1]["session"] == mock_session


@pytest.mark.asyncio
async def test_process_items_with_gallery_with_multiple_items(
    mixin, mock_session, sample_account, mock_performer, mock_studio
):
    """Test _process_items_with_gallery with multiple items."""
    # Create items with no attachments
    post1 = AccessibleAsyncMock()
    post1.id = "post_123"
    post1._awaitable_attrs = MagicMock()
    post1._awaitable_attrs.attachments = AsyncMock(return_value=[])
    make_asyncmock_awaitable(post1._awaitable_attrs.attachments)
    post1._awaitable_attrs.hashtags = AsyncMock(return_value=[])
    make_asyncmock_awaitable(post1._awaitable_attrs.hashtags)
    post1._awaitable_attrs.accountMentions = AsyncMock(return_value=[])
    make_asyncmock_awaitable(post1._awaitable_attrs.accountMentions)

    post2 = AccessibleAsyncMock()
    post2.id = "post_456"
    post2._awaitable_attrs = MagicMock()
    post2._awaitable_attrs.attachments = AsyncMock(return_value=[])
    make_asyncmock_awaitable(post2._awaitable_attrs.attachments)
    post2._awaitable_attrs.hashtags = AsyncMock(return_value=[])
    make_asyncmock_awaitable(post2._awaitable_attrs.hashtags)
    post2._awaitable_attrs.accountMentions = AsyncMock(return_value=[])
    make_asyncmock_awaitable(post2._awaitable_attrs.accountMentions)

    await mixin._process_items_with_gallery(
        account=sample_account,
        performer=mock_performer,
        studio=mock_studio,
        item_type="post",
        items=[post1, post2],
        url_pattern_func=lambda x: f"https://example.com/post/{x.id}",
        session=mock_session,
    )

    # Verify _process_item_gallery was called twice, once for each item
    assert mixin._process_item_gallery.call_count == 2
    calls = mixin._process_item_gallery.call_args_list
    assert calls[0][1]["item"] == post1
    assert calls[1][1]["item"] == post2


@pytest.mark.asyncio
async def test_process_creator_posts_no_posts(
    mixin, mock_session, sample_account, mock_performer, mock_studio
):
    """Test process_creator_posts with no posts."""
    # Mock session.execute to return no posts
    mock_result = AccessibleAsyncMock()
    mock_result.scalar_one = AsyncMock(return_value=sample_account)
    make_asyncmock_awaitable(mock_result.scalar_one)

    mock_scalars_result = AccessibleAsyncMock()
    mock_scalars_result.all = AsyncMock(return_value=[])
    make_asyncmock_awaitable(mock_scalars_result.all)

    mock_unique_result = AccessibleAsyncMock()
    mock_unique_result.scalars = MagicMock(return_value=mock_scalars_result)
    mock_result.unique = MagicMock(return_value=mock_unique_result)

    mock_session.execute = AsyncMock(return_value=mock_result)
    make_asyncmock_awaitable(mock_session.execute)

    # Mock batch processing functions
    mixin._setup_batch_processing.return_value = (
        MagicMock(),  # task_pbar
        MagicMock(),  # process_pbar
        MagicMock(),  # semaphore
        MagicMock(),  # queue
    )

    await mixin.process_creator_posts(
        account=sample_account,
        performer=mock_performer,
        studio=mock_studio,
        session=mock_session,
    )

    # Verify session operations
    mock_session.add.assert_called_with(sample_account)
    mock_session.execute.assert_called()

    # Verify batch processing setup
    mixin._setup_batch_processing.assert_called_once()
    assert "post" in str(mixin._setup_batch_processing.call_args)

    # Verify batch processor execution with empty items
    mixin._run_batch_processor.assert_called_once()
    batch_args = mixin._run_batch_processor.call_args[1]
    assert batch_args["items"] == []


@pytest.mark.asyncio
async def test_process_creator_messages_no_messages(
    mixin, mock_session, sample_account, mock_performer, mock_studio
):
    """Test process_creator_messages with no messages."""
    # Mock session.execute to return no messages
    mock_result = AccessibleAsyncMock()
    mock_result.scalar_one = AsyncMock(return_value=sample_account)
    make_asyncmock_awaitable(mock_result.scalar_one)

    mock_scalars_result = AccessibleAsyncMock()
    mock_scalars_result.all = AsyncMock(return_value=[])
    make_asyncmock_awaitable(mock_scalars_result.all)

    mock_unique_result = AccessibleAsyncMock()
    mock_unique_result.scalars = MagicMock(return_value=mock_scalars_result)
    mock_result.unique = MagicMock(return_value=mock_unique_result)

    mock_session.execute = AsyncMock(return_value=mock_result)
    make_asyncmock_awaitable(mock_session.execute)

    # Mock batch processing functions
    mixin._setup_batch_processing.return_value = (
        MagicMock(),  # task_pbar
        MagicMock(),  # process_pbar
        MagicMock(),  # semaphore
        MagicMock(),  # queue
    )

    await mixin.process_creator_messages(
        account=sample_account,
        performer=mock_performer,
        studio=mock_studio,
        session=mock_session,
    )

    # Verify session operations
    mock_session.add.assert_called_with(sample_account)
    mock_session.execute.assert_called()

    # Verify batch processing setup
    mixin._setup_batch_processing.assert_called_once()
    assert "message" in str(mixin._setup_batch_processing.call_args)

    # Verify batch processor execution with empty items
    mixin._run_batch_processor.assert_called_once()
    batch_args = mixin._run_batch_processor.call_args[1]
    assert batch_args["items"] == []
