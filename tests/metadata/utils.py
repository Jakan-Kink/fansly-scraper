"""Test utilities for metadata tests.

This module provides helper functions and utilities for testing the metadata
package, including data generation, validation, and common test operations.
"""

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from metadata import Account, AccountMedia, Media, Message, Post, Wall


def create_test_account(
    session: Session, id: int = 1, username: str = "test_user", **kwargs
) -> Account:
    """Create a test account with optional attributes."""
    account = Account(id=id, username=username, **kwargs)
    session.add(account)
    session.commit()
    return account


def create_test_account_media(
    session: Session, account_id: int, media_id: int, id: int = 1, **kwargs
) -> AccountMedia:
    """Create a test account media association with optional attributes."""
    account_media = AccountMedia(
        id=id,
        accountId=account_id,
        mediaId=media_id,
        createdAt=kwargs.pop("createdAt", datetime.now(timezone.utc)),
        **kwargs,
    )
    session.add(account_media)
    session.commit()
    return account_media


def create_test_media(
    session: Session,
    account_id: int,
    id: int = 1,
    mimetype: str = "video/mp4",
    **kwargs,
) -> Media:
    """Create a test media item with optional attributes."""
    media = Media(id=id, accountId=account_id, mimetype=mimetype, **kwargs)
    session.add(media)
    session.commit()
    return media


def create_test_post(
    session: Session, account_id: int, id: int = 1, content: str = "Test post", **kwargs
) -> Post:
    """Create a test post with optional attributes."""
    post = Post(
        id=id,
        accountId=account_id,
        content=content,
        createdAt=kwargs.pop("createdAt", datetime.now(timezone.utc)),
        **kwargs,
    )
    session.add(post)
    session.commit()
    return post


def create_test_wall(
    session: Session, account_id: int, id: int = 1, name: str = "Test Wall", **kwargs
) -> Wall:
    """Create a test wall with optional attributes."""
    wall = Wall(id=id, accountId=account_id, name=name, **kwargs)
    session.add(wall)
    session.commit()
    return wall


def create_test_message(
    session: Session,
    sender_id: int,
    id: int = 1,
    content: str = "Test message",
    **kwargs,
) -> Message:
    """Create a test message with optional attributes."""
    message = Message(
        id=id,
        senderId=sender_id,
        content=content,
        createdAt=kwargs.pop("createdAt", datetime.now(timezone.utc)),
        **kwargs,
    )
    session.add(message)
    session.commit()
    return message


def verify_index_usage(session: Session, table: str, column: str) -> bool:
    """Verify that a query on a column uses an index.

    Args:
        session: Database session
        table: Name of the table
        column: Name of the column to check

    Returns:
        bool: True if the query uses an index, False otherwise
    """
    result = session.execute(
        text(f"EXPLAIN QUERY PLAN SELECT * FROM {table} WHERE {column} = 1")
    )
    plan = result.fetchall()
    return any("USING INDEX" in str(row) for row in plan)


