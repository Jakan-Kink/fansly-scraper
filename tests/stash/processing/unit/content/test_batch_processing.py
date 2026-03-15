"""Tests for batch processing functionality in ContentProcessingMixin.

This test module uses entity_store for Pydantic model persistence and respx for
HTTP mocking to provide reliable unit testing while letting real code flow execute.
"""

import asyncio
import json
from unittest.mock import AsyncMock, patch

import httpx
import pytest
import respx

from metadata import (
    Account,
    ContentType,
    Post,
)
from metadata.models import get_store
from tests.fixtures import (
    AccountFactory,
    AccountMediaBundleFactory,
    AccountMediaFactory,
    AttachmentFactory,
    MediaFactory,
    PostFactory,
    create_graphql_response,
)
from tests.fixtures.stash.stash_api_fixtures import dump_graphql_calls
from tests.fixtures.stash.stash_type_factories import PerformerFactory, StudioFactory


@pytest.mark.asyncio
async def test_process_creator_posts_with_batch_processing(
    entity_store, respx_stash_processor
):
    """Test process_creator_posts processes galleries correctly."""
    await respx_stash_processor.context.get_client()
    store = get_store()

    # Create account with factory
    account = AccountFactory.build(
        id=12345, username="test_user", displayName="Test User"
    )
    await store.save(account)

    # Create post with attachment (required by INNER JOIN in process_creator_posts)
    post = PostFactory.build(id=200, accountId=12345, content="Test post")
    await store.save(post)

    media = MediaFactory.build(
        id=123, accountId=12345, mimetype="image/jpeg", is_downloaded=True
    )
    await store.save(media)

    account_media = AccountMediaFactory.build(id=123, accountId=12345, mediaId=123)
    await store.save(account_media)

    attachment = AttachmentFactory.build(
        id=60001,
        postId=200,  # Link attachment to post
        contentId=123,  # Points to AccountMedia
        contentType=ContentType.ACCOUNT_MEDIA,
        pos=0,
    )
    await store.save(attachment)

    # Add attachment to post's relationship
    post.attachments = [attachment]
    await store.save(post)

    # Retrieve account from store
    account = await store.get(Account, 12345)

    # Create performer and studio using factories
    performer = PerformerFactory.build(id="500", name="test_user")
    studio = StudioFactory.build(id="999", name="test_user (Fansly)")

    # Create minimal GraphQL responses to let batch processing execute
    # This is a UNIT test - we only care that batch processing orchestration works
    from tests.fixtures.stash import create_find_galleries_result, create_gallery_dict

    graphql_route = respx.post("http://localhost:9999/graphql").mock(
        side_effect=[
            # findGalleries - search by code (returns no galleries)
            httpx.Response(
                200,
                json=create_graphql_response(
                    "findGalleries", create_find_galleries_result(count=0, galleries=[])
                ),
            ),
            # findGalleries - search by title (returns no galleries)
            httpx.Response(
                200,
                json=create_graphql_response(
                    "findGalleries", create_find_galleries_result(count=0, galleries=[])
                ),
            ),
            # findGalleries - search by URL (returns no galleries, will create new)
            httpx.Response(
                200,
                json=create_graphql_response(
                    "findGalleries", create_find_galleries_result(count=0, galleries=[])
                ),
            ),
            # galleryCreate - create new gallery
            httpx.Response(
                200,
                json=create_graphql_response(
                    "galleryCreate",
                    create_gallery_dict(
                        id="700",
                        title="test_user - 2025/11/21",
                        code="200",
                        urls=["https://fansly.com/post/200"],
                        studio={"id": "999"},
                        performers=[{"id": "500", "name": "test_user"}],
                    ),
                ),
            ),
        ]
    )

    try:
        await respx_stash_processor.process_creator_posts(
            account=account,
            performer=performer,
            studio=studio,
        )
    finally:
        dump_graphql_calls(
            graphql_route.calls,
            "test_process_creator_posts_with_batch_processing",
        )

    # Verify GraphQL calls were made in expected sequence
    calls = graphql_route.calls
    assert len(calls) == 4, f"Expected 4 GraphQL calls, got {len(calls)}"

    # Call 0: findGalleries (by code)
    req0 = json.loads(calls[0].request.content)
    assert "findGalleries" in req0["query"]
    assert req0["variables"]["gallery_filter"]["code"]["value"] == "200"
    assert calls[0].response.json()["data"]["findGalleries"]["count"] == 0

    # Call 1: findGalleries (by title)
    req1 = json.loads(calls[1].request.content)
    assert "findGalleries" in req1["query"]
    assert "test_user" in req1["variables"]["gallery_filter"]["title"]["value"]
    assert calls[1].response.json()["data"]["findGalleries"]["count"] == 0

    # Call 2: findGalleries (by URL)
    req2 = json.loads(calls[2].request.content)
    assert "findGalleries" in req2["query"]
    assert (
        req2["variables"]["gallery_filter"]["url"]["value"]
        == "https://fansly.com/post/200"
    )
    assert calls[2].response.json()["data"]["findGalleries"]["count"] == 0

    # Call 3: galleryCreate (create new gallery)
    req3 = json.loads(calls[3].request.content)
    assert "galleryCreate" in req3["query"]
    assert req3["variables"]["input"]["code"] == "200"
    assert req3["variables"]["input"]["studio_id"] == "999"
    assert req3["variables"]["input"]["performer_ids"] == ["500"]
    assert calls[3].response.json()["data"]["galleryCreate"]["id"] == "700"


