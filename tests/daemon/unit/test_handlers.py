"""Unit tests for daemon.handlers — WebSocket event → WorkItem dispatch.

Tests are pure: no async, no fixtures, no RESPX.  Each test builds a
realistic decoded event dict (matching the shapes in api/websocket.py's
_monitor_* methods) and asserts on the returned WorkItem or None.

Frozen dataclasses provide structural equality so ``==`` comparisons work
without extra assertion helpers.
"""

import pytest

from daemon.handlers import (
    CheckCreatorAccess,
    DownloadMessagesForGroup,
    DownloadTimelineOnly,
    FullCreatorDownload,
    RedownloadCreatorMedia,
    WorkItem,
    dispatch_ws_event,
)


# ---------------------------------------------------------------------------
# svc=5 type=1 — New message (MessageService)
# ---------------------------------------------------------------------------


def test_new_message_with_attachments_returns_download_messages() -> None:
    """svc=5 type=1 with attachments → DownloadMessagesForGroup."""
    event = {
        "message": {
            "id": "900000000001",
            "senderId": "100000000001",
            "groupId": "200000000001",
            "content": "check this out",
            "attachments": [{"contentId": "300000000001", "contentType": 1}],
        }
    }
    result = dispatch_ws_event(5, 1, event)
    assert result == DownloadMessagesForGroup(group_id=200_000_000_001)


def test_new_message_without_attachments_returns_none() -> None:
    """svc=5 type=1 with empty attachments list → None (pure text message)."""
    event = {
        "message": {
            "id": "900000000002",
            "senderId": "100000000001",
            "groupId": "200000000001",
            "content": "just a text message",
            "attachments": [],
        }
    }
    result = dispatch_ws_event(5, 1, event)
    assert result is None


def test_new_message_missing_attachments_key_returns_none() -> None:
    """svc=5 type=1 with no 'attachments' key → None (treat as text)."""
    event = {
        "message": {
            "id": "900000000003",
            "senderId": "100000000001",
            "groupId": "200000000001",
            "content": "another text",
        }
    }
    result = dispatch_ws_event(5, 1, event)
    assert result is None


# ---------------------------------------------------------------------------
# svc=15 type=5 — Subscription events (SubscriptionService)
# ---------------------------------------------------------------------------


def test_subscription_confirmed_status_3_returns_full_creator_download() -> None:
    """svc=15 type=5 status=3 → FullCreatorDownload(creator_id=...)."""
    event = {
        "subscription": {
            "id": "400000000001",
            "accountId": "500000000001",
            "status": 3,
            "price": 1000,
            "subscriptionTierName": "Standard",
        }
    }
    result = dispatch_ws_event(15, 5, event)
    assert result == FullCreatorDownload(creator_id=500_000_000_001)


def test_subscription_pending_status_2_returns_none() -> None:
    """svc=15 type=5 status=2 (pending) → None."""
    event = {
        "subscription": {
            "id": "400000000002",
            "accountId": "500000000001",
            "status": 2,
            "price": 1000,
        }
    }
    result = dispatch_ws_event(15, 5, event)
    assert result is None


def test_subscription_other_status_returns_none() -> None:
    """svc=15 type=5 status=1 (any non-3 status) → None."""
    event = {
        "subscription": {
            "id": "400000000003",
            "accountId": "500000000001",
            "status": 1,
        }
    }
    result = dispatch_ws_event(15, 5, event)
    assert result is None


# ---------------------------------------------------------------------------
# svc=2 type=7 and type=8 — PPV media / bundle purchase (MediaService)
# ---------------------------------------------------------------------------


def test_ppv_media_purchase_returns_redownload_creator_media() -> None:
    """svc=2 type=7 (PPV media purchased) → RedownloadCreatorMedia."""
    event = {
        "order": {
            "orderId": "700000000001",
            "accountMediaId": "800000000001",
            "correlationAccountId": "500000000002",
        }
    }
    result = dispatch_ws_event(2, 7, event)
    assert result == RedownloadCreatorMedia(creator_id=500_000_000_002)


def test_ppv_bundle_purchase_returns_redownload_creator_media() -> None:
    """svc=2 type=8 (PPV bundle purchased) → RedownloadCreatorMedia."""
    event = {
        "order": {
            "orderId": "700000000002",
            "accountMediaBundleId": "800000000002",
            "correlationAccountId": "500000000002",
        }
    }
    result = dispatch_ws_event(2, 8, event)
    assert result == RedownloadCreatorMedia(creator_id=500_000_000_002)


