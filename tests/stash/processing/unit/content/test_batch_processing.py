"""Tests for batch processing functionality in ContentProcessingMixin.

This test module uses entity_store for Pydantic model persistence and respx for
HTTP mocking to provide reliable unit testing while letting real code flow execute.
"""

import pytest

from metadata import ContentType
from metadata.models import get_store
from tests.fixtures import (
    AccountFactory,
    AccountMediaBundleFactory,
    AccountMediaFactory,
    AttachmentFactory,
    MediaFactory,
    PostFactory,
)
from tests.fixtures.utils.test_isolation import snowflake_id


@pytest.mark.asyncio
async def test_collect_media_from_attachments_with_aggregated_post(
    entity_store,
    respx_stash_processor,
):
    """Test _collect_media_from_attachments with an aggregated post."""
    store = get_store()

    acct_id = snowflake_id()
    media_id = snowflake_id()
    agg_post_id = snowflake_id()
    main_post_id = snowflake_id()

    # Create account and media
    account = AccountFactory.build(id=acct_id, username="test_user")
    await store.save(account)

    media = MediaFactory.build(id=media_id, accountId=acct_id, mimetype="image/jpeg")
    await store.save(media)

    account_media = AccountMediaFactory.build(
        id=media_id, accountId=acct_id, mediaId=media_id
    )
    await store.save(account_media)
    # account_media.media auto-resolves via identity map (Media already saved)

    # Create aggregated post with nested attachment
    agg_post = PostFactory.build(
        id=agg_post_id, accountId=acct_id, content="Aggregated post"
    )
    await store.save(agg_post)

    nested_attachment = AttachmentFactory.build(
        id=60003,
        postId=agg_post_id,  # Link nested attachment to aggregated post
        contentId=media_id,  # Points to AccountMedia
        contentType=ContentType.ACCOUNT_MEDIA,
        pos=0,
    )
    await store.save(nested_attachment)

    # Add nested attachment to aggregated post
    agg_post.attachments = [nested_attachment]
    await store.save(agg_post)

    # nested_attachment.media auto-resolves via identity map (AccountMedia already saved)

    # Create main post with attachment pointing to aggregated post
    main_post = PostFactory.build(
        id=main_post_id, accountId=acct_id, content="Main post"
    )
    await store.save(main_post)

    main_attachment = AttachmentFactory.build(
        id=60004,
        postId=main_post_id,  # Link attachment to main post
        contentId=agg_post_id,  # Points to aggregated post
        contentType=ContentType.AGGREGATED_POSTS,
        pos=0,
    )
    await store.save(main_attachment)

    # main_attachment.aggregated_post auto-resolves via identity map (Post already saved)

    # Call the method
    result = await respx_stash_processor._collect_media_from_attachments(
        [main_attachment]
    )

    # Verify nested media was found
    assert len(result) == 1
    assert result[0].id == media_id


@pytest.mark.asyncio
async def test_collect_media_from_attachments_with_bundle(
    entity_store,
    respx_stash_processor,
):
    """Test _collect_media_from_attachments with a media bundle."""
    store = get_store()

    acct_id = snowflake_id()
    bundle_id = snowflake_id()
    media_id_1 = snowflake_id()
    media_id_2 = snowflake_id()
    post_id = snowflake_id()

    # Create account
    account = AccountFactory.build(id=acct_id, username="test_user")
    await store.save(account)

    # Create bundle
    bundle = AccountMediaBundleFactory.build(id=bundle_id, accountId=acct_id)
    await store.save(bundle)

    # Create media for bundle
    media1 = MediaFactory.build(id=media_id_1, accountId=acct_id, mimetype="image/jpeg")
    await store.save(media1)

    account_media1 = AccountMediaFactory.build(
        id=media_id_1, accountId=acct_id, mediaId=media_id_1
    )
    await store.save(account_media1)
    # account_media1.media auto-resolves via identity map (Media already saved)

    media2 = MediaFactory.build(id=media_id_2, accountId=acct_id, mimetype="video/mp4")
    await store.save(media2)

    account_media2 = AccountMediaFactory.build(
        id=media_id_2, accountId=acct_id, mediaId=media_id_2
    )
    await store.save(account_media2)
    # account_media2.media auto-resolves via identity map (Media already saved)

    # Add media to bundle
    bundle.accountMedia = [account_media1, account_media2]
    await store.save(bundle)

    # Create post attachment pointing to bundle
    post = PostFactory.build(id=post_id, accountId=acct_id, content="Post with bundle")
    await store.save(post)

    attachment = AttachmentFactory.build(
        id=60005,
        postId=post_id,  # Link attachment to post
        contentId=bundle_id,  # Points to bundle
        contentType=ContentType.ACCOUNT_MEDIA_BUNDLE,
        pos=0,
    )
    await store.save(attachment)

    # attachment.bundle auto-resolves via identity map (AccountMediaBundle already saved)

    # Call the method
    result = await respx_stash_processor._collect_media_from_attachments([attachment])

    # Verify all media was collected
    assert len(result) >= 2  # At least two bundle media (preview is optional)
    media_ids = {m.id for m in result}
    assert media_id_1 in media_ids
    assert media_id_2 in media_ids