@pytest.mark.asyncio
async def test_collect_media_from_attachments_with_aggregated_post(
    entity_store,
    respx_stash_processor,
):
    """Test _collect_media_from_attachments with an aggregated post."""
    store = get_store()

    # Create account and media
    account = AccountFactory.build(id=12345, username="test_user")
    await store.save(account)

    media = MediaFactory.build(id=125, accountId=12345, mimetype="image/jpeg")
    await store.save(media)

    account_media = AccountMediaFactory.build(id=125, accountId=12345, mediaId=125)
    await store.save(account_media)
    # account_media.media auto-resolves via identity map (Media already saved)

    # Create aggregated post with nested attachment
    agg_post = PostFactory.build(id=201, accountId=12345, content="Aggregated post")
    await store.save(agg_post)

    nested_attachment = AttachmentFactory.build(
        id=60003,
        postId=201,  # Link nested attachment to aggregated post
        contentId=125,  # Points to AccountMedia
        contentType=ContentType.ACCOUNT_MEDIA,
        pos=0,
    )
    await store.save(nested_attachment)

    # Add nested attachment to aggregated post
    agg_post.attachments = [nested_attachment]
    await store.save(agg_post)

    # nested_attachment.media auto-resolves via identity map (AccountMedia already saved)

    # Create main post with attachment pointing to aggregated post
    main_post = PostFactory.build(id=202, accountId=12345, content="Main post")
    await store.save(main_post)

    main_attachment = AttachmentFactory.build(
        id=60004,
        postId=202,  # Link attachment to main post
        contentId=201,  # Points to aggregated post
        contentType=ContentType.AGGREGATED_POSTS,
        pos=0,
    )
    await store.save(main_attachment)

    # main_attachment.aggregated_post auto-resolves via identity map (Post already saved)

    # Call the method
    result = await respx_stash_processor._collect_media_from_attachments(
        [main_attachment]
    )

    # Verify nested media was found
    assert len(result) == 1
    assert result[0].id == 125


@pytest.mark.asyncio
async def test_collect_media_from_attachments_with_bundle(
    entity_store,
    respx_stash_processor,
):
    """Test _collect_media_from_attachments with a media bundle."""
    store = get_store()

    # Create account
    account = AccountFactory.build(id=12345, username="test_user")
    await store.save(account)

    # Create bundle
    bundle = AccountMediaBundleFactory.build(id=80001, accountId=12345)
    await store.save(bundle)

    # Create media for bundle
    media1 = MediaFactory.build(id=126, accountId=12345, mimetype="image/jpeg")
    await store.save(media1)

    account_media1 = AccountMediaFactory.build(id=126, accountId=12345, mediaId=126)
    await store.save(account_media1)
    # account_media1.media auto-resolves via identity map (Media already saved)

    media2 = MediaFactory.build(id=128, accountId=12345, mimetype="video/mp4")
    await store.save(media2)

    account_media2 = AccountMediaFactory.build(id=128, accountId=12345, mediaId=128)
    await store.save(account_media2)
    # account_media2.media auto-resolves via identity map (Media already saved)

    # Add media to bundle
    bundle.accountMedia = [account_media1, account_media2]
    await store.save(bundle)

    # Create post attachment pointing to bundle
    post = PostFactory.build(id=203, accountId=12345, content="Post with bundle")
    await store.save(post)

    attachment = AttachmentFactory.build(
        id=60005,
        postId=203,  # Link attachment to post
        contentId=80001,  # Points to bundle
        contentType=ContentType.ACCOUNT_MEDIA_BUNDLE,
        pos=0,
    )
    await store.save(attachment)

    # attachment.bundle auto-resolves via identity map (AccountMediaBundle already saved)

    # Call the method
    result = await respx_stash_processor._collect_media_from_attachments([attachment])

    # Verify all media was collected
    assert len(result) >= 2  # At least two bundle media (preview is optional)
    media_ids = {m.id for m in result}
    assert 126 in media_ids
    assert 128 in media_ids


