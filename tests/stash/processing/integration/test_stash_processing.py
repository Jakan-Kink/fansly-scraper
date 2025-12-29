"""Integration tests for StashProcessing.

This module tests the StashProcessing class using real database fixtures
and factory-based test data. These are TRUE integration tests that make
real GraphQL calls to a Stash instance.
"""

import time

import pytest
from sqlalchemy import select
from stash_graphql_client.types import Performer, Studio

from metadata import Account
from tests.fixtures.metadata.metadata_factories import AccountFactory
from tests.fixtures.stash.stash_integration_fixtures import capture_graphql_calls


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
        assert hasattr(real_stash_processor, "process_creator_posts")
        assert hasattr(real_stash_processor, "process_creator_messages")

    @pytest.mark.asyncio
    async def test_find_account_by_id(
        self,
        factory_session,
        real_stash_processor,
        test_database_sync,
        stash_cleanup_tracker,
    ):
        """Test _find_account method using creator_id with real database."""
        # Create a real account in the database
        _account = AccountFactory(id=12345, username="test_user")
        factory_session.commit()

        # Setup state to search by ID
        real_stash_processor.state.creator_id = "12345"
        real_stash_processor.state.creator_name = None

        # Use real async session from database
        async with test_database_sync.async_session_scope() as async_session:
            result = await real_stash_processor._find_account(session=async_session)

        # Verify result
        assert result is not None
        assert result.id == 12345
        assert result.username == "test_user"

    @pytest.mark.asyncio
    async def test_find_account_by_name(
        self,
        factory_session,
        real_stash_processor,
        test_database_sync,
        stash_cleanup_tracker,
    ):
        """Test _find_account method using creator_name with real database."""
        # Create a real account in the database
        _account = AccountFactory(username="test_creator")
        factory_session.commit()

        # Setup state to search by name
        real_stash_processor.state.creator_id = None
        real_stash_processor.state.creator_name = "test_creator"

        # Use real async session from database
        async with test_database_sync.async_session_scope() as async_session:
            result = await real_stash_processor._find_account(session=async_session)

        # Verify result
        assert result is not None
        assert result.username == "test_creator"

    @pytest.mark.asyncio
    async def test_find_account_not_found(
        self,
        factory_session,
        real_stash_processor,
        test_database_sync,
        stash_cleanup_tracker,
    ):
        """Test _find_account method when account not found."""
        # Setup state for non-existent account
        real_stash_processor.state.creator_id = "99999"
        real_stash_processor.state.creator_name = None

        # Use real async session - no account exists so will return None
        async with test_database_sync.async_session_scope() as async_session:
            result = await real_stash_processor._find_account(session=async_session)

        # Verify result is None
        assert result is None

    @pytest.mark.asyncio
    async def test_find_existing_performer(
        self, factory_session, real_stash_processor, stash_cleanup_tracker
    ):
        """Test _find_existing_performer method with real account.

        TRUE INTEGRATION TEST: Creates real performer in Stash, verifies lookup works.
        """
        async with stash_cleanup_tracker(
            real_stash_processor.context.client
        ) as cleanup:
            # Create a real performer in Stash
            test_performer = Performer(
                id="new",
                name="performer_user_integration_test",
                urls=["https://fansly.com/performer_user_integration_test"],
            )
            created_performer = (
                await real_stash_processor.context.client.create_performer(
                    test_performer
                )
            )
            cleanup["performers"].append(created_performer.id)

            # Create a real account with the performer's stash_id
            account = AccountFactory(
                username="performer_user_integration_test",
                stash_id=int(created_performer.id),
            )
            factory_session.commit()

            # Test finding by stash_id - should find the performer we just created
            performer = await real_stash_processor._find_existing_performer(account)

            # Verify result
            assert performer is not None
            assert performer.id == created_performer.id
            assert performer.name == "performer_user_integration_test"

    @pytest.mark.asyncio
    async def test_find_existing_performer_by_username(
        self, factory_session, real_stash_processor, stash_cleanup_tracker
    ):
        """Test _find_existing_performer finds by username when no stash_id.

        TRUE INTEGRATION TEST: Creates real performer, verifies username lookup.
        """
        async with stash_cleanup_tracker(
            real_stash_processor.context.client
        ) as cleanup:
            # Create a real performer in Stash with a unique name
            test_performer = Performer(
                id="new",
                name="new_performer_by_username_test",
                urls=["https://fansly.com/new_performer_by_username_test"],
            )
            created_performer = (
                await real_stash_processor.context.client.create_performer(
                    test_performer
                )
            )
            cleanup["performers"].append(created_performer.id)

            # Create a real account WITHOUT stash_id (to force username lookup)
            account = AccountFactory(
                username="new_performer_by_username_test", stash_id=None
            )
            factory_session.commit()

            # Test finding by username - should find the performer by name
            performer = await real_stash_processor._find_existing_performer(account)

            # Verify result
            assert performer is not None
            assert performer.name == "new_performer_by_username_test"
            assert performer.id == created_performer.id

    @pytest.mark.asyncio
    async def test_find_existing_studio(
        self, factory_session, real_stash_processor, stash_cleanup_tracker
    ):
        """Test _find_existing_studio method with real account when studio exists.

        TRUE INTEGRATION TEST: Creates real studio in Stash, verifies lookup finds it.
        """
        async with stash_cleanup_tracker(
            real_stash_processor.context.client
        ) as cleanup:
            # First find or create the Fansly (network) parent studio
            fansly_result = await real_stash_processor.context.client.find_studios(
                q="Fansly (network)"
            )
            if fansly_result.count == 0:
                fansly_parent = Studio(
                    id="new",
                    name="Fansly (network)",
                    urls=["https://fansly.com"],
                )
                fansly_parent = await real_stash_processor.context.client.create_studio(
                    fansly_parent
                )
                cleanup["studios"].append(fansly_parent.id)
            else:
                # Using Pydantic models from stash-graphql-client
                fansly_parent = fansly_result.studios[0]

            # Create a real creator studio in Stash BEFORE calling _find_existing_studio
            creator_studio = Studio(
                id="new",
                name="studio_exists_test (Fansly)",
                urls=["https://fansly.com/studio_exists_test"],
                parent_studio=fansly_parent,
            )
            created_studio = await real_stash_processor.context.client.create_studio(
                creator_studio
            )
            cleanup["studios"].append(created_studio.id)

            # Create a real account with matching username
            account = AccountFactory(username="studio_exists_test")
            factory_session.commit()

            # Test finding existing studio - should find the one we just created
            studio = await real_stash_processor._find_existing_studio(account)

            # Verify result - should return existing creator studio, NOT create a new one
            assert studio is not None
            assert studio.name == f"{account.username} (Fansly)"
            assert studio.id == created_studio.id  # Same ID means it found existing

    @pytest.mark.asyncio
    async def test_find_existing_studio_creates_new(
        self, factory_session, real_stash_processor, stash_cleanup_tracker
    ):
        """Test _find_existing_studio creates new studio when not found.

        TRUE INTEGRATION TEST: Makes real GraphQL calls to Stash instance.
        Uses stash_cleanup_tracker to clean up created studio after test.
        """
        async with stash_cleanup_tracker(
            real_stash_processor.context.client, auto_capture=False
        ) as cleanup:
            # Create a real account with unique username to ensure studio doesn't exist
            unique_id = int(time.time() * 1000) % 1000000  # Last 6 digits of timestamp
            account = AccountFactory(username=f"new_studio_creator_{unique_id}")
            factory_session.commit()

            # Capture GraphQL calls while making real API calls
            with capture_graphql_calls(real_stash_processor.context.client) as calls:
                # Call the real method - this will make actual GraphQL calls to Stash
                # Expected flow:
                # 1. Find "Fansly (network)" studio (should exist in test Stash)
                # 2. Search for "new_studio_creator (Fansly)" (won't exist)
                # 3. Create "new_studio_creator (Fansly)" studio
                studio = await real_stash_processor._find_existing_studio(account)

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
            found_studio = await real_stash_processor.context.client.find_studios(
                q=f"{account.username} (Fansly)"
            )
            assert found_studio.count == 1
            assert found_studio.studios[0].id == studio.id

            # Verify GraphQL call sequence (permanent assertion with request/response data)
            assert len(calls) == 3, (
                f"Expected exactly 3 GraphQL calls, got {len(calls)}"
            )

            # Call 0: findStudios for Fansly (network)
            assert "findStudios" in calls[0]["query"]
            assert calls[0]["variables"]["filter"]["q"] == "Fansly (network)"
            assert "findStudios" in calls[0]["result"]

            # Call 1: findStudios for creator studio
            assert "findStudios" in calls[1]["query"]
            assert (
                calls[1]["variables"]["filter"]["q"] == f"{account.username} (Fansly)"
            )
            assert "findStudios" in calls[1]["result"]
            assert (
                calls[1]["result"]["findStudios"]["count"] == 0
            )  # Should not exist yet

            # Call 2: studioCreate
            assert "studioCreate" in calls[2]["query"]
            assert (
                calls[2]["variables"]["input"]["name"] == f"{account.username} (Fansly)"
            )
            # Note: StudioCreateInput uses 'urls' (plural), not 'url' (singular)
            assert calls[2]["variables"]["input"]["urls"] == [
                f"https://fansly.com/{account.username}"
            ]
            assert "studioCreate" in calls[2]["result"]
            assert calls[2]["result"]["studioCreate"]["id"] == studio.id
            assert (
                calls[2]["result"]["studioCreate"]["name"]
                == f"{account.username} (Fansly)"
            )

            # Manual tracking after validation (earns opt-out qualification)
            if studio and studio.id != "new":
                cleanup["studios"].append(studio.id)

    @pytest.mark.asyncio
    async def test_update_performer_avatar_no_avatar(
        self, factory_session, real_stash_processor, stash_cleanup_tracker, session
    ):
        """Test _update_performer_avatar when account has no avatar.

        TRUE INTEGRATION TEST: Creates real performer, verifies avatar update skipped.
        """
        async with stash_cleanup_tracker(
            real_stash_processor.context.client
        ) as cleanup:
            # Create a real account WITHOUT avatar (no Media associated)
            account = AccountFactory(username="no_avatar_user_test")
            factory_session.commit()

            # Refresh account in async session
            result = await session.execute(
                select(Account).where(Account.id == account.id)
            )
            account = result.scalar_one()

            # Create a real performer in Stash
            test_performer = Performer(
                id="new",
                name="no_avatar_user_test",
                urls=["https://fansly.com/no_avatar_user_test"],
            )
            created_performer = (
                await real_stash_processor.context.client.create_performer(
                    test_performer
                )
            )
            cleanup["performers"].append(created_performer.id)

            # Store original image_path (should be None or default)
            original_image_path = created_performer.image_path

            # Call _update_performer_avatar - should return early since no avatar
            await real_stash_processor._update_performer_avatar(
                account, created_performer, session=session
            )

            # Fetch performer again to verify image_path unchanged
            refreshed_performer = (
                await real_stash_processor.context.client.find_performer(
                    created_performer.id
                )
            )

            # Verify no avatar update occurred
            assert refreshed_performer.image_path == original_image_path
