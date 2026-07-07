"""Tests for download.media.refresh_locked_account_media.

Validates the filter that keeps the bulk re-fetch scoped to AM/bundle rows
that can actually change state on a sub/follow/PPV access event. Real
respx + real entity_store throughout — no internal mocks.
"""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest
import respx

from download.media import refresh_locked_account_media
from metadata.models import (
    Account,
    AccountMedia,
    AccountMediaBundle,
    Media,
    get_store,
)
from tests.fixtures.api import dump_fansly_calls
from tests.fixtures.utils.test_isolation import snowflake_id


def _ok_account_media(payload):
    return httpx.Response(200, json={"success": True, "response": payload})


async def _save_am(store, *, am_id, account_id, deleted=False, access=False):
    """Save a Media + AccountMedia pair (FK requires Media to exist first)."""
    media_id = snowflake_id()
    await store.save(Media(id=media_id, accountId=account_id))
    await store.save(
        AccountMedia(
            id=am_id,
            accountId=account_id,
            mediaId=media_id,
            createdAt=datetime.now(UTC),
            deleted=deleted,
            access=access,
        )
    )


@pytest.mark.asyncio
async def test_refresh_filter_skips_immune_rows(
    respx_fansly_api, entity_store, mock_config
):
    """Refresh targets rows whose state can flip on access-change:
        - AM: deleted=False AND access=False
        - Bundle: deleted=False AND access=False AND purchased=False AND whitelisted=False
    Everything else (deleted, already-accessible, purchased, whitelisted)
    is excluded.
    """
    store = get_store()
    creator_id = snowflake_id()
    await store.save(Account(id=creator_id, username=f"refresh_{creator_id}"))

    # Eligible: 1 locked AM + 1 locked-and-clean bundle.
    eligible_am_id = snowflake_id()
    eligible_bundle_id = snowflake_id()
    await _save_am(store, am_id=eligible_am_id, account_id=creator_id, access=False)
    await store.save(
        AccountMediaBundle(
            id=eligible_bundle_id,
            accountId=creator_id,
            createdAt=datetime.now(UTC),
            deleted=False,
            access=False,
            purchased=False,
            whitelisted=False,
        )
    )

    # Excluded: every reason the filter should skip.
    excluded_am_access = snowflake_id()
    excluded_am_deleted = snowflake_id()
    excluded_bundle_purchased = snowflake_id()
    excluded_bundle_whitelisted = snowflake_id()
    excluded_bundle_deleted = snowflake_id()
    excluded_bundle_access = snowflake_id()
    await _save_am(store, am_id=excluded_am_access, account_id=creator_id, access=True)
    await _save_am(
        store, am_id=excluded_am_deleted, account_id=creator_id, deleted=True
    )
    await store.save(
        AccountMediaBundle(
            id=excluded_bundle_purchased,
            accountId=creator_id,
            createdAt=datetime.now(UTC),
            deleted=False,
            access=False,
            purchased=True,
            whitelisted=False,
        )
    )
    await store.save(
        AccountMediaBundle(
            id=excluded_bundle_whitelisted,
            accountId=creator_id,
            createdAt=datetime.now(UTC),
            deleted=False,
            access=False,
            purchased=False,
            whitelisted=True,
        )
    )
    await store.save(
        AccountMediaBundle(
            id=excluded_bundle_deleted,
            accountId=creator_id,
            createdAt=datetime.now(UTC),
            deleted=True,
            access=False,
            purchased=False,
            whitelisted=False,
        )
    )
    await store.save(
        AccountMediaBundle(
            id=excluded_bundle_access,
            accountId=creator_id,
            createdAt=datetime.now(UTC),
            deleted=False,
            access=True,
            purchased=False,
            whitelisted=False,
        )
    )

    # Only the one eligible AM should be in the refresh batch (bundle has
    # no constituent AMs cached, so it contributes nothing).
    am_route = respx.get(
        url__startswith=respx_fansly_api.ACCOUNT_MEDIA_ENDPOINT.split("?")[0]
    ).mock(side_effect=[_ok_account_media([])])

    try:
        count = await refresh_locked_account_media(mock_config, creator_id)
    finally:
        dump_fansly_calls(am_route.calls, "refresh_filter_skips_immune")

    assert count == 1
    assert am_route.call_count == 1
    request_url = str(am_route.calls[0].request.url)
    assert str(eligible_am_id) in request_url
    for excluded in (
        excluded_am_access,
        excluded_am_deleted,
    ):
        assert str(excluded) not in request_url


@pytest.mark.asyncio
async def test_refresh_pulls_bundle_constituent_ams(
    respx_fansly_api, entity_store, mock_config
):
    """A locked-and-clean bundle contributes its cached constituent AM ids
    to the refresh batch. The bundle's own access flag isn't refreshed
    directly (no dedicated bundle endpoint) — its rows surface on the
    next natural pagination encounter."""
    store = get_store()
    creator_id = snowflake_id()
    await store.save(Account(id=creator_id, username=f"bundle_{creator_id}"))

    # Three constituent AMs already cached.
    am_ids = [snowflake_id() for _ in range(3)]
    am_objs = []
    for am_id in am_ids:
        # individually accessible AMs — but bundle is locked, so they're
        # still in the refresh batch via bundle expansion.
        await _save_am(store, am_id=am_id, account_id=creator_id, access=True)
        am_obj = store.get_from_cache(AccountMedia, am_id)
        assert am_obj is not None
        am_objs.append(am_obj)

    bundle_id = snowflake_id()
    bundle = AccountMediaBundle(
        id=bundle_id,
        accountId=creator_id,
        createdAt=datetime.now(UTC),
        deleted=False,
        access=False,
        purchased=False,
        whitelisted=False,
    )
    bundle.accountMedia = am_objs
    await store.save(bundle)

    am_route = respx.get(
        url__startswith=respx_fansly_api.ACCOUNT_MEDIA_ENDPOINT.split("?")[0]
    ).mock(side_effect=[_ok_account_media([])])

    try:
        count = await refresh_locked_account_media(mock_config, creator_id)
    finally:
        dump_fansly_calls(am_route.calls, "refresh_bundle_constituents")

    # All 3 constituent AMs in the refresh batch (despite each AM's own
    # access=True — bundle-level locking gates them collectively).
    assert count == 3
    assert am_route.call_count == 1
    request_url = str(am_route.calls[0].request.url)
    for am_id in am_ids:
        assert str(am_id) in request_url


@pytest.mark.asyncio
async def test_refresh_no_op_when_nothing_locked(
    respx_fansly_api, entity_store, mock_config
):
    """Creator has only accessible/purchased/whitelisted rows → no HTTP call,
    returns 0. Guards against burning API budget on creators with nothing
    that could plausibly have flipped."""
    store = get_store()
    creator_id = snowflake_id()
    await store.save(Account(id=creator_id, username=f"clean_{creator_id}"))

    await _save_am(store, am_id=snowflake_id(), account_id=creator_id, access=True)
    await store.save(
        AccountMediaBundle(
            id=snowflake_id(),
            accountId=creator_id,
            createdAt=datetime.now(UTC),
            deleted=False,
            access=False,
            purchased=True,
            whitelisted=False,
        )
    )

    am_route = respx.get(
        url__startswith=respx_fansly_api.ACCOUNT_MEDIA_ENDPOINT.split("?")[0]
    ).mock(side_effect=[_ok_account_media([])])

    try:
        count = await refresh_locked_account_media(mock_config, creator_id)
    finally:
        dump_fansly_calls(am_route.calls, "refresh_no_op")

    assert count == 0
    assert am_route.call_count == 0
