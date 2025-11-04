"""Tests for the MediaProcessingMixin.

This module imports all the media mixin tests to ensure they are discovered by pytest.
"""

from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.orm import selectinload

# Import the modules instead of the classes to avoid fixture issues
from metadata import Account, AccountMedia, Media, Post
from metadata.attachment import ContentType
from stash.types import FindImagesResultType, Image
from tests.fixtures import (
    AccountFactory,
    AccountMediaFactory,
    AttachmentFactory,
    MediaFactory,
    PostFactory,
)


class TestMediaProcessingWithRealData:
    """Test media processing mixin with real JSON data."""

    @pytest.mark.asyncio
    async def test_process_media_with_real_data(
        self, media_mixin, factory_async_session, session
    ):
        """Test processing media with real data using factories."""
        # Create test data with factories
        AccountFactory(id=12345, username="test_user")
        PostFactory(id=200, accountId=12345, content="Test post #test")
        MediaFactory(id=123, accountId=12345, mimetype="image/jpeg", is_downloaded=True)
        AccountMediaFactory(id=123, accountId=12345, mediaId=123)
        AttachmentFactory(
            id=60001,
            postId=200,
            contentId=123,
            contentType=ContentType.ACCOUNT_MEDIA,
            pos=0,
        )

        # Commit factory changes
        factory_async_session.commit()

        # Query fresh objects from async session with eager loading
        result_account = await session.execute(
            select(Account).where(Account.id == 12345)
        )
        account = result_account.scalar_one()

        # Eager load relationships to prevent lazy loading in async context
        result_post = await session.execute(
            select(Post)
            .where(Post.id == 200)
            .options(
                selectinload(Post.accountMentions),
                selectinload(Post.hashtags),
            )
        )
        post = result_post.unique().scalar_one()

        result_media = await session.execute(
            select(Media).where(Media.id == 123).options(selectinload(Media.variants))
        )
        media = result_media.unique().scalar_one()

        # Create ImageFile dict (GraphQL returns dicts, not objects)
        image_file_data = {
            "id": "800",
            "path": f"/path/to/{media.id}.jpg",
            "basename": f"{media.id}.jpg",
            "parent_folder_id": "folder_1",
            "size": 1024000,
            "width": 1920,
            "height": 1080,
            "mod_time": "2024-01-01T00:00:00Z",
            "fingerprints": [],
        }

        # Create Image dict with visual_files (GraphQL returns dicts, not objects)
        image_data = {
            "id": "600",
            "title": "Test Image",
            "visual_files": [image_file_data],  # List of dicts
            "organized": False,
            "urls": [],
            "tags": [],
            "performers": [],
            "galleries": [],
        }

        # Create FindImagesResultType with dict data (like GraphQL returns)
        find_images_result = FindImagesResultType(
            count=1,
            megapixels=2.0,
            filesize=1024000.0,
            images=[image_data],  # List of dicts, NOT Image objects
        )

        # Mock context.client.find_images to return the result type
        media_mixin.context.client.find_images = AsyncMock(
            return_value=find_images_result
        )

        # Mock context.client.execute for save() calls (GraphQL mutation)
        media_mixin.context.client.execute = AsyncMock(
            return_value={"imageUpdate": image_data}
        )

        # Mock the methods called by _update_stash_metadata
        media_mixin._find_existing_performer = AsyncMock(return_value=None)
        media_mixin._find_existing_studio = AsyncMock(return_value=None)
        media_mixin._process_hashtags_to_tags = AsyncMock(return_value=[])

        # Create an empty result dictionary
        result = {"images": [], "scenes": []}

        # Call _process_media with queried data
        await media_mixin._process_media(media, post, account, result)

        # Verify results
        assert len(result["images"]) == 1
        assert isinstance(result["images"][0], Image)
        assert result["images"][0].id == "600"
        assert len(result["scenes"]) == 0

        # Verify client calls - media has no stash_id, so should find by path
        media_mixin.context.client.find_images.assert_called_once()
        path_filter = media_mixin.context.client.find_images.call_args[1][
            "image_filter"
        ]
        assert str(media.id) in str(path_filter)

    @pytest.mark.asyncio
    async def test_process_creator_attachment_with_real_data(
        self, media_mixin, factory_async_session, session
    ):
        """Test process_creator_attachment with real data using factories."""
        # Create test data with factories
        AccountFactory(id=12346, username="test_user_2")
        PostFactory(id=201, accountId=12346, content="Test post #test")
        MediaFactory(id=124, accountId=12346, mimetype="image/jpeg")
        AccountMediaFactory(id=124, accountId=12346, mediaId=124)
        AttachmentFactory(
            id=60002,
            postId=201,
            contentId=124,
            contentType=ContentType.ACCOUNT_MEDIA,
            pos=0,
        )

        # Commit factory changes
        factory_async_session.commit()

        # Query fresh objects from async session
        result_account = await session.execute(
            select(Account).where(Account.id == 12346)
        )
        account = result_account.scalar_one()

        result_post = await session.execute(select(Post).where(Post.id == 201))
        post = result_post.unique().scalar_one()

        # Query attachment with eager loading of media relationship
        from metadata.attachment import Attachment

        result_attachment = await session.execute(
            select(Attachment)
            .where(Attachment.id == 60002)
            .options(
                selectinload(Attachment.media).selectinload(AccountMedia.media),
                selectinload(Attachment.bundle),
                selectinload(Attachment.aggregated_post),
            )
        )
        attachment = result_attachment.scalar_one()

        # Patch the batch processing method (process_creator_attachment calls this)
        with patch.object(
            media_mixin,
            "_process_media_batch_by_mimetype",
            new=AsyncMock(return_value={"images": [], "scenes": []}),
        ) as mock_batch_process:
            # Call process_creator_attachment with queried data
            await media_mixin.process_creator_attachment(
                attachment=attachment,
                item=post,
                account=account,
            )

            # Verify _process_media_batch_by_mimetype was called
            mock_batch_process.assert_called()


# No need to import classes directly as they're discovered by pytest
