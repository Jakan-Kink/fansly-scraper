"""Tests for batch composition of creator attachments.

These tests verify which Media objects flatten into the batch list passed
to ``_process_batch_internal`` from various attachment shapes (direct
media, bundle, aggregated post recursion). They exercise the real
``_process_batch_internal`` end-to-end against Docker Stash via
``real_stash_processor`` — the canonical TrueSpy pattern documented at
``tests/stash/processing/integration/test_message_processing.py:173-238``
(audit Cat-D #13). The unique value preserved over the existing
integration tests in ``test_media_processing.py:505,664,827`` is the
intermediate batch-composition assertion (which Media objects the spy
captures), not the eventual created Image/Scene shape.

Located under ``integration/media_mixin/`` (Wave 4 directory hygiene)
because the test surface is real Docker Stash + EntityStore.
"""

from datetime import UTC, datetime
from unittest.mock import patch

import pytest

from metadata import ContentType
from tests.fixtures.metadata.metadata_factories import (
    AccountFactory,
    AccountMediaBundleFactory,
    AccountMediaFactory,
    AttachmentFactory,
    MediaFactory,
    PostFactory,
)
from tests.fixtures.utils.test_isolation import snowflake_id


class TestAttachmentProcessing:
    """Verify batch composition produced by ``process_creator_attachment``."""

    @pytest.mark.asyncio
    async def test_process_attachment_with_direct_media(
        self, real_stash_processor, stash_cleanup_tracker, entity_store
    ):
        """Direct attachment.media collects exactly that Media into the batch."""
        async with stash_cleanup_tracker(real_stash_processor.context.client):
            account = AccountFactory.build(username="attach_direct")
            await entity_store.save(account)

            post = PostFactory.build(
                accountId=account.id,
                content="direct media post",
                createdAt=datetime(2024, 1, 1, tzinfo=UTC),
            )
            await entity_store.save(post)

            media = MediaFactory.build(
                id=snowflake_id(),
                mimetype="image/jpeg",
                is_downloaded=True,
                accountId=account.id,
                stash_id=789,
            )
            await entity_store.save(media)

            account_media = AccountMediaFactory.build(
                id=snowflake_id(),
                accountId=account.id,
                mediaId=media.id,
            )
            await entity_store.save(account_media)

            attachment = AttachmentFactory.build(
                contentId=account_media.id,
                contentType=ContentType.ACCOUNT_MEDIA,
                postId=post.id,
            )
            await entity_store.save(attachment)

            captured_batches = []
            original_process_batch = real_stash_processor._process_batch_internal

            async def spy_process_batch(media_list, item, account):
                # Capture composition BEFORE delegating so the assertion fires
                # even if the underlying Stash call returns nothing.
                captured_batches.append(
                    {
                        "media_ids": [m.id for m in media_list],
                        "mimetypes": [m.mimetype for m in media_list],
                        "stash_ids": [m.stash_id for m in media_list],
                        "item": item,
                        "account": account,
                    }
                )
                return await original_process_batch(media_list, item, account)

            with patch.object(
                real_stash_processor,
                "_process_batch_internal",
                side_effect=spy_process_batch,
            ):
                result = await real_stash_processor.process_creator_attachment(
                    attachment=attachment,
                    item=post,
                    account=account,
                )

            # Batch composition: exactly one media, the direct one.
            assert len(captured_batches) == 1
            batch = captured_batches[0]
            assert batch["media_ids"] == [media.id]
            assert batch["mimetypes"] == ["image/jpeg"]
            assert batch["stash_ids"] == [789]
            assert batch["item"] is post
            assert batch["account"] is account

            # Smoke check: delegation completed and produced the expected dict shape.
            assert "images" in result
            assert "scenes" in result

    @pytest.mark.asyncio
    async def test_process_attachment_with_bundle(
        self, real_stash_processor, stash_cleanup_tracker, entity_store
    ):
        """Bundle attachment flattens accountMedia[*].media into the batch."""
        async with stash_cleanup_tracker(real_stash_processor.context.client):
            account = AccountFactory.build(username="attach_bundle")
            await entity_store.save(account)

            post = PostFactory.build(
                accountId=account.id,
                content="bundle post",
                createdAt=datetime(2024, 1, 1, tzinfo=UTC),
            )
            await entity_store.save(post)

            media1 = MediaFactory.build(
                id=snowflake_id(),
                mimetype="image/jpeg",
                is_downloaded=True,
                accountId=account.id,
                stash_id=456,
            )
            media2 = MediaFactory.build(
                id=snowflake_id(),
                mimetype="video/mp4",
                is_downloaded=True,
                accountId=account.id,
                stash_id=457,
            )
            await entity_store.save(media1)
            await entity_store.save(media2)

            account_media1 = AccountMediaFactory.build(
                id=snowflake_id(), accountId=account.id, mediaId=media1.id
            )
            account_media2 = AccountMediaFactory.build(
                id=snowflake_id(), accountId=account.id, mediaId=media2.id
            )
            await entity_store.save(account_media1)
            await entity_store.save(account_media2)

            bundle = AccountMediaBundleFactory.build(
                id=snowflake_id(), accountId=account.id
            )
            bundle.accountMedia = [account_media1, account_media2]
            await entity_store.save(bundle)

            attachment = AttachmentFactory.build(
                contentId=bundle.id,
                contentType=ContentType.ACCOUNT_MEDIA_BUNDLE,
                postId=post.id,
            )
            await entity_store.save(attachment)

            captured_batches = []
            original_process_batch = real_stash_processor._process_batch_internal

            async def spy_process_batch(media_list, item, account):
                captured_batches.append(
                    {
                        "media_ids": [m.id for m in media_list],
                        "mimetypes": [m.mimetype for m in media_list],
                    }
                )
                return await original_process_batch(media_list, item, account)

            with patch.object(
                real_stash_processor,
                "_process_batch_internal",
                side_effect=spy_process_batch,
            ):
                result = await real_stash_processor.process_creator_attachment(
                    attachment=attachment,
                    item=post,
                    account=account,
                )

            # Bundle flattens into a single batch with both media.
            assert len(captured_batches) == 1
            batch = captured_batches[0]
            assert set(batch["media_ids"]) == {media1.id, media2.id}
            assert set(batch["mimetypes"]) == {"image/jpeg", "video/mp4"}

            assert "images" in result
            assert "scenes" in result

    @pytest.mark.asyncio
    async def test_process_attachment_with_aggregated_post(
        self, real_stash_processor, stash_cleanup_tracker, entity_store
    ):
        """Aggregated-post attachment recursively collects from nested attachments."""
        async with stash_cleanup_tracker(real_stash_processor.context.client):
            account = AccountFactory.build(username="attach_agg")
            await entity_store.save(account)

            outer_post = PostFactory.build(
                accountId=account.id,
                content="outer post",
                createdAt=datetime(2024, 1, 1, tzinfo=UTC),
            )
            await entity_store.save(outer_post)

            agg_post = PostFactory.build(
                accountId=account.id,
                content="aggregated post content",
                createdAt=datetime(2024, 1, 2, tzinfo=UTC),
            )
            await entity_store.save(agg_post)

            agg_media = MediaFactory.build(
                id=snowflake_id(),
                mimetype="image/jpeg",
                is_downloaded=True,
                accountId=account.id,
                stash_id=999,
            )
            await entity_store.save(agg_media)

            agg_account_media = AccountMediaFactory.build(
                id=snowflake_id(), accountId=account.id, mediaId=agg_media.id
            )
            await entity_store.save(agg_account_media)

            agg_attachment = AttachmentFactory.build(
                contentId=agg_account_media.id,
                contentType=ContentType.ACCOUNT_MEDIA,
                postId=agg_post.id,
            )
            await entity_store.save(agg_attachment)

            agg_post.attachments = [agg_attachment]

            outer_attachment = AttachmentFactory.build(
                contentId=agg_post.id,
                contentType=ContentType.AGGREGATED_POSTS,
                postId=outer_post.id,
            )
            await entity_store.save(outer_attachment)

            captured_batches = []
            original_process_batch = real_stash_processor._process_batch_internal

            async def spy_process_batch(media_list, item, account):
                captured_batches.append(
                    {
                        "media_ids": [m.id for m in media_list],
                        "mimetypes": [m.mimetype for m in media_list],
                        "item_id": item.id,
                    }
                )
                return await original_process_batch(media_list, item, account)

            with patch.object(
                real_stash_processor,
                "_process_batch_internal",
                side_effect=spy_process_batch,
            ):
                result = await real_stash_processor.process_creator_attachment(
                    attachment=outer_attachment,
                    item=outer_post,
                    account=account,
                )

            # Recursion: the inner attachment's media surfaces in a single batch
            # whose `item` is the aggregated post (not the outer one).
            assert len(captured_batches) == 1
            batch = captured_batches[0]
            assert batch["media_ids"] == [agg_media.id]
            assert batch["mimetypes"] == ["image/jpeg"]
            assert batch["item_id"] == agg_post.id

            assert "images" in result
            assert "scenes" in result
