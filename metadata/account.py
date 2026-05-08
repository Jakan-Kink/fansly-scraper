"""Account processing module.

Handles account data, media bundles, avatars, banners, timeline stats,
and story states.
"""

from __future__ import annotations

import copy
from typing import TYPE_CHECKING, Any

from textio import json_output

from .entity_store import PostgresEntityStore
from .models import (
    Account,
    AccountMedia,
    AccountMediaBundle,
    get_store,
)


if TYPE_CHECKING:
    from config import FanslyConfig
    from download.core import DownloadState


async def process_media_bundles_data(
    config: FanslyConfig,
    data: dict[str, Any],
    id_fields: list[str] | None = None,
) -> None:
    """Process media bundles from data."""
    if id_fields is None:
        id_fields = ["senderId", "recipientId"]

    if "accountMediaBundles" not in data:
        return

    account_id = None
    if data.get("messages") or data.get("posts"):
        first_item = (data.get("messages") or data.get("posts"))[0]
        for field in id_fields:
            if account_id := first_item.get(field):
                break

    if account_id:
        account_id = int(account_id)
        await process_media_bundles(
            config=config,
            account_id=account_id,
            media_bundles=data["accountMediaBundles"],
        )


async def _process_single_bundle(
    bundle: dict,
    account_id: int,
    config: FanslyConfig,
) -> None:
    """Process a single media bundle.

    _prepare_bundle_data: converts bundleContent/accountMediaIds → accountMedia IDs
    _process_nested_cache_lookups: resolves preview → Media, accountMedia → AccountMedia
    _sync_associations: ordered junction (DELETE + re-INSERT with pos)

    The Fansly API truncates accountMedia objects in timeline responses
    when a bundle has >5 items (positions 0-4 present, 5+ dropped).
    After model_validate, we detect missing IDs and backfill them via
    a follow-up API call before saving (#63).
    """
    store = get_store()
    bundle.setdefault("accountId", account_id)

    # Capture all accountMediaIds before model_validate resolves (and drops) them
    all_media_ids = [int(mid) for mid in bundle.get("accountMediaIds", [])]

    bundle_obj = AccountMediaBundle.model_validate(bundle)

    # Detect accountMedia truncation: IDs the API listed but didn't include
    resolved_ids = {am.id for am in bundle_obj.accountMedia}
    missing_ids = [mid for mid in all_media_ids if mid not in resolved_ids]

    if missing_ids:
        await _backfill_missing_account_media(
            config,
            store,
            bundle_obj,
            missing_ids,
        )

    # Save preview Media first if resolved (FK constraint)
    if bundle_obj.preview:
        await store.save(bundle_obj.preview)

    await store.save(bundle_obj)


async def _backfill_missing_account_media(
    config: FanslyConfig,
    store: PostgresEntityStore,
    bundle_obj: AccountMediaBundle,
    missing_ids: list[int],
) -> None:
    """Fetch truncated accountMedia from API and attach to bundle.

    The Fansly API truncates accountMedia in timeline/message responses
    at 5 items per bundle. This fetches the missing items, processes
    them through the normal Pydantic pipeline, and appends them to the
    bundle so the junction table gets all positions.
    """
    from .media import process_media_info  # noqa: PLC0415,I001  # circular: metadata.media → metadata.account

    json_output(
        1,
        "meta/account - backfill_truncated_bundle",
        {
            "bundleId": bundle_obj.id,
            "missing_count": len(missing_ids),
            "missing_ids": missing_ids,
        },
    )

    try:
        api = config.get_api()
        media_ids_str = ",".join(str(mid) for mid in missing_ids)
        response = await api.get_account_media(media_ids_str)
        media_infos = api.get_json_response_contents(response)
    except Exception:
        json_output(
            2,
            "meta/account - backfill_api_error",
            {"bundleId": bundle_obj.id, "missing_ids": missing_ids},
        )
        return

    # Process through normal pipeline → populates identity map cache
    await process_media_info(config, {"batch": media_infos})

    # Re-resolve from cache and append to bundle
    for mid in missing_ids:
        cached = store.get_from_cache(AccountMedia, mid)
        if cached:
            bundle_obj.accountMedia.append(cached)
        else:
            json_output(
                2,
                "meta/account - backfill_still_missing",
                {"bundleId": bundle_obj.id, "accountMediaId": mid},
            )

    # Mark relationship dirty so save() syncs the junction table
    bundle_obj.mark_dirty()


async def process_media_bundles(
    config: FanslyConfig,
    account_id: int,
    media_bundles: list[dict],
) -> None:
    """Process media bundles for an account."""
    media_bundles = copy.deepcopy(media_bundles)
    for bundle in media_bundles:
        await _process_single_bundle(
            bundle=bundle,
            account_id=account_id,
            config=config,
        )


async def process_account_data(
    config: FanslyConfig,
    data: dict[str, Any],
    state: DownloadState | None = None,  # noqa: ARG001
) -> None:
    """Process account data from the API and store in the database.

    _process_nested_cache_lookups handles:
    - avatar/banner → Media resolution via identity map (assoc_table)
    - timelineStats → TimelineStats with accountId injection (reverse_fk)
    - mediaStoryState → MediaStoryState with accountId injection (reverse_fk)

    Walls still need async processing (stale-wall deletion, wall_posts junction).
    """
    from .stub_tracker import remove_stub  # noqa: PLC0415, I001  # circular: metadata.stub_tracker → metadata.account via models
    from .wall import process_account_walls  # noqa: PLC0415  # circular: metadata.wall → metadata.account

    store = get_store()
    data = copy.deepcopy(data)

    if "id" not in data:
        return

    account = Account.model_validate(data)

    # save() handles related-entity ordering + junction-FK stub creation.
    await store.save(account)

    if account.timelineStats:
        await store.save(account.timelineStats)
    if account.mediaStoryState:
        await store.save(account.mediaStoryState)

    # Walls need async processing (stale-wall deletion logic)
    if "walls" in data:
        await process_account_walls(
            config=config,
            account=account,
            walls_data=data["walls"],
        )

    # Remove stub tracking
    await remove_stub("accounts", account.id)
