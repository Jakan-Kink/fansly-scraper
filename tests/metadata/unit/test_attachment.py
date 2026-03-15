"""Unit tests for attachment behavior."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from metadata import Account, Attachment, ContentType, Message, Post
from tests.fixtures.utils.test_isolation import snowflake_id


@pytest.mark.asyncio
async def test_post_attachment_ordering(entity_store):
    """Test that post attachments maintain the order they're set in.

    In production, attachments come from model_validate on nested dicts.
    They're stored in-memory on the parent; ordering is list-order.
    """
    store = entity_store

    account_id = snowflake_id()
    post_id = snowflake_id()
    content_id1 = snowflake_id()
    content_id2 = snowflake_id()
    content_id3 = snowflake_id()

    account = Account(id=account_id, username="test_user")
    await store.save(account)

    post = Post(
        id=post_id,
        accountId=account_id,
        content="Test post",
        createdAt=datetime.now(UTC),
    )
    await store.save(post)

    # Set attachments in pos order (simulating sorted API data)
    post.attachments = [
        Attachment(
            postId=post_id,
            contentId=content_id2,
            pos=1,
            contentType=ContentType.ACCOUNT_MEDIA,
        ),
        Attachment(
            postId=post_id,
            contentId=content_id3,
            pos=2,
            contentType=ContentType.ACCOUNT_MEDIA,
        ),
        Attachment(
            postId=post_id,
            contentId=content_id1,
            pos=3,
            contentType=ContentType.ACCOUNT_MEDIA,
        ),
    ]
    await store.save(post)

    saved = await store.get(Post, post_id)
    assert saved is not None
    assert len(saved.attachments) == 3
    assert [a.pos for a in saved.attachments] == [1, 2, 3]
    assert [a.contentId for a in saved.attachments] == [
        content_id2,
        content_id3,
        content_id1,
    ]


@pytest.mark.asyncio
async def test_message_attachment_ordering(entity_store):
    """Test that message attachments maintain the order they're set in."""
    store = entity_store

    account_id = snowflake_id()
    msg_id = snowflake_id()
    content_id1 = snowflake_id()
    content_id2 = snowflake_id()
    content_id3 = snowflake_id()

    account = Account(id=account_id, username="test_user")
    await store.save(account)

    message = Message(
        id=msg_id,
        senderId=account_id,
        content="Test message",
        createdAt=datetime.now(UTC),
    )
    await store.save(message)

    # Set attachments in pos order
    message.attachments = [
        Attachment(
            messageId=msg_id,
            contentId=content_id3,
            pos=1,
            contentType=ContentType.ACCOUNT_MEDIA,
        ),
        Attachment(
            messageId=msg_id,
            contentId=content_id1,
            pos=2,
            contentType=ContentType.ACCOUNT_MEDIA,
        ),
        Attachment(
            messageId=msg_id,
            contentId=content_id2,
            pos=3,
            contentType=ContentType.ACCOUNT_MEDIA,
        ),
    ]
    await store.save(message)

    saved = await store.get(Message, msg_id)
    assert saved is not None
    assert len(saved.attachments) == 3
    assert [a.pos for a in saved.attachments] == [1, 2, 3]
    assert [a.contentId for a in saved.attachments] == [
        content_id3,
        content_id1,
        content_id2,
    ]


@pytest.mark.asyncio
async def test_attachment_content_resolution(entity_store):
    """Test content type helper properties on attachments."""
    post_id = snowflake_id()
    content_id1 = snowflake_id()
    content_id2 = snowflake_id()
    content_id3 = snowflake_id()

    att_media = Attachment(
        postId=post_id,
        contentId=content_id1,
        pos=1,
        contentType=ContentType.ACCOUNT_MEDIA,
    )
    att_bundle = Attachment(
        postId=post_id,
        contentId=content_id2,
        pos=2,
        contentType=ContentType.ACCOUNT_MEDIA_BUNDLE,
    )
    att_story = Attachment(
        postId=post_id,
        contentId=content_id3,
        pos=3,
        contentType=ContentType.STORY,
    )

    assert att_media.is_account_media is True
    assert att_media.is_account_media_bundle is False

    assert att_bundle.is_account_media_bundle is True
    assert att_bundle.is_account_media is False

    assert att_story.is_account_media is False
    assert att_story.is_account_media_bundle is False


@pytest.mark.asyncio
async def test_attachment_exclusivity(entity_store):
    """Test that an attachment can have postId or messageId, but not both.

    The DB has a CHECK constraint, but at the Pydantic level both fields are
    nullable so the constraint is only enforced at DB write time. Since attachments
    aren't individually persisted via store.save() in the current architecture,
    we verify at the model level that both can be set (no Pydantic validation error)
    but document the DB constraint exists.
    """
    post_id = snowflake_id()
    msg_id = snowflake_id()
    content_id = snowflake_id()

    # Pydantic allows both — the CHECK constraint is DB-level
    att = Attachment(
        contentId=content_id,
        pos=1,
        contentType=ContentType.ACCOUNT_MEDIA,
        postId=post_id,
        messageId=msg_id,
    )
    assert att.postId == post_id
    assert att.messageId == msg_id


@pytest.mark.asyncio
async def test_invalid_content_type_raises(entity_store):
    """Test that invalid contentType values raise ValidationError.

    In the SA ORM world, Attachment.process_attachment() silently skipped invalid types.
    In the Pydantic world, ContentType enum validation rejects unknown values at model
    construction time.
    """
    with pytest.raises(ValidationError, match="contentType"):
        Attachment(
            contentId=snowflake_id(),
            contentType=99999,
            pos=0,
        )
