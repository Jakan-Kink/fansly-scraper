"""Common utilities for media handling across different models.

This module provides shared functionality for processing media items, previews,
and media bundles across different models in the application.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

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
    # Handle integer IDs
    if isinstance(media_item, int):
        if not (-(2**63) <= media_item <= 2**63 - 1):
            json_output(
                2,
                "meta/media - media_id_out_of_range",
                {
                    "media_id": media_item,
                    f"{context_type}_id": context_id,
                    "pos": pos,
                },
            )
            return None
        return media_item

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
                return None
            return media_id
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

    # Handle non-dict objects
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
        return None

    return None  # For dict case, handled separately


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

    if (
        not isinstance(preview_data, (dict, str))
        or isinstance(preview_data, str)
        and not preview_data.strip()
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
) -> None:
    """Link media to bundle with position.

    Args:
        session: SQLAlchemy session
        bundle_id: ID of the bundle
        media_id: ID of the media to link
        pos: Position in the bundle
        table: Name of the junction table
    """
    from .base import Base

    # Get the table object
    bundle_media_table = Base.metadata.tables[table]

    # Link media to bundle
    await session.execute(
        bundle_media_table.insert()
        .prefix_with("OR IGNORE")
        .values(
            bundle_id=bundle_id,
            media_id=media_id,
            pos=pos,
        )
    )
    await session.flush()
