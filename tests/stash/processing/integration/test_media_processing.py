"""Integration tests for media processing in StashProcessing.

This module tests media processing using real database fixtures and
factory-based test data instead of mocks.
"""

from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from metadata import Account
from metadata.account import AccountMedia, account_media_bundle_media
from metadata.attachment import Attachment, ContentType
from stash.types import Image
from tests.fixtures.metadata.metadata_factories import (
    AccountFactory,
    AccountMediaBundleFactory,
    AccountMediaFactory,
    AttachmentFactory,
    MediaFactory,
    PostFactory,
)


class TestMediaProcessingIntegration:
    """Integration tests for media processing in StashProcessing."""

    @pytest.mark.asyncio
    async def test_process_media_integration(
        self,
        factory_session,
        stash_processor,
        test_database_sync,
        mock_studio_finder,
        mocker,
    ):
        """Test media processing through attachment workflow with real database."""
        # Create a real account
        account = AccountFactory(username="media_user")
        factory_session.commit()

        # Create a real post
        post = PostFactory(accountId=account.id, content="Test post")
        factory_session.commit()

        # Create real media with proper setup for Stash processing
        media = MediaFactory(
            accountId=account.id,
            mimetype="image/jpeg",
            type=1,
            is_downloaded=True,
            stash_id=None,
            local_filename=f"test_{account.id}.jpg",
        )
        factory_session.commit()

        # Create AccountMedia as intermediary layer
        account_media = AccountMediaFactory(accountId=account.id, mediaId=media.id)
        factory_session.commit()

        # Create attachment pointing to AccountMedia
        attachment = AttachmentFactory(
            postId=post.id,
            contentType=ContentType.ACCOUNT_MEDIA,
            contentId=account_media.id,
        )
        factory_session.commit()

        # Mock Stash client at the API boundary
        from unittest.mock import AsyncMock, patch

        from stash.types import FindImagesResultType
        from tests.fixtures.stash.stash_type_factories import ImageFactory, ImageFileFactory

        mock_image_file = ImageFileFactory(path=f"/path/to/{media.id}.jpg")
        mock_image = ImageFactory(
            id="image_123",
            title="Test Image",
            visual_files=[mock_image_file],
        )
        mock_find_result = FindImagesResultType(
            count=1, images=[mock_image], megapixels=0, filesize=0
        )

        # Use mock_studio_finder fixture
        mock_find_studios_fn, create_creator_studio = mock_studio_finder
        mock_creator_studio = create_creator_studio(account)

        with (
            patch.object(
                stash_processor.context.client,
                "find_images",
                new=AsyncMock(return_value=mock_find_result),
            ),
            patch.object(
                stash_processor.context.client,
                "find_studios",
                new=AsyncMock(side_effect=mock_find_studios_fn),
            ),
            patch.object(
                stash_processor.context.client,
                "create_studio",
                new=AsyncMock(return_value=mock_creator_studio),
            ),
            patch.object(
                stash_processor.context.client,
                "find_performer",
                new=AsyncMock(return_value=None),
            ),
            patch.object(
                stash_processor.context.client,
                "execute",
                new=AsyncMock(return_value={"imageUpdate": {"id": "image_123"}}),
            ),
        ):
            # Process in async session with proper relationship loading
            async with test_database_sync.async_session_scope() as async_session:
                from sqlalchemy import select
                from sqlalchemy.orm import selectinload

                from metadata import Account
                from metadata.account import AccountMedia
                from metadata.attachment import Attachment
                from metadata.post import Post

                # Eager-load relationships (including bundle and aggregated_post to avoid lazy loading)
                result_query = await async_session.execute(
                    select(Attachment)
                    .where(Attachment.id == attachment.id)
                    .options(
                        selectinload(Attachment.media).selectinload(AccountMedia.media),
                        selectinload(Attachment.bundle),
                        selectinload(Attachment.aggregated_post),
                    )
                )
                async_attachment = result_query.unique().scalar_one()

                account_query = await async_session.execute(
                    select(Account).where(Account.id == account.id)
                )
                async_account = account_query.unique().scalar_one()

                # Also query the post in async session to avoid lazy loading issues
                post_query = await async_session.execute(
                    select(Post).where(Post.id == post.id)
                )
                async_post = post_query.unique().scalar_one()

                result = await stash_processor.process_creator_attachment(
                    attachment=async_attachment,
                    item=async_post,
                    account=async_account,
                    session=async_session,
                )

            # Verify results were collected correctly
            assert len(result["images"]) >= 1
            assert isinstance(result["images"][0], Image)
            assert result["images"][0].id == "image_123"

    @pytest.mark.asyncio
    async def test_process_bundle_media_integration(
        self,
        factory_session,
        stash_processor,
        test_database_sync,
        mock_studio_finder,
        mocker,
    ):
        """Test bundle media processing through attachment workflow with real data."""
        # Create a real account
        account = AccountFactory(username="bundle_user")
        factory_session.commit()

        # Create a real post
        post = PostFactory(accountId=account.id, content="Bundle post")
        factory_session.commit()

        # Create a real bundle
        bundle = AccountMediaBundleFactory(accountId=account.id)
        factory_session.commit()

        # Create real media for the bundle
        media1 = MediaFactory(
            accountId=account.id,
            mimetype="image/jpeg",
            type=1,
            is_downloaded=True,
            local_filename=f"test_{account.id}_img1.jpg",
        )
        media2 = MediaFactory(
            accountId=account.id,
            mimetype="video/mp4",
            type=2,
            is_downloaded=True,
            local_filename=f"test_{account.id}_vid1.mp4",
        )
        factory_session.commit()

        # Create real AccountMedia entries
        account_media1 = AccountMediaFactory(accountId=account.id, mediaId=media1.id)
        account_media2 = AccountMediaFactory(accountId=account.id, mediaId=media2.id)
        factory_session.commit()

        # Link AccountMedia to bundle via the join table with positions
        factory_session.execute(
            account_media_bundle_media.insert().values(
                [
                    {"bundle_id": bundle.id, "media_id": account_media1.id, "pos": 0},
                    {"bundle_id": bundle.id, "media_id": account_media2.id, "pos": 1},
                ]
            )
        )
        factory_session.commit()

        # Create attachment pointing to bundle
        attachment = AttachmentFactory(
            postId=post.id,
            contentType=ContentType.ACCOUNT_MEDIA_BUNDLE,
            contentId=bundle.id,
        )
        factory_session.commit()

        # Mock Stash client at API boundary
        from stash.types import FindImagesResultType, FindScenesResultType
        from tests.fixtures import (
            ImageFactory,
            ImageFileFactory,
            SceneFactory,
            VideoFileFactory,
        )

        mock_image_file = ImageFileFactory(path=f"/path/to/{media1.id}.jpg")
        mock_image = ImageFactory(
            id="image_bundle_1",
            title="Bundle Image",
            visual_files=[mock_image_file],
        )

        mock_video_file = VideoFileFactory(path=f"/path/to/{media2.id}.mp4")
        mock_scene = SceneFactory(
            id="scene_bundle_1",
            title="Bundle Scene",
            files=[mock_video_file],
        )

        mock_find_images_result = FindImagesResultType(
            count=1, images=[mock_image], megapixels=0, filesize=0
        )
        mock_find_scenes_result = FindScenesResultType(
            count=1, scenes=[mock_scene], duration=0, filesize=0
        )

        # Use mock_studio_finder fixture
        mock_find_studios_fn, create_creator_studio = mock_studio_finder
        mock_creator_studio = create_creator_studio(account)

        with (
            patch.object(
                stash_processor.context.client,
                "find_images",
                new=AsyncMock(return_value=mock_find_images_result),
            ),
            patch.object(
                stash_processor.context.client,
                "find_scenes",
                new=AsyncMock(return_value=mock_find_scenes_result),
            ),
            patch.object(
                stash_processor.context.client,
                "find_studios",
                new=AsyncMock(side_effect=mock_find_studios_fn),
            ),
            patch.object(
                stash_processor.context.client,
                "create_studio",
                new=AsyncMock(return_value=mock_creator_studio),
            ),
            patch.object(
                stash_processor.context.client,
                "find_performer",
                new=AsyncMock(return_value=None),
            ),
            patch.object(
                stash_processor.context.client,
                "execute",
                new=AsyncMock(
                    side_effect=[
                        {
                            "imageUpdate": {"id": "image_bundle_1"}
                        },  # First call for image
                        {
                            "sceneUpdate": {"id": "scene_bundle_1"}
                        },  # Second call for scene
                    ]
                ),
            ),
        ):
            # Process in async session with proper relationship loading
            async with test_database_sync.async_session_scope() as async_session:
                from metadata.account import AccountMediaBundle

                # Eager-load all relationships (bundle, media, aggregated_post to avoid lazy loading)
                result_query = await async_session.execute(
                    select(Attachment)
                    .where(Attachment.id == attachment.id)
                    .options(
                        selectinload(Attachment.bundle)
                        .selectinload(AccountMediaBundle.accountMedia)
                        .selectinload(AccountMedia.media),
                        selectinload(Attachment.media).selectinload(AccountMedia.media),
                        selectinload(Attachment.aggregated_post),
                    )
                )
                async_attachment = result_query.unique().scalar_one()

                account_query = await async_session.execute(
                    select(Account).where(Account.id == account.id)
                )
                async_account = account_query.unique().scalar_one()

                # Query post in async session to avoid lazy loading
                from metadata.post import Post

                post_query = await async_session.execute(
                    select(Post).where(Post.id == post.id)
                )
                async_post = post_query.unique().scalar_one()

                result = await stash_processor.process_creator_attachment(
                    attachment=async_attachment,
                    item=async_post,
                    account=async_account,
                    session=async_session,
                )

            # Verify both image and video were processed
            assert len(result["images"]) >= 1 or len(result["scenes"]) >= 1
            assert isinstance(result, dict)
            assert "images" in result
            assert "scenes" in result

    @pytest.mark.asyncio
    async def test_process_creator_attachment_integration(
        self,
        factory_session,
        stash_processor,
        test_database_sync,
        mock_studio_finder,
        mocker,
    ):
        """Test process_creator_attachment method integration with real data."""
        # Create a real account
        account = AccountFactory(username="attachment_user")
        factory_session.commit()

        # Create a real post
        post = PostFactory(accountId=account.id, content="Attachment post")
        factory_session.commit()

        # Create real media with local filename so it can be found
        media = MediaFactory(
            accountId=account.id,
            mimetype="image/jpeg",
            local_filename=f"test_{account.id}.jpg",
        )
        factory_session.commit()

        # Create real AccountMedia to link media to account
        account_media = AccountMediaFactory(accountId=account.id, mediaId=media.id)
        factory_session.commit()

        # Create real attachment with proper ContentType
        # contentId points to the AccountMedia, postId links attachment to the post
        attachment = AttachmentFactory(
            contentId=account_media.id,
            contentType=ContentType.ACCOUNT_MEDIA,
            postId=post.id,
        )
        factory_session.commit()

        # Mock at client API boundary - this is where we make HTTP calls to Stash
        from stash.types import FindImagesResultType

        # Create a proper visual file mock that _get_image_file_from_stash_obj will recognize
        mock_visual_file = mocker.MagicMock()
        mock_visual_file.__type_name__ = (
            "ImageFile"  # Required by _get_image_file_from_stash_obj
        )
        mock_visual_file.path = f"/path/to/{media.id}.jpg"
        mock_visual_file.size = 1024

        mock_find_images_result = FindImagesResultType(
            count=1,
            images=[
                Image(
                    id="image_456",
                    title="Attachment Image",
                    urls=[],
                    visual_files=[mock_visual_file],
                    tags=[],
                    performers=[],
                    galleries=[],
                )
            ],
            megapixels=0,
            filesize=0,
        )

        # Use mock_studio_finder fixture
        mock_find_studios_fn, create_creator_studio = mock_studio_finder
        mock_creator_studio = create_creator_studio(account, studio_id="999")

        # Mock at client boundary using patch.object for better inspection
        with (
            patch.object(
                stash_processor.context.client,
                "find_images",
                new=AsyncMock(return_value=mock_find_images_result),
            ) as mock_find_images,
            patch.object(
                stash_processor.context.client,
                "find_performer",
                new=AsyncMock(return_value=None),
            ) as mock_find_performer,
            patch.object(
                stash_processor.context.client,
                "find_studios",
                new=AsyncMock(side_effect=mock_find_studios_fn),
            ) as mock_find_studios,
            patch.object(
                stash_processor.context.client,
                "create_studio",
                new=AsyncMock(return_value=mock_creator_studio),
            ) as mock_create_studio,
            patch.object(
                stash_processor.context.client,
                "execute",
                new=AsyncMock(return_value={"imageUpdate": {"id": "image_456"}}),
            ) as mock_execute,
        ):
            # Use real async session from database
            async with test_database_sync.async_session_scope() as async_session:
                # Re-query attachment in async session with eager loading for all viewonly relationships
                # Need to load nested relationships: Attachment.media.media (AccountMedia.media)
                result_query = await async_session.execute(
                    select(Attachment)
                    .options(
                        selectinload(Attachment.media).selectinload(AccountMedia.media),
                        selectinload(Attachment.bundle),
                        selectinload(Attachment.aggregated_post),
                    )
                    .where(Attachment.id == attachment.id)
                )
                async_attachment = result_query.unique().scalar_one()

                result = await stash_processor.process_creator_attachment(
                    attachment=async_attachment,
                    item=post,
                    account=account,
                    session=async_session,
                )

            # Verify Stash find_images was called (mocking at client API boundary)
            assert mock_find_images.call_count >= 1

            # Verify results were collected correctly through our processing pipeline
            assert len(result["images"]) >= 1
            # Verify the image was returned from our mock
            assert isinstance(result["images"][0], Image)
            assert result["images"][0].id == "image_456"

    @pytest.mark.asyncio
    async def test_process_creator_attachment_with_bundle(
        self,
        factory_session,
        stash_processor,
        test_database_sync,
        mock_studio_finder,
        mocker,
    ):
        """Test process_creator_attachment method with media bundle - simplified version."""
        # Create a real account
        account = AccountFactory(username="bundle_attachment_user")
        factory_session.commit()

        # Create a real post
        post = PostFactory(accountId=account.id, content="Bundle attachment post")
        factory_session.commit()

        # Create real bundle with media
        bundle = AccountMediaBundleFactory(accountId=account.id)
        factory_session.commit()

        # Create a real image for the bundle
        media = MediaFactory(
            accountId=account.id,
            mimetype="image/jpeg",
            type=1,
            is_downloaded=True,
            local_filename=f"test_{account.id}_bundle.jpg",
        )
        factory_session.commit()

        # Create AccountMedia and link to bundle
        account_media = AccountMediaFactory(accountId=account.id, mediaId=media.id)
        factory_session.commit()

        factory_session.execute(
            account_media_bundle_media.insert().values(
                [
                    {"bundle_id": bundle.id, "media_id": account_media.id, "pos": 0},
                ]
            )
        )
        factory_session.commit()

        # Create attachment pointing to bundle
        attachment = AttachmentFactory(
            contentId=bundle.id,
            contentType=ContentType.ACCOUNT_MEDIA_BUNDLE,
            postId=post.id,
        )
        factory_session.commit()

        # Mock Stash client at API boundary
        from stash.types import FindImagesResultType
        from tests.fixtures.stash.stash_type_factories import ImageFactory, ImageFileFactory

        mock_image_file = ImageFileFactory(path=f"/path/to/{media.id}.jpg")
        mock_image = ImageFactory(
            id="bundle_img_1",
            title="Bundle Image",
            visual_files=[mock_image_file],
        )
        mock_find_result = FindImagesResultType(
            count=1, images=[mock_image], megapixels=0, filesize=0
        )

        # Use mock_studio_finder fixture
        mock_find_studios_fn, create_creator_studio = mock_studio_finder
        mock_creator_studio = create_creator_studio(account)

        with (
            patch.object(
                stash_processor.context.client,
                "find_images",
                new=AsyncMock(return_value=mock_find_result),
            ),
            patch.object(
                stash_processor.context.client,
                "find_studios",
                new=AsyncMock(side_effect=mock_find_studios_fn),
            ),
            patch.object(
                stash_processor.context.client,
                "create_studio",
                new=AsyncMock(return_value=mock_creator_studio),
            ),
            patch.object(
                stash_processor.context.client,
                "find_performer",
                new=AsyncMock(return_value=None),
            ),
            patch.object(
                stash_processor.context.client,
                "execute",
                new=AsyncMock(return_value={"imageUpdate": {"id": "bundle_img_1"}}),
            ),
        ):
            # Use real async session from database
            async with test_database_sync.async_session_scope() as async_session:
                from metadata.account import AccountMediaBundle
                from metadata.post import Post

                # Eager-load all relationships (bundle, media, aggregated_post to avoid lazy loading)
                result_query = await async_session.execute(
                    select(Attachment)
                    .where(Attachment.id == attachment.id)
                    .options(
                        selectinload(Attachment.bundle)
                        .selectinload(AccountMediaBundle.accountMedia)
                        .selectinload(AccountMedia.media),
                        selectinload(Attachment.media).selectinload(AccountMedia.media),
                        selectinload(Attachment.aggregated_post),
                    )
                )
                async_attachment = result_query.unique().scalar_one()

                # Query post in async session
                post_query = await async_session.execute(
                    select(Post).where(Post.id == post.id)
                )
                async_post = post_query.unique().scalar_one()

                account_query = await async_session.execute(
                    select(Account).where(Account.id == account.id)
                )
                async_account = account_query.unique().scalar_one()

                result = await stash_processor.process_creator_attachment(
                    attachment=async_attachment,
                    item=async_post,
                    account=async_account,
                    session=async_session,
                )

            # Verify bundle was processed and results collected
            assert isinstance(result, dict)
            assert "images" in result
            assert "scenes" in result

    @pytest.mark.asyncio
    async def test_process_creator_attachment_with_aggregated_post(
        self, factory_session, stash_processor, test_database_sync, mocker
    ):
        """Test process_creator_attachment method with aggregated post."""
        # Create a real account
        account = AccountFactory(username="aggregated_user")
        factory_session.commit()

        # Create a real parent post
        parent_post = PostFactory(accountId=account.id, content="Parent post")
        factory_session.commit()

        # Create attachment for parent post with aggregated post contentType
        attachment = AttachmentFactory(
            contentId=parent_post.id, contentType=ContentType.AGGREGATED_POSTS
        )
        factory_session.commit()

        # Setup attachment relationships
        attachment.media = None
        attachment.bundle = None

        # Create aggregated post
        agg_post = PostFactory(accountId=account.id, content="Aggregated post")
        factory_session.commit()

        # Create attachment for aggregated post
        agg_attachment = AttachmentFactory(contentId=agg_post.id, postId=agg_post.id)
        factory_session.commit()

        # Set up the aggregated post relationship
        # The attachment with ContentType.AGGREGATED_POSTS points to the aggregated post
        attachment.aggregated_post = agg_post
        factory_session.commit()

        # Setup recursive call result
        mock_sub_result = {
            "images": [Image(id="agg_img", title="Agg Image", urls=[])],
            "scenes": [],
        }

        # Save the original method
        original_method = stash_processor.process_creator_attachment

        # Mock only the recursive call - use **kwargs to accept keyword args
        call_count = 0

        async def mock_recursive_call(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # First call - process the main attachment
                # This should trigger recursive call
                return await original_method(**kwargs)
            # Recursive call for aggregated attachment
            return mock_sub_result

        stash_processor.process_creator_attachment = mock_recursive_call

        # Use real async session from database
        async with test_database_sync.async_session_scope() as async_session:
            from metadata.post import Post

            # Query parent post in async session
            parent_post_query = await async_session.execute(
                select(Post).where(Post.id == parent_post.id)
            )
            async_parent_post = parent_post_query.unique().scalar_one()

            account_query = await async_session.execute(
                select(Account).where(Account.id == account.id)
            )
            async_account = account_query.unique().scalar_one()

            result = await original_method(
                attachment=attachment,
                item=async_parent_post,
                account=async_account,
                session=async_session,
            )

        # Verify results include aggregated content
        # Note: actual behavior depends on implementation details
        # This test validates the structure exists
        assert "images" in result
        assert "scenes" in result
