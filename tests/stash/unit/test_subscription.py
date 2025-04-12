"""Unit tests for subscription functionality."""

import asyncio
import os

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
                    if update.job.id == job_id:
                        # Break if we hit a terminal state
                        if update.job.status in [
                            JobStatus.FINISHED,
                            JobStatus.CANCELLED,
                            JobStatus.FAILED,
                        ]:
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
    result = False  # Initialize result variable
    # Set up job subscription first
    async with stash_client.subscribe_to_jobs() as subscription:
        # Start a simple generation job
        job_id = await stash_client.metadata_generate(
            {
                "phashes": True,  # Minimal options to ensure job completes
            },
        )
        assert job_id is not None, "Job ID should not be None"

        try:
            async with asyncio.timeout(5.0):
                async for update in subscription:
                    if update.job and update.job.id == job_id:
                        if update.job.status == JobStatus.FINISHED:
                            result = True
                            break
                        elif update.job.status in [
                            JobStatus.CANCELLED,
                            JobStatus.FAILED,
                        ]:
                            result = False
                            break
        except TimeoutError:
            pytest.fail("Timed out waiting for job completion")

        # Print details and verify the job completed successfully
        if not result:
            print(
                f"Job failed to complete successfully. Last update status: {update.job.status}"
            )
            if update.job:
                print(f"Job details: {update.job}")
        assert result is True, "Job should complete successfully"
