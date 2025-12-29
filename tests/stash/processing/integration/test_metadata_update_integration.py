"""Integration tests for _update_stash_metadata with real infrastructure.

These tests use:
1. Real PostgreSQL database with FactoryBoy factories
2. Real Docker Stash instance via StashClient (NO MOCKS)
3. End-to-end testing of metadata update workflow

This differs from unit tests which mock everything, and from other integration tests
which mock the Stash API. These tests verify the full workflow works with real services.

IMPORTANT NOTES:
- Images CANNOT be created via Stash API (no ImageCreateInput exists)
- Images must be pre-scanned into Stash from filesystem
- Scenes CAN be created via API (SceneCreateInput exists)
- These tests focus on Scene objects which can be fully tested end-to-end
"""

from datetime import UTC, datetime

import pytest
from stash_graphql_client.types import Scene

from tests.fixtures.metadata.metadata_factories import AccountFactory, PostFactory


class TestMetadataUpdateIntegration:
    """Integration tests for _update_stash_metadata with real infrastructure."""

    @pytest.mark.asyncio
    async def test_update_stash_metadata_find_existing_image(
        self, media_mixin, factory_session, stash_client, stash_cleanup_tracker
    ):
        """Test _update_stash_metadata with existing Image from Stash.

        NOTE: This test requires that Stash has at least one scanned image.
        Images cannot be created via API - they must be scanned from filesystem.

        This test:
        1. Creates a real Account and Post in PostgreSQL
        2. Finds an existing Image in Stash (if available)
        3. Updates the Image metadata using _update_stash_metadata
        4. Verifies the Image was updated in Stash
        5. Restores original metadata

        Note: stash_cleanup_tracker is included to satisfy the enforcement hook in conftest.py,
        even though this test doesn't create new objects (only modifies existing ones).
        """
        async with stash_cleanup_tracker(stash_client) as cleanup:
            # Create real account in database
            account = AccountFactory(username="integration_test_user")

            # Create real post in database
            post = PostFactory(
                accountId=account.id,
                content="Integration test post #test #integration",
            )

            # Try to find an existing image in Stash
            results = await stash_client.find_images(filter_={"per_page": 1})

            if not results or results.count == 0:
                pytest.skip("No images found in Stash - cannot test Image update")

            # Get the Image object (Pydantic returns native objects, no conversion needed)
            image = results.images[0]

            # Save original metadata for restoration
            original_title = image.title
            original_code = image.code
            original_date = image.date
            original_details = image.details
            original_urls = image.urls.copy() if image.urls else []

            # Call the method under test
            # NOTE: This will create a Studio via process_creator_studio()
            await media_mixin._update_stash_metadata(
                stash_obj=image,
                item=post,
                account=account,
                media_id="media_12345",
                is_preview=False,
            )

            # Track the created studio for cleanup
            studio_name = f"{account.username} (Fansly)"
            studio_results = await stash_client.find_studios(q=studio_name)
            if studio_results.count > 0:
                studio = studio_results.studios[0]
                cleanup["studios"].append(studio.id)

            # Verify metadata was set correctly
            assert image.title is not None
            assert image.details == post.content
            assert image.date == post.createdAt.strftime("%Y-%m-%d")
            assert image.code == "media_12345"
            assert f"https://fansly.com/post/{post.id}" in image.urls

            # Restore original metadata
            image.title = original_title
            image.code = original_code
            image.date = original_date
            image.details = original_details
            image.urls = original_urls
            await image.save(stash_client)

            # Automatic cleanup of studio happens when exiting context

    @pytest.mark.asyncio
    async def test_update_stash_metadata_real_scene(
        self,
        media_mixin,
        session_sync,
        factory_session,
        stash_client,
        stash_cleanup_tracker,
        enable_scene_creation,
    ):
        """Test _update_stash_metadata with real Scene object created in Stash.

        This test:
        1. Creates a real Account and Post in PostgreSQL (using UUID DB session for cleanup)
        2. Creates a real Scene in Stash via API
        3. Updates the Scene metadata using _update_stash_metadata
        4. Verifies the Scene was updated in Stash
        5. Cleans up by deleting the Scene from Stash (automatic via cleanup tracker)
        """
        # Create real account in database
        account = AccountFactory(username="integration_scene_user")
        session_sync.commit()

        # Create real post in database
        post = PostFactory(
            accountId=account.id,
            content="Integration test scene #video #test",
        )
        session_sync.commit()

        async with stash_cleanup_tracker(stash_client) as cleanup:
            # Create a real Scene in Stash via API
            scene = Scene(
                id="new",  # Signal to to_input() this is a new object
                title="Test Scene Before Update",
                urls=["https://example.com/original_scene"],
                organized=False,
            )
            created_scene = await stash_client.create_scene(scene)
            cleanup["scenes"].append(created_scene.id)

            # Fetch the scene to ensure we have the full object
            scene = await stash_client.find_scene(created_scene.id)

            # Call the method under test
            # NOTE: This will create a Studio via process_creator_studio()
            await media_mixin._update_stash_metadata(
                stash_obj=scene,
                item=post,
                account=account,
                media_id="media_67890",
                is_preview=False,
            )

            # Track the created studio for cleanup
            studio_name = f"{account.username} (Fansly)"
            studio_results = await stash_client.find_studios(q=studio_name)
            if studio_results.count > 0:
                studio = studio_results.studios[0]
                cleanup["studios"].append(studio.id)

            # Verify metadata was set correctly
            assert scene.title is not None
            assert scene.details == post.content
            assert scene.date == post.createdAt.strftime("%Y-%m-%d")
            assert scene.code == "media_67890"
            assert f"https://fansly.com/post/{post.id}" in scene.urls

            # Verify the changes were persisted to Stash
            # (the save() call happens inside _update_stash_metadata)
            fetched_scene = await stash_client.find_scene(created_scene.id)
            assert fetched_scene.code == "media_67890"
            assert fetched_scene.details == post.content

            # Automatic cleanup of scene happens when exiting context

    @pytest.mark.asyncio
    async def test_update_stash_metadata_preserves_earliest_date(
        self,
        media_mixin,
        session_sync,
        factory_session,
        stash_client,
        stash_cleanup_tracker,
        enable_scene_creation,
    ):
        """Test that _update_stash_metadata preserves the earliest date.

        This verifies the production behavior:
        - When a Scene has an earlier date than the new item, don't update
        - When a Scene has a later date than the new item, update to earlier date
        """
        # Create real account
        account = AccountFactory(username="date_test_user")
        session_sync.commit()

        # Create earlier post
        earlier_post = PostFactory(
            accountId=account.id,
            content="Earlier post",
            createdAt=datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC),
        )
        session_sync.commit()

        # Create later post
        later_post = PostFactory(
            accountId=account.id,
            content="Later post",
            createdAt=datetime(2024, 6, 1, 12, 0, 0, tzinfo=UTC),
        )
        session_sync.commit()

        async with stash_cleanup_tracker(stash_client) as cleanup:
            # Create scene with earlier date in Stash
            scene = Scene(
                id="new",  # Signal to to_input() this is a new object
                title="Original Title",
                date="2024-01-01",  # Earlier date
                code="original_code",
                organized=False,
                urls=["https://example.com/original"],
            )
            created_scene = await stash_client.create_scene(scene)
            cleanup["scenes"].append(created_scene.id)

            # Fetch the scene
            scene = await stash_client.find_scene(created_scene.id)

            # Try to update with later post - should NOT update
            await media_mixin._update_stash_metadata(
                stash_obj=scene,
                item=later_post,
                account=account,
                media_id="media_later",
                is_preview=False,
            )

            # Verify metadata was NOT changed (kept earliest)
            assert scene.title == "Original Title"
            assert scene.date == "2024-01-01"
            assert scene.code == "original_code"

            # Now create a scene with later date
            scene.date = "2024-05-01"  # Later date
            await scene.save(stash_client)

            # Fetch updated scene
            scene = await stash_client.find_scene(created_scene.id)

            # Update with earlier post - should UPDATE
            await media_mixin._update_stash_metadata(
                stash_obj=scene,
                item=earlier_post,
                account=account,
                media_id="media_earlier",
                is_preview=False,
            )

            # Verify metadata WAS changed (to earlier date)
            assert scene.title != "Original Title"
            assert scene.date == "2024-01-01"  # Updated to earlier date
            assert scene.code == "media_earlier"  # Updated code

            # Track the created studio for cleanup
            # NOTE: Studio may have been created on first or second _update_stash_metadata call
            studio_name = f"{account.username} (Fansly)"
            studio_results = await stash_client.find_studios(q=studio_name)
            if studio_results.count > 0:
                studio = studio_results.studios[0]
                cleanup["studios"].append(studio.id)

            # Automatic cleanup of scene happens when exiting context

    @pytest.mark.asyncio
    async def test_update_stash_metadata_skips_organized(
        self,
        media_mixin,
        session_sync,
        factory_session,
        stash_client,
        stash_cleanup_tracker,
        enable_scene_creation,
    ):
        """Test that _update_stash_metadata skips organized objects.

        Organized objects should not be modified.
        """
        # Create real account and post
        account = AccountFactory(username="organized_test_user")
        post = PostFactory(accountId=account.id, content="Test post")
        session_sync.commit()

        async with stash_cleanup_tracker(stash_client) as cleanup:
            # Create organized scene in Stash
            scene = Scene(
                id="new",  # Signal to to_input() this is a new object
                title="Original Organized Title",
                date="2024-03-01",
                code="organized_code",
                organized=True,  # Already organized
                urls=["https://example.com/organized"],
            )
            created_scene = await stash_client.create_scene(scene)
            cleanup["scenes"].append(created_scene.id)

            # Fetch the scene
            scene = await stash_client.find_scene(created_scene.id)

            # Try to update - should be skipped
            await media_mixin._update_stash_metadata(
                stash_obj=scene,
                item=post,
                account=account,
                media_id="new_media_id",
                is_preview=False,
            )

            # Track the created studio for cleanup
            studio_name = f"{account.username} (Fansly)"
            studio_results = await stash_client.find_studios(q=studio_name)
            if studio_results.count > 0:
                studio = studio_results.studios[0]
                cleanup["studios"].append(studio.id)

            # Verify metadata was NOT changed
            assert scene.title == "Original Organized Title"
            assert scene.code == "organized_code"

            # Verify nothing was saved (scene.save was not called)
            fetched_scene = await stash_client.find_scene(created_scene.id)
            assert fetched_scene.title == "Original Organized Title"
            assert fetched_scene.code == "organized_code"
