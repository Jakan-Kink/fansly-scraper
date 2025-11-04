"""Test utilities for metadata tests.

This module provides helper functions and utilities for testing the metadata
package, including data generation, validation, and common test operations.
"""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from metadata import Account, AccountMedia, Media, Message, Post, Wall


async def create_test_account(
    session: AsyncSession, id: int = 1, username: str = "test_user", **kwargs
) -> Account:
    """Create a test account with optional attributes."""
    account = Account(id=id, username=username, **kwargs)
    session.add(account)
    await session.commit()
    return account


async def create_test_account_media(
    session: AsyncSession, account_id: int, media_id: int, id: int = 1, **kwargs
) -> AccountMedia:
    """Create a test account media association with optional attributes."""
    account_media = AccountMedia(
        id=id,
        accountId=account_id,
        mediaId=media_id,
        createdAt=kwargs.pop("createdAt", datetime.now(UTC)),
        **kwargs,
    )
    session.add(account_media)
    await session.commit()
    return account_media


async def create_test_media(
    session: AsyncSession,
    account_id: int,
    id: int = 1,
    mimetype: str = "video/mp4",
    **kwargs,
) -> Media:
    """Create a test media item with optional attributes."""
    media = Media(id=id, accountId=account_id, mimetype=mimetype, **kwargs)
    session.add(media)
    await session.commit()
    return media


def create_test_post(
    session: Session, account_id: int, id: int = 1, content: str = "Test post", **kwargs
) -> Post:
    """Create a test post with optional attributes."""
    post = Post(
        id=id,
        accountId=account_id,
        content=content,
        createdAt=kwargs.pop("createdAt", datetime.now(UTC)),
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
        createdAt=kwargs.pop("createdAt", datetime.now(UTC)),
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


async def create_test_data_set(
    session: AsyncSession,
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
        await session.flush()

        # Create media for each account
        for account in data["accounts"]:
            for _ in range(num_media_per_account):
                media = await create_test_media(
                    session=session,
                    account_id=account.id,
                    id=len(data["media"]) + 1,
                    mimetype="video/mp4",
                )
                session.add(media)
                data["media"].append(media)
        await session.flush()

        # Create account_media associations
        for account in data["accounts"]:
            account_media_list = [
                media for media in data["media"] if media.accountId == account.id
            ]
            for media in account_media_list:
                account_media = await create_test_account_media(
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
                await session.refresh(account_media)
                await session.refresh(media)
                await session.refresh(account)
        await session.flush()

        # Create walls for each account
        for account in data["accounts"]:
            for j in range(num_walls_per_account):
                wall = Wall(
                    id=len(data["walls"]) + 1,
                    accountId=account.id,
                    name=f"Test wall {j + 1} for account {account.id}",
                    pos=j + 1,
                    createdAt=datetime.now(UTC),
                )
                session.add(wall)
                data["walls"].append(wall)
                print(f"Created wall {wall.id} for account {account.id}")
        await session.flush()

        # Create posts for each account
        for account in data["accounts"]:
            for j in range(num_posts_per_account):
                post = Post(
                    id=len(data["posts"]) + 1,
                    accountId=account.id,
                    content=f"Test post {j + 1} for account {account.id}",
                    createdAt=datetime.now(UTC),
                )
                session.add(post)
                data["posts"].append(post)
                print(f"Created post {post.id} for account {account.id}")
        await session.flush()

        # Refresh accounts to update their relationships
        for account in data["accounts"]:
            await session.refresh(account)

        # Commit all changes
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    else:
        return data


async def verify_relationship_integrity(
    session: AsyncSession, parent: Any, child_attr: str, expected_count: int
) -> bool:
    """Verify relationship integrity between parent and child objects."""
    print(
        f"\nVerifying {child_attr} relationship for {parent.__class__.__name__} (id={parent.id})"
    )

    if child_attr == "accountMedia":
        # Special handling for accountMedia relationship which goes through Media
        query = """
            SELECT COUNT(*)
            FROM account_media am
            JOIN media m ON m.id = am.mediaId
            WHERE m.accountId = :account_id
        """
        result = await session.execute(text(query), {"account_id": parent.id})
        count = result.scalar()

        # Debug queries
        media_result = await session.execute(
            text("SELECT * FROM media WHERE accountId = :account_id"),
            {"account_id": parent.id},
        )
        media_items = media_result.fetchall()
        print(f"Found {len(media_items)} media items for account {parent.id}")

        acc_media_result = await session.execute(
            text(
                "SELECT * FROM account_media am JOIN media m ON m.id = am.mediaId WHERE m.accountId = :account_id"
            ),
            {"account_id": parent.id},
        )
        acc_media_items = acc_media_result.fetchall()
        print(
            f"Found {len(acc_media_items)} account_media items for account {parent.id}"
        )
    elif child_attr == "walls":
        # Direct query for walls
        result = await session.execute(
            text("SELECT COUNT(*) FROM walls WHERE accountId = :account_id"),
            {"account_id": parent.id},
        )
        count = result.scalar()

        # Debug info
        walls_result = await session.execute(
            text("SELECT * FROM walls WHERE accountId = :account_id"),
            {"account_id": parent.id},
        )
        walls = walls_result.fetchall()
        print(f"Found {len(walls)} walls for account {parent.id}")
    else:
        # Default relationship counting with account filter
        result = await session.execute(
            text(f"SELECT COUNT(*) FROM {child_attr} WHERE accountId = :account_id"),
            {"account_id": parent.id},
        )
        count = result.scalar()

    print(f"Expected count: {expected_count}, Actual count: {count}")
    return count == expected_count


async def verify_media_variants(
    session: AsyncSession, media_id: str, expected_variant_types: list[int]
) -> bool:
    """Verify media variants exist and match expected types."""
    result = await session.execute(
        text("SELECT variants FROM account_media WHERE id = :media_id"),
        {"media_id": media_id},
    )
    variants = result.scalar()

    if not variants:
        return False

    variant_types = [v.get("type") for v in variants]
    return all(t in variant_types for t in expected_variant_types)


async def verify_media_bundle_content(
    session: AsyncSession, bundle_id: str, expected_media_ids: list[str]
) -> bool:
    """Verify media bundle contains expected media in correct order."""
    result = await session.execute(
        text(
            "SELECT accountMediaId FROM account_media_bundle_media "
            "WHERE bundleId = :bundle_id ORDER BY pos"
        ),
        {"bundle_id": bundle_id},
    )
    stored_media_ids = [row[0] for row in result]
    return stored_media_ids == expected_media_ids


async def verify_preview_variants(
    session: AsyncSession, preview_id: str, expected_resolutions: list[tuple[int, int]]
) -> bool:
    """Verify preview image variants exist with expected resolutions."""
    result = await session.execute(
        text("SELECT variants FROM account_media WHERE id = :preview_id"),
        {"preview_id": preview_id},
    )
    variants = result.scalar()

    if not variants:
        return False

    variant_resolutions = [(v.get("width"), v.get("height")) for v in variants]
    return all(res in variant_resolutions for res in expected_resolutions)
