"""Integration tests for subscription functionality.

These tests require a real Stash server because they test WebSocket subscriptions
which cannot be mocked with respx (HTTP-only mocking).

IMPORTANT: WebSocket subscriptions are a valid exception to the mock-free testing
guideline because:
1. respx only supports HTTP mocking, not WebSocket
2. The gql library's subscription transport uses WebSocket
3. These tests validate real subscription behavior with actual job events
"""

import asyncio
import os

import pytest

from stash import StashClient
from stash.types import JobStatus


# Skip all tests in this module when running in the OpenHands sandbox
# These tests require a real Stash server to run properly
pytestmark = pytest.mark.skipif(
    os.environ.get("OPENHANDS_SANDBOX") in ("1", "true"),
    reason="Subscription tests require a real Stash server and cannot run in the OpenHands sandbox",
)


@pytest.mark.asyncio
async def test_subscribe_to_jobs(
    stash_client: StashClient, stash_cleanup_tracker
) -> None:
    """Test job subscription by triggering a metadata scan job.

    This test verifies:
    1. Subscribe to job updates works
    2. Job status updates are received correctly
    3. Terminal states (FINISHED, CANCELLED, FAILED) are detected
    """
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
async def test_subscribe_to_logs(
    stash_client: StashClient, stash_cleanup_tracker
) -> None:
    """Test log subscription.

    This test verifies:
    1. Subscribe to logs works
    2. Log entries have required fields (time, level, message)
    3. Job-related logs are captured
    """
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
async def test_subscribe_to_scan_complete(
    stash_client: StashClient, stash_cleanup_tracker
) -> None:
    """Test scan complete subscription.

    This test verifies:
    1. Subscribe to scan complete events works
    2. Scan completion events are received as booleans
    """
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
async def test_wait_for_job_with_updates(
    stash_client: StashClient, stash_cleanup_tracker
) -> None:
    """Test the wait_for_job_with_updates method.

    This test verifies:
    1. The method starts a job and subscribes to updates
    2. It properly waits for job completion
    3. It returns True when job reaches desired status

    Note: This uses metadata_generate which creates a quick job for testing.
    """
    # Start a metadata generation job (quick operation)
    job_id = await stash_client.metadata_generate(
        {
            "phashes": True,
        },
    )
    assert job_id is not None, "Job ID should not be None"

    # Use the actual wait_for_job_with_updates method
    result = await stash_client.wait_for_job_with_updates(
        job_id,
        status=JobStatus.FINISHED,
        timeout_seconds=30.0,
    )

    # Job should complete successfully
    assert result is True, f"Job {job_id} should complete successfully"


@pytest.mark.asyncio
async def test_wait_for_job_with_updates_timeout(
    stash_client: StashClient, stash_cleanup_tracker
) -> None:
    """Test wait_for_job_with_updates with a very short timeout.

    This test verifies:
    1. The method handles timeout correctly
    2. It returns None when timeout is reached
    """
    # Start a metadata generation job
    job_id = await stash_client.metadata_generate(
        {
            "phashes": True,
        },
    )
    assert job_id is not None, "Job ID should not be None"

    # Use a very short timeout that will likely expire
    # Note: This may pass if the job completes very quickly
    result = await stash_client.wait_for_job_with_updates(
        job_id,
        status=JobStatus.FINISHED,
        timeout_seconds=0.001,  # Very short timeout
    )

    # Result should be None (timeout) or True (job completed very fast)
    assert result in [None, True], f"Expected None or True, got {result}"


@pytest.mark.asyncio
async def test_wait_for_job_with_updates_not_found(
    stash_client: StashClient, stash_cleanup_tracker
) -> None:
    """Test wait_for_job_with_updates with non-existent job ID.

    This test verifies:
    1. The method handles non-existent jobs correctly
    2. It returns None for jobs that don't exist
    """
    # Use a non-existent job ID
    result = await stash_client.wait_for_job_with_updates(
        "999999999",  # Non-existent job ID
        status=JobStatus.FINISHED,
        timeout_seconds=5.0,
    )

    # Should return None for non-existent job
    assert result is None, "Should return None for non-existent job"
