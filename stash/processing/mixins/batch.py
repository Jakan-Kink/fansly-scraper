"""Worker pool processing mixin."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Callable
from typing import Any

from helpers.rich_progress import get_progress_manager

from ...logging import processing_logger as logger


class BatchProcessingMixin:
    """Worker pool processing utilities."""

    async def _setup_worker_pool(
        self,
        items: list[Any],
        item_type: str,
    ) -> tuple[str, str, asyncio.Semaphore, asyncio.Queue]:
        """Set up common worker pool infrastructure.

        Args:
            items: List of items to process
            item_type: Type of items ("post" or "message")

        Returns:
            Tuple of (task_name, process_name, semaphore, queue)
        """
        # Get progress manager
        progress_mgr = get_progress_manager()

        # Create progress tasks
        task_name = progress_mgr.add_task(
            name=f"add_{item_type}_tasks",
            description=f"Adding {len(items)} {item_type} tasks",
            total=len(items),
            show_elapsed=False,
        )
        process_name = progress_mgr.add_task(
            name=f"process_{item_type}s",
            description=f"Processing {len(items)} {item_type}s",
            total=len(items),
            show_elapsed=False,
        )

        # Use reasonable default concurrency limit
        # Limited to avoid overwhelming Stash server
        max_concurrent = min(10, (os.cpu_count() // 2) or 1)
        semaphore = asyncio.Semaphore(max_concurrent)
        # No maximum queue size - allow unlimited buffering
        queue = asyncio.Queue(maxsize=0)

        return task_name, process_name, semaphore, queue

    async def _run_worker_pool(
        self,
        items: list[Any],
        task_name: str,
        process_name: str,
        semaphore: asyncio.Semaphore,
        queue: asyncio.Queue,
        process_item: Callable,
    ) -> None:
        """Run processing with worker pool pattern.

        Args:
            items: List of items to process
            task_name: Progress task name for task creation
            process_name: Progress task name for processing
            semaphore: Semaphore for concurrency control
            queue: Queue for worker pool pattern
            process_item: Callback function to process each item
        """
        # Get progress manager
        progress_mgr = get_progress_manager()

        # Use same concurrency as semaphore
        max_concurrent = semaphore._value
        # Track all created tasks for proper cleanup
        all_tasks = []
        # Flag to track if consumer tasks are already started
        consumers_started = asyncio.Event()
        # Keep track of enqueued items
        enqueued_count = 0

        async def producer():
            nonlocal enqueued_count

            # Add items to the queue
            for item in items:
                await queue.put(item)
                progress_mgr.update_task(task_name, advance=1)
                enqueued_count += 1

                # Start consumers when we have 40+ items in the queue
                # or when all items are queued (for smaller jobs)
                if (
                    enqueued_count >= 40 or enqueued_count == len(items)
                ) and not consumers_started.is_set():
                    consumers_started.set()

            # Always set the event once all items are queued
            # This ensures consumers start even with a small number of items
            if not consumers_started.is_set():
                consumers_started.set()

            # Signal consumers we're done
            for _ in range(max_concurrent):
                await queue.put(None)
            # Remove task from progress manager
            progress_mgr.remove_task(task_name)

        async def consumer():
            # Wait until producer signals to start
            await consumers_started.wait()

            while True:
                try:
                    item = await queue.get()
                    if item is None:  # Sentinel value
                        queue.task_done()
                        break
                    try:
                        await process_item(item)
                        progress_mgr.update_task(process_name, advance=1)
                    except asyncio.CancelledError:
                        # Handle cancellation gracefully
                        raise
                    except Exception as e:
                        # Log error but continue processing
                        logger.exception(f"Error in item processing: {e}")
                    finally:
                        queue.task_done()
                except asyncio.CancelledError:
                    # Allow task to be cancelled while waiting for queue
                    raise

        try:
            # Start consumers
            consumers = [asyncio.create_task(consumer()) for _ in range(max_concurrent)]
            all_tasks.extend(consumers)

            # Start producer
            producer_task = asyncio.create_task(producer())
            all_tasks.append(producer_task)

            # Register tasks with config for cleanup if available
            if hasattr(self, "config") and hasattr(self.config, "get_background_tasks"):
                for task in all_tasks:
                    self.config.get_background_tasks().append(task)

            # Wait for all work to complete with timeout
            try:
                # Add timeout to prevent indefinite hanging
                await asyncio.wait_for(queue.join(), timeout=120)  # 2-minute timeout
                await producer_task
                await asyncio.gather(*consumers, return_exceptions=True)
            except TimeoutError:
                # If timeout occurs, cancel all tasks
                for task in all_tasks:
                    if not task.done():
                        task.cancel()
                # Let cancelled tasks clean up
                await asyncio.sleep(0.5)
        except asyncio.CancelledError:
            # Cancel all child tasks if parent is cancelled
            for task in all_tasks:
                if not task.done():
                    task.cancel()
            # Let cancelled tasks clean up
            await asyncio.sleep(0.5)
            raise
        except Exception as e:
            # Handle unexpected errors
            logger.exception(f"Unexpected error in worker pool processing: {e}")
            # Cancel all tasks in case of unexpected error
            for task in all_tasks:
                if not task.done():
                    task.cancel()
            # Let cancelled tasks clean up
            await asyncio.sleep(0.5)
            raise
        finally:
            # Clean up tasks if they're still in the background tasks list
            if hasattr(self, "config") and hasattr(self.config, "get_background_tasks"):
                for task in all_tasks:
                    # Remove task from background tasks if it's there
                    if task in self.config.get_background_tasks():
                        self.config.get_background_tasks().remove(task)
            # Remove process task from progress manager
            progress_mgr.remove_task(process_name)
