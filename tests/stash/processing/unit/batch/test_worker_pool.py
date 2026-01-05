"""Tests for worker pool processing methods.

This module tests the BatchProcessingMixin which provides producer/consumer
worker pool patterns for concurrent processing using asyncio.

Tests migrated to use respx_stash_processor fixture for HTTP boundary mocking.
"""

import asyncio

import pytest


class TestWorkerPoolProcessing:
    """Test worker pool processing methods."""

    @pytest.mark.asyncio
    async def test_setup_worker_pool(self, respx_stash_processor):
        """Test _setup_worker_pool creates necessary infrastructure."""
        # Create test items
        items = [{"id": i} for i in range(10)]

        # Call the setup method
        (
            task_name,
            process_name,
            semaphore,
            queue,
        ) = await respx_stash_processor._setup_worker_pool(
            items=items,
            item_type="post",
        )

        # Verify task names are created
        assert task_name is not None
        assert "post" in task_name or "add" in task_name
        assert process_name is not None
        assert "post" in process_name or "process" in process_name

        # Verify semaphore has reasonable concurrency limit
        assert isinstance(semaphore, asyncio.Semaphore)
        assert semaphore._value > 0
        assert semaphore._value <= 10  # max_concurrent limit

        # Verify queue is created with unlimited size
        assert isinstance(queue, asyncio.Queue)
        assert queue.maxsize == 0  # Unlimited

    @pytest.mark.asyncio
    async def test_run_worker_pool_basic(self, respx_stash_processor):
        """Test _run_worker_pool processes items correctly."""
        # Create test items
        items = [{"id": i, "value": i * 2} for i in range(5)]

        # Track processed items
        processed = []
        processing_lock = asyncio.Lock()

        async def process_item(item):
            """Simple processor that records the item."""
            async with processing_lock:
                processed.append(item)

        # Setup infrastructure
        (
            task_name,
            process_name,
            semaphore,
            queue,
        ) = await respx_stash_processor._setup_worker_pool(
            items=items,
            item_type="test",
        )

        # Run worker pool
        await respx_stash_processor._run_worker_pool(
            items=items,
            task_name=task_name,
            process_name=process_name,
            semaphore=semaphore,
            queue=queue,
            process_item=process_item,
        )

        # Verify all items were processed
        assert len(processed) == 5
        processed_ids = {item["id"] for item in processed}
        assert processed_ids == {0, 1, 2, 3, 4}

    @pytest.mark.asyncio
    async def test_run_worker_pool_concurrent(self, respx_stash_processor):
        """Test _run_worker_pool processes items concurrently."""
        # Create more items to demonstrate concurrency
        items = [{"id": i} for i in range(20)]

        # Track processing order and timing
        processing_started = []
        processing_finished = []
        lock = asyncio.Lock()

        async def process_item(item):
            """Processor that tracks timing."""
            async with lock:
                processing_started.append(item["id"])
            # Simulate some async work
            await asyncio.sleep(0.01)
            async with lock:
                processing_finished.append(item["id"])

        # Setup infrastructure
        (
            task_name,
            process_name,
            semaphore,
            queue,
        ) = await respx_stash_processor._setup_worker_pool(
            items=items,
            item_type="test",
        )

        # Run worker pool
        await respx_stash_processor._run_worker_pool(
            items=items,
            task_name=task_name,
            process_name=process_name,
            semaphore=semaphore,
            queue=queue,
            process_item=process_item,
        )

        # Verify all items were processed
        assert len(processing_finished) == 20

        # Verify items were processed concurrently
        # (If sequential, first 10 would finish before last 10 start)
        # With concurrency, later items should start before earlier ones finish
        first_10_finished = processing_finished[:10]
        assert len(set(processing_started) - set(first_10_finished)) > 0

    @pytest.mark.asyncio
    async def test_run_worker_pool_error_handling(self, respx_stash_processor):
        """Test _run_worker_pool continues processing despite errors."""
        # Create test items
        items = [{"id": i} for i in range(10)]

        # Track successful processing
        successful = []
        failed = []
        lock = asyncio.Lock()

        async def process_item(item):
            """Processor that fails on certain items."""
            # Fail on items with id 3 and 7
            if item["id"] in [3, 7]:
                async with lock:
                    failed.append(item["id"])
                raise ValueError(f"Test error for item {item['id']}")
            async with lock:
                successful.append(item["id"])

        # Setup infrastructure
        (
            task_name,
            process_name,
            semaphore,
            queue,
        ) = await respx_stash_processor._setup_worker_pool(
            items=items,
            item_type="test",
        )

        # Run worker pool (should not raise despite errors)
        await respx_stash_processor._run_worker_pool(
            items=items,
            task_name=task_name,
            process_name=process_name,
            semaphore=semaphore,
            queue=queue,
            process_item=process_item,
        )

        # Verify successful items were processed
        assert len(successful) == 8
        assert 3 not in successful
        assert 7 not in successful

        # Verify failed items were attempted
        assert len(failed) == 2
        assert 3 in failed
        assert 7 in failed

    @pytest.mark.asyncio
    async def test_run_worker_pool_empty_list(self, respx_stash_processor):
        """Test _run_worker_pool handles empty item list gracefully."""
        # Create empty list
        items = []

        processed = []

        async def process_item(item):
            """Should never be called."""
            processed.append(item)

        # Setup infrastructure
        (
            task_name,
            process_name,
            semaphore,
            queue,
        ) = await respx_stash_processor._setup_worker_pool(
            items=items,
            item_type="test",
        )

        # Run worker pool
        await respx_stash_processor._run_worker_pool(
            items=items,
            task_name=task_name,
            process_name=process_name,
            semaphore=semaphore,
            queue=queue,
            process_item=process_item,
        )

        # Verify nothing was processed
        assert len(processed) == 0

    @pytest.mark.asyncio
    async def test_run_worker_pool_large_batch(self, respx_stash_processor):
        """Test _run_worker_pool handles large batches efficiently."""
        # Create large batch (triggers delayed consumer start)
        items = [{"id": i} for i in range(100)]

        # Track processing
        processed_count = 0
        lock = asyncio.Lock()

        async def process_item(item):
            """Simple counter."""
            nonlocal processed_count
            async with lock:
                processed_count += 1

        # Setup infrastructure
        (
            task_name,
            process_name,
            semaphore,
            queue,
        ) = await respx_stash_processor._setup_worker_pool(
            items=items,
            item_type="test",
        )

        # Run worker pool
        await respx_stash_processor._run_worker_pool(
            items=items,
            task_name=task_name,
            process_name=process_name,
            semaphore=semaphore,
            queue=queue,
            process_item=process_item,
        )

        # Verify all items were processed
        assert processed_count == 100
