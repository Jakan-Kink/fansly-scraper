from __future__ import annotations

import copy
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Table,
)
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column, relationship

from config.decorators import with_database_session
from textio import json_output

from .account import Account, process_media_bundles_data
from .base import Base
from .database import require_database_config
from .relationship_logger import log_missing_relationship


if TYPE_CHECKING:
    from config import FanslyConfig
    from download.core import DownloadState

    from .attachment import Attachment


# Table definitions must come before the Group class to allow direct references in relationships
group_users = Table(
    "group_users",
    Base.metadata,
    Column("groupId", BigInteger, ForeignKey("groups.id"), primary_key=True),
    Column(
        "accountId", BigInteger, primary_key=True
    ),  # NO FK - partner might not exist yet
    Index("ix_group_users_accountId", "accountId"),  # Explicit index name
)


class Group(Base):
    """Represents a message group or conversation with multiple users.

    This class handles group messaging functionality, tracking participants and messages.
    Groups can have multiple users and maintain a reference to their last message.

    Attributes:
        id: Unique identifier for the group
        createdBy: ID of the account that created this group
        users: Set of Account objects representing group members
        messages: List of Message objects in this group
        lastMessageId: ID of the most recent message in the group
    """

    __tablename__ = "groups"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    createdBy: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("accounts.id"), nullable=False
    )
    users: Mapped[set[Account]] = relationship(
        "Account",
        secondary=group_users,
        primaryjoin="Group.id == group_users.c.groupId",
        secondaryjoin="group_users.c.accountId == Account.id",
        collection_class=set,
        viewonly=True,  # Mark as viewonly since accountId in group_users may not reference an existing Account
    )
    messages: Mapped[list[Message]] = relationship(
        "Message", cascade="all, delete-orphan", foreign_keys="[Message.groupId]"
    )
    lastMessageId: Mapped[int | None] = mapped_column(
        BigInteger,
        nullable=True,
    )
    last_message: Mapped[Message | None] = relationship(
        "Message",
        primaryjoin="and_(Group.id == Message.groupId, Group.lastMessageId == Message.id)",
        foreign_keys=[lastMessageId],
        post_update=True,  # Prevents circular dependency issues
        uselist=False,
    )


class Message(Base):
    """Represents a message in a conversation or group.

    This class handles both direct messages between users and messages in groups.
    Messages can have attachments and maintain metadata about their status.

    Attributes:
        id: Unique identifier for the message
        groupId: ID of the group this message belongs to (if any)
        senderId: ID of the account that sent this message
        recipientId: ID of the account this message was sent to (for direct messages)
        content: Text content of the message
        createdAt: When this message was sent
        deletedAt: When this message was deleted (if applicable)
        deleted: Whether this message is marked as deleted
        attachments: List of Attachment objects associated with this message
        sender: Relationship to the Account that sent this message
        recipient: Relationship to the Account that received this message (for direct messages)
    """

    __tablename__ = "messages"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    groupId: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("groups.id"), nullable=True, index=True
    )
    senderId: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("accounts.id"), nullable=False
    )
    recipientId: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("accounts.id"), nullable=True
    )
    content: Mapped[str] = mapped_column(String, nullable=False)
    createdAt: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deletedAt: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    deleted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    attachments: Mapped[list[Attachment]] = relationship(
        "Attachment",
        back_populates="message",
        cascade="all, delete-orphan",
        order_by="Attachment.pos",  # Order attachments by position
    )
    sender: Mapped[Account] = relationship(
        "Account",
        foreign_keys=[senderId],
        lazy="joined",  # Use joined loading since we always need sender info
        back_populates="sent_messages",
    )
    recipient: Mapped[Account] = relationship(
        "Account",
        foreign_keys=[recipientId],
        lazy="joined",  # Use joined loading since we always need recipient info
        back_populates="received_messages",
    )
    group: Mapped[Group] = relationship(
        "Group",
        foreign_keys=[groupId],
        lazy="noload",
        overlaps="messages",  # Don't auto-load group to reduce SQL queries
    )
    stash_id: Mapped[int | None] = mapped_column(Integer, nullable=True)


