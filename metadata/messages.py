from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Table
from sqlalchemy.inspection import inspect

# from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Mapped, mapped_column, relationship

from textio import json_output

from .base import Base

if TYPE_CHECKING:
    from config import FanslyConfig
    from download.core import DownloadState

    from .account import Account
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
                    message[date_field] = datetime.fromtimestamp(
                        (
                            message[date_field] / 1000
                            if message[date_field] > 1e10
                            else message[date_field]
                        ),
                        tz=timezone.utc,
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
            if existing_message:
                for key, value in filtered_message.items():
                    setattr(existing_message, key, value)
            else:
                session.add(Message(**filtered_message))
            session.flush()
            existing_message = (
                session.query(Message)
                .filter_by(
                    senderId=message.get("senderId"),
                    recipientId=message.get("recipientId"),
                    createdAt=message.get("createdAt"),
                )
                .first()
            )
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

    group_columns = {column.name for column in inspect(Group).columns}
    data: list[dict] = response.get("data", {})
    json_output(1, "meta/mess - p_g_resp - data", data)
    with config._database.sync_session() as session:
        for data_group in data:
            data_group["id"] = data_group.get("groupId")
            existing_group = (
                session.query(Group).filter_by(id=data_group.get("id")).first()
            )
            if existing_group:
                if data_group.get("lastMessageId"):
                    existing_group.lastMessageId = data_group.get("lastMessageId")
            else:
                filtered_group = {
                    key: data_group[key] for key in data_group if key in group_columns
                }
                session.add(Group(**filtered_group))
            session.commit()
    aggregation_data: dict = response.get("aggregationData", {})
    json_output(1, "meta/mess - p_g_resp - aggregation_data", aggregation_data)
    groups: list = aggregation_data.get("groups", {})
    accounts: list = aggregation_data.get("accounts", {})
    # Get valid column names for Group and Account models
    group_columns = {column.name for column in inspect(Group).columns}
    with config._database.sync_session() as session:
        for group in groups:
            json_output(1, "meta/mess - p_g_resp - group", group)
            filtered_group = {key: group[key] for key in group if key in group_columns}
            existing_group = (
                session.query(Group).filter_by(id=filtered_group.get("id")).first()
            )
            if existing_group:
                for key, value in filtered_group.items():
                    setattr(existing_group, key, value)
            else:
                session.add(Group(**filtered_group))
            session.commit()
            existing_group = (
                session.query(Group).filter_by(id=filtered_group.get("id")).first()
            )
            existing_group.users = set()
            for user in group.get("users", ()):
                group_user = {
                    "groupId": existing_group.id,
                    "accountId": user.get("userId"),
                }
                existing_group_user = (
                    session.query(group_users).filter_by(**group_user).first()
                )
                if not existing_group_user:
                    session.execute(group_users.insert().values(group_user))
            session.commit()
    for account in accounts:
        json_output(1, "meta/mess - p_g_resp - account", account)
        process_account_data(config, data=account, state=state)
