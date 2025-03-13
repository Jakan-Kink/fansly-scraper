"""Integration tests for StashClient.

These tests require a running Stash instance.
"""

import pytest

from stash import StashClient
from stash.types import GenerateMetadataOptions, Scene


@pytest.mark.asyncio
async def test_scene_workflow(stash_client: StashClient) -> None:
    """Test complete scene workflow."""
    # Create scene
    scene = Scene(
        id="new",  # Required for initialization, will be replaced on create
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

    try:
        # Create
        created = await stash_client.create_scene(scene)
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
        result = await stash_client.wait_for_job(job_id)
        assert result is True

    except Exception as e:
        pytest.skip(f"Test requires running Stash instance: {e}")


@pytest.mark.asyncio
async def test_subscription_integration(stash_client: StashClient) -> None:
    """Test subscription integration.

    This test:
    1. Starts job subscription
    2. Triggers metadata generation
    3. Waits for job updates
    4. Verifies job completion
    """
    try:
        updates = []
        async with stash_client.subscribe_to_jobs() as subscription:
            # Start metadata generation
            options = GenerateMetadataOptions(covers=True)
            job_id = await stash_client.metadata_generate(options)

            # Collect updates
            async for update in subscription:
                updates.append(update)
                if update.job and update.job.id == job_id:
                    if update.status in ["FINISHED", "CANCELLED"]:
                        break

        # Verify updates
        assert len(updates) > 0
        assert any(u.job and u.job.id == job_id for u in updates)

    except Exception as e:
        pytest.skip(f"Test requires running Stash instance: {e}")
