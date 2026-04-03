"""Account processing module.

Handles account data, media bundles, avatars, banners, timeline stats,
and story states.
"""

from __future__ import annotations

import copy
from typing import TYPE_CHECKING, Any

from .models import (
    Account,
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
    config: FanslyConfig,  # noqa: ARG001
) -> None:
    """Process a single media bundle.

    _prepare_bundle_data: converts bundleContent/accountMediaIds → accountMedia IDs
    _process_nested_cache_lookups: resolves preview → Media, accountMedia → AccountMedia
    _sync_associations: ordered junction (DELETE + re-INSERT with pos)
    """
    store = get_store()
    bundle.setdefault("accountId", account_id)

    bundle_obj = AccountMediaBundle.model_validate(bundle)

    # Save preview Media first if resolved (FK constraint)
    if bundle_obj.preview:
        await store.save(bundle_obj.preview)

    await store.save(bundle_obj)


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
    from .stub_tracker import remove_stub
    from .wall import process_account_walls

    store = get_store()
    data = copy.deepcopy(data)

    if "id" not in data:
        return

    account = Account.model_validate(data)

    # save() handles the correct order:
    # 1. INSERT Account row (scalars only)
    # 2. Save related entities for junctions (avatar/banner Media)
    # 3. Sync junction tables — _ensure_junction_fk_targets creates
    #    stubs for any missing FK targets (e.g., Posts for pinned_posts)
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
