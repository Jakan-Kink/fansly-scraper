from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Table
from sqlalchemy.inspection import inspect

# from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Mapped, mapped_column, relationship

from textio import json_output

from .account import Account
from .base import Base
from .relationship_logger import log_missing_relationship

if TYPE_CHECKING:
    from config import FanslyConfig
    from download.core import DownloadState

    from .attachment import Attachment


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
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    createdBy: Mapped[int] = mapped_column(
        Integer, ForeignKey("accounts.id"), nullable=False
    )
    users: Mapped[set[Account]] = relationship(
        "Account", secondary="group_users", collection_class=set
    )
    messages: Mapped[list[Message]] = relationship(
        "Message", cascade="all, delete-orphan", foreign_keys="[Message.groupId]"
    )
    lastMessageId: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("messages.id"), nullable=True
    )


group_users = Table(
    "group_users",
    Base.metadata,
    Column("groupId", Integer, ForeignKey("groups.id"), primary_key=True),
    Column("accountId", Integer, ForeignKey("accounts.id"), primary_key=True),
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
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    groupId: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("groups.id"), nullable=True
    )
    senderId: Mapped[int] = mapped_column(
        Integer, ForeignKey("accounts.id"), nullable=False
    )
    recipientId: Mapped[int] = mapped_column(
        Integer, ForeignKey("accounts.id"), nullable=True
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
    sender: Mapped[Account] = relationship("Account", foreign_keys=[senderId])
    recipient: Mapped[Account] = relationship("Account", foreign_keys=[recipientId])


def process_messages_metadata(
    config: FanslyConfig, state: DownloadState, messages: list[dict[str, any]]
) -> None:
    """Process message metadata and store in the database.

    Processes a list of messages, creating or updating message records and their
    attachments. Handles message timestamps and content relationships.

    Args:
        config: FanslyConfig instance for database access
        state: Current download state
        messages: List of message data dictionaries
    """
    from .account import process_media_bundles
    from .attachment import Attachment, ContentType

    message_columns = {column.name for column in inspect(Message).columns}

    with config._database.sync_session() as session:
        # Process any media bundles if present
        if "accountMediaBundles" in messages:
            # Try to get account ID from the first message
            account_id = None
            if messages.get("messages"):
                first_message = messages["messages"][0]
                account_id = first_message.get("senderId") or first_message.get(
                    "recipientId"
                )

            if account_id:
                process_media_bundles(
                    config, account_id, messages["accountMediaBundles"], session
                )
        session.commit()

        for message in messages:
            # Convert timestamps to datetime objects
            date_fields = ("createdAt", "deletedAt")
            for date_field in date_fields:
                if date_field in message and message[date_field]:
                    timestamp = message[date_field]
                    # Convert to float to handle both int and float timestamps
                    timestamp_float = float(timestamp)
                    # If timestamp is in milliseconds (13 digits), convert to seconds
                    if (
                        timestamp_float > 1e11
                    ):  # More precise threshold for milliseconds
                        timestamp_float /= 1000
                    message[date_field] = datetime.fromtimestamp(
                        timestamp_float, tz=timezone.utc
                    )
            filtered_message = {
                key: message[key] for key in message if key in message_columns
            }
            existing_message = (
                session.query(Message)
                .filter_by(
                    senderId=message.get("senderId"),
                    recipientId=message.get("recipientId"),
                    createdAt=message.get("createdAt"),
                )
                .first()
            )
            # Query first approach
            existing_message = (
                session.query(Message)
                .filter_by(
                    senderId=message.get("senderId"),
                    recipientId=message.get("recipientId"),
                    createdAt=message.get("createdAt"),
                )
                .first()
            )

            if existing_message:
                for key, value in filtered_message.items():
                    setattr(existing_message, key, value)
            else:
                # Ensure required fields are present
                required_fields = {
                    "senderId": None,
                    "content": "",
                    "createdAt": datetime.now(timezone.utc),
                    "deleted": False,
                }
                # Update with any provided values
                message_data = {**required_fields, **filtered_message}
                existing_message = Message(**message_data)
                session.add(existing_message)
            session.flush()
            if "attachments" in message:
                for attachment in message["attachments"]:
                    attachment["messageId"] = existing_message.id
                    # Convert contentType to enum
                    try:
                        attachment["contentType"] = ContentType(
                            attachment["contentType"]
                        )
                    except ValueError:
                        old_content_type = attachment["contentType"]
                        attachment["contentType"] = None
                        json_output(
                            2,
                            f"meta/mess - _p_m_p - invalid_content_type: {old_content_type}",
                            attachment,
                        )
                    existing_attachment = (
                        session.query(Attachment)
                        .filter_by(
                            messageId=existing_message.id,
                            contentId=attachment["contentId"],
                        )
                        .first()
                    )
                    if existing_attachment:
                        for key, value in attachment.items():
                            setattr(existing_attachment, key, value)
                    else:
                        session.add(Attachment(**attachment))
                    session.flush()
        session.commit()


def process_groups_response(
    config: FanslyConfig, state: DownloadState, response: dict[str, any]
) -> None:
    """Process group messaging response data.

    Processes group messaging data, creating or updating groups and their
    relationships with users. Also handles aggregated data like accounts.

    Args:
        config: FanslyConfig instance for database access
        state: Current download state
        response: Response data containing groups and aggregated data
    """
    from .account import process_account_data

    # Process accounts first
    aggregation_data: dict = response.get("aggregationData", {})
    json_output(1, "meta/mess - p_g_resp - aggregation_data", aggregation_data)
    accounts: list = aggregation_data.get("accounts", {})
    for account in accounts:
        json_output(1, "meta/mess - p_g_resp - account", account)
        process_account_data(config, data=account, state=state)

    # Now process groups
    group_columns = {column.name for column in inspect(Group).columns}
    data: list[dict] = response.get("data", {})
    json_output(1, "meta/mess - p_g_resp - data", data)
    groups: list = aggregation_data.get("groups", {})

    with config._database.sync_session() as session:
        # Process groups from data
        for data_group in data:
            data_group["id"] = data_group.get("groupId")
            if "account_id" in data_group:
                data_group["createdBy"] = data_group["account_id"]

            # Filter group data
            filtered_group = {
                key: data_group[key] for key in data_group if key in group_columns
            }

            # Track relationships even though we're not enforcing them
            if "createdBy" in filtered_group:
                log_missing_relationship(
                    session=session,
                    table_name="groups",
                    field_name="createdBy",
                    missing_id=filtered_group["createdBy"],
                    referenced_table="accounts",
                    context={
                        "groupId": filtered_group.get("id"),
                        "source": "data_groups",
                    },
                )

            if "lastMessageId" in filtered_group:
                message_exists = log_missing_relationship(
                    session=session,
                    table_name="groups",
                    field_name="lastMessageId",
                    missing_id=filtered_group["lastMessageId"],
                    referenced_table="messages",
                    context={
                        "groupId": filtered_group.get("id"),
                        "source": "data_groups",
                    },
                )
                # Still remove lastMessageId if message doesn't exist to avoid potential issues
                if not message_exists:
                    del filtered_group["lastMessageId"]
            # Query first approach
            existing_group = (
                session.query(Group).filter_by(id=filtered_group.get("id")).first()
            )

            # Ensure required fields are present
            if "createdBy" not in filtered_group:
                json_output(
                    1,
                    "meta/mess - missing_required_field",
                    {
                        "groupId": filtered_group.get("id"),
                        "missing_field": "createdBy",
                        "source": "data_groups",
                    },
                )
                continue  # Skip this group if createdBy is missing

            if existing_group:
                for key, value in filtered_group.items():
                    setattr(existing_group, key, value)
            else:
                existing_group = Group(**filtered_group)
                session.add(existing_group)

        # Process groups from aggregation data
        for group in groups:
            json_output(1, "meta/mess - p_g_resp - group", group)
            if "account_id" in group:
                group["createdBy"] = group["account_id"]

            # Filter group data
            filtered_group = {key: group[key] for key in group if key in group_columns}

            # Check if createdBy account exists
            if "createdBy" in filtered_group:
                account_exists = (
                    session.query(Account)
                    .filter_by(id=filtered_group["createdBy"])
                    .first()
                    is not None
                )
                if not account_exists:
                    json_output(
                        1,
                        "meta/mess - p_g_resp - skipping_group",
                        {
                            "groupId": filtered_group.get("id"),
                            "createdBy": filtered_group["createdBy"],
                            "reason": "Creator account does not exist in database",
                        },
                    )
                    continue  # Skip this group entirely since creator is required

            # Check if lastMessageId exists
            if "lastMessageId" in filtered_group:
                message_exists = (
                    session.query(Message)
                    .filter_by(id=filtered_group["lastMessageId"])
                    .first()
                    is not None
                )
                if not message_exists:
                    json_output(
                        1,
                        "meta/mess - p_g_resp - skipping_last_message",
                        {
                            "groupId": filtered_group.get("id"),
                            "lastMessageId": filtered_group["lastMessageId"],
                            "reason": "Message does not exist in database",
                        },
                    )
                    del filtered_group["lastMessageId"]
            # Query first approach
            existing_group = (
                session.query(Group).filter_by(id=filtered_group.get("id")).first()
            )

            # Ensure required fields are present
            if "createdBy" not in filtered_group:
                json_output(
                    1,
                    "meta/mess - missing_required_field",
                    {
                        "groupId": filtered_group.get("id"),
                        "missing_field": "createdBy",
                        "source": "aggregation_groups",
                    },
                )
                continue  # Skip this group if createdBy is missing

            if existing_group:
                for key, value in filtered_group.items():
                    setattr(existing_group, key, value)
            else:
                existing_group = Group(**filtered_group)
                session.add(existing_group)

            # Process group users
            existing_group = (
                session.query(Group).filter_by(id=filtered_group.get("id")).first()
            )
            if existing_group:
                existing_group.users = set()
                for user in group.get("users", ()):
                    user_id = user.get("userId")

                    # Track missing user accounts
                    account_exists = log_missing_relationship(
                        session=session,
                        table_name="group_users",
                        field_name="accountId",
                        missing_id=user_id,
                        referenced_table="accounts",
                        context={"groupId": existing_group.id, "source": "group_users"},
                    )

                    # Always try to add the user since foreign keys are disabled
                    group_user = {
                        "groupId": existing_group.id,
                        "accountId": user_id,
                    }
                    existing_group_user = (
                        session.query(group_users).filter_by(**group_user).first()
                    )
                    if not existing_group_user:
                        session.execute(group_users.insert().values(group_user))

        # Commit all changes
        session.commit()

        # Print summary of missing relationships
        from .relationship_logger import print_missing_relationships_summary

        print_missing_relationships_summary()
