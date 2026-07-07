"""Tests for collecting media from attachments.

These tests use entity_store for database persistence, following the Pydantic
EntityStore migration patterns.
"""

import pytest

from metadata import ContentType
from metadata.entity_store import PostgresEntityStore
from stash.processing import StashProcessing
from tests.fixtures.metadata import (
    AccountFactory,
    AccountMediaBundleFactory,
    AccountMediaFactory,
    AttachmentFactory,
    MediaFactory,
)
from tests.fixtures.utils.test_isolation import snowflake_id


class TestCollectMediaFromAttachments:
    """Test _collect_media_from_attachments method.

    These tests verify pure object manipulation without HTTP calls.
    """

    @pytest.mark.parametrize(
        "attachment_count",
        [0, 2],
        ids=["empty_attachments", "attachments_without_media"],
    )
    @pytest.mark.asyncio
    async def test_attachments_yield_no_media(
        self,
        respx_stash_processor: StashProcessing,
        attachment_count: int,
    ) -> None:
        """Test _collect_media_from_attachments yields nothing.

        Covers both the empty-attachments case (loop never entered) and
        attachments whose contentId points at non-existent AccountMedia
        (dict-lookup miss branch).
        """
        attachments = [
            AttachmentFactory.build(
                id=60001 + pos,
                contentType=ContentType.ACCOUNT_MEDIA,
                contentId=snowflake_id(),  # Non-existent AccountMedia
                pos=pos,
            )
            for pos in range(attachment_count)
        ]

        result = await respx_stash_processor._collect_media_from_attachments(
            attachments
        )
        assert result == []

    @pytest.mark.asyncio
    async def test_attachments_with_media(
        self,
        entity_store: PostgresEntityStore,
        respx_stash_processor: StashProcessing,
    ) -> None:
        """Test _collect_media_from_attachments with attachments that have media."""
        acct_id = snowflake_id()
        media_id_1 = snowflake_id()
        media_id_2 = snowflake_id()

        # Create account first (FK parent for media)
        account = AccountFactory.build(id=acct_id, username="test_user")
        await entity_store.save(account)

        # Create media objects and save to entity_store (populates identity map)
        media1 = MediaFactory.build(
            id=media_id_1,
            accountId=acct_id,
            mimetype="image/jpeg",
            location="https://example.com/media_123.jpg",
            width=800,
            height=600,
        )
        media2 = MediaFactory.build(
            id=media_id_2,
            accountId=acct_id,
            mimetype="video/mp4",
            location="https://example.com/media_456.mp4",
            width=1280,
            height=720,
        )
        await entity_store.save(media1)
        await entity_store.save(media2)

        # Create AccountMedia and save (identity map resolves .media property)
        acct_media1 = AccountMediaFactory.build(
            id=media_id_1, accountId=acct_id, mediaId=media_id_1
        )
        acct_media2 = AccountMediaFactory.build(
            id=media_id_2, accountId=acct_id, mediaId=media_id_2
        )
        await entity_store.save(acct_media1)
        await entity_store.save(acct_media2)

        # Create attachments with media - contentId points to AccountMedia IDs
        # Attachment.media is a read-only property that resolves via identity map
        att1 = AttachmentFactory.build(
            id=60003,
            contentType=ContentType.ACCOUNT_MEDIA,
            contentId=media_id_1,  # Points to AccountMedia.id
            pos=0,
        )

        att2 = AttachmentFactory.build(
            id=60004,
            contentType=ContentType.ACCOUNT_MEDIA,
            contentId=media_id_2,  # Points to AccountMedia.id
            pos=1,
        )

        result = await respx_stash_processor._collect_media_from_attachments(
            [att1, att2]
        )

        # Verify we got media objects back
        assert len(result) == 2
        media_ids = [m.id for m in result]
        assert media_id_1 in media_ids
        assert media_id_2 in media_ids

    @pytest.mark.asyncio
    async def test_attachments_with_previews(
        self,
        entity_store: PostgresEntityStore,
        respx_stash_processor: StashProcessing,
    ) -> None:
        """Test _collect_media_from_attachments collects preview media (lines 241, 250, 253).

        AccountMedia and bundles can have a previewId pointing to a separate
        Media object (e.g., a thumbnail). These should also be collected.
        """
        acct_id = snowflake_id()
        main_media_id = snowflake_id()
        preview_media_id = snowflake_id()
        bundle_preview_id = snowflake_id()
        bundle_item_media_id = snowflake_id()
        bundle_item_preview_id = snowflake_id()

        # FK parent
        account = AccountFactory.build(id=acct_id, username="preview_user")
        await entity_store.save(account)

        # Main media + its preview
        main_media = MediaFactory.build(
            id=main_media_id,
            accountId=acct_id,
            mimetype="video/mp4",
        )
        preview_media = MediaFactory.build(
            id=preview_media_id,
            accountId=acct_id,
            mimetype="image/jpeg",
        )
        await entity_store.save(main_media)
        await entity_store.save(preview_media)

        # AccountMedia with both mediaId and previewId → hits line 241
        acct_media = AccountMediaFactory.build(
            id=main_media_id,
            accountId=acct_id,
            mediaId=main_media_id,
            previewId=preview_media_id,
        )
        await entity_store.save(acct_media)

        att1 = AttachmentFactory.build(
            id=70001,
            contentType=ContentType.ACCOUNT_MEDIA,
            contentId=main_media_id,
            pos=0,
        )

        # Bundle media + bundle preview → hits lines 250, 253
        bundle_item_media = MediaFactory.build(
            id=bundle_item_media_id,
            accountId=acct_id,
            mimetype="image/png",
        )
        bundle_item_preview = MediaFactory.build(
            id=bundle_item_preview_id,
            accountId=acct_id,
            mimetype="image/jpeg",
        )
        bundle_preview = MediaFactory.build(
            id=bundle_preview_id,
            accountId=acct_id,
            mimetype="image/jpeg",
        )
        await entity_store.save(bundle_item_media)
        await entity_store.save(bundle_item_preview)
        await entity_store.save(bundle_preview)

        # AccountMedia inside the bundle (with its own preview)
        bundle_acct_media = AccountMediaFactory.build(
            id=bundle_item_media_id,
            accountId=acct_id,
            mediaId=bundle_item_media_id,
            previewId=bundle_item_preview_id,
        )
        await entity_store.save(bundle_acct_media)

        # The bundle itself
        bundle = AccountMediaBundleFactory.build(
            id=snowflake_id(),
            accountId=acct_id,
            previewId=bundle_preview_id,
        )
        await bundle._add_to_relationship("accountMedia", bundle_acct_media)
        await entity_store.save(bundle)

        att2 = AttachmentFactory.build(
            id=70002,
            contentType=ContentType.ACCOUNT_MEDIA_BUNDLE,
            contentId=bundle.id,
            pos=1,
        )

        result = await respx_stash_processor._collect_media_from_attachments(
            [att1, att2]
        )

        result_ids = {m.id for m in result}

        # Direct media: main + preview (lines 238-241)
        assert main_media_id in result_ids
        assert preview_media_id in result_ids

        # Bundle item media + its preview (lines 247-250)
        assert bundle_item_media_id in result_ids
        assert bundle_item_preview_id in result_ids

        # Bundle's own preview (line 252-253)
        assert bundle_preview_id in result_ids
