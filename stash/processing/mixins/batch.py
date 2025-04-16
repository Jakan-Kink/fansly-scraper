"""Batch processing mixin."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from tqdm import tqdm

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class BatchProcessingMixin:
    """Batch processing utilities."""

    async def _setup_batch_processing(
        self,
        items: list[Any],
        item_type: str,
    ) -> tuple[tqdm, tqdm, asyncio.Semaphore, asyncio.Queue]:
        """Set up common batch processing infrastructure.

        Args:
            items: List of items to process
            item_type: Type of items ("post" or "message")

        Returns:
            Tuple of (task_pbar, process_pbar, semaphore, queue)
        """
        # Create progress bars
        task_pbar = tqdm(
            total=len(items),
            desc=f"Adding {len(items)} {item_type} tasks",
            position=0,
            unit="task",
        )
        process_pbar = tqdm(
            total=len(items),
            desc=f"Processing {len(items)} {item_type}s",
            position=1,
            unit=item_type,
        )

        # Use reasonable default concurrency limit
        # Limited to avoid overwhelming Stash server
        max_concurrent = min(10, (os.cpu_count() // 2) or 1)
        semaphore = asyncio.Semaphore(max_concurrent)
        queue = asyncio.Queue(
            maxsize=max_concurrent * 4
        )  # Quadruple buffer for more throughput

        return task_pbar, process_pbar, semaphore, queue

    async def _run_batch_processor(
        self,
        items: list[Any],
        batch_size: int,
        task_pbar: tqdm,
        process_pbar: tqdm,
        semaphore: asyncio.Semaphore,
        queue: asyncio.Queue,
        process_batch: Callable,
    ) -> None:
        """Run batch processing with producer/consumer pattern.

        Args:
            items: List of items to process
            batch_size: Size of each batch
            task_pbar: Progress bar for task creation
            process_pbar: Progress bar for processing
            semaphore: Semaphore for concurrency control
            queue: Queue for producer/consumer pattern
            process_batch: Callback function to process each batch
        """
        # Use same concurrency as semaphore
        max_concurrent = semaphore._value

        async def producer():
            # Process in batches
            for i in range(0, len(items), batch_size):
                batch = items[i : i + batch_size]
                await queue.put(batch)
                task_pbar.update(len(batch))
            # Signal consumers we're done
            for _ in range(max_concurrent):
                await queue.put(None)
            task_pbar.close()

        async def consumer():
            while True:
                batch = await queue.get()
                if batch is None:  # Sentinel value
                    queue.task_done()
                    break
                try:
                    await process_batch(batch)
                finally:
                    queue.task_done()

        try:
            # Start consumers
            consumers = [asyncio.create_task(consumer()) for _ in range(max_concurrent)]
            # Start producer
            producer_task = asyncio.create_task(producer())
            # Wait for all work to complete
            await queue.join()
            await producer_task
            await asyncio.gather(*consumers, return_exceptions=True)
        finally:
            process_pbar.close()
