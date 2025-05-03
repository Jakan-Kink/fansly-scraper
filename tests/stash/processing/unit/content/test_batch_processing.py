"""Tests for batch processing functionality in ContentProcessingMixin."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.stash.processing.unit.media_mixin.async_mock_helper import (
    AccessibleAsyncMock,
    make_asyncmock_awaitable,
)


@pytest.mark.asyncio
async def test_process_creator_posts_with_batch_processing(
    mixin, mock_session, sample_account, mock_performer, mock_studio
):
    """Test process_creator_posts with batch processing enabled."""
    # Enable batch processing
    mixin.use_batch_processing = True
    mixin._batch_processing_done = False

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

    # Mock batch processing result
    mock_batch_result = {"images": ["img1"], "scenes": ["scene1"]}
    mixin.process_account_media_by_mimetype = AsyncMock(return_value=mock_batch_result)
    make_asyncmock_awaitable(mixin.process_account_media_by_mimetype)

    await mixin.process_creator_posts(
        account=sample_account,
        performer=mock_performer,
        studio=mock_studio,
        session=mock_session,
    )

    # Verify batch processing was called
    mixin.process_account_media_by_mimetype.assert_called_once()
    assert mixin._batch_processing_done is True


@pytest.mark.asyncio
async def test_process_creator_messages_skip_batch_processing(
    mixin, mock_session, sample_account, mock_performer, mock_studio
):
    """Test process_creator_messages skips batch processing when already done."""
    # Enable batch processing but mark it as already done
    mixin.use_batch_processing = True
    mixin._batch_processing_done = True

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

    # Verify batch processing was not called since it was already done
    assert not hasattr(mixin, "process_account_media_by_mimetype")


@pytest.mark.asyncio
async def test_collect_media_from_attachments_with_aggregated_post(mixin, mock_session):
    """Test _collect_media_from_attachments with an aggregated post."""
    # Create nested media in aggregated post
    nested_media = AccessibleAsyncMock()
    nested_media.id = "nested_media_123"
    nested_media.media = AccessibleAsyncMock()
    nested_media.media.id = "nested_media_123"
    nested_media.media.mime = "image/jpeg"

    # Create nested attachment in aggregated post
    nested_attachment = AccessibleAsyncMock()
    nested_attachment._awaitable_attrs = MagicMock()
    nested_attachment._awaitable_attrs.media = AsyncMock(return_value=nested_media)
    make_asyncmock_awaitable(nested_attachment._awaitable_attrs.media)

    # Create aggregated post
    agg_post = AccessibleAsyncMock()
    agg_post._awaitable_attrs = MagicMock()
    agg_post._awaitable_attrs.attachments = AsyncMock(return_value=[nested_attachment])
    make_asyncmock_awaitable(agg_post._awaitable_attrs.attachments)
    agg_post.attachments = [nested_attachment]

    # Create main attachment with aggregated post
    main_attachment = AccessibleAsyncMock()
    main_attachment.is_aggregated_post = True
    main_attachment._awaitable_attrs = MagicMock()
    main_attachment._awaitable_attrs.aggregated_post = AsyncMock(return_value=agg_post)
    main_attachment._awaitable_attrs.media = AsyncMock(return_value=None)
    make_asyncmock_awaitable(main_attachment._awaitable_attrs.aggregated_post)
    make_asyncmock_awaitable(main_attachment._awaitable_attrs.media)

    # Call the method
    result = await mixin._collect_media_from_attachments([main_attachment])

    # Verify nested media was found
    assert len(result) == 1
    assert result[0].id == "nested_media_123"


@pytest.mark.asyncio
async def test_collect_media_from_attachments_with_bundle(mixin, mock_session):
    """Test _collect_media_from_attachments with a media bundle."""
    # Create bundle media
    bundle_media1 = AccessibleAsyncMock()
    bundle_media1.id = "bundle_media_123"
    bundle_media1.media = AccessibleAsyncMock()
    bundle_media1.media.id = "bundle_media_123"
    bundle_media1.media.mime = "image/jpeg"
    bundle_media1.preview = AccessibleAsyncMock()
    bundle_media1.preview.id = "preview_123"
    bundle_media1.preview.mime = "image/jpeg"

    bundle_media2 = AccessibleAsyncMock()
    bundle_media2.id = "bundle_media_456"
    bundle_media2.media = AccessibleAsyncMock()
    bundle_media2.media.id = "bundle_media_456"
    bundle_media2.media.mime = "video/mp4"
    bundle_media2.preview = None

    # Create bundle
    bundle = AccessibleAsyncMock()
    bundle._awaitable_attrs = MagicMock()
    bundle._awaitable_attrs.accountMedia = AsyncMock(
        return_value=[bundle_media1, bundle_media2]
    )
    bundle._awaitable_attrs.preview = AsyncMock(return_value=None)
    make_asyncmock_awaitable(bundle._awaitable_attrs.accountMedia)
    make_asyncmock_awaitable(bundle._awaitable_attrs.preview)
    bundle.accountMedia = [bundle_media1, bundle_media2]
    bundle.preview = None

    # Create attachment with bundle
    attachment = AccessibleAsyncMock()
    attachment._awaitable_attrs = MagicMock()
    attachment._awaitable_attrs.media = AsyncMock(return_value=None)
    attachment._awaitable_attrs.bundle = AsyncMock(return_value=bundle)
    make_asyncmock_awaitable(attachment._awaitable_attrs.media)
    make_asyncmock_awaitable(attachment._awaitable_attrs.bundle)
    attachment.bundle = bundle

    # Call the method
    result = await mixin._collect_media_from_attachments([attachment])

    # Verify all media was collected
    assert len(result) == 3  # Two bundle media + one preview
    media_ids = {m.id for m in result}
    assert "bundle_media_123" in media_ids
    assert "bundle_media_456" in media_ids
    assert "preview_123" in media_ids


@pytest.mark.asyncio
async def test_process_items_with_gallery_error_handling(
    mixin, mock_session, sample_account, mock_performer, mock_studio
):
    """Test error handling in _process_items_with_gallery."""
    # Create a post that will trigger an error
    error_post = AccessibleAsyncMock()
    error_post.id = "error_post_123"
    error_post._awaitable_attrs = MagicMock()
    error_post._awaitable_attrs.attachments = AsyncMock(
        side_effect=Exception("Test error")
    )
    make_asyncmock_awaitable(error_post._awaitable_attrs.attachments)

    # Create a working post
    working_post = AccessibleAsyncMock()
    working_post.id = "working_post_123"
    working_post._awaitable_attrs = MagicMock()
    working_post._awaitable_attrs.attachments = AsyncMock(return_value=[])
    working_post._awaitable_attrs.hashtags = AsyncMock(return_value=[])
    working_post._awaitable_attrs.accountMentions = AsyncMock(return_value=[])
    make_asyncmock_awaitable(working_post._awaitable_attrs.attachments)
    make_asyncmock_awaitable(working_post._awaitable_attrs.hashtags)
    make_asyncmock_awaitable(working_post._awaitable_attrs.accountMentions)

    # Mock session setup
    mock_result = AccessibleAsyncMock()
    mock_result.scalar_one = AsyncMock(return_value=sample_account)
    make_asyncmock_awaitable(mock_result.scalar_one)
    mock_session.execute = AsyncMock(return_value=mock_result)
    make_asyncmock_awaitable(mock_session.execute)

    # Process both posts
    await mixin._process_items_with_gallery(
        account=sample_account,
        performer=mock_performer,
        studio=mock_studio,
        item_type="post",
        items=[error_post, working_post],
        url_pattern_func=lambda x: f"https://example.com/post/{x.id}",
        session=mock_session,
    )

    # Verify the working post was processed despite the error in the first post
    assert mixin._process_item_gallery.call_count == 1
    assert mixin._process_item_gallery.call_args[1]["item"] == working_post
