"""Unit tests for ContentProcessingMixin._gather_creator_messages.

The message half of the file-first gather: resolve the account's groups, then the
attachment-bearing messages in those groups. Runs the REAL store (a uuid-named
test DB via ``entity_store``); no GraphQL is involved, so no respx routes are
mounted. Covers the live message path that the de-sprawl pass left untested.
"""

import pytest

from metadata import ContentType
from tests.fixtures.metadata.metadata_factories import (
    AccountFactory,
    AccountMediaFactory,
    AttachmentFactory,
    GroupFactory,
    MediaFactory,
    MessageFactory,
)
from tests.fixtures.utils.test_isolation import snowflake_id


@pytest.mark.asyncio
async def test_gather_messages_returns_only_attachment_bearing(
    entity_store, respx_stash_processor
):
    """Returns the account's group messages that carry attachments, not others.

    A group the account belongs to holds two messages: one with an attachment,
    one without. The gather must resolve the group via membership, then return
    only the attachment-bearing message.
    """
    acct_id = snowflake_id()
    account = AccountFactory.build(id=acct_id, username="test_user")
    await entity_store.save(account)

    group = GroupFactory.build(id=snowflake_id(), createdBy=acct_id)
    await entity_store.save(group)
    group.users = [account]  # membership (habtm) resolved cache-first

    media = MediaFactory.build(id=snowflake_id(), accountId=acct_id, is_downloaded=True)
    await entity_store.save(media)
    account_media = AccountMediaFactory.build(
        id=snowflake_id(), accountId=acct_id, mediaId=media.id
    )
    await entity_store.save(account_media)

    msg_with = MessageFactory.build(
        id=snowflake_id(), groupId=group.id, senderId=acct_id
    )
    await entity_store.save(msg_with)
    msg_with.attachments = [
        AttachmentFactory.build(
            messageId=msg_with.id,
            contentId=account_media.id,
            contentType=ContentType.ACCOUNT_MEDIA,
            pos=0,
        )
    ]
    msg_without = MessageFactory.build(
        id=snowflake_id(), groupId=group.id, senderId=acct_id
    )
    await entity_store.save(msg_without)  # no attachments -> filtered out

    result = await respx_stash_processor._gather_creator_messages(account)

    ids = {m.id for m in result}
    assert msg_with.id in ids
    assert msg_without.id not in ids


@pytest.mark.asyncio
async def test_gather_messages_skips_groups_without_account(
    entity_store, respx_stash_processor
):
    """A group the account does NOT belong to contributes no messages."""
    acct_id = snowflake_id()
    other_id = snowflake_id()
    account = AccountFactory.build(id=acct_id, username="test_user")
    other = AccountFactory.build(id=other_id, username="other_user")
    await entity_store.save(account)
    await entity_store.save(other)

    foreign_group = GroupFactory.build(id=snowflake_id(), createdBy=other_id)
    await entity_store.save(foreign_group)
    foreign_group.users = [other]  # account is NOT a member

    media = MediaFactory.build(
        id=snowflake_id(), accountId=other_id, is_downloaded=True
    )
    await entity_store.save(media)
    account_media = AccountMediaFactory.build(
        id=snowflake_id(), accountId=other_id, mediaId=media.id
    )
    await entity_store.save(account_media)
    foreign_msg = MessageFactory.build(
        id=snowflake_id(), groupId=foreign_group.id, senderId=other_id
    )
    await entity_store.save(foreign_msg)
    foreign_msg.attachments = [
        AttachmentFactory.build(
            messageId=foreign_msg.id,
            contentId=account_media.id,
            contentType=ContentType.ACCOUNT_MEDIA,
            pos=0,
        )
    ]

    result = await respx_stash_processor._gather_creator_messages(account)

    assert result == []
