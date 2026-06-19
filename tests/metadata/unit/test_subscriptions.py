"""Subscription / SubscriptionPlan / SubscriptionPromo / FollowEvent processor
tests. Uses the real entity_store and exercises the same paths the WS handler
and session-start orchestration go through.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from metadata import (
    Account,
    FollowEvent,
    Subscription,
    SubscriptionPlan,
    SubscriptionPromo,
    process_account_data,
)
from metadata.subscriptions import (
    _access_changed_accounts,
    apply_subscription_snapshot,
    apply_subscription_ws_event,
    consume_access_change,
    get_access_changed_accounts,
    process_subscriptions_response,
    record_follow_observation,
)
from tests.fixtures.utils.test_isolation import snowflake_id


# ── Helpers ─────────────────────────────────────────────────────────────────


def _sub_payload(
    sub_id: int,
    account_id: int,
    *,
    status: int = 3,
    tier_id: int | None = None,
    version: int | None = None,
    plan_id: int | None = None,
) -> dict:
    """A plausibly-shaped /subscriptions row. Field set matches what the
    Fansly API actually returns (subscriptions-2.json capture)."""
    return {
        "id": sub_id,
        "accountId": account_id,
        "subscriptionTierId": tier_id or snowflake_id(),
        "subscriptionTierName": "Bronze",
        "subscriptionTierColor": "#878787",
        "planId": plan_id or snowflake_id(),
        "promoId": "0",
        "giftCodeId": None,
        "status": status,
        "price": 5000,
        "renewPrice": 5000,
        "renewCorrelationId": str(snowflake_id()),
        "autoRenew": 1,
        "billingCycle": 30,
        "duration": 30,
        "renewDate": 1772040349000,
        "renewDatexD": 1772040349000,
        "createdAt": 1772040349000,
        "updatedAt": 1772040350000,
        "endsAt": 1779729949000,
        "promoPrice": None,
        "promoDuration": None,
        "promoStatus": None,
        "promoStartsAt": None,
        "promoEndsAt": None,
        "version": version,
    }


def _plan_payload(
    plan_id: int,
    account_id: int,
    tier_id: int,
    promos: list[dict] | None = None,
) -> dict:
    return {
        "id": plan_id,
        "accountId": account_id,
        "subscriptionTierId": tier_id,
        "billingCycle": 30,
        "price": 5000,
        "useAmounts": 0,
        "promos": promos or [],
    }


def _promo_payload(promo_id: int) -> dict:
    return {
        "id": promo_id,
        "status": 1,
        "price": 2500,
        "duration": 30,
        "maxUses": 1,
        "maxUsesBefore": None,
        "newSubscribersOnly": 0,
        "description": "test promo",
        "startsAt": 1770000000000,
        "endsAt": 1780000000000,
    }


# ── process_subscriptions_response ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_process_subscriptions_response_persists_three_shapes_and_registers_access(
    entity_store,
):
    """End-to-end ingest: subs + plans + nested promos all land in their
    tables, FK ordering is right (no stub for planId), and every newly-active
    sub registers an access-change against its accountId."""
    store = entity_store

    creator_a = snowflake_id()
    creator_b = snowflake_id()
    tier_a = snowflake_id()
    tier_b = snowflake_id()
    plan_a = snowflake_id()
    plan_b = snowflake_id()
    promo_a = snowflake_id()

    await store.save(Account(id=creator_a, username="creator_a"))
    await store.save(Account(id=creator_b, username="creator_b"))

    response = {
        "subscriptionPlans": [
            _plan_payload(plan_a, creator_a, tier_a, promos=[_promo_payload(promo_a)]),
            _plan_payload(plan_b, creator_b, tier_b),
        ],
        "subscriptions": [
            _sub_payload(snowflake_id(), creator_a, tier_id=tier_a, plan_id=plan_a),
            _sub_payload(snowflake_id(), creator_b, tier_id=tier_b, plan_id=plan_b),
        ],
    }

    added = await process_subscriptions_response(response)
    assert added == 2

    plans_a = store.filter(SubscriptionPlan, lambda p, c=creator_a: p.accountId == c)
    assert {p.id for p in plans_a} == {plan_a}
    assert store.get_from_cache(SubscriptionPromo, promo_a) is not None
    assert store.filter(Subscription, lambda s, c=creator_a: s.accountId == c)
    assert get_access_changed_accounts() == {
        creator_a: "sub-activated",
        creator_b: "sub-activated",
    }


@pytest.mark.asyncio
async def test_subscription_transition_triggers(entity_store):
    """All five transition paths: bootstrap-activate, expired→active,
    tier-upgrade, same-tier renewal, missing-required-field. Each goes through
    the same processor entry point but produces different registry effects."""
    store = entity_store

    creator = snowflake_id()
    sub_id = snowflake_id()
    tier_old = snowflake_id()
    tier_new = snowflake_id()
    await store.save(Account(id=creator, username="creator"))

    # 1) bootstrap (no prior) + active → sub-activated.
    await process_subscriptions_response(
        {"subscriptions": [_sub_payload(sub_id, creator, tier_id=tier_old)]}
    )
    assert get_access_changed_accounts() == {creator: "sub-activated"}
    _access_changed_accounts.clear()

    # 2) cached active + same payload (no-op renewal) → no registry change.
    await process_subscriptions_response(
        {"subscriptions": [_sub_payload(sub_id, creator, tier_id=tier_old)]}
    )
    assert get_access_changed_accounts() == {}

    # 3) cached active + different tier → tier-upgraded.
    await process_subscriptions_response(
        {"subscriptions": [_sub_payload(sub_id, creator, tier_id=tier_new)]}
    )
    assert get_access_changed_accounts() == {creator: "tier-upgraded"}
    _access_changed_accounts.clear()

    # 4) expire it, then re-activate → sub-activated again.
    await process_subscriptions_response(
        {"subscriptions": [_sub_payload(sub_id, creator, tier_id=tier_new, status=5)]}
    )
    assert get_access_changed_accounts() == {}
    await process_subscriptions_response(
        {"subscriptions": [_sub_payload(sub_id, creator, tier_id=tier_new, status=3)]}
    )
    assert get_access_changed_accounts() == {creator: "sub-activated"}
    _access_changed_accounts.clear()

    # 5) malformed (missing id) → no row, no registry entry.
    await process_subscriptions_response(
        {"subscriptions": [{"accountId": creator, "status": 3}]}
    )
    assert get_access_changed_accounts() == {}


# ── apply_subscription_ws_event ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_apply_subscription_ws_event_version_guard(entity_store):
    """Version guard skips stale events; equal/newer versions persist.
    Missing version on either side bypasses the guard (preserves
    pre-version-tracking behavior)."""
    store = entity_store

    creator = snowflake_id()
    sub_id = snowflake_id()
    tier = snowflake_id()
    await store.save(Account(id=creator, username="creator"))

    # Seed with version=5.
    await store.save(
        Subscription(
            id=sub_id,
            accountId=creator,
            status=3,
            subscriptionTierId=tier,
            version=5,
        )
    )
    _access_changed_accounts.clear()

    # Stale (version=4) → no upsert, no registry entry, cached version unchanged.
    result = await apply_subscription_ws_event(
        _sub_payload(sub_id, creator, tier_id=tier, version=4)
    )
    assert result is None
    assert get_access_changed_accounts() == {}
    cached = store.get_from_cache(Subscription, sub_id)
    assert cached.version == 5

    # Newer (version=6) — tier unchanged here so no access-change trigger,
    # but the row IS upserted (version bumps).
    result = await apply_subscription_ws_event(
        _sub_payload(sub_id, creator, tier_id=tier, version=6)
    )
    assert result is None
    cached = store.get_from_cache(Subscription, sub_id)
    assert cached.version == 6

    # Missing version on incoming → guard doesn't fire, upsert proceeds
    # (tier change triggers tier-upgraded).
    new_tier = snowflake_id()
    result = await apply_subscription_ws_event(
        _sub_payload(sub_id, creator, tier_id=new_tier, version=None)
    )
    assert result == (creator, "tier-upgraded")
    assert get_access_changed_accounts() == {creator: "tier-upgraded"}


@pytest.mark.asyncio
async def test_apply_subscription_ws_event_ignores_malformed(entity_store):
    """Non-dict, missing id — both no-op."""
    assert await apply_subscription_ws_event(None) is None  # type: ignore[arg-type]
    assert await apply_subscription_ws_event("not-a-dict") is None  # type: ignore[arg-type]
    assert await apply_subscription_ws_event({"accountId": snowflake_id()}) is None
    assert get_access_changed_accounts() == {}


# ── record_follow_observation ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_record_follow_observation_bootstrap_and_transitions(entity_store):
    """Bootstrap: no prior + following=True → append. No prior + False → skip.
    Steady-state: matching prior → skip. Transitions in either direction →
    append."""
    store = entity_store
    creator = snowflake_id()
    other = snowflake_id()
    await store.save(Account(id=creator, username="creator"))
    await store.save(Account(id=other, username="other"))

    # Bootstrap — not following + no history → no append (avoids cluttering
    # the audit log with every account we've ever seen).
    assert await record_follow_observation(other, current_following=False) is False
    assert not store.filter(FollowEvent, lambda e, c=other: e.accountId == c)

    # Bootstrap — following=True + no history → append.
    assert await record_follow_observation(creator, current_following=True) is True
    events = store.filter(FollowEvent, lambda e, c=creator: e.accountId == c)
    assert len(events) == 1
    assert events[0].following_state is True

    # Steady-state — same value → no append.
    assert await record_follow_observation(creator, current_following=True) is False
    assert len(store.filter(FollowEvent, lambda e, c=creator: e.accountId == c)) == 1

    # Transition True → False → append.
    assert await record_follow_observation(creator, current_following=False) is True
    events = store.filter(FollowEvent, lambda e, c=creator: e.accountId == c)
    assert len(events) == 2
    assert sorted(e.following_state for e in events) == [False, True]

    # Transition False → True → append.
    assert await record_follow_observation(creator, current_following=True) is True
    assert len(store.filter(FollowEvent, lambda e, c=creator: e.accountId == c)) == 3


# ── consume_access_change ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_consume_access_change_pops_once():
    """Each consume returns the reason and removes the entry; a second
    consume for the same accountId returns None (the per-creator code path
    relies on this idempotency)."""
    a = snowflake_id()
    b = snowflake_id()
    _access_changed_accounts[a] = "sub-activated"
    _access_changed_accounts[b] = "tier-upgraded"

    assert consume_access_change(a) == "sub-activated"
    assert consume_access_change(a) is None
    assert consume_access_change(b) == "tier-upgraded"
    assert get_access_changed_accounts() == {}


# ── Embedded `Account.subscription` (singular) coercion + persistence ───────


@pytest.mark.asyncio
async def test_account_validator_coerces_singular_subscription_to_list(
    entity_store,
):
    """The API gives ``account_data["subscription"]`` (singular dict) when
    the user is subscribed; the model has ``subscriptions: list[Subscription]``.
    Account's ``_coerce_embedded_subscription`` mode='before' validator
    reshapes the singular dict into a 1-item list so the existing has_many
    resolution path populates ``account.subscriptions`` in memory.
    Idempotent on the same sub.id.
    """
    creator_id = snowflake_id()
    sub_id = snowflake_id()
    sub_payload = _sub_payload(sub_id, creator_id)

    # Singular form → coerced into a 1-item list.
    account = Account.model_validate(
        {"id": creator_id, "username": "embed_test", "subscription": sub_payload}
    )
    assert len(account.subscriptions) == 1
    assert account.subscriptions[0].id == sub_id
    assert account.subscriptions[0].accountId == creator_id

    # Same sub.id provided twice → deduped (idempotent).
    account2 = Account.model_validate(
        {
            "id": creator_id,
            "username": "embed_test",
            "subscription": sub_payload,
            "subscriptions": [sub_payload],
        }
    )
    assert len(account2.subscriptions) == 1


@pytest.mark.asyncio
async def test_process_account_data_embedded_subscription_persists_and_detects_bootstrap(
    mock_config, entity_store
):
    """process_account_data with an embedded ``subscription`` persists the
    row AND runs snapshot-vs-cache transition detection via
    apply_subscription_snapshot. First observation (no prior cached row)
    counts as bootstrap-activation per _process_single_subscription's
    contract — registry receives "sub-activated".
    """
    creator_id = snowflake_id()
    sub_id = snowflake_id()
    sub_payload = _sub_payload(sub_id, creator_id)

    await process_account_data(
        config=mock_config,
        data={
            "id": creator_id,
            "username": "persist_embedded",
            "following": True,
            "subscription": sub_payload,
        },
    )

    persisted = await entity_store.get(Subscription, sub_id)
    assert persisted is not None
    assert persisted.accountId == creator_id
    assert persisted.status == 3
    # First observation of an active sub for this creator → bootstrap-activation.
    assert get_access_changed_accounts() == {creator_id: "sub-activated"}


@pytest.mark.asyncio
async def test_process_account_data_embedded_subscription_no_transition_on_steady_state(
    mock_config, entity_store
):
    """Second call with the same embedded subscription (same id, status,
    tier) must NOT fire a transition. Steady-state snapshot ⇒ no registry
    write."""
    creator_id = snowflake_id()
    sub_id = snowflake_id()
    sub_payload = _sub_payload(sub_id, creator_id)

    await process_account_data(
        config=mock_config,
        data={
            "id": creator_id,
            "username": "steady_state",
            "subscription": sub_payload,
        },
    )
    _access_changed_accounts.clear()

    # Re-snapshot with identical data → no transition detected.
    await process_account_data(
        config=mock_config,
        data={
            "id": creator_id,
            "username": "steady_state",
            "subscription": sub_payload,
        },
    )
    assert get_access_changed_accounts() == {}


@pytest.mark.asyncio
async def test_process_account_data_embedded_subscription_detects_tier_upgrade(
    mock_config, entity_store
):
    """Second call with the same sub.id but a different
    ``subscriptionTierId`` (and status still active) must fire
    "tier-upgraded". This is the per-creator transition detection the
    original plan called out: catch upgrades without needing a fresh
    /api/v1/subscriptions fetch."""
    creator_id = snowflake_id()
    sub_id = snowflake_id()
    old_tier = snowflake_id()
    new_tier = snowflake_id()

    await process_account_data(
        config=mock_config,
        data={
            "id": creator_id,
            "username": "tier_upgrade",
            "subscription": _sub_payload(sub_id, creator_id, tier_id=old_tier),
        },
    )
    _access_changed_accounts.clear()

    await process_account_data(
        config=mock_config,
        data={
            "id": creator_id,
            "username": "tier_upgrade",
            "subscription": _sub_payload(sub_id, creator_id, tier_id=new_tier),
        },
    )

    assert get_access_changed_accounts() == {creator_id: "tier-upgraded"}
    persisted = await entity_store.get(Subscription, sub_id)
    assert persisted.subscriptionTierId == new_tier


@pytest.mark.asyncio
async def test_process_account_data_no_subscription_field_is_safe(
    mock_config, entity_store
):
    """The common case: user isn't subscribed to this creator, so the API
    omits the ``subscription`` key entirely. process_account_data must run
    cleanly, persist the Account, and leave no Subscription rows or
    registry entries behind."""
    creator_id = snowflake_id()

    await process_account_data(
        config=mock_config,
        data={
            "id": creator_id,
            "username": "no_sub",
            "following": True,
            "subscribed": None,  # explicit None — separate from missing key
        },
    )

    account = await entity_store.get(Account, creator_id)
    assert account is not None
    assert account.subscriptions == []
    assert get_access_changed_accounts() == {}


@pytest.mark.asyncio
async def test_process_account_data_updates_embedded_subscription_on_resnapshot(
    mock_config, entity_store
):
    """A second process_account_data call with an updated embedded
    subscription (same sub.id, different status/endsAt) must overwrite the
    persisted row — the snapshot is authoritative for each invocation.
    This is the actual use case the snapshot path exists for: refresh the
    DB state with whatever the API last returned, no change-detection.
    """
    creator_id = snowflake_id()
    sub_id = snowflake_id()
    tier_id = snowflake_id()

    # First snapshot: active sub with original endsAt.
    first_payload = _sub_payload(sub_id, creator_id, tier_id=tier_id, status=3)
    first_payload["endsAt"] = 1779729949000
    await process_account_data(
        config=mock_config,
        data={
            "id": creator_id,
            "username": "resnapshot",
            "subscription": first_payload,
        },
    )
    first_persisted = await entity_store.get(Subscription, sub_id)
    assert first_persisted.status == 3
    first_ends_at = first_persisted.endsAt
    assert first_ends_at is not None

    # Second snapshot: same sub renewed (endsAt extended) + tier upgrade.
    new_tier_id = snowflake_id()
    second_payload = _sub_payload(sub_id, creator_id, tier_id=new_tier_id, status=3)
    second_payload["endsAt"] = 1799729949000  # later
    second_payload["updatedAt"] = 1779730000000  # advanced
    await process_account_data(
        config=mock_config,
        data={
            "id": creator_id,
            "username": "resnapshot",
            "subscription": second_payload,
        },
    )
    second_persisted = await entity_store.get(Subscription, sub_id)
    # Same row id (upsert), but field values reflect the second snapshot.
    assert second_persisted.id == sub_id
    assert second_persisted.subscriptionTierId == new_tier_id
    assert second_persisted.endsAt is not None
    assert second_persisted.endsAt > first_ends_at


@pytest.mark.asyncio
async def test_account_validator_singular_and_plural_with_different_ids_kept(
    entity_store,
):
    """Edge case: API gives singular `subscription` AND model receives a
    `subscriptions` list with a DIFFERENT sub.id (e.g., from prior session
    cache). Both must be kept — the dedup only matches by id, not by
    presence of one or the other."""
    creator_id = snowflake_id()
    sub_a_id = snowflake_id()
    sub_b_id = snowflake_id()

    account = Account.model_validate(
        {
            "id": creator_id,
            "username": "two_subs",
            "subscription": _sub_payload(sub_a_id, creator_id),
            "subscriptions": [_sub_payload(sub_b_id, creator_id)],
        }
    )
    ids = sorted(s.id for s in account.subscriptions)
    assert ids == sorted([sub_a_id, sub_b_id])


def test_account_validator_no_subscription_or_null_is_safe():
    """No `subscription` key, or `subscription: None`, must not crash the
    validator and must leave the subscriptions list empty."""
    creator_id = snowflake_id()

    # Missing key entirely.
    a1 = Account.model_validate({"id": creator_id, "username": "no_sub_key"})
    assert a1.subscriptions == []

    # Explicit None.
    a2 = Account.model_validate(
        {"id": creator_id, "username": "null_sub", "subscription": None}
    )
    assert a2.subscriptions == []

    # Non-dict (defensive — shouldn't happen in real responses but guard
    # against malformed inputs).
    a3 = Account.model_validate(
        {"id": creator_id, "username": "bad_sub", "subscription": "garbage"}
    )
    assert a3.subscriptions == []


def test_current_subscription_handles_mixed_none_timestamps():
    """``current_subscription`` derives the latest active sub from the
    has_many list. Mixed-None updatedAt/createdAt rows must not crash
    ``max()`` — the sentinel falls back to ``datetime.min`` so ``None``
    rows just sort to the bottom.
    """
    creator_id = snowflake_id()
    sub_no_dates = Subscription(
        id=snowflake_id(),
        accountId=creator_id,
        status=3,
        updatedAt=None,
        createdAt=None,
    )
    sub_with_date = Subscription(
        id=snowflake_id(),
        accountId=creator_id,
        status=3,
        updatedAt=datetime(2026, 5, 1, tzinfo=UTC),
        createdAt=datetime(2026, 4, 1, tzinfo=UTC),
    )
    expired = Subscription(
        id=snowflake_id(),
        accountId=creator_id,
        status=5,
        updatedAt=datetime(2026, 5, 22, tzinfo=UTC),
    )

    account = Account.model_validate({"id": creator_id, "username": "current_sub_test"})
    account.subscriptions = [sub_no_dates, sub_with_date, expired]

    current = account.current_subscription
    assert current is sub_with_date  # dated active row wins over None

    # No active subs → None.
    account.subscriptions = [expired]
    assert account.current_subscription is None

    # Empty list → None.
    account.subscriptions = []
    assert account.current_subscription is None


# ── _process_single_plan edge paths (via process_subscriptions_response) ─────


@pytest.mark.asyncio
async def test_process_single_plan_edge_paths(entity_store):
    """Plan branches through the real response entry point: a plan missing
    its required id is skipped (no row); a plan with no ``promos`` key
    persists with zero promo rows; a non-dict promo entry is skipped rather
    than validated.
    """
    store = entity_store
    creator = snowflake_id()
    tier = snowflake_id()
    plan_no_promos = snowflake_id()
    plan_bad_promo = snowflake_id()
    await store.save(Account(id=creator, username="plan_edges"))

    response = {
        "subscriptionPlans": [
            # No "promos" key → pop returns None → promo loop skipped entirely.
            {
                "id": plan_no_promos,
                "accountId": creator,
                "subscriptionTierId": tier,
                "billingCycle": 30,
                "price": 5000,
                "useAmounts": 0,
            },
            # Non-dict promo entry → that entry is skipped, plan still persists.
            {
                "id": plan_bad_promo,
                "accountId": creator,
                "subscriptionTierId": tier,
                "billingCycle": 30,
                "price": 5000,
                "useAmounts": 0,
                "promos": [12345],
            },
            # Missing required id → json_output + early return, never persisted.
            {"accountId": creator, "subscriptionTierId": tier},
        ]
    }

    await process_subscriptions_response(response)

    assert store.get_from_cache(SubscriptionPlan, plan_no_promos) is not None
    assert store.get_from_cache(SubscriptionPlan, plan_bad_promo) is not None
    # Only the two valid plans persisted (the id-less plan was skipped).
    assert len(store.filter(SubscriptionPlan, lambda _p: True)) == 2
    # No promo rows: plan_no_promos had none, the non-dict entry was skipped.
    assert not store.filter(SubscriptionPromo, lambda _p: True)


@pytest.mark.asyncio
async def test_apply_subscription_snapshot_ignores_non_dict():
    """The defensive non-dict guard on apply_subscription_snapshot is a no-op
    (REST-embedded ``Account.subscription`` may be absent or malformed)."""
    assert await apply_subscription_snapshot(None) is None
    assert await apply_subscription_snapshot("garbage") is None
    assert get_access_changed_accounts() == {}


def test_subscription_coerces_int_id_fields_to_string():
    """Fansly returns promoId / giftCodeId / renewCorrelationId as ints in
    embedded Account.subscription payloads (observed in production:
    promoId=0, gift code / correlation IDs as Snowflake-shaped ints).
    The DB columns are String, so the model_validator must stringify
    before strict Pydantic typing rejects int → str.
    """
    creator_id = snowflake_id()
    sub_id = snowflake_id()

    # Exact shape from production error log (2026-05-23): int promoId=0,
    # giftCodeId as Snowflake, renewCorrelationId as Snowflake.
    sub = Subscription.model_validate(
        {
            "id": sub_id,
            "accountId": creator_id,
            "status": 3,
            "promoId": 0,
            "giftCodeId": 904016029032665088,
            "renewCorrelationId": 851005063466864640,
        }
    )

    assert sub.promoId == "0"
    assert sub.giftCodeId == "904016029032665088"
    assert sub.renewCorrelationId == "851005063466864640"

    # None / missing must still pass through untouched.
    sub_null = Subscription.model_validate(
        {
            "id": snowflake_id(),
            "accountId": creator_id,
            "status": 3,
            "promoId": None,
            "giftCodeId": None,
            "renewCorrelationId": None,
        }
    )
    assert sub_null.promoId is None
    assert sub_null.giftCodeId is None
    assert sub_null.renewCorrelationId is None

    # Already-string values must be preserved verbatim.
    sub_str = Subscription.model_validate(
        {
            "id": snowflake_id(),
            "accountId": creator_id,
            "status": 3,
            "promoId": "promo_abc",
            "giftCodeId": "gift_xyz",
            "renewCorrelationId": "corr_123",
        }
    )
    assert sub_str.promoId == "promo_abc"
    assert sub_str.giftCodeId == "gift_xyz"
    assert sub_str.renewCorrelationId == "corr_123"
