"""Integration tests for StashProcessing.

This module tests the StashProcessing class using real database fixtures
and factory-based test data. These are TRUE integration tests that make
real GraphQL calls to a Stash instance.
"""

import time
from datetime import UTC, datetime

import pytest
from stash_graphql_client.types import Performer, Studio

from metadata import Account
from tests.fixtures.metadata.metadata_factories import AccountFactory
from tests.fixtures.stash.stash_api_fixtures import (
    assert_op_with_vars,
    dump_graphql_calls,
)
from tests.fixtures.stash.stash_integration_fixtures import capture_graphql_calls
from tests.fixtures.utils.test_isolation import snowflake_id


@pytest.mark.xdist_group("find_account")
class TestStashProcessingIntegration:
    """Integration tests for StashProcessing."""

    @pytest.mark.asyncio
    async def test_initialization(
        self, factory_session, real_stash_processor, test_state, stash_cleanup_tracker
    ):
        """Test StashProcessing initialization with real database."""
        # Verify the processor was properly initialized with real dependencies
        assert real_stash_processor.config is not None
        assert real_stash_processor.state.creator_id == test_state.creator_id
        assert real_stash_processor.state.creator_name == test_state.creator_name
        assert real_stash_processor.context is not None
        assert real_stash_processor.database is not None

        # Verify the processor has expected methods
        assert hasattr(real_stash_processor, "process_creator")

    @pytest.mark.asyncio
    async def test_find_account_by_id_name_and_missing(
        self,
        factory_session,
        real_stash_processor,
        test_database_sync,
        entity_store,
        stash_cleanup_tracker,
    ):
        """Test _find_account by id, by name, and the not-found path on one DB.

        Saves account A (looked up by id) and account B (looked up by name)
        once into a shared entity_store, then exercises all three _find_account
        facets in sequence, resetting state.creator_id/creator_name between
        each sub-assert exactly as the original per-facet tests did.
        """
        # Save account A (resolved by id) and account B (resolved by name) once.
        a_id = snowflake_id()
        account_a = Account(
            id=a_id,
            username="test_user",
            createdAt=datetime.now(UTC),
        )
        await entity_store.save(account_a)

        b_id = snowflake_id()
        account_b = Account(
            id=b_id,
            username="test_creator",
            createdAt=datetime.now(UTC),
        )
        await entity_store.save(account_b)

        # Facet 1: lookup by id returns account A.
        real_stash_processor.state.creator_id = a_id
        real_stash_processor.state.creator_name = None
        # _find_account no longer accepts session - uses get_store() internally
        result = await real_stash_processor._find_account()
        assert result is not None
        assert result.id == a_id
        assert result.username == "test_user"

        # Facet 2: lookup by name returns account B.
        real_stash_processor.state.creator_id = None
        real_stash_processor.state.creator_name = "test_creator"
        result = await real_stash_processor._find_account()
        assert result is not None
        assert result.username == "test_creator"

        # Facet 3: lookup of a nonexistent id returns None.
        real_stash_processor.state.creator_id = snowflake_id()
        real_stash_processor.state.creator_name = None
        result = await real_stash_processor._find_account()
        assert result is None

    @pytest.mark.asyncio
    async def test_find_existing_performer(
        self, real_stash_processor, stash_cleanup_tracker
    ):
        """Test _find_existing_performer method with real account.

        TRUE INTEGRATION TEST: Creates real performer in Stash, verifies lookup works.
        """
        client = real_stash_processor.context.client
        async with stash_cleanup_tracker(client) as cleanup:
            # Create a real performer in Stash
            test_performer = Performer(
                name="performer_user_integration_test",
                urls=["https://fansly.com/performer_user_integration_test"],
            )
            created_performer = await client.create_performer(test_performer)
            cleanup["performers"].append(created_performer.id)

            account = AccountFactory.build(
                username="performer_user_integration_test",
                stash_id=int(created_performer.id),
            )

            # Test finding by stash_id - should find the performer we just created
            try:
                with capture_graphql_calls(client) as calls:
                    performer = await real_stash_processor._find_existing_performer(
                        account
                    )
            finally:
                dump_graphql_calls(calls, "find_existing_performer")

            # Verify result
            assert performer is not None
            assert performer.id == created_performer.id
            assert performer.name == "performer_user_integration_test"

    @pytest.mark.asyncio
    async def test_find_existing_performer_by_username(
        self, real_stash_processor, stash_cleanup_tracker
    ):
        """Test _find_existing_performer finds by username when no stash_id.

        TRUE INTEGRATION TEST: Creates real performer, verifies username lookup.
        """
        client = real_stash_processor.context.client
        async with stash_cleanup_tracker(client) as cleanup:
            # Create a real performer in Stash with a unique name
            test_performer = Performer(
                name="new_performer_by_username_test",
                urls=["https://fansly.com/new_performer_by_username_test"],
            )
            created_performer = await client.create_performer(test_performer)
            cleanup["performers"].append(created_performer.id)

            # Clear store cache to remove the locally-constructed test_performer
            # (which has a UUID id). Without this, store.filter() may return the
            # unsaved UUID object instead of the server-created numeric-id object.
            real_stash_processor.context.store.invalidate_all()

            account = AccountFactory.build(
                username="new_performer_by_username_test", stash_id=None
            )

            # Test finding by username - should find the performer by name
            try:
                with capture_graphql_calls(client) as calls:
                    performer = await real_stash_processor._find_existing_performer(
                        account
                    )
            finally:
                dump_graphql_calls(calls, "find_existing_performer_by_username")

            # Verify result
            assert performer is not None
            assert performer.name == "new_performer_by_username_test"
            assert performer.id == created_performer.id

    @pytest.mark.asyncio
    async def test_find_existing_studio(
        self, real_stash_processor, stash_cleanup_tracker
    ):
        """Test _find_existing_studio method with real account when studio exists.

        TRUE INTEGRATION TEST: Creates real studio in Stash, verifies lookup finds it.
        """
        client = real_stash_processor.context.client
        async with stash_cleanup_tracker(client) as cleanup:
            # First find or create the Fansly (network) parent studio
            fansly_result = await client.find_studios(q="Fansly (network)")
            if fansly_result.count == 0:
                fansly_parent = Studio(
                    name="Fansly (network)",
                    urls=["https://fansly.com"],
                )
                fansly_parent = await client.create_studio(fansly_parent)
                cleanup["studios"].append(fansly_parent.id)
            else:
                # Using Pydantic models from stash-graphql-client
                fansly_parent = fansly_result.studios[0]

            # Create a real creator studio in Stash BEFORE calling _find_existing_studio
            creator_studio = Studio(
                name="studio_exists_test (Fansly)",
                urls=["https://fansly.com/studio_exists_test"],
                parent_studio=fansly_parent,
            )
            created_studio = await client.create_studio(creator_studio)
            cleanup["studios"].append(created_studio.id)

            # Clear store cache to remove the locally-constructed creator_studio
            # (which has a UUID id and was never saved to the server). Without this,
            # store.filter() finds both the unsaved UUID object and the server-created
            # numeric-id object, and may return the wrong one.
            real_stash_processor.context.store.invalidate_all()

            account = AccountFactory.build(username="studio_exists_test")

            # Test finding existing studio - should find the one we just created
            try:
                with capture_graphql_calls(client) as calls:
                    studio = await real_stash_processor._find_existing_studio(account)
            finally:
                dump_graphql_calls(calls, "find_existing_studio")

            # Verify result - should return existing creator studio, NOT create a new one
            assert studio is not None
            assert studio.name == f"{account.username} (Fansly)"
            assert studio.id == created_studio.id  # Same ID means it found existing

    @pytest.mark.asyncio
    async def test_find_existing_studio_creates_new(
        self, real_stash_processor, stash_cleanup_tracker
    ):
        """Test _find_existing_studio creates new studio when not found.

        TRUE INTEGRATION TEST: Makes real GraphQL calls to Stash instance.
        Uses stash_cleanup_tracker to clean up created studio after test.
        """
        client = real_stash_processor.context.client
        async with stash_cleanup_tracker(client, auto_capture=False) as cleanup:
            # Create a real account with unique username to ensure studio doesn't exist
            unique_id = int(time.time() * 1000) % 1000000  # Last 6 digits of timestamp
            account = AccountFactory.build(username=f"new_studio_creator_{unique_id}")

            # Capture GraphQL calls while making real API calls
            # Expected flow:
            # 1. Find "Fansly (network)" studio (should exist in test Stash)
            # 2. Search for "new_studio_creator (Fansly)" (won't exist)
            # 3. Create "new_studio_creator (Fansly)" studio
            try:
                with capture_graphql_calls(client) as calls:
                    studio = await real_stash_processor._find_existing_studio(account)
            finally:
                dump_graphql_calls(calls, "find_existing_studio_creates_new")

            # Verify studio was created with correct properties
            assert studio is not None
            assert studio.name == f"{account.username} (Fansly)"
            assert studio.id != "new"  # Should have real ID from Stash

            # Verify parent studio relationship exists
            assert studio.parent_studio is not None
            assert studio.parent_studio.id is not None

            # Verify studio URLs
            assert f"https://fansly.com/{account.username}" in studio.urls

            # Verify we can find it again (proving it was really created in Stash)
            found_studio = await client.find_studios(q=f"{account.username} (Fansly)")
            assert found_studio.count == 1
            assert found_studio.studios[0].id == studio.id

            # Verify GraphQL call sequence (permanent assertion with request/response data)
            assert len(calls) == 3, (
                f"Expected exactly 3 GraphQL calls, got {len(calls)}"
            )

            # Call 0: findStudios for Fansly (network)
            assert_op_with_vars(
                calls[0],
                "findStudios",
                studio_filter__name__value="Fansly (network)",
            )
            assert calls[0]["result"] is not None, (
                f"Call 0 raised exception: {calls[0]['exception']}"
            )
            assert "findStudios" in calls[0]["result"]

            # Call 1: findStudios for creator studio
            assert_op_with_vars(
                calls[1],
                "findStudios",
                studio_filter__name__value=f"{account.username} (Fansly)",
            )
            assert calls[1]["result"] is not None, (
                f"Call 1 raised exception: {calls[1]['exception']}"
            )
            assert "findStudios" in calls[1]["result"]
            assert (
                calls[1]["result"]["findStudios"]["count"] == 0
            )  # Should not exist yet

            # Call 2: studioCreate
            assert_op_with_vars(
                calls[2],
                "studioCreate",
                input__name=f"{account.username} (Fansly)",
            )
            # Note: StudioCreateInput uses 'urls' (plural), not 'url' (singular)
            assert calls[2]["variables"]["input"]["urls"] == [
                f"https://fansly.com/{account.username}"
            ]
            assert "studioCreate" in calls[2]["result"]
            # Mutation result only includes ID (GraphQL query only requests id field)
            assert calls[2]["result"]["studioCreate"]["id"] == studio.id
            # Verify name in the returned Studio object (not in GraphQL result)
            assert studio.name == f"{account.username} (Fansly)"

            # Manual tracking after validation (earns opt-out qualification)
            if studio and studio.id != "new":
                cleanup["studios"].append(studio.id)

    @pytest.mark.asyncio
    async def test_update_performer_avatar_no_avatar(
        self, real_stash_processor, stash_cleanup_tracker
    ):
        """Test _update_performer_avatar when account has no avatar.

        TRUE INTEGRATION TEST: Creates real performer, verifies avatar update skipped.
        """
        client = real_stash_processor.context.client
        async with stash_cleanup_tracker(client) as cleanup:
            account = AccountFactory.build(username="no_avatar_user_test")

            # Create a real performer in Stash
            test_performer = Performer(
                name="no_avatar_user_test",
                urls=["https://fansly.com/no_avatar_user_test"],
            )
            created_performer = await client.create_performer(test_performer)
            cleanup["performers"].append(created_performer.id)

            # Store original image_path (should be None or default)
            original_image_path = created_performer.image_path

            # Call _update_performer_avatar - no longer accepts session param
            try:
                with capture_graphql_calls(client) as calls:
                    await real_stash_processor._update_performer_avatar(
                        account, created_performer
                    )
            finally:
                dump_graphql_calls(calls, "update_performer_avatar_no_avatar")

            # Fetch performer again to verify image_path unchanged
            refreshed_performer = await client.find_performer(created_performer.id)

            # Verify no avatar update occurred
            assert refreshed_performer.image_path == original_image_path