async def _process_single_message(
    session: AsyncSession,
    message_data: dict[str, any],
    known_relations: set[str],
    flush_immediately: bool = True,
) -> Message | None:
    """Process a single message's data and return the message instance.

    Args:
        session: SQLAlchemy async session
        message_data: Dictionary containing message data
        known_relations: Set of known relationship fields

    Returns:
        Message instance if successful, None if required fields are missing
    """
    # Process message data
    filtered_message, _ = Message.process_data(
        message_data,
        known_relations,
        "meta/mess - p_m_m-message",
        ("createdAt", "deletedAt"),
    )

    # Validate required fields
    required_fields = {"id", "senderId", "createdAt"}
    missing_required = {
        field for field in required_fields if field not in filtered_message
    }
    if missing_required:
        for field in missing_required:
            json_output(
                1,
                "meta/mess - missing_required_field",
                {"missing_field": field},
            )
        return None

    # Get or create message - use ID as primary filter
    message, _created = await Message.async_get_or_create(
        session,
        {
            "id": filtered_message["id"],
        },
        {
            "senderId": filtered_message["senderId"],
            "recipientId": filtered_message.get("recipientId"),
            "createdAt": filtered_message["createdAt"],
            "content": filtered_message.get("content", ""),
            "deleted": filtered_message.get("deleted", False),
        },
    )

    # Update fields
    Base.update_fields(message, filtered_message)
    if flush_immediately:
        await session.flush()

    return message


@require_database_config
@with_database_session(async_session=True)
async def process_messages_metadata(
    config: FanslyConfig,
    _state: DownloadState,
    messages: list[dict[str, any]],
    session: AsyncSession | None = None,
) -> None:
    """Process message metadata and store in the database.

    Processes a list of messages, creating or updating message records and their
    attachments. Handles message timestamps and content relationships.

    Args:
        config: FanslyConfig instance for database access
        state: Current download state
        messages: List of message data dictionaries
        session: Optional AsyncSession for database operations
    """
    # Create a deep copy of messages to avoid modifying the original
    messages = copy.deepcopy(messages)

    # Known attributes that are handled separately
    known_relations = {
        # Handled relationships
        "attachments",
        "accountMediaBundles",
        # Intentionally ignored fields
        "sender",
        "recipient",
        "group",
        "messageFlags",
        "messageStatus",
        "messageType",
        "replyTo",
        "replyToRoot",
        "reactions",
        "mentions",
        "type",
        "dataVersion",
        "correlationId",
        "inReplyTo",
        "inReplyToRoot",
        "embeds",
        "interactions",
        "likes",
        "totalTipAmount",
    }

    # Known attributes for attachments that are handled separately
    attachment_known_relations = {
        "message",
        "post",
        "media",
        "preview",
        "variants",
        "attachmentFlags",
        "attachmentStatus",
        "attachmentType",
        "permissions",
    }

    # Process any media bundles if present
    await process_media_bundles_data(config, messages, session=session)
    from .attachment import Attachment

    # Process messages
    for message_data in messages:
        message = await _process_single_message(session, message_data, known_relations)
        if not message:
            continue

        # Process attachments if present
        if "attachments" in message_data:
            for attachment_data in message_data["attachments"]:
                await Attachment.process_attachment(
                    attachment_data,
                    message,
                    attachment_known_relations,
                    "messageId",
                    session=session,
                    context="mess",
                )


async def _process_single_group(
    session: AsyncSession,
    group_data: dict[str, any],
    known_relations: set[str],
    source: str,
) -> Group | None:
    """Process a single group's data and return the group instance.

    Args:
        session: SQLAlchemy async session
        group_data: Dictionary containing group data
        known_relations: Set of known relationship fields
        source: Source identifier for logging (e.g., "data_groups")

    Returns:
        Group instance if successful, None if required fields are missing
    """
    # Map fields
    if "groupId" in group_data:
        group_data["id"] = group_data["groupId"]
    if "account_id" in group_data:
        group_data["createdBy"] = group_data["account_id"]

    # Process group data
    filtered_group, _ = Group.process_data(
        group_data, known_relations, f"meta/mess - p_g_resp-{source}"
    )

    # Validate required fields
    required_fields = {"id", "createdBy"}
    missing_required = {
        field for field in required_fields if field not in filtered_group
    }
    if missing_required:
        for field in missing_required:
            json_output(
                1,
                "meta/mess - missing_required_field",
                {
                    "groupId": filtered_group.get("id"),
                    "missing_field": field,
                    "source": source,
                },
            )
        return None

    # Track relationships
    await log_missing_relationship(
        session=session,
        table_name="groups",
        field_name="createdBy",
        missing_id=filtered_group["createdBy"],
        referenced_table="accounts",
        context={"groupId": filtered_group.get("id"), "source": source},
    )

    if "lastMessageId" in filtered_group:
        # Check if message exists
        message_exists = await log_missing_relationship(
            session=session,
            table_name="groups",
            field_name="lastMessageId",
            missing_id=filtered_group["lastMessageId"],
            referenced_table="messages",
            context={"groupId": filtered_group.get("id"), "source": source},
        )
        # Only set lastMessageId if message exists or this is not from aggregation data
        if source == "aggregation_groups" and not message_exists:
            del filtered_group["lastMessageId"]

    # Get or create group
    group, _created = await Group.async_get_or_create(
        session,
        {"id": filtered_group["id"]},
        {"createdBy": filtered_group["createdBy"]},
    )

    # Update fields
    Base.update_fields(group, filtered_group)
    await session.flush()

    return group


