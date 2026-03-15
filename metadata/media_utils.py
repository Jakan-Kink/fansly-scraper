"""Common utilities for media handling across different models."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Protocol

from textio import json_output

from .media import process_media_item_dict
from .models import AccountMedia, Media, get_store


if TYPE_CHECKING:
    from config import FanslyConfig


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
    """Validate and convert media item to ID."""

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

    if isinstance(media_item, int):
        return media_item if _validate_range(media_item) else None

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


async def process_preview(
    config: FanslyConfig,
    parent: HasPreview,
    preview_data: dict | str | None,
    context_type: str = "bundle",
    account_id: int | None = None,
) -> None:
    """Process preview media for a model."""

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
        await process_media_item_dict(config, preview_data, account_id=account_id)


async def link_media_to_bundle(
    bundle_id: int,
    media_id: int,
    pos: int,
    account_id: int | None = None,
) -> None:
    """Link media to bundle with position."""
    store = get_store()

    media_obj = await store.get(Media, media_id)
    if account_id is None and media_obj:
        account_id = media_obj.accountId

    if account_id is None:
        json_output(
            2,
            "meta/media - bundle_media_missing_account",
            {"bundle_id": bundle_id, "media_id": media_id, "pos": pos},
        )
        return

    # Ensure Media record exists
    if media_obj is None:
        media_obj = Media(id=media_id, accountId=account_id)
        media_obj._is_new = True
        await store.save(media_obj)

    # Ensure AccountMedia record exists
    am_obj = await store.get(AccountMedia, media_id)
    if am_obj is None:
        am_obj = AccountMedia(
            id=media_id,
            accountId=account_id,
            mediaId=media_id,
            createdAt=datetime.now(UTC),
            deleted=False,
            access=False,
        )
        am_obj._is_new = True
        await store.save(am_obj)

    # Link to bundle via junction table
    await store.bulk_upsert_records(
        "account_media_bundle_media",
        [{"bundle_id": bundle_id, "media_id": media_id, "pos": pos}],
    )
