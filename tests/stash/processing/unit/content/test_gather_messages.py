"""Unit tests for ContentProcessingMixin._gather_creator_messages.

The message half of the file-first gather: resolve the account's groups, then the
attachment-bearing messages in those groups. Runs the REAL store (a uuid-named
test DB via ``entity_store``); no GraphQL is involved, so no respx routes are
mounted. Covers the live message path that the de-sprawl pass left untested.
"""

import pytest

from metadata import ContentType
from metadata.entity_store import PostgresEntityStore
from stash.processing import StashProcessing
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
async def test_gather_messages_attachment_bearing_from_member_groups_only(
    entity_store: PostgresEntityStore,
    respx_stash_processor: StashProcessing,
) -> None:
    """One seeded graph covers both _gather_creator_messages branches.

    Branch 1 (attachment filter): a group the account belongs to holds two
    messages — one with an attachment, one without. The gather must resolve
    the group via membership, then return only the attachment-bearing message.

    Branch 2 (membership filter): a group the account does NOT belong to holds
    an attachment-bearing message from another account; it contributes no
    messages to the result.

    Branch 3 (cache-miss + empty): gathering for an account that belongs to no
    group at all falls back to the DB group scan (content.py:204-205), still
    resolves no memberships, and returns [] (content.py:174).
    """
    acct_id = snowflake_id()
    other_id = snowflake_id()
    account = AccountFactory.build(id=acct_id, username="test_user")
    other = AccountFactory.build(id=other_id, username="other_user")
    await entity_store.save(account)
    await entity_store.save(other)

    # === Member group: two messages, only one carries an attachment. ===
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

    # === Foreign group: attachment-bearing message, but account is no member. ===
    foreign_group = GroupFactory.build(id=snowflake_id(), createdBy=other_id)
    await entity_store.save(foreign_group)
    foreign_group.users = [other]  # account is NOT a member

    foreign_media = MediaFactory.build(
        id=snowflake_id(), accountId=other_id, is_downloaded=True
    )
    await entity_store.save(foreign_media)
    foreign_account_media = AccountMediaFactory.build(
        id=snowflake_id(), accountId=other_id, mediaId=foreign_media.id
    )
    await entity_store.save(foreign_account_media)
    foreign_msg = MessageFactory.build(
        id=snowflake_id(), groupId=foreign_group.id, senderId=other_id
    )
    await entity_store.save(foreign_msg)
    foreign_msg.attachments = [
        AttachmentFactory.build(
            messageId=foreign_msg.id,
            contentId=foreign_account_media.id,
            contentType=ContentType.ACCOUNT_MEDIA,
            pos=0,
        )
    ]

    result = await respx_stash_processor._gather_creator_messages(account)

    # Exactly the attachment-bearing member-group message: msg_without is
    # filtered (no attachments) and foreign_msg contributes nothing (not a
    # member of foreign_group).
    ids = {m.id for m in result}
    assert ids == {msg_with.id}
    assert msg_without.id not in ids
    assert foreign_msg.id not in ids

    # === Cache-miss membership: an account in no cached group falls back to
    # the DB group scan and contributes no messages (empty gather). ===
    loner = AccountFactory.build(id=snowflake_id(), username="loner_user")
    await entity_store.save(loner)
    assert await respx_stash_processor._gather_creator_messages(loner) == []