def test_ppv_missing_correlation_account_id_returns_none() -> None:
    """svc=2 type=7 with no correlationAccountId → None (defensive)."""
    event = {
        "order": {
            "orderId": "700000000003",
            "accountMediaId": "800000000003",
            # correlationAccountId intentionally absent
        }
    }
    result = dispatch_ws_event(2, 7, event)
    assert result is None


# ---------------------------------------------------------------------------
# svc=3 type=2 — New follow (FollowerService)
# ---------------------------------------------------------------------------


def test_new_follow_returns_check_creator_access() -> None:
    """svc=3 type=2 (new follow) → CheckCreatorAccess(creator_id=...)."""
    event = {
        "follow": {
            "id": "600000000001",
            "accountId": "500000000003",
            "followerId": "999000000001",
        }
    }
    result = dispatch_ws_event(3, 2, event)
    assert result == CheckCreatorAccess(creator_id=500_000_000_003)


# ---------------------------------------------------------------------------
# svc=6 type=2 — Wallet credited (WalletService)
# ---------------------------------------------------------------------------


def test_wallet_credited_returns_none() -> None:
    """svc=6 type=2 (wallet credited) → None (informational only)."""
    event = {
        "wallet": {
            "id": "610000000001",
            "balance": 5000,
            "walletVersion": 7,
        }
    }
    result = dispatch_ws_event(6, 2, event)
    assert result is None


# ---------------------------------------------------------------------------
# Unknown (svc, type) pairs
# ---------------------------------------------------------------------------


def test_unknown_svc_type_returns_none() -> None:
    """Completely unknown (svc, type) → None, no exception."""
    event = {"foo": "bar"}
    result = dispatch_ws_event(99, 99, event)
    assert result is None


def test_known_svc_unknown_type_returns_none() -> None:
    """Known service but unregistered event type → None, no exception."""
    event = {"message": {"groupId": "200000000001", "attachments": []}}
    result = dispatch_ws_event(5, 999, event)
    assert result is None


# ---------------------------------------------------------------------------
# Malformed events — must never raise, must return None
# ---------------------------------------------------------------------------


def test_malformed_event_completely_empty_dict_returns_none() -> None:
    """svc=5 type=1 with empty dict payload → None, no exception."""
    result = dispatch_ws_event(5, 1, {})
    assert result is None


def test_malformed_event_wrong_types_in_fields_returns_none() -> None:
    """svc=2 type=7 with None correlationAccountId → None, no exception."""
    event = {"order": {"correlationAccountId": None}}
    result = dispatch_ws_event(2, 7, event)
    assert result is None


def test_malformed_event_boolean_id_rejected() -> None:
    """svc=5 type=1 with a boolean groupId must NOT silently coerce to 1/0.

    Regression guard: Python ``bool`` is a subclass of ``int``, so a
    naive ``int(value)`` would turn ``True`` into ``1`` and produce a
    WorkItem pointing at a nonsensical target. ``_safe_int`` rejects
    ``bool`` explicitly so we return None on this shape.
    """
    event = {"message": {"groupId": True, "attachments": [{"id": "x"}]}}
    result = dispatch_ws_event(5, 1, event)
    assert result is None


def test_malformed_event_no_message_key_returns_none() -> None:
    """svc=5 type=1 missing 'message' key entirely → None, no exception."""
    result = dispatch_ws_event(5, 1, {"unexpected_key": "data"})
    assert result is None


def test_malformed_subscription_missing_account_id_returns_none() -> None:
    """svc=15 type=5 status=3 but accountId missing → None, no exception."""
    event = {"subscription": {"status": 3}}
    result = dispatch_ws_event(15, 5, event)
    assert result is None


def test_malformed_follow_missing_account_id_returns_none() -> None:
    """svc=3 type=2 missing 'accountId' in follow dict → None, no exception."""
    event = {"follow": {"id": "600000000001", "followerId": "999000000001"}}
    result = dispatch_ws_event(3, 2, event)
    assert result is None


# ---------------------------------------------------------------------------
# WorkItem structural checks
# ---------------------------------------------------------------------------


def test_work_item_subclasses_are_frozen() -> None:
    """Frozen dataclasses reject attribute mutation."""
    item = DownloadMessagesForGroup(group_id=12345)
    with pytest.raises((AttributeError, TypeError)):
        item.group_id = 99999  # type: ignore[misc]