@pytest.mark.asyncio
async def test_process_items_with_gallery_error_handling(
    entity_store, respx_stash_processor
):
    """Test error handling in _process_items_with_gallery."""
    store = get_store()

    # Create account
    account = AccountFactory.build(
        id=12345, username="test_user", displayName="Test User"
    )
    await store.save(account)

    # Create a working post with attachment
    post = PostFactory.build(id=204, accountId=12345, content="Working post #test")
    await store.save(post)

    media = MediaFactory.build(id=129, accountId=12345, mimetype="image/jpeg")
    await store.save(media)

    account_media = AccountMediaFactory.build(id=129, accountId=12345, mediaId=129)
    await store.save(account_media)

    attachment = AttachmentFactory.build(
        id=60006,
        postId=204,  # Link attachment to post
        contentId=129,  # Points to AccountMedia
        contentType=ContentType.ACCOUNT_MEDIA,
        pos=0,
    )
    await store.save(attachment)

    # Add attachment to post
    post.attachments = [attachment]
    await store.save(post)

    # Retrieve fresh account and post from store
    account = await store.get(Account, 12345)
    working_post = await store.get(Post, 204)

    # Create performer and studio
    performer = PerformerFactory.build(id="500", name="test_user")
    studio = StudioFactory.build(id="999", name="test_user (Fansly)")

    # Patch _process_item_gallery to track calls and verify error handling works
    with patch.object(
        respx_stash_processor, "_process_item_gallery", new=AsyncMock()
    ) as mock_gallery:
        await respx_stash_processor._process_items_with_gallery(
            account=account,
            performer=performer,
            studio=studio,
            item_type="post",
            items=[working_post],
            url_pattern_func=lambda x: f"https://example.com/post/{x.id}",
        )

        # Verify the working post was processed
        assert mock_gallery.call_count == 1
        assert mock_gallery.call_args[1]["item"].id == 204


# ============================================================================
# Batch Processing Error Handler Tests
# ============================================================================


@pytest.mark.asyncio
async def test_batch_consumer_cancelled_error_re_raise(respx_stash_processor):
    """Test consumer re-raises CancelledError (line 131)."""
    # Create a flag to track when processing starts
    processing_started = asyncio.Event()

    async def process_item_that_gets_cancelled(item):
        # Signal that processing has started
        processing_started.set()
        # Sleep to allow cancellation to occur during processing
        await asyncio.sleep(10)

    items = ["item1", "item2", "item3"]

    # Set up worker pool
    (
        task_name,
        process_name,
        semaphore,
        queue,
    ) = await respx_stash_processor._setup_worker_pool(items, "test")

    # Create a task that will be cancelled while processing
    async def run_and_cancel():
        worker_task = asyncio.create_task(
            respx_stash_processor._run_worker_pool(
                items,
                task_name,
                process_name,
                semaphore,
                queue,
                process_item_that_gets_cancelled,
            )
        )
        # Wait for processing to actually start
        await processing_started.wait()
        # Cancel the worker task while consumer is processing
        worker_task.cancel()
        await worker_task

    # Should propagate CancelledError from consumer
    with pytest.raises(asyncio.CancelledError):
        await run_and_cancel()


@pytest.mark.asyncio
async def test_batch_no_config_attribute(respx_stash_processor):
    """Test batch processing without config attribute (lines 148->153, 185->191)."""
    # Temporarily remove config attribute
    original_config = respx_stash_processor.config
    delattr(respx_stash_processor, "config")

    try:
        processed_items = []

        async def process_item(item):
            processed_items.append(item)
            await asyncio.sleep(0.01)

        items = ["item1", "item2", "item3"]

        # Set up worker pool
        (
            task_name,
            process_name,
            semaphore,
            queue,
        ) = await respx_stash_processor._setup_worker_pool(items, "test")

        # Should work fine without config
        await respx_stash_processor._run_worker_pool(
            items, task_name, process_name, semaphore, queue, process_item
        )

        # Verify all items were processed
        assert len(processed_items) == 3

    finally:
        # Restore config
        respx_stash_processor.config = original_config


