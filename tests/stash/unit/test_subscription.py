"""Unit tests for subscription functionality."""

import asyncio
import os

import pytest

from stash import StashClient
from stash.types import (
    GenerateMetadataInput,
    GenerateMetadataOptions,
    JobStatus,
    ScanMetadataInput,
    ScanMetadataOptions,
)

# Skip all tests in this module when running in the OpenHands sandbox
# These tests require a real Stash server to run properly
pytestmark = pytest.mark.skipif(
    os.environ.get("OPENHANDS_SANDBOX") in ("1", "true"),
    reason="Subscription tests require a real Stash server and cannot run in the OpenHands sandbox",
)


@pytest.mark.asyncio
async def test_subscribe_to_jobs(stash_client: StashClient) -> None:
    """Test job subscription by triggering a metadata scan job."""
    # Start subscription first
    async with stash_client.subscribe_to_jobs() as subscription:
        # Trigger a job in a separate task
        task = asyncio.create_task(
            stash_client.metadata_scan(
                ScanMetadataInput(
                    paths=["/"],  # Use root path for test server
                    rescan=False,
                    scanGenerateCovers=False,
                    scanGeneratePreviews=False,
                    scanGenerateImagePreviews=False,
                    scanGenerateSprites=False,
                    scanGeneratePhashes=False,
                    scanGenerateThumbnails=False,
                    scanGenerateClipPreviews=False,
                )
            )
        )

        # Wait for job update with timeout
        try:
            async with asyncio.timeout(30):  # 30-second timeout
                async for update in subscription:
                    assert update.type is not None
                    assert update.status is not None
                    assert update.job is not None
                    assert update.job.id is not None
                    # We got an update, test passes
                    break
        finally:
            # Clean up the task
            if not task.done():
                task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass


@pytest.mark.asyncio
async def test_subscribe_to_logs(stash_client: StashClient) -> None:
    """Test log subscription by triggering an operation that generates logs."""
    # Start subscription first
    async with stash_client.subscribe_to_logs() as subscription:
        # Trigger an operation that generates logs (e.g., metadata scan)
        task = asyncio.create_task(
            stash_client.metadata_scan(
                ScanMetadataInput(
                    paths=["/"],  # Use root path for test server
                    rescan=False,
                    scanGenerateCovers=False,
                    scanGeneratePreviews=False,
                    scanGenerateImagePreviews=False,
                    scanGenerateSprites=False,
                    scanGeneratePhashes=False,
                    scanGenerateThumbnails=False,
                    scanGenerateClipPreviews=False,
                )
            )
        )

        # Wait for log entries with timeout
        try:
            async with asyncio.timeout(30):  # 30-second timeout
                async for logs in subscription:
                    assert isinstance(logs, list)
                    if logs:  # Only validate if we got logs
                        for log in logs:
                            assert log.time is not None
                            assert log.level is not None
                            assert log.message is not None
                        # We got logs, test passes
                        break
        finally:
            # Clean up the task
            if not task.done():
                task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass


@pytest.mark.asyncio
async def test_subscribe_to_scan_complete(stash_client: StashClient) -> None:
    """Test scan completion subscription by triggering a scan."""
    # Start subscription first
    async with stash_client.subscribe_to_scan_complete() as subscription:
        # Trigger a scan operation
        task = asyncio.create_task(
            stash_client.metadata_scan(
                ScanMetadataInput(
                    paths=["/"],  # Use root path for test server
                    rescan=False,
                    scanGenerateCovers=False,
                    scanGeneratePreviews=False,
                    scanGenerateImagePreviews=False,
                    scanGenerateSprites=False,
                    scanGeneratePhashes=False,
                    scanGenerateThumbnails=False,
                    scanGenerateClipPreviews=False,
                )
            )
        )

        # Wait for scan complete event with timeout
        try:
            async with asyncio.timeout(60):  # 60-second timeout (scans can take longer)
                async for completed in subscription:
                    assert isinstance(completed, bool)
                    # We got a scan complete event, test passes
                    break
        finally:
            # Clean up the task
            if not task.done():
                task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass


@pytest.mark.asyncio
async def test_wait_for_job_with_updates(stash_client: StashClient) -> None:
    """Test waiting for job with updates using a real job."""
    # Create a real job (e.g., metadata generation for a scene)
    # First find a scene to use
    result = await stash_client.execute(
        """
        query FindScenes {
            findScenes(filter: {per_page: 1}) {
                scenes {
                    id
                }
            }
        }
        """
    )

    scenes = result.get("findScenes", {}).get("scenes", [])
    if not scenes:
        pytest.skip("No scenes available for testing")

    scene_id = scenes[0]["id"]

    # Start a metadata generation job
    job_id = await stash_client.metadata_generate(
        GenerateMetadataInput(
            sceneIDs=[scene_id],
            phashes=True,  # This corresponds to perceptualHash
            previews=False,  # This corresponds to previewGeneration
        )
    )

    # Wait for the job with a reasonable timeout
    result = await stash_client.wait_for_job_with_updates(
        job_id,
        status=JobStatus.FINISHED,
        timeout=60.0,  # 60-second timeout
    )

    # Verify the result
    assert result is not None, "Job result should not be None"