def test_work_item_equality_is_structural() -> None:
    """Two WorkItem subclass instances with same fields compare equal."""
    a = RedownloadCreatorMedia(creator_id=777)
    b = RedownloadCreatorMedia(creator_id=777)
    assert a == b
    assert a is not b


def test_work_item_base_class_relationship() -> None:
    """All concrete WorkItem subclasses are instances of WorkItem base."""
    items: list[WorkItem] = [
        DownloadMessagesForGroup(group_id=1),
        FullCreatorDownload(creator_id=2),
        RedownloadCreatorMedia(creator_id=3),
        CheckCreatorAccess(creator_id=4),
    ]
    for item in items:
        assert isinstance(item, WorkItem)


# ---------------------------------------------------------------------------
# Warning-path coverage — malformed fields that trigger logger.warning
# ---------------------------------------------------------------------------


def test_new_message_with_attachments_invalid_group_id_returns_none() -> None:
    """svc=5 type=1 with attachments but unparseable groupId → None (warning logged)."""
    event = {
        "message": {
            "groupId": "not-an-int",
            "attachments": [{"contentType": 1}],
        }
    }
    result = dispatch_ws_event(5, 1, event)
    assert result is None


def test_new_message_with_attachments_none_group_id_returns_none() -> None:
    """svc=5 type=1 with attachments but null groupId → None (warning logged)."""
    event = {
        "message": {
            "groupId": None,
            "attachments": [{"contentType": 1}],
        }
    }
    result = dispatch_ws_event(5, 1, event)
    assert result is None


def test_ppv_order_not_a_dict_returns_none() -> None:
    """svc=2 type=7 with 'order' as a non-dict value → None."""
    event = {"order": "unexpected-string"}
    result = dispatch_ws_event(2, 7, event)
    assert result is None


def test_subscription_not_a_dict_returns_none() -> None:
    """svc=15 type=5 with 'subscription' as a non-dict → None."""
    event = {"subscription": 42}
    result = dispatch_ws_event(15, 5, event)
    assert result is None


def test_follow_not_a_dict_returns_none() -> None:
    """svc=3 type=2 with 'follow' as a non-dict → None."""
    event = {"follow": [1, 2, 3]}
    result = dispatch_ws_event(3, 2, event)
    assert result is None


# ---------------------------------------------------------------------------
# DownloadTimelineOnly — structural and dispatch-exclusion tests
# ---------------------------------------------------------------------------


def test_download_timeline_only_instantiates_with_creator_id() -> None:
    """DownloadTimelineOnly can be constructed with a creator_id int."""
    item = DownloadTimelineOnly(creator_id=12345)
    assert isinstance(item, DownloadTimelineOnly)
    assert isinstance(item, WorkItem)


def test_download_timeline_only_stores_creator_id() -> None:
    """DownloadTimelineOnly.creator_id matches the value supplied at construction."""
    item = DownloadTimelineOnly(creator_id=12345)
    assert item.creator_id == 12345


def test_download_timeline_only_is_frozen() -> None:
    """DownloadTimelineOnly is a frozen dataclass — mutation raises FrozenInstanceError."""
    item = DownloadTimelineOnly(creator_id=12345)
    with pytest.raises((AttributeError, TypeError)):
        item.creator_id = 99999  # type: ignore[misc]


# All registered (svc, type) pairs — dispatch_ws_event must never emit
# DownloadTimelineOnly for any of them.
_ALL_DISPATCH_PAIRS: list[tuple[int, int, dict]] = [
    (5, 1, {"message": {"groupId": "200000000001", "attachments": [{"id": "x"}]}}),
    (15, 5, {"subscription": {"accountId": "500000000001", "status": 3}}),
    (2, 7, {"order": {"correlationAccountId": "500000000002"}}),
    (2, 8, {"order": {"correlationAccountId": "500000000002"}}),
    (3, 2, {"follow": {"accountId": "500000000003"}}),
    (6, 2, {"wallet": {"balance": 5000}}),
]


@pytest.mark.parametrize(("svc", "etype", "event"), _ALL_DISPATCH_PAIRS)
def test_dispatch_never_emits_download_timeline_only(
    svc: int, etype: int, event: dict
) -> None:
    """dispatch_ws_event must NOT return DownloadTimelineOnly for any svc/type pair.

    DownloadTimelineOnly is produced only by poll_home_timeline (Wave 2),
    never by the WS dispatch table.
    """
    result = dispatch_ws_event(svc, etype, event)
    assert not isinstance(result, DownloadTimelineOnly)
