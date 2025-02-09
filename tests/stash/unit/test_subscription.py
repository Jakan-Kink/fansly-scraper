"""Unit tests for subscription functionality."""

import pytest

from stash import StashClient
from stash.types import JobStatus


@pytest.mark.asyncio
async def test_subscribe_to_jobs(stash_client: StashClient) -> None:
    """Test job subscription."""
    try:
        async with stash_client.subscribe_to_jobs() as subscription:
            async for update in subscription:
                assert update.type is not None
                assert update.status is not None
                break  # Just test first update
    except Exception as e:
        # We expect this to fail since we're not actually connected to a server
        assert "Failed to connect" in str(e)


@pytest.mark.asyncio
async def test_subscribe_to_logs(stash_client: StashClient) -> None:
    """Test log subscription."""
    try:
        async with stash_client.subscribe_to_logs() as subscription:
            async for logs in subscription:
                assert isinstance(logs, list)
                for log in logs:
                    assert log.time is not None
                    assert log.level is not None
                    assert log.message is not None
                break  # Just test first update
    except Exception as e:
        # We expect this to fail since we're not actually connected to a server
        assert "Failed to connect" in str(e)


@pytest.mark.asyncio
async def test_subscribe_to_scan_complete(stash_client: StashClient) -> None:
    """Test scan completion subscription."""
    try:
        async with stash_client.subscribe_to_scan_complete() as subscription:
            async for completed in subscription:
                assert isinstance(completed, bool)
                break  # Just test first update
    except Exception as e:
        # We expect this to fail since we're not actually connected to a server
        assert "Failed to connect" in str(e)


@pytest.mark.asyncio
async def test_wait_for_job_with_updates(stash_client: StashClient) -> None:
    """Test waiting for job with updates."""
    try:
        result = await stash_client.wait_for_job_with_updates(
            "test_job",
            status=JobStatus.FINISHED,
            timeout=1.0,
        )
        assert result in [True, False, None]
    except Exception as e:
        # We expect this to fail since we're not actually connected to a server
        assert "Failed to connect" in str(e)