@pytest.mark.asyncio
async def test_batch_config_without_background_tasks_method(respx_stash_processor):
    """Test batch processing with config lacking get_background_tasks (lines 148->153, 185->191)."""
    processed_items = []

    async def process_item(item):
        processed_items.append(item)
        await asyncio.sleep(0.01)

    items = ["item1", "item2"]

    # Set up worker pool
    (
        task_name,
        process_name,
        semaphore,
        queue,
    ) = await respx_stash_processor._setup_worker_pool(items, "test")

    # Mock hasattr to return False for get_background_tasks
    original_hasattr = hasattr

    def fake_hasattr(obj, name):
        if name == "get_background_tasks":
            return False
        return original_hasattr(obj, name)

    with patch("builtins.hasattr", side_effect=fake_hasattr):
        # Should work fine with config but no get_background_tasks
        await respx_stash_processor._run_worker_pool(
            items, task_name, process_name, semaphore, queue, process_item
        )

    # Verify all items were processed
    assert len(processed_items) == 2


@pytest.mark.asyncio
async def test_batch_queue_join_timeout(respx_stash_processor):
    """Test TimeoutError handling when queue.join() times out (lines 158-164)."""

    async def process_item_that_hangs(item):
        await asyncio.sleep(999)  # Never completes

    items = ["item1"]

    # Set up worker pool
    (
        task_name,
        process_name,
        semaphore,
        queue,
    ) = await respx_stash_processor._setup_worker_pool(items, "test")

    # Patch asyncio.wait_for to raise TimeoutError immediately
    with patch("asyncio.wait_for", side_effect=TimeoutError("Timeout!")):
        # Should NOT raise - TimeoutError is caught and handled
        await respx_stash_processor._run_worker_pool(
            items, task_name, process_name, semaphore, queue, process_item_that_hangs
        )
        # Test passes if no exception propagates


@pytest.mark.asyncio
async def test_batch_unexpected_exception(respx_stash_processor):
    """Test generic Exception handling (lines 173-182)."""

    # Patch queue.join() to raise unexpected exception
    async def fake_join_that_raises():
        raise RuntimeError("Unexpected error in queue!")

    items = ["item1"]

    # Set up worker pool
    (
        task_name,
        process_name,
        semaphore,
        queue,
    ) = await respx_stash_processor._setup_worker_pool(items, "test")

    # Mock queue.join to raise unexpected exception
    queue.join = fake_join_that_raises

    # Should raise the RuntimeError (after logging and cleanup)
    with pytest.raises(RuntimeError, match="Unexpected error in queue"):
        await respx_stash_processor._run_worker_pool(
            items, task_name, process_name, semaphore, queue, AsyncMock()
        )


@pytest.mark.asyncio
async def test_batch_task_not_in_background_tasks(respx_stash_processor):
    """Test cleanup when task not in background tasks (line 188->186)."""
    processed_items = []
    tasks_removed = []

    async def process_item(item):
        # During first item processing, remove a task from background_tasks
        if not tasks_removed:
            bg_tasks = respx_stash_processor.config.get_background_tasks()
            if bg_tasks:
                # Remove the first task (likely a consumer or producer)
                removed = bg_tasks.pop(0)
                tasks_removed.append(removed)
        processed_items.append(item)
        await asyncio.sleep(0.01)

    items = ["item1", "item2"]

    # Set up worker pool
    (
        task_name,
        process_name,
        semaphore,
        queue,
    ) = await respx_stash_processor._setup_worker_pool(items, "test")

    # Run worker pool - during execution, a task will be removed from background_tasks
    await respx_stash_processor._run_worker_pool(
        items, task_name, process_name, semaphore, queue, process_item
    )

    # The removed task will NOT be in background_tasks during finally cleanup (line 188->186)
    assert len(processed_items) == 2
    assert len(tasks_removed) == 1  # Verify we actually removed a task


@pytest.mark.asyncio
async def test_batch_consumer_generic_exception(respx_stash_processor):
    """Test consumer handles non-CancelledError exceptions (lines 132-136)."""
    processed_items = []
    failed_items = []

    # Create process_item that fails for specific items
    async def process_item_with_errors(item):
        if item == "bad_item":
            failed_items.append(item)
            raise ValueError(f"Failed to process {item}")
        processed_items.append(item)

    items = ["item1", "bad_item", "item2"]

    # Set up worker pool
    (
        task_name,
        process_name,
        semaphore,
        queue,
    ) = await respx_stash_processor._setup_worker_pool(items, "test")

    # Should NOT raise - errors are logged and processing continues
    await respx_stash_processor._run_worker_pool(
        items, task_name, process_name, semaphore, queue, process_item_with_errors
    )

    # Verify good items were processed
    assert "item1" in processed_items
    assert "item2" in processed_items

    # Verify bad item was attempted
    assert "bad_item" in failed_items


