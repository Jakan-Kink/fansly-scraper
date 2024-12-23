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

    # Create accounts
    for i in range(num_accounts):
        account = create_test_account(session, id=i + 1, username=f"test_user_{i + 1}")
        data["accounts"].append(account)

        # Create media and account_media for account
        for j in range(num_media_per_account):
            # Create media
            media = create_test_media(session, account.id, id=len(data["media"]) + 1)
            data["media"].append(media)

            # Create account_media association
            account_media = create_test_account_media(
                session,
                account_id=account.id,
                media_id=media.id,
                id=len(data["account_media"]) + 1,
            )
            data["account_media"].append(account_media)

        # Create posts for account
        for j in range(num_posts_per_account):
            post = create_test_post(session, account.id, id=len(data["posts"]) + 1)
            data["posts"].append(post)

        # Create walls for account
        for j in range(num_walls_per_account):
            wall = create_test_wall(
                session, account.id, id=len(data["walls"]) + 1, pos=j + 1
            )
            data["walls"].append(wall)

    return data


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
            return False

    # Verify bidirectional relationships
    for child in children:
        # Get the parent relationship attribute from the child
        for rel in child.__mapper__.relationships:
            if rel.back_populates == child_attr:
                parent_attr = rel.key
                child_parent = getattr(child, parent_attr)
                if child_parent != parent:
                    return False

    return True
