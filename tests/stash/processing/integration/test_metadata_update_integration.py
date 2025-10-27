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

from stash.processing.mixins.media import MediaProcessingMixin
from stash.types import Scene
from tests.fixtures.metadata_factories import AccountFactory, PostFactory


class TestMediaMixin(MediaProcessingMixin):
    """Test class that implements MediaProcessingMixin for integration testing."""

    def __init__(self, context, database):
        """Initialize test mixin with real context and database."""
        self.context = context
        self.database = database
        self.log = None  # Not used in integration tests


@pytest.mark.skip(
    reason="Requires real Docker Stash instance - run manually when Stash is available"
)
class TestMetadataUpdateIntegration:
    """Integration tests for _update_stash_metadata with real infrastructure."""

    @pytest.fixture
    def media_mixin(self, stash_context, test_database_sync):
        """Create media mixin with real Stash context and database."""
        return TestMediaMixin(context=stash_context, database=test_database_sync)

    @pytest.mark.asyncio
    async def test_update_stash_metadata_find_existing_image(
        self, media_mixin, session_sync, factory_session, stash_client
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
        """
        # Create real account in database
        account = AccountFactory(username="integration_test_user")
        session_sync.commit()

        # Create real post in database
        post = PostFactory(
            accountId=account.id,
            content="Integration test post #test #integration",
        )
        session_sync.commit()

        try:
            # Try to find an existing image in Stash
            results = await stash_client.find_images(filter_={"per_page": 1})

            if not results or results.count == 0:
                pytest.skip("No images found in Stash - cannot test Image update")

            image = results.images[0]

            # Save original metadata for restoration
            original_title = image.title
            original_code = image.code
            original_date = image.date
            original_details = image.details
            original_urls = image.urls.copy() if image.urls else []

            # Call the method under test
            await media_mixin._update_stash_metadata(
                stash_obj=image,
                item=post,
                account=account,
                media_id="media_12345",
                is_preview=False,
            )

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

        finally:
            # Cleanup: Delete test objects from database
            session_sync.delete(post)
            session_sync.delete(account)
            session_sync.commit()

    @pytest.mark.asyncio
    async def test_update_stash_metadata_real_scene(
        self,
        media_mixin,
        session_sync,
        factory_session,
        stash_client,
        enable_scene_creation,
    ):
        """Test _update_stash_metadata with real Scene object created in Stash.

        This test:
        1. Creates a real Account and Post in PostgreSQL
        2. Creates a real Scene in Stash via API
        3. Updates the Scene metadata using _update_stash_metadata
        4. Verifies the Scene was updated in Stash
        5. Cleans up by deleting the Scene from Stash
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

        scene_id = None
        try:
            # Create a real Scene in Stash via API
            scene = Scene(
                title="Test Scene Before Update",
                urls=["https://example.com/original_scene"],
                organized=False,
            )
            created_scene = await stash_client.create_scene(scene)
            scene_id = created_scene.id

            # Fetch the scene to ensure we have the full object
            scene = await stash_client.find_scene(scene_id)

            # Call the method under test
            await media_mixin._update_stash_metadata(
                stash_obj=scene,
                item=post,
                account=account,
                media_id="media_67890",
                is_preview=False,
            )

            # Verify metadata was set correctly
            assert scene.title is not None
            assert scene.details == post.content
            assert scene.date == post.createdAt.strftime("%Y-%m-%d")
            assert scene.code == "media_67890"
            assert f"https://fansly.com/post/{post.id}" in scene.urls

            # Verify the changes were persisted to Stash
            # (the save() call happens inside _update_stash_metadata)
            fetched_scene = await stash_client.find_scene(scene_id)
            assert fetched_scene.code == "media_67890"
            assert fetched_scene.details == post.content

        finally:
            # Cleanup: Delete scene from Stash
            if scene_id:
                await stash_client.execute(
                    """
                    mutation DeleteScene($id: ID!) {
                        sceneDestroy(input: { id: $id })
                    }
                    """,
                    {"id": scene_id},
                )

            # Cleanup: Delete test objects from database
            session_sync.delete(post)
            session_sync.delete(account)
            session_sync.commit()

    @pytest.mark.asyncio
    async def test_update_stash_metadata_preserves_earliest_date(
        self,
        media_mixin,
        session_sync,
        factory_session,
        stash_client,
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

        scene_id = None
        try:
            # Create scene with earlier date in Stash
            scene = Scene(
                title="Original Title",
                date="2024-01-01",  # Earlier date
                code="original_code",
                organized=False,
                urls=["https://example.com/original"],
            )
            created_scene = await stash_client.create_scene(scene)
            scene_id = created_scene.id

            # Fetch the scene
            scene = await stash_client.find_scene(scene_id)

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
            scene = await stash_client.find_scene(scene_id)

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

        finally:
            # Cleanup: Delete scene from Stash
            if scene_id:
                await stash_client.execute(
                    """
                    mutation DeleteScene($id: ID!) {
                        sceneDestroy(input: { id: $id })
                    }
                    """,
                    {"id": scene_id},
                )

            # Cleanup
            session_sync.delete(earlier_post)
            session_sync.delete(later_post)
            session_sync.delete(account)
            session_sync.commit()

    @pytest.mark.asyncio
    async def test_update_stash_metadata_skips_organized(
        self,
        media_mixin,
        session_sync,
        factory_session,
        stash_client,
        enable_scene_creation,
    ):
        """Test that _update_stash_metadata skips organized objects.

        Organized objects should not be modified.
        """
        # Create real account and post
        account = AccountFactory(username="organized_test_user")
        post = PostFactory(accountId=account.id, content="Test post")
        session_sync.commit()

        scene_id = None
        try:
            # Create organized scene in Stash
            scene = Scene(
                title="Original Organized Title",
                date="2024-03-01",
                code="organized_code",
                organized=True,  # Already organized
                urls=["https://example.com/organized"],
            )
            created_scene = await stash_client.create_scene(scene)
            scene_id = created_scene.id

            # Fetch the scene
            scene = await stash_client.find_scene(scene_id)

            # Try to update - should be skipped
            await media_mixin._update_stash_metadata(
                stash_obj=scene,
                item=post,
                account=account,
                media_id="new_media_id",
                is_preview=False,
            )

            # Verify metadata was NOT changed
            assert scene.title == "Original Organized Title"
            assert scene.code == "organized_code"

            # Verify nothing was saved (scene.save was not called)
            fetched_scene = await stash_client.find_scene(scene_id)
            assert fetched_scene.title == "Original Organized Title"
            assert fetched_scene.code == "organized_code"

        finally:
            # Cleanup: Delete scene from Stash
            if scene_id:
                await stash_client.execute(
                    """
                    mutation DeleteScene($id: ID!) {
                        sceneDestroy(input: { id: $id })
                    }
                    """,
                    {"id": scene_id},
                )

            # Cleanup
            session_sync.delete(post)
            session_sync.delete(account)
            session_sync.commit()
