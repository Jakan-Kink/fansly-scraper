"""Subscription / SubscriptionPlan / SubscriptionPromo + FollowEvent
processing.

Persists the three response shapes from ``/api/v1/subscriptions``
(``subscriptions[]``, ``subscriptionPlans[]``, nested ``promos[]``) and
detects per-creator access-state transitions that should invalidate
download dedup short-circuits.

Also exposes ``record_follow_observation`` for the follow-state audit log.

Triggers that produce an entry in the returned access-change set:
- Subscription status flips inactive (or missing) → active (3)
- subscriptionTierId changes while status remains active (tier upgrade)

Renewal-only changes (endsAt extension, updatedAt advance) are NOT
treated as access changes — they extend the existing access window
without unlocking new content.
"""

from __future__ import annotations

import copy
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from textio import json_output

from .models import (
    FollowEvent,
    Subscription,
    SubscriptionPlan,
    SubscriptionPromo,
    get_store,
)


if TYPE_CHECKING:
    pass


_SUBSCRIPTION_STATUS_ACTIVE = 3


_access_changed_accounts: dict[int, str] = {}


def get_access_changed_accounts() -> dict[int, str]:
    """Module-level registry of accountIds with detected access-state changes
    (mapped to trigger reason). Mirrors the get_store() singleton pattern —
    written by session-start /subscriptions ingestion and WS sub events,
    consumed and cleared per-creator by get_creator_account_info.
    """
    return _access_changed_accounts


def consume_access_change(account_id: int) -> str | None:
    """Pop and return the access-change reason for ``account_id``, or None.

    Called once per get_creator_account_info pass so the same transition
    doesn't keep re-firing across daemon cycles.
    """
    return _access_changed_accounts.pop(account_id, None)


def _is_active(status: int | None) -> bool:
    """Return True when the status indicates the sub currently grants access."""
    return status == _SUBSCRIPTION_STATUS_ACTIVE


async def _process_single_subscription(
    sub_data: dict[str, Any],
) -> tuple[int, str] | None:
    """Persist one Subscription row and return (accountId, reason) when
    the upsert constitutes an access-change for that creator.

    Reasons:
        "sub-activated"  — prior row missing OR prior status not active,
                           new status is active.
        "tier-upgraded"  — prior + new both active, subscriptionTierId differs.

    Returns None when no access-change trigger fires (steady-state renewal,
    no-op update, or sub that was and remains inactive).
    """
    store = get_store()

    sub_id = sub_data.get("id")
    account_id = sub_data.get("accountId")
    if sub_id is None or account_id is None:
        json_output(
            1,
            "meta/sub - missing_required_field",
            {"id": sub_id, "accountId": account_id},
        )
        return None

    prev = store.get_from_cache(Subscription, int(sub_id))
    prev_active = prev is not None and _is_active(prev.status)
    prev_tier = prev.subscriptionTierId if prev else None

    new_status = sub_data.get("status")
    new_active = _is_active(new_status)
    new_tier = sub_data.get("subscriptionTierId")
    new_tier_int = int(new_tier) if new_tier is not None else None

    sub = Subscription.model_validate(sub_data)
    await store.save(sub)

    if new_active and not prev_active:
        return (int(account_id), "sub-activated")
    if new_active and prev_active and prev_tier != new_tier_int:
        return (int(account_id), "tier-upgraded")
    return None


async def _process_single_plan(plan_data: dict[str, Any]) -> None:
    """Persist one SubscriptionPlan + its nested promos.

    The API embeds ``promos: [...]`` inside each plan. We strip them before
    Pydantic validation (the relationship is has_many → reverse FK, not a
    nested-validate path) and persist each promo separately with
    ``planId`` injected.
    """
    store = get_store()

    plan_id = plan_data.get("id")
    account_id = plan_data.get("accountId")
    if plan_id is None or account_id is None:
        json_output(
            1,
            "meta/sub - missing_required_field",
            {"id": plan_id, "accountId": account_id},
        )
        return

    promos = plan_data.pop("promos", []) or []
    plan = SubscriptionPlan.model_validate(plan_data)
    await store.save(plan)

    for promo_data in promos:
        if not isinstance(promo_data, dict):
            continue
        promo_payload = {**promo_data, "planId": plan_id}
        promo = SubscriptionPromo.model_validate(promo_payload)
        await store.save(promo)


