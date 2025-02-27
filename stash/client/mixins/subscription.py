"""Subscription-related client functionality."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from gql import gql

from ...types import JobStatus, JobStatusUpdate, LogEntry


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
        # Store original transport
        original_transport = self.client.transport

        # Switch to WebSocket transport
        self.client.transport = self.ws_transport
        try:
            yield self.client
        finally:
            # Switch back to HTTP transport
            self.client.transport = original_transport

    async def subscribe_to_jobs(self) -> AsyncIterator[JobStatusUpdate]:
        """Subscribe to job status updates.

        Yields:
            JobStatusUpdate objects as they arrive

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
                    message
                    progress
                    status
                    error
                    job {
                        id
                        status
                        subTasks
                        description
                        progress
                    }
                }
            }
        """
        )

        async with self._subscription_client() as client:
            async for result in client.subscribe(subscription):
                yield JobStatusUpdate(**result["jobsSubscribe"])

    async def subscribe_to_logs(self) -> AsyncIterator[list[LogEntry]]:
        """Subscribe to log entries.

        Yields:
            Lists of LogEntry objects as they arrive

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

        async with self._subscription_client() as client:
            async for result in client.subscribe(subscription):
                yield [LogEntry(**entry) for entry in result["loggingSubscribe"]]

    async def subscribe_to_scan_complete(self) -> AsyncIterator[bool]:
        """Subscribe to scan completion events.

        Yields:
            True when a scan completes

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

        async with self._subscription_client() as client:
            async for result in client.subscribe(subscription):
                yield result["scanCompleteSubscribe"]

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
            async with self.subscribe_to_jobs() as subscription:
                async for update in subscription:
                    if update.job.id == job_id:
                        self.log.info(
                            f"Job {job_id}: {update.status} "
                            f"({update.progress:.1f}%) - {update.message}"
                        )

                        if update.status == status:
                            return True
                        if update.status in [JobStatus.FINISHED, JobStatus.CANCELLED]:
                            return False

            return None
        except Exception as e:
            self.log.error(f"Failed to wait for job {job_id}: {e}")
            # Fall back to polling if subscription fails
            return await self.wait_for_job(job_id, status, timeout=timeout)
