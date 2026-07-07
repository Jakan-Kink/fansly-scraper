"""Media processing module.

Handles media items, their locations, variants, and download tracking.

Media construction uses the Pydantic pipeline:
- model_validate handles: str→int IDs, timestamp coercion, extra="ignore",
  identity map caching, nested variant/location construction via
  _process_nested_cache_lookups, accountId enrichment via _enrich_child_dict
- store.save() handles: DB persistence + _sync_associations for variant
  junction and location records
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import JsonValue

from helpers.common import JsonDict, expect_dict, expect_list
from textio import json_output

from .models import AccountMedia, Media, get_store


if TYPE_CHECKING:
    from config import FanslyConfig
    from download.core import DownloadState


async def process_media_info(
    config: FanslyConfig,  # noqa: ARG001
    media_infos: JsonDict,
) -> None:
    """Process accountMedia dicts — persist Media + AccountMedia to DB.

    Media objects (with variants/locations) must be saved before AccountMedia
    (FK constraint: AccountMedia.mediaId → Media.id).

    model_validate handles: dedup (identity map), str→int IDs, timestamp
    coercion, nested variant/location construction, accountId enrichment.
    """
    store = get_store()
    raw_items = media_infos.get("batch")
    items_to_process: list[JsonValue] = (
        expect_list(raw_items, "media_infos.batch")
        if raw_items is not None
        else [media_infos]
    )

    for raw_info in items_to_process:
        info = expect_dict(raw_info, "accountMedia item")
        account_id = info.get("accountId")

        if account_id is None:
            json_output(
                2,
                "meta/media - missing_accountId",
                {"mediaId": info.get("id"), "keys": list(info.keys())},
            )
            continue

        # Persist Media (with nested variants/locations) — FK constraint first
        for key in ("media", "preview"):
            if key in info:
                nested = expect_dict(info[key], f"accountMedia.{key}")
                nested.setdefault("accountId", account_id)
                media = Media.model_validate(nested)
                await store.save(media)

        # Persist AccountMedia (extra="ignore" drops nested media/preview dicts)
        am = AccountMedia.model_validate(info)
        await store.save(am)


async def process_media_item_dict(
    config: FanslyConfig,  # noqa: ARG001
    media_item: JsonDict,
    account_id: int | None = None,
) -> None:
    """Process a single media item dictionary."""
    store = get_store()
    media_item.setdefault("accountId", account_id)
    media = Media.model_validate(media_item)
    await store.save(media)


def _should_skip_media(media_obj: Media | None) -> bool:
    """Check if media should be skipped (already downloaded with hash)."""
    return bool(
        media_obj
        and media_obj.is_downloaded
        and media_obj.content_hash
        and media_obj.local_filename
    )


async def process_media_download(
    config: FanslyConfig,  # noqa: ARG001
    state: DownloadState,
    media: Media,
) -> Media | None:
    """Check if media needs downloading, persist if needed.

    The identity map (preloaded at store init) has DB state including
    is_downloaded/content_hash/local_filename. The media object from
    parse_media_info is the same identity-map instance, so checking
    it directly gives us the DB state.

    Returns:
        Media if it needs downloading, None if already downloaded
    """
    if _should_skip_media(media):
        return None

    store = get_store()

    if not state.creator_id:
        raise ValueError(
            "Cannot create Media record: creator_id is required but not available in state"
        )

    if not media.accountId:
        media.accountId = state.creator_id

    await store.save(media)
    return media