async def _process_group_users(
    session: AsyncSession, group: Group, users: list[dict[str, any]]
) -> None:
    """Process users for a group, updating group_users table.

    Args:
        session: SQLAlchemy async session
        group: Group instance
        users: List of user data dictionaries
    """
    # First, remove existing group_users entries instead of accessing group.users directly
    await session.execute(group_users.delete().where(group_users.c.groupId == group.id))
    await session.flush()

    # Process each user
    for user in users:
        user_id = user.get("userId")
        if not user_id:
            continue

        # Convert string IDs to integers for PostgreSQL
        if isinstance(user_id, str):
            user_id = int(user_id)

        # Track missing user accounts
        await log_missing_relationship(
            session=session,
            table_name="group_users",
            field_name="accountId",
            missing_id=user_id,
            referenced_table="accounts",
            context={"groupId": group.id, "source": "group_users"},
        )

        # Add user to group_users table using PostgreSQL upsert
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        insert_stmt = pg_insert(group_users).values(groupId=group.id, accountId=user_id)
        upsert_stmt = insert_stmt.on_conflict_do_nothing()
        await session.execute(upsert_stmt)

    await session.flush()


@require_database_config
@with_database_session(async_session=True)
async def process_groups_response(
    config: FanslyConfig,
    state: DownloadState,
    response: dict[str, any],
    session: AsyncSession | None = None,
) -> None:
    """Process group messaging response data.

    Processes group messaging data, creating or updating groups and their
    relationships with users. Also handles aggregated data like accounts.

    Args:
        config: FanslyConfig instance for database access
        state: Current download state
        response: Response data containing groups and aggregated data
        session: Optional AsyncSession for database operations
    """
    from .account import process_account_data
    from .relationship_logger import print_missing_relationships_summary

    response = copy.deepcopy(response)

    # Known attributes that are handled separately
    known_relations = {
        # Handled relationships
        "users",
        "messages",
        "lastMessage",
        # Intentionally ignored fields
        "metadata",
        "groupFlags",
        "groupStatus",
        "groupType",
        "permissions",
        "roles",
        "settings",
        "account_id",  # Handled by mapping to createdBy
        "groupId",  # Handled by mapping to id
        "partnerAccountId",
        "partnerUsername",
        "flags",
        "unreadCount",
        "subscriptionTier",
        "subscriptionTierId",
        "lastUnreadMessageId",
        # from p_g_resp-group
        "type",
        "groupFlagsMetadata",
        "permissionFlags",
        "recipients",
        "hasDmPermissionFlags",
        "dmPermissionFlags",
        "accountDmPermissionFlags",
    }

    # Process accounts first
    aggregation_data: dict = response.get("aggregationData", {})
    json_output(1, "meta/mess - p_g_resp - aggregation_data", aggregation_data)
    accounts: list = aggregation_data.get("accounts", {})
    for account in accounts:
        json_output(1, "meta/mess - p_g_resp - account", account)
        await process_account_data(config, data=account, state=state, session=session)

    # Process groups
    data: list[dict] = response.get("data", {})
    json_output(1, "meta/mess - p_g_resp - data", data)
    groups: list = aggregation_data.get("groups", {})

    # Process groups from data
    for data_group in data:
        group = await _process_single_group(
            session, data_group, known_relations, "data_groups"
        )
        if group:
            await session.flush()

    # Process groups from aggregation data
    for group_data in groups:
        json_output(1, "meta/mess - p_g_resp - group", group_data)
        group = await _process_single_group(
            session, group_data, known_relations, "aggregation_groups"
        )
        if group and "users" in group_data:
            await _process_group_users(session, group, group_data["users"])
            await session.flush()

        print_missing_relationships_summary()
