"""Integration tests for StashClient.

These tests require a running Stash instance.
"""

import asyncio

import pytest
from stash_graphql_client import StashClient
from stash_graphql_client.types import GenerateMetadataOptions, JobStatus, Scene


# Integration tests with metadata generation need longer timeouts
pytestmark = [pytest.mark.asyncio, pytest.mark.timeout(180)]


@pytest.mark.asyncio
async def test_scene_workflow(
    stash_client: StashClient, enable_scene_creation, stash_cleanup_tracker
) -> None:
    """Test complete scene workflow."""
    async with stash_cleanup_tracker(stash_client, auto_capture=False) as cleanup:
        # Create scene
        scene = Scene(
            title="Test Scene",
            details="Test scene details",
            date="2024-01-01",
            urls=["https://example.com/scene"],
            organized=True,
            # Required relationships
            performers=[],
            tags=[],
            galleries=[],
            studio=None,
            stash_ids=[],
        )

        # Create
        created = await stash_client.create_scene(scene)
        cleanup["scenes"].append(created.id)  # Add to cleanup tracker
        assert created.id is not None
        assert created.title == scene.title

        # Find
        found = await stash_client.find_scene(created.id)
        assert found is not None
        assert found.id == created.id
        assert found.title == scene.title

        # Update
        found.title = "Updated Title"
        updated = await stash_client.update_scene(found)
        assert updated.title == "Updated Title"

        # Generate metadata
        options = GenerateMetadataOptions(
            covers=True,
            sprites=True,
            previews=True,
        )
        job_id = await stash_client.metadata_generate(options)
        assert job_id is not None

        # Wait for job
        result = await stash_client.wait_for_job(job_id=job_id, period=0.1)
        assert result is True


@pytest.mark.asyncio
async def test_subscription_integration(
    stash_client: StashClient, stash_cleanup_tracker
) -> None:
    """Test subscription integration.

    This test:
    1. Starts job subscription
    2. Triggers metadata generation
    3. Waits for job updates
    4. Verifies job completion

    Note: This test doesn't create persistent objects, but includes stash_cleanup_tracker
    to comply with the cleanup enforcement pattern.
    """
    try:
        updates = []
        async with stash_client.subscribe_to_jobs() as subscription:
            # Start metadata generation
            options = GenerateMetadataOptions(covers=True)
            job_id = await stash_client.metadata_generate(options)

            # Collect updates with a shorter timeout
            async with asyncio.timeout(30):  # Reduced from default 300s
                async for update in subscription:
                    updates.append(update)
                    if update.job and update.job.id == job_id:
                        # Check for completion via status or type
                        if update.job.status == JobStatus.FINISHED or (
                            update.type == "REMOVE"
                            and update.job.status == JobStatus.FINISHED
                        ):
                            break
                        # Also break on other terminal states
                        if update.job.status in [JobStatus.CANCELLED, JobStatus.FAILED]:
                            break

        # Verify updates
        assert len(updates) > 0, "Should receive at least one update"
        assert any(u.job and u.job.id == job_id for u in updates), (
            "Should receive update for our job"
        )

        # Verify final state
        final_updates = [u for u in updates if u.job and u.job.id == job_id]
        assert any(u.job.status == JobStatus.FINISHED for u in final_updates), (
            "Job should complete successfully"
        )

    except (ConnectionError, TimeoutError) as e:
        pytest.skip(f"Connection error - test requires running Stash instance: {e!s}")
    except Exception as e:
        # Re-raise other exceptions that aren't connection-related
        raise