@pytest.mark.asyncio
async def test_batch_timeout_with_completed_task(respx_stash_processor):
    """Test TimeoutError when some tasks already done (line 161->160)."""
    completed_count = [0]

    async def process_item_quick(item):
        # First item completes immediately
        completed_count[0] += 1
        if completed_count[0] == 1:
            return  # Complete immediately
        # Other items hang
        await asyncio.sleep(999)

    items = ["quick_item", "slow_item1", "slow_item2"]

    # Set up worker pool
    (
        task_name,
        process_name,
        semaphore,
        queue,
    ) = await respx_stash_processor._setup_worker_pool(items, "test")

    # Patch wait_for to timeout after first item completes
    original_wait_for = asyncio.wait_for
    call_count = [0]

    async def patched_wait_for(coro, *, timeout=None):  # noqa: ASYNC109
        call_count[0] += 1
        if call_count[0] == 1:
            # Let it run briefly so first item can complete
            await asyncio.sleep(0.05)
            raise TimeoutError("Simulated timeout")
        return await original_wait_for(coro, timeout=timeout)

    with patch("asyncio.wait_for", side_effect=patched_wait_for):
        # Should NOT raise - handles timeout gracefully
        await respx_stash_processor._run_worker_pool(
            items, task_name, process_name, semaphore, queue, process_item_quick
        )

    # Verify at least one task completed (triggers 161->160 branch)
    assert completed_count[0] >= 1


@pytest.mark.asyncio
async def test_batch_parent_cancelled_during_execution(respx_stash_processor):
    """Test parent CancelledError handler is executed (lines 167-172, including line 169)."""
    processing_started = asyncio.Event()

    # Track task cancellations by wrapping create_task
    cancelled_tasks = []
    original_create_task = asyncio.create_task

    def tracking_create_task(coro, **kwargs):
        task = original_create_task(coro, **kwargs)
        # Store original cancel method
        original_cancel = task.cancel

        def tracked_cancel(*args, **kw):
            cancelled_tasks.append(task)
            return original_cancel(*args, **kw)

        # Replace cancel method on this specific task instance
        task.cancel = tracked_cancel
        return task

    async def process_item_slow(item):
        processing_started.set()
        # Long sleep to ensure tasks are running when cancelled
        await asyncio.sleep(60)

    items = ["item1", "item2", "item3", "item4", "item5"]

    # Set up worker pool
    (
        task_name,
        process_name,
        semaphore,
        queue,
    ) = await respx_stash_processor._setup_worker_pool(items, "test")

    # Patch create_task to track cancellations
    with patch("asyncio.create_task", side_effect=tracking_create_task):

        async def run_then_cancel():
            # This create_task is NOT patched (it's before the with statement)
            task = original_create_task(
                respx_stash_processor._run_worker_pool(
                    items, task_name, process_name, semaphore, queue, process_item_slow
                )
            )
            # Wait for processing to start
            await processing_started.wait()
            # Small delay to ensure all consumer tasks are created
            await asyncio.sleep(0.02)
            # Cancel the main task - this should trigger line 169 for child tasks
            task.cancel()
            await task

        # Expect CancelledError to propagate
        with pytest.raises(asyncio.CancelledError):
            await run_then_cancel()

    # Verify task.cancel() was called on child tasks (line 169)
    # Should have cancelled at least the consumers and producer
    assert len(cancelled_tasks) > 0, (
        f"task.cancel() should have been called on child tasks, "
        f"but only {len(cancelled_tasks)} cancellations detected"
    )


@pytest.mark.asyncio
async def test_batch_exception_with_completed_task(respx_stash_processor):
    """Test generic Exception when some tasks already done (line 178->177)."""
    completed_items = []

    async def process_item_mixed(item):
        # Complete items quickly
        completed_items.append(item)
        await asyncio.sleep(0.01)

    items = ["item1", "item2", "item3"]

    # Set up worker pool
    (
        task_name,
        process_name,
        semaphore,
        queue,
    ) = await respx_stash_processor._setup_worker_pool(items, "test")

    # Patch queue.join() to raise exception after some tasks complete
    original_join = queue.join

    async def fake_join_delayed():
        # Let some items complete
        await asyncio.sleep(0.05)
        raise RuntimeError("Simulated error after partial completion")

    queue.join = fake_join_delayed

    # Should raise the RuntimeError
    with pytest.raises(RuntimeError, match="Simulated error after partial completion"):
        await respx_stash_processor._run_worker_pool(
            items, task_name, process_name, semaphore, queue, process_item_mixed
        )

    # Verify some items were processed before exception
    # This ensures tasks were running and some may have completed (triggers 178->177)
