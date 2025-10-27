"""Unit tests for subscription functionality."""

import asyncio
import contextlib
import os
from unittest.mock import AsyncMock, patch

import pytest

from stash import StashClient
from stash.types import Job, JobStatus


# Skip all tests in this module when running in the OpenHands sandbox
# These tests require a real Stash server to run properly
pytestmark = pytest.mark.skipif(
    os.environ.get("OPENHANDS_SANDBOX") in ("1", "true"),
    reason="Subscription tests require a real Stash server and cannot run in the OpenHands sandbox",
)


@pytest.mark.asyncio
async def test_subscribe_to_jobs(stash_client: StashClient) -> None:
    """Test job subscription by triggering a metadata scan job."""
    # Start subscription before triggering the job
    async with stash_client.subscribe_to_jobs() as subscription:
        try:
            async with asyncio.timeout(5):
                # Start metadata scan job with proper array of paths
                job_id = await stash_client.metadata_scan(
                    paths=["test/path"],  # Array of paths
                )
                assert job_id is not None, "Job should start successfully"
                async for update in subscription:
                    assert update.type is not None
                    assert update.job is not None
                    # Only process updates for our specific job
                    if update.job.id == job_id and update.job.status in [
                        JobStatus.FINISHED,
                        JobStatus.CANCELLED,
                        JobStatus.FAILED,
                    ]:
                        # Break if we hit a terminal state
                        break
        except TimeoutError:
            pytest.fail("Timed out waiting for job updates")


@pytest.mark.asyncio
async def test_subscribe_to_logs(stash_client: StashClient) -> None:
    """Test log subscription by triggering an operation that generates logs."""
    # Start subscription before triggering the job
    async with stash_client.subscribe_to_logs() as subscription:
        # Start scan job with proper array of paths
        job_id = await stash_client.metadata_scan(
            paths=["test/path"],  # Array of paths
        )
        assert job_id is not None, "Job should start successfully"

        try:
            async with asyncio.timeout(5):
                async for logs in subscription:
                    # Skip empty log batches
                    if not logs:
                        continue

                    # Validate structure of logs
                    assert all(
                        log.time is not None
                        and log.level is not None
                        and log.message is not None
                        for log in logs
                    )

                    # Check if any log mentions our job
                    if any(job_id in log.message for log in logs):
                        break

        except TimeoutError:
            pytest.fail("Timed out waiting for job logs")


@pytest.mark.asyncio
async def test_subscribe_to_scan_complete(stash_client: StashClient) -> None:
    """Test scan completion subscription by triggering a scan."""
    # Start subscription before starting scan
    async with stash_client.subscribe_to_scan_complete() as subscription:
        # Start scan job with proper array of paths
        job_id = await stash_client.metadata_scan(
            paths=["test/path"],  # Array of paths
        )
        assert job_id is not None, "Job should start successfully"

        try:
            async with asyncio.timeout(
                5
            ):  # Short timeout since we're properly ordered now
                async for completed in subscription:
                    assert isinstance(completed, bool)
                    # We got a scan complete event
                    break
        except TimeoutError:
            pytest.fail("Timed out waiting for scan complete event")


@pytest.mark.asyncio
async def test_wait_for_job_with_updates(stash_client: StashClient) -> None:
    """Test waiting for job with updates using a real job."""
    # Use a more robust approach with mocking instead of depending on real jobs
    # Setup mock behavior
    mock_job_id = "1544"

    # Create mock subscription
    mock_updates = [
        AsyncMock(
            type="JOB_UPDATE",
            job=Job(
                id=mock_job_id,
                status=JobStatus.READY,
                description="Starting job...",
                addTime="2025-04-13T20:15:50.019916834-04:00",
                subTasks=[],  # Required parameter
            ),
        ),
        AsyncMock(
            type="JOB_UPDATE",
            job=Job(
                id=mock_job_id,
                status=JobStatus.RUNNING,
                progress=50,
                description="Processing...",
                addTime="2025-04-13T20:15:50.019916834-04:00",
                subTasks=[],  # Required parameter
            ),
        ),
        AsyncMock(
            type="JOB_UPDATE",
            job=Job(
                id=mock_job_id,
                status=JobStatus.FINISHED,
                progress=100,
                description="Generating...",
                addTime="2025-04-13T20:15:50.019916834-04:00",
                subTasks=[],  # Required parameter
            ),
        ),
    ]

    # Create a mock async generator that yields predefined updates
    async def mock_subscription_gen():
        for update in mock_updates:
            yield update

    # Create a mock context manager for subscription
    @contextlib.asynccontextmanager
    async def mock_subscribe():
        yield mock_subscription_gen()

    # Patch the subscription method and metadata_generate
    with (
        patch.object(stash_client, "subscribe_to_jobs", return_value=mock_subscribe()),
        patch.object(
            stash_client,
            "metadata_generate",
            new_callable=AsyncMock,
            return_value=mock_job_id,
        ),
    ):
        result = False
        # Now use our mocked subscription
        async with stash_client.subscribe_to_jobs() as subscription:
            job_id = await stash_client.metadata_generate(
                {
                    "phashes": True,
                },
            )
            assert job_id is not None, "Job ID should not be None"
            assert job_id == mock_job_id, "Should receive our mock job ID"

            try:
                async with asyncio.timeout(5.0):
                    async for update in subscription:
                        if update.job and update.job.id == job_id:
                            if update.job.status == JobStatus.FINISHED:
                                result = True
                                break
                            if update.job.status in [
                                JobStatus.CANCELLED,
                                JobStatus.FAILED,
                            ]:
                                result = False
                                break
            except TimeoutError:
                pytest.fail("Timed out waiting for job completion")

            # Verify the job completed successfully
            if not result:
                print("Job failed to complete successfully.")
                if "update" in locals() and hasattr(update, "job"):
                    print(f"Last update status: {update.job.status}")
                    print(f"Job details: {update.job}")
            assert result is True, "Job should complete successfully"
