"""Subscription-related client functionality."""

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import TypeVar

from gql import Client, gql

from ...types import Job, JobStatus, JobStatusUpdate, LogEntry

T = TypeVar("T")
U = TypeVar("U")


class AsyncIteratorWrapper(AsyncIterator[T]):
    """Wrapper for async iterators that transforms their output."""

    def __init__(self, iterator: AsyncIterator[U], transform: Callable[[U], T]):
        """Initialize the wrapper.

        Args:
            iterator: The async iterator to wrap
            transform: Function to transform the iterator's output
        """
        self.iterator = iterator
        self.transform = transform

    def __aiter__(self):
        """Return self as an async iterator."""
        return self

    async def __anext__(self) -> T:
        """Get the next transformed value from the iterator.

        Returns:
            The next transformed value

        Raises:
            StopAsyncIteration: When the iterator is exhausted
        """
        value = await self.iterator.__anext__()
        return self.transform(value)


class SubscriptionClientMixin:
    """Mixin for subscription-related client methods."""

    @asynccontextmanager
    async def _subscription_client(self):
        """Get a client configured for subscriptions.

        This is a context manager that switches the client to WebSocket transport
        and switches it back when done.

        Example:
            ```python
            async with self._subscription_client() as client:
                async for result in client.subscribe(...):
                    ...
            ```
        """
        # Ensure client is initialized
        if not hasattr(self, "client"):
            raise Exception("Failed to connect")

        # Store original transport
        original_transport = self.client.transport

        # Switch to WebSocket transport
        self.client.transport = self.ws_transport
        try:
            yield self.client
        finally:
            # Switch back to HTTP transport
            self.client.transport = original_transport

    @asynccontextmanager
    async def subscribe_to_jobs(self) -> AsyncIterator[AsyncIterator[JobStatusUpdate]]:
        """Subscribe to job status updates.

        Returns:
            An async context manager that yields an async iterator of JobStatusUpdate objects

        Example:
            ```python
            async with client.subscribe_to_jobs() as subscription:
                async for update in subscription:
                    print(f"Job {update.job.id}: {update.status} ({update.progress}%)")
                    if update.status == "FINISHED":
                        break
            ```
        """
        subscription = gql(
            """
            subscription {
                jobsSubscribe {
                    type
                    job {
                        id
                        addTime
                        status
                        subTasks
                        description
                        progress
                        error
                    }
                }
            }
        """
        )
        client: Client

        async with self._subscription_client() as client:
            try:
                async with client as session:
                    subscription_gen = session.subscribe(subscription)
                    yield AsyncIteratorWrapper(
                        subscription_gen,
                        lambda x: JobStatusUpdate(
                            type=x["jobsSubscribe"]["type"],
                            job=Job(**x["jobsSubscribe"]["job"]),
                        ),
                    )
            finally:
                await client.close_async()

    @asynccontextmanager
    async def subscribe_to_logs(self) -> AsyncIterator[AsyncIterator[list[LogEntry]]]:
        """Subscribe to log entries.

        Returns:
            An async context manager that yields an async iterator of LogEntry lists

        Example:
            ```python
            async with client.subscribe_to_logs() as subscription:
                async for logs in subscription:
                    for entry in logs:
                        print(f"{entry.time} [{entry.level}] {entry.message}")
            ```
        """
        subscription = gql(
            """
            subscription {
                loggingSubscribe {
                    time
                    level
                    message
                }
            }
        """
        )
        client: Client

        async with self._subscription_client() as client:
            try:
                async with client as session:
                    subscription_gen = session.subscribe(subscription)
                    yield AsyncIteratorWrapper(
                        subscription_gen,
                        lambda x: [
                            LogEntry(**entry) for entry in x["loggingSubscribe"]
                        ],
                    )
            finally:
                await client.close_async()

    @asynccontextmanager
    async def subscribe_to_scan_complete(self) -> AsyncIterator[AsyncIterator[bool]]:
        """Subscribe to scan completion events.

        Returns:
            An async context manager that yields an async iterator of scan completion events

        Example:
            ```python
            async with client.subscribe_to_scan_complete() as subscription:
                async for _ in subscription:
                    print("Scan completed!")
                    await client.metadata_generate(...)  # Generate after scan
            ```
        """
        subscription = gql(
            """
            subscription {
                scanCompleteSubscribe
            }
        """
        )
        client: Client

        async with self._subscription_client() as client:
            try:
                async with client as session:
                    subscription_gen = session.subscribe(subscription)
                    yield AsyncIteratorWrapper(
                        subscription_gen, lambda x: x["scanCompleteSubscribe"]
                    )
            finally:
                await client.close_async()

    async def _check_job_status(
        self, job_id: str
    ) -> tuple[bool | None, JobStatus | None]:
        """Check current job status.

        Args:
            job_id: Job ID to check

        Returns:
            Tuple of (is_done, status):
            - is_done: True if job is in final state, False if running, None if not found
            - status: Current job status if found, None if not found
        """
        result = await self.execute(
            """
            query FindJob($id: ID!) {
                findJob(id: $id) {
                    id
                    status
                    progress
                    description
                }
            }
            """,
            {"id": job_id},
        )

        if job := result.get("findJob"):
            job_status = JobStatus(job["status"])
            self.log.info(
                f"Job {job_id}: {job_status} "
                f"({job.get('progress', 0):.1f}%) - {job.get('description', '')}"
            )

            is_done = job_status in [JobStatus.FINISHED, JobStatus.CANCELLED]
            return is_done, job_status

        return None, None

    async def wait_for_job_with_updates(
        self,
        job_id: str,
        status: JobStatus = JobStatus.FINISHED,
        timeout: float = 120,
    ) -> bool | None:
        """Wait for a job to complete with real-time updates.

        Args:
            job_id: Job ID to wait for
            status: Status to wait for
            timeout: Maximum time to wait in seconds

        Returns:
            True if job reached desired status
            False if job finished with different status
            None if job not found

        Example:
            ```python
            job_id = await client.metadata_generate(...)
            if await client.wait_for_job_with_updates(job_id):
                print("Generation complete!")
            ```
        """
        try:
            # First check if the job is already finished
            is_done, job_status = await self._check_job_status(job_id)
            if is_done is None:
                return None  # Job not found
            if is_done:
                return job_status == status

            # Job not finished, wait for updates
            async with asyncio.timeout(timeout):
                async with self.subscribe_to_jobs() as subscription:
                    async for update in subscription:
                        if update.job.id == job_id:
                            self.log.info(
                                f"Job {job_id}: {update.job.status} "
                                f"({update.job.progress:.1f}%) - {update.job.description}"
                            )

                            if update.job.status == status:
                                return True
                            if update.job.status in [
                                JobStatus.FINISHED,
                                JobStatus.CANCELLED,
                            ]:
                                return False

            return None
        except TimeoutError:
            self.log.error(f"Timeout waiting for job {job_id}")
            return None
        except Exception as e:
            self.log.error(f"Failed to wait for job {job_id}: {e}")
            # Fall back to polling if subscription fails
            return await self.wait_for_job(job_id, status, timeout=timeout)
