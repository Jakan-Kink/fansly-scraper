"""Common utilities for media handling across different models.

This module provides shared functionality for processing media items, previews,
and media bundles across different models in the application.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Protocol

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from textio import json_output


if TYPE_CHECKING:
    from config import FanslyConfig

    from .media import Media


class HasPreview(Protocol):
    """Protocol for models that can have preview media."""

    id: int
    preview: Media | None


def validate_media_id(
    media_item: str | dict | int,
    context_id: int,
    pos: int | None = None,
    context_type: str = "bundle",
) -> int | None:
    """Validate and convert media item to ID.

    Args:
        media_item: Media item to validate (string ID, dict with data, or int ID)
        context_id: ID of the parent object (e.g., bundle ID)
        pos: Optional position of the media item
        context_type: Type of parent object for logging

    Returns:
        Valid media ID or None if invalid
    """

    # Helper to validate range
    def _validate_range(media_id: int) -> bool:
        if not (-(2**63) <= media_id <= 2**63 - 1):
            json_output(
                2,
                "meta/media - media_id_out_of_range",
                {
                    "media_id": media_id,
                    f"{context_type}_id": context_id,
                    "pos": pos,
                },
            )
            return False
        return True

    # Handle integer IDs
    if isinstance(media_item, int):
        return media_item if _validate_range(media_item) else None

    # Handle string IDs
    if isinstance(media_item, str):
        if not media_item.isdigit():
            json_output(
                2,
                "meta/media - non_numeric_media_id",
                {
                    "media_id": media_item,
                    f"{context_type}_id": context_id,
                    "pos": pos,
                },
            )
            return None

        try:
            media_id = int(media_item)
        except ValueError:
            json_output(
                2,
                "meta/media - invalid_media_id",
                {
                    "media_id": media_item,
                    f"{context_type}_id": context_id,
                    "pos": pos,
                },
            )
            return None
        else:
            return media_id if _validate_range(media_id) else None

    # Handle dict and other types (return None for both)
    if not isinstance(media_item, dict):
        json_output(
            2,
            "meta/media - invalid_media_item_type",
            {
                "type": type(media_item).__name__,
                "value": str(media_item),
                f"{context_type}_id": context_id,
                "pos": pos,
            },
        )

    # For dict case and invalid types, both return None
    return None


async def process_preview(
    session: AsyncSession,
    config: FanslyConfig,
    parent: HasPreview,
    preview_data: dict | str | None,
    context_type: str = "bundle",
) -> None:
    """Process preview media for a model.

    Args:
        session: SQLAlchemy session
        config: FanslyConfig instance
        parent: Parent model instance that has preview
        preview_data: Preview data to process
        context_type: Type of parent object for logging
    """
    from .media import _process_media_item_dict_inner

    if not preview_data:
        return

    if not isinstance(preview_data, (dict, str)) or (
        isinstance(preview_data, str) and not preview_data.strip()
    ):
        json_output(
            2,
            "meta/media - invalid_preview_type",
            {
                "type": type(preview_data).__name__,
                "value": str(preview_data),
                f"{context_type}_id": parent.id,
            },
        )
        return

    if isinstance(preview_data, dict):
        await _process_media_item_dict_inner(config, preview_data, session=session)


async def link_media_to_bundle(
    session: AsyncSession,
    bundle_id: int,
    media_id: int,
    pos: int,
    table: str = "account_media_bundle_media",
    account_id: int | None = None,
) -> None:
    """Link media to bundle with position.

    Args:
        session: SQLAlchemy session
        bundle_id: ID of the bundle
        media_id: ID of the media to link
        pos: Position in the bundle
        table: Name of the junction table
    """
    from .account import AccountMedia
    from .base import Base
    from .media import Media

    media_obj = await session.get(Media, media_id)
    if account_id is None and media_obj:
        account_id = media_obj.accountId

    if account_id is None:
        json_output(
            2,
            "meta/media - bundle_media_missing_account",
            {
                "bundle_id": bundle_id,
                "media_id": media_id,
                "pos": pos,
            },
        )
        return

    if media_obj is None:
        session.add(Media(id=media_id, accountId=account_id))
        await session.flush()

    account_media_obj = await session.get(AccountMedia, media_id)
    if account_media_obj is None:
        session.add(
            AccountMedia(
                id=media_id,
                accountId=account_id,
                mediaId=media_id,
                createdAt=datetime.now(UTC),
                deleted=False,
                access=False,
            )
        )
        await session.flush()

    # Get the table object
    bundle_media_table = Base.metadata.tables[table]

    # Link media to bundle using PostgreSQL upsert
    insert_stmt = pg_insert(bundle_media_table).values(
        bundle_id=bundle_id,
        media_id=media_id,
        pos=pos,
    )
    upsert_stmt = insert_stmt.on_conflict_do_nothing()
    await session.execute(upsert_stmt)
    await session.flush()