async def process_subscriptions_response(
    response: dict[str, Any],
) -> int:
    """Persist the full /api/v1/subscriptions response and write detected
    access-state changes into the module-level registry
    (see get_access_changed_accounts).

    Args:
        response: The ``response`` envelope from ``/api/v1/subscriptions``,
            with ``subscriptions[]``, ``subscriptionPlans[]``, and an
            optional ``stats`` block.

    Returns:
        Count of accountIds added to the access-change registry by this call.

    Order matters: plans must be persisted BEFORE subscriptions so that
    the planId FK on Subscription resolves through the cache. Promos are
    persisted inside _process_single_plan so they share the plan's FK
    context.
    """
    response = copy.deepcopy(response)
    added = 0

    for plan_data in response.get("subscriptionPlans", []):
        await _process_single_plan(plan_data)

    for sub_data in response.get("subscriptions", []):
        result = await _process_single_subscription(sub_data)
        if result is not None:
            account_id, reason = result
            _access_changed_accounts[account_id] = reason
            added += 1

    return added


async def apply_subscription_snapshot(
    sub_data: dict[str, Any],
) -> tuple[int, str] | None:
    """Apply a subscription snapshot from a REST source (e.g., the
    embedded ``Account.subscription`` field in /account?usernames= responses).

    Distinct from ``apply_subscription_ws_event``:
    - No version guard (REST is always the latest server snapshot; staleness
      isn't a concern the way it is for re-ordered WS events).
    - No assumption that the source verified a state change live — this
      compares the snapshot against cached state and detects transitions
      itself via ``_process_single_subscription``.

    Caller (process_account_data) is responsible for writing the returned
    ``(accountId, reason)`` into the access-change registry. Same return
    shape as the WS variant so the two paths converge on the same
    downstream registry semantics.
    """
    if not isinstance(sub_data, dict):
        return None
    return await _process_single_subscription(dict(sub_data))


async def apply_subscription_ws_event(
    sub_data: dict[str, Any],
) -> tuple[int, str] | None:
    """Apply an embedded subscription payload from a WS event to the store.

    Mirrors the Fansly client's version-guarded merge (main.js line 21084):
    cached_version > incoming_version → skip as stale. Otherwise upsert via
    the normal processor and propagate the access-change result back to the
    caller, who is responsible for updating session-scoped registries.

    Args:
        sub_data: The ``event.subscription`` payload from a SubscriptionService
            WS event (svc=15, type in {5, 102}) or unwrapped notification.

    Returns:
        ``(accountId, reason)`` when an access-change trigger fired, else None.
    """
    if not isinstance(sub_data, dict):
        return None

    sub_id_raw = sub_data.get("id")
    if sub_id_raw is None:
        return None

    store = get_store()
    cached = store.get_from_cache(Subscription, int(sub_id_raw))
    incoming_version = sub_data.get("version")
    if (
        cached is not None
        and incoming_version is not None
        and cached.version is not None
        and incoming_version < cached.version
    ):
        return None

    result = await _process_single_subscription(dict(sub_data))
    if result is not None:
        account_id, reason = result
        _access_changed_accounts[account_id] = reason
    return result


async def record_follow_observation(
    account_id: int,
    current_following: bool,
) -> bool:
    """Append a FollowEvent only when this observation differs from the
    latest cached row for the account.

    Returns:
        True when a new FollowEvent was appended (i.e., the follow state
        transitioned). False when steady-state.

    Bootstrap semantics: a missing prior FollowEvent + currently-following
    is treated as a transition (returns True). This drives the aggressive
    first-run behavior — every currently-followed creator gets one
    FollowEvent on first observation and counts as access-changed.
    """
    store = get_store()

    prior = store.filter(
        FollowEvent,
        lambda e: e.accountId == account_id,
    )
    prior_sorted = sorted(prior, key=lambda e: e.observed_at, reverse=True)
    latest = prior_sorted[0] if prior_sorted else None

    if latest is not None and latest.following_state == current_following:
        return False

    if latest is None and not current_following:
        # Bootstrap: not following + no history is the dominant case
        # (any followed creator the API doesn't return). Don't record —
        # avoids cluttering the audit log with "we observed not-following"
        # for every account we've ever seen. Transition will be recorded
        # the first time the user actually follows them.
        return False

    event = FollowEvent(
        accountId=account_id,
        observed_at=datetime.now(UTC),
        following_state=current_following,
    )
    await store.save(event)
    return True
