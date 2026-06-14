"""True integration tests for StashProcessing - hits real Docker Stash."""

import pytest

from stash.processing import StashProcessing
from tests.fixtures.stash.stash_api_fixtures import dump_graphql_calls
from tests.fixtures.stash.stash_integration_fixtures import capture_graphql_calls


class TestIntegrationErrorHandling:
    """Integration tests for error handling in StashProcessing."""

    @pytest.mark.asyncio
    async def test_missing_account_handling(
        self,
        real_stash_processor: StashProcessing,
        entity_store,
        stash_cleanup_tracker,
    ):
        """Test graceful handling when account is not found.

        This test verifies that the processor handles missing accounts
        without crashing or creating invalid data.
        """
        client = real_stash_processor.context.client
        async with stash_cleanup_tracker(client):
            # Try to process a creator that doesn't exist in database
            try:
                with capture_graphql_calls(client) as calls:
                    account = await real_stash_processor._find_account()
            finally:
                dump_graphql_calls(calls, "missing_account_handling")

            # Should return None gracefully
            assert account is None

    @pytest.mark.asyncio
    async def test_duplicate_performer_creation(
        self,
        real_stash_processor: StashProcessing,
        entity_store,
        test_account,
        stash_cleanup_tracker,
    ):
        """Test that calling process_creator twice doesn't create duplicates.

        This test:
        1. Creates performer first time
        2. Calls process_creator again with same account
        3. Verifies only one performer exists (finds existing, doesn't duplicate)
        """
        client = real_stash_processor.context.client
        async with stash_cleanup_tracker(client) as cleanup:
            # Configure processor state to match test account
            real_stash_processor.state.creator_id = test_account.id
            real_stash_processor.state.creator_name = test_account.username

            try:
                with capture_graphql_calls(client) as calls:
                    # First call - creates performer
                    _account1, performer1 = await real_stash_processor.process_creator()

                    # Track for cleanup
                    if performer1:
                        cleanup["performers"].append(performer1.id)

                    # Second call - should find existing
                    _account2, performer2 = await real_stash_processor.process_creator()

                    # Search by displayName since that's what the performer name
                    # is based on (INSIDE cleanup context, pre-teardown!)
                    performers_result = await client.find_performers(
                        q=test_account.displayName
                    )
            finally:
                dump_graphql_calls(calls, "duplicate_performer_creation")

            # Should be the same performer (found existing)
            assert performer1 is not None
            assert performer2 is not None
            assert performer1.id == performer2.id, "Should reuse existing performer"

            # Verify only one performer with this name exists.
            matching_performers = [
                p
                for p in performers_result.performers
                if p.name == test_account.displayName
            ]
            assert len(matching_performers) == 1, (
                "Should not create duplicate performers"
            )
