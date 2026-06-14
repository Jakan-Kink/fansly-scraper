"""Tests for is_preview flag set by _collect_media_from_attachments.

Verifies that preview Media objects have is_preview=True after collection,
while main media objects remain is_preview=False.
"""

import pytest

from metadata import ContentType
from tests.fixtures.metadata.metadata_factories import (
    AccountFactory,
    AccountMediaFactory,
    AttachmentFactory,
    MediaFactory,
)
from tests.fixtures.utils.test_isolation import snowflake_id


class TestCollectMediaPreviewFlag:
    @pytest.mark.asyncio
    async def test_direct_media_preview_flagged(
        self, entity_store, respx_stash_processor
    ):
        """Direct AccountMedia.preview → is_preview=True; main media stays False."""
        acct_id = snowflake_id()
        account = AccountFactory.build(id=acct_id, username="test_user")
        await entity_store.save(account)

        main = MediaFactory.build(
            id=snowflake_id(), accountId=acct_id, mimetype="video/mp4", type=2
        )
        preview = MediaFactory.build(
            id=snowflake_id(), accountId=acct_id, mimetype="image/jpeg", type=1
        )
        await entity_store.save(main)
        await entity_store.save(preview)

        account_media = AccountMediaFactory.build(
            id=snowflake_id(),
            accountId=acct_id,
            mediaId=main.id,
            previewId=preview.id,
        )
        await entity_store.save(account_media)

        attachment = AttachmentFactory.build(
            contentType=ContentType.ACCOUNT_MEDIA,
            contentId=account_media.id,
            pos=0,
        )

        collected = await respx_stash_processor._collect_media_from_attachments(
            [attachment]
        )

        assert preview in collected
        assert preview.is_preview is True
        assert main.is_preview is False