def create_test_data_set(
    session: Session,
    num_accounts: int = 2,
    num_media_per_account: int = 3,
    num_posts_per_account: int = 2,
    num_walls_per_account: int = 1,
) -> dict[str, list[Any]]:
    """Create a complete set of test data.

    Args:
        session: Database session
        num_accounts: Number of accounts to create
        num_media_per_account: Number of media items per account
        num_posts_per_account: Number of posts per account
        num_walls_per_account: Number of walls per account

    Returns:
        Dict containing lists of created objects by type
    """
    data = {"accounts": [], "media": [], "account_media": [], "posts": [], "walls": []}

    try:
        # Create accounts first
        for i in range(num_accounts):
            account = Account(id=i + 1, username=f"test_user_{i + 1}")
            session.add(account)
            data["accounts"].append(account)
        session.flush()

        # Create media for each account
        for account in data["accounts"]:
            for j in range(num_media_per_account):
                media = create_test_media(
                    session=session,
                    account_id=account.id,
                    id=len(data["media"]) + 1,
                    mimetype="video/mp4",
                )
                session.add(media)
                data["media"].append(media)
        session.flush()

        # Create account_media associations
        for account in data["accounts"]:
            account_media_list = [
                media for media in data["media"] if media.accountId == account.id
            ]
            for j, media in enumerate(account_media_list):
                account_media = create_test_account_media(
                    session=session,
                    account_id=account.id,
                    media_id=media.id,
                    id=len(data["account_media"]) + 1,
                )
                data["account_media"].append(account_media)
                print(
                    f"Created AccountMedia: {account_media.id} for Account: {account.id} with Media: {media.id}"
                )
                # Ensure the media object is correctly associated with the account
                session.refresh(account_media)
                session.refresh(media)
                session.refresh(account)
        session.flush()

        # Create walls for each account
        for account in data["accounts"]:
            for j in range(num_walls_per_account):
                wall = Wall(
                    id=len(data["walls"]) + 1,
                    accountId=account.id,
                    name=f"Test wall {j + 1} for account {account.id}",
                    pos=j + 1,
                    createdAt=datetime.now(timezone.utc),
                )
                session.add(wall)
                data["walls"].append(wall)
                print(f"Created wall {wall.id} for account {account.id}")
        session.flush()

        # Create posts for each account
        for account in data["accounts"]:
            for j in range(num_posts_per_account):
                post = Post(
                    id=len(data["posts"]) + 1,
                    accountId=account.id,
                    content=f"Test post {j + 1} for account {account.id}",
                    createdAt=datetime.now(timezone.utc),
                )
                session.add(post)
                data["posts"].append(post)
                print(f"Created post {post.id} for account {account.id}")
        session.flush()

        # Refresh accounts to update their relationships
        for account in data["accounts"]:
            session.refresh(account)

        # Commit all changes
        session.commit()
        return data
    except Exception:
        session.rollback()
        raise


def verify_relationship_integrity(
    session: Session, parent: Any, child_attr: str, expected_count: int | None = None
) -> bool:
    """Verify the integrity of a relationship.

    Args:
        session: Database session
        parent: Parent object
        child_attr: Name of the relationship attribute
        expected_count: Expected number of children (if known)

    Returns:
        bool: True if relationship is valid, False otherwise
    """
    session.refresh(parent)
    children = getattr(parent, child_attr)

    if expected_count is not None:
        if len(children) != expected_count:
            print(
                f"Expected {expected_count} children, but found {len(children)} for parent {parent} and child_attr {child_attr}"
            )
            return False

    # Verify bidirectional relationships
    for child in children:
        # Get the parent relationship attribute from the child
        parent_attr = None
        for rel in child.__mapper__.relationships:
            if rel.back_populates == child_attr:
                parent_attr = rel.key
                break

        if parent_attr is None:
            print(
                f"No back_populates relationship found for {child_attr} in {type(child).__name__} for parent {parent}"
            )
            return False

        child_parent = getattr(child, parent_attr)
        if child_parent != parent:
            print(
                f"Child's parent attribute {parent_attr} does not match the parent in {type(child).__name__} for parent {parent}"
            )
            print(
                f"Expected parent: {parent}, but found: {child_parent} for child {child}"
            )
            return False

        # Verify foreign key constraints
        for column in child.__table__.columns:
            if column.foreign_keys:
                for fk in column.foreign_keys:
                    if fk.column.table == parent.__table__:
                        if getattr(child, column.name) != getattr(
                            parent, fk.column.name
                        ):
                            print(
                                f"Foreign key constraint failed for column {column.name} in {type(child).__name__} for parent {parent}"
                            )
                            print(
                                f"Expected value: {getattr(parent, fk.column.name)}, but found: {getattr(child, column.name)} for child {child}"
                            )
                            return False

    # Verify that the accountId in AccountMedia matches the id in Account
    if child_attr == "accountMedia":
        for account_media in children:
            if account_media.accountId != parent.id:
                print(
                    f"AccountMedia's accountId {account_media.accountId} does not match Account's id {parent.id} for account_media {account_media}"
                )
                return False
            # Verify that the mediaId in AccountMedia references a valid Media object
            if account_media.mediaId is None or account_media.media is None:
                print(
                    f"AccountMedia's mediaId {account_media.mediaId} does not reference a valid Media object for account_media {account_media}"
                )
                return False
            if account_media.media.accountId != parent.id:
                print(
                    f"Media's accountId {account_media.media.accountId} does not match Account's id {parent.id} for account_media {account_media}"
                )
                return False
            if account_media.media.id != account_media.mediaId:
                print(
                    f"AccountMedia's mediaId {account_media.mediaId} does not match Media's id {account_media.media.id} for account_media {account_media}"
                )
                return False
            print(f"Verified AccountMedia: {account_media.id} for Account: {parent.id}")
    return True
