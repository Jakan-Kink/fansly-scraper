"""Tests for daemon.handlers.dispatch_ws_event and the WorkItem dataclasses.

The dispatch behaviors are consolidated into two parametrized matrices that
collapse 31 near-identical single-assertion tests into 2 test definitions
(31 cases each surfaced individually by pytest):

* ``test_dispatch_returns_expected_workitem`` — happy paths where a known
  ``(svc, type, event)`` triple should produce a specific ``WorkItem``.
* ``test_dispatch_returns_none`` — filter/malformed/edge inputs that must
  return ``None`` without raising. Includes documentation cases (intentional
  observation-only handlers), service-event filters (e.g. subscription
  status != 3), and defensive malformed-payload handling.

The ``WorkItem`` dataclass tests (``TestWorkItemStructure``) stay separate
because they exercise different concerns (frozen-ness, equality, base-class
relationship) that don't reduce to a (input, expected) shape.
"""

from __future__ import annotations

import io
import logging

import pytest
from loguru import logger as loguru_logger

from daemon.handlers import (
    _GATHERING_DESCRIPTIONS,
    _NOOP_DESCRIPTIONS,
    CheckCreatorAccess,
    DownloadMessagesForGroup,
    DownloadTimelineOnly,
    FullCreatorDownload,
    MarkMessagesDeleted,
    RedownloadCreatorMedia,
    WorkItem,
    dispatch_ws_event,
    has_handler,
)
from tests.fixtures.utils.test_isolation import snowflake_id


# ---------------------------------------------------------------------------
# Happy-path dispatch matrix: (svc, type, event) → expected WorkItem
# ---------------------------------------------------------------------------


# IDs that must match between input payload and expected WorkItem assertion.
# Generated once at module-load so the two sides of each paired case agree.
_PPV_CREATOR_ID = snowflake_id()
_DELETED_MSG_ID = snowflake_id()


_HAPPY_PATH_CASES: list[tuple[int, int, dict, WorkItem]] = [
    # svc=5 type=1 — new message with attachments → DownloadMessagesForGroup
    pytest.param(
        5,
        1,
        {
            "message": {
                "id": "900000000001",
                "senderId": "100000000001",
                "groupId": "200000000001",
                "content": "check this out",
                "attachments": [{"contentId": "300000000001", "contentType": 1}],
            }
        },
        DownloadMessagesForGroup(group_id=200_000_000_001, sender_id=100_000_000_001),
        id="new_message_with_attachments",
    ),
    # svc=15 type=5 status=3 — confirmed subscription → FullCreatorDownload
    pytest.param(
        15,
        5,
        {
            "subscription": {
                "id": "400000000001",
                "accountId": "500000000001",
                "status": 3,
                "price": 1000,
                "subscriptionTierName": "Standard",
            }
        },
        FullCreatorDownload(creator_id=500_000_000_001),
        id="subscription_confirmed_status_3",
    ),
    # svc=2 type=7 — PPV media purchase → RedownloadCreatorMedia
    pytest.param(
        2,
        7,
        {
            "order": {
                "orderId": "700000000001",
                "accountMediaId": "800000000001",
                "correlationAccountId": "500000000002",
            }
        },
        RedownloadCreatorMedia(creator_id=500_000_000_002),
        id="ppv_media_purchase",
    ),
    # svc=2 type=8 — PPV bundle purchase → RedownloadCreatorMedia
    pytest.param(
        2,
        8,
        {
            "order": {
                "orderId": "700000000002",
                "accountMediaBundleId": "800000000002",
                "correlationAccountId": "500000000002",
            }
        },
        RedownloadCreatorMedia(creator_id=500_000_000_002),
        id="ppv_bundle_purchase",
    ),
    # svc=3 type=2 — new follow → CheckCreatorAccess
    pytest.param(
        3,
        2,
        {
            "follow": {
                "id": "600000000001",
                "accountId": "500000000003",
                "followerId": "999000000001",
            }
        },
        CheckCreatorAccess(creator_id=500_000_000_003),
        id="new_follow",
    ),
    # svc=32 type=7 — story PPV (shares payload shape with svc=2 type=7)
    pytest.param(
        32,
        7,
        {
            "order": {
                "orderId": "900000000001",
                "accountMediaId": "800000000001",
                "correlationAccountId": str(_PPV_CREATOR_ID),
                "type": 1,
            }
        },
        RedownloadCreatorMedia(creator_id=_PPV_CREATOR_ID),
        id="story_ppv_purchase",
    ),
    # svc=5 type=10 — message deleted via ids list
    pytest.param(
        5,
        10,
        {"message": {"ids": ["900000000001", "900000000002"]}},
        MarkMessagesDeleted(message_ids=(900_000_000_001, 900_000_000_002)),
        id="message_deleted_ids_list",
    ),
    # Mixed coercible/uncoercible ids — bad ones skipped (covers handlers.py 259→257).
    pytest.param(
        5,
        10,
        {"message": {"ids": ["not-a-number", "900000000003"]}},
        MarkMessagesDeleted(message_ids=(900_000_000_003,)),
        id="message_deleted_ids_skip_uncoercible",
    ),
    # svc=5 type=10 — single id + deletedAt (the shape Fansly emits in practice)
    pytest.param(
        5,
        10,
        {"message": {"id": str(_DELETED_MSG_ID), "deletedAt": 1_776_837_144}},
        MarkMessagesDeleted(
            message_ids=(_DELETED_MSG_ID,),
            deleted_at_epoch=1_776_837_144,
        ),
        id="message_deleted_single_id_with_deletedAt",
    ),
    # svc=5 type=10 — single id, no deletedAt → falls back to None
    pytest.param(
        5,
        10,
        {"message": {"id": "900000000042"}},
        MarkMessagesDeleted(message_ids=(900_000_000_042,), deleted_at_epoch=None),
        id="message_deleted_no_deletedAt_falls_back",
    ),
]


@pytest.mark.parametrize(("svc", "etype", "event", "expected"), _HAPPY_PATH_CASES)
def test_dispatch_returns_expected_workitem(
    svc: int, etype: int, event: dict, expected: WorkItem
) -> None:
    """Each well-formed (svc, type, event) triple produces the expected WorkItem.

    Covers the entire happy-path dispatch table — adding a new handler means
    adding a single ``pytest.param(...)`` row, not a new test function.
    """
    assert dispatch_ws_event(svc, etype, event) == expected


# ---------------------------------------------------------------------------
# Filter / malformed dispatch matrix: (svc, type, event) → None
# ---------------------------------------------------------------------------


_RETURNS_NONE_CASES: list[tuple[int, int, dict]] = [
    # ── Intentional filters: handler ran but had nothing actionable ──
    pytest.param(
        5,
        1,
        {
            "message": {
                "id": "900000000002",
                "senderId": "100000000001",
                "groupId": "200000000001",
                "content": "just a text message",
                "attachments": [],
            }
        },
        id="new_message_empty_attachments",
    ),
    pytest.param(
        5,
        1,
        {
            "message": {
                "id": "900000000003",
                "senderId": "100000000001",
                "groupId": "200000000001",
                "content": "another text",
            }
        },
        id="new_message_missing_attachments_key",
    ),
    pytest.param(
        15,
        5,
        {
            "subscription": {
                "id": "400000000002",
                "accountId": "500000000001",
                "status": 2,
                "price": 1000,
            }
        },
        id="subscription_pending_status_2",
    ),
    pytest.param(
        15,
        5,
        {
            "subscription": {
                "id": "400000000003",
                "accountId": "500000000001",
                "status": 1,
            }
        },
        id="subscription_other_status",
    ),
    # ── Observation-only handlers: known events, no WorkItem produced ──
    pytest.param(
        6,
        2,
        {"wallet": {"id": "610000000001", "balance": 5000, "walletVersion": 7}},
        id="wallet_credited_observation_only",
    ),
    pytest.param(
        6,
        3,
        {
            "transaction": {
                "id": str(snowflake_id()),
                "type": 58000,
                "originWalletId": str(snowflake_id()),
                "destinationWalletId": None,
                "status": 2,
                "amount": 6990,
                "correlationId": str(snowflake_id()),
            }
        },
        id="wallet_transaction_observation_only",
    ),
    pytest.param(6, 3, {}, id="wallet_transaction_no_payload"),
    pytest.param(
        12,
        2,
        {
            "account": {
                "id": str(snowflake_id()),
                "displayName": "Test",
                "flags": 0,
            }
        },
        id="account_profile_updated_observation_only",
    ),
    # Defensive: account not a dict (the `if not isinstance(account, dict)` guard)
    pytest.param(12, 2, {"account": None}, id="account_profile_not_a_dict"),
    # ── _NOOP_DESCRIPTIONS path (engagement / receipts; routed through _handle_noop_events) ──
    pytest.param(
        1,
        2,
        {
            "like": {
                "accountId": str(snowflake_id()),
                "postId": str(snowflake_id()),
                "id": str(snowflake_id()),
            }
        },
        id="noop_post_like",
    ),
    pytest.param(
        2,
        2,
        {
            "like": {
                "accountId": str(snowflake_id()),
                "accountMediaId": str(snowflake_id()),
                "id": str(snowflake_id()),
            }
        },
        id="noop_media_like",
    ),
    pytest.param(4, 1, {"foo": "bar"}, id="noop_message_delivered_ack"),
    pytest.param(4, 2, {"foo": "bar"}, id="noop_message_read_receipt_ack"),
    # Phase 5: svc=5 type=22 typing-announce — intentional noop (fires every 3-5 s)
    pytest.param(
        5,
        22,
        {
            "typingAnnounceEvent": {
                "accountId": str(snowflake_id()),
                "groupId": str(snowflake_id()),
                "lastAnnounce": 0,
            }
        },
        id="noop_typing_announce",
    ),
    # ── Empty / unknown events ──
    pytest.param(5, 10, {"message": {}}, id="message_deleted_empty_payload"),
    # Defensive: message.ids is not a list (the `if not isinstance(raw_ids, list)` guard)
    pytest.param(
        5, 10, {"message": {"ids": "not-a-list"}}, id="message_deleted_ids_not_a_list"
    ),
    # Defensive: message itself is not a dict (covers handlers.py:250).
    pytest.param(5, 10, {"message": "not-a-dict"}, id="message_deleted_not_a_dict"),
    pytest.param(99, 99, {"foo": "bar"}, id="unknown_svc_unknown_type"),
    pytest.param(
        5,
        999,
        {"message": {"groupId": "200000000001", "attachments": []}},
        id="known_svc_unknown_type",
    ),
    # ── Defensive: malformed payloads must never raise ──
    pytest.param(
        2, 7, {"order": {"correlationAccountId": None}}, id="ppv_correlation_None"
    ),
    pytest.param(
        2,
        7,
        {"order": {"orderId": "700000000003", "accountMediaId": "800000000003"}},
        id="ppv_missing_correlation_account_id",
    ),
    pytest.param(5, 1, {}, id="malformed_completely_empty_dict"),
    pytest.param(
        5,
        1,
        {"message": {"groupId": True, "attachments": [{"id": "x"}]}},
        id="malformed_boolean_group_id_rejected",  # bool not silently coerced to 1/0
    ),
    pytest.param(5, 1, {"unexpected_key": "data"}, id="malformed_no_message_key"),
    pytest.param(
        5,
        1,
        {"message": {"groupId": "not-an-int", "attachments": [{"contentType": 1}]}},
        id="malformed_unparseable_group_id",
    ),
    pytest.param(
        5,
        1,
        {"message": {"groupId": None, "attachments": [{"contentType": 1}]}},
        id="malformed_null_group_id",
    ),
    pytest.param(
        15,
        5,
        {"subscription": {"status": 3}},
        id="malformed_subscription_missing_account_id",
    ),
    pytest.param(
        3,
        2,
        {"follow": {"id": "600000000001", "followerId": "999000000001"}},
        id="malformed_follow_missing_account_id",
    ),
    # ── Non-dict payload variants — handlers must isinstance-guard ──
    pytest.param(2, 7, {"order": "unexpected-string"}, id="ppv_order_not_a_dict"),
    pytest.param(15, 5, {"subscription": 42}, id="subscription_not_a_dict"),
    pytest.param(3, 2, {"follow": [1, 2, 3]}, id="follow_not_a_dict"),
]


@pytest.mark.parametrize(("svc", "etype", "event"), _RETURNS_NONE_CASES)
def test_dispatch_returns_none(svc: int, etype: int, event: dict) -> None:
    """Filter/malformed/edge events return None without raising.

    Covers (1) intentional filters where the handler had no actionable
    output, (2) observation-only handlers (wallet/profile updates),
    (3) unknown svc/type pairs, and (4) defensive malformed-payload
    handling. All must complete without exception.
    """
    assert dispatch_ws_event(svc, etype, event) is None


# ---------------------------------------------------------------------------
# WorkItem dataclass structural checks
# ---------------------------------------------------------------------------


class TestWorkItemStructure:
    """Concerns specific to the dataclass shape, not the dispatch table.

    These don't reduce to (input, expected) parametrization because each
    test exercises a distinct invariant: frozen-ness (mutation rejected),
    structural equality (two instances compare equal), base-class
    inheritance, and per-class instantiation contracts.
    """

    def test_work_item_subclasses_are_frozen(self) -> None:
        """Frozen dataclasses reject attribute mutation."""
        item = DownloadMessagesForGroup(group_id=12345)
        with pytest.raises((AttributeError, TypeError)):
            item.group_id = 99999  # type: ignore[misc]

    def test_work_item_equality_is_structural(self) -> None:
        """Two WorkItem subclass instances with same fields compare equal."""
        a = RedownloadCreatorMedia(creator_id=777)
        b = RedownloadCreatorMedia(creator_id=777)
        assert a == b
        assert a is not b

    def test_work_item_base_class_relationship(self) -> None:
        """All concrete WorkItem subclasses are instances of WorkItem base."""
        items: list[WorkItem] = [
            DownloadMessagesForGroup(group_id=1),
            FullCreatorDownload(creator_id=2),
            RedownloadCreatorMedia(creator_id=3),
            CheckCreatorAccess(creator_id=4),
        ]
        for item in items:
            assert isinstance(item, WorkItem)

    def test_download_timeline_only_instantiates_with_creator_id(self) -> None:
        """DownloadTimelineOnly can be constructed with a creator_id int."""
        item = DownloadTimelineOnly(creator_id=12345)
        assert isinstance(item, DownloadTimelineOnly)
        assert isinstance(item, WorkItem)

    def test_download_timeline_only_stores_creator_id(self) -> None:
        """DownloadTimelineOnly.creator_id matches the value supplied at construction."""
        item = DownloadTimelineOnly(creator_id=12345)
        assert item.creator_id == 12345

    def test_download_timeline_only_is_frozen(self) -> None:
        """DownloadTimelineOnly is a frozen dataclass — mutation raises FrozenInstanceError."""
        item = DownloadTimelineOnly(creator_id=12345)
        with pytest.raises((AttributeError, TypeError)):
            item.creator_id = 99999  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Dispatch invariant: never emits DownloadTimelineOnly (only poll path does)
# ---------------------------------------------------------------------------


# All registered (svc, type) pairs — dispatch_ws_event must never emit
# DownloadTimelineOnly for any of them.
_ALL_DISPATCH_PAIRS: list[tuple[int, int, dict]] = [
    (5, 1, {"message": {"groupId": "200000000001", "attachments": [{"id": "x"}]}}),
    (5, 10, {"message": {"id": "900000000001"}}),
    (15, 5, {"subscription": {"accountId": "500000000001", "status": 3}}),
    (2, 7, {"order": {"correlationAccountId": "500000000002"}}),
    (2, 8, {"order": {"correlationAccountId": "500000000002"}}),
    (3, 2, {"follow": {"accountId": "500000000003"}}),
    (6, 2, {"wallet": {"balance": 5000}}),
    (6, 3, {"transaction": {"type": 58000, "status": 2, "amount": 6990}}),
    (12, 2, {"account": {"id": str(snowflake_id())}}),
    (32, 7, {"order": {"correlationAccountId": "500000000004"}}),
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


# ---------------------------------------------------------------------------
# Phase 5: has_handler() recognises (5, 22) typing-announce as handled
# ---------------------------------------------------------------------------


def test_has_handler_typing_announce() -> None:
    """Phase 5: has_handler(5, 22) returns True.

    Ensures the runner won't log “unknown/unhandled” for every typing-announce
    event, which fires every 3-5 seconds and would flood the log.
    """
    assert has_handler(5, 22) is True


# ---------------------------------------------------------------------------
# Four-class taxonomy: gathering tier (provisional classification)
# ---------------------------------------------------------------------------


def test_gathering_entry_routes_to_gathering_handler(caplog) -> None:
    """An entry in _GATHERING_DESCRIPTIONS dispatches via the gathering path."""
    caplog.set_level(logging.DEBUG)
    _GATHERING_DESCRIPTIONS[(9999, 1)] = "test gathering entry"
    try:
        result = dispatch_ws_event(9999, 1, {"sample": "payload"})
    finally:
        _GATHERING_DESCRIPTIONS.pop((9999, 1), None)

    assert result is None
    debug_msgs = [r.getMessage() for r in caplog.records if r.levelname == "DEBUG"]
    assert any(
        "gathering" in m and "svc=9999 type=1" in m and "test gathering entry" in m
        for m in debug_msgs
    )


def test_has_handler_recognises_gathering_entries() -> None:
    """has_handler returns True for entries in _GATHERING_DESCRIPTIONS."""
    _GATHERING_DESCRIPTIONS[(9999, 2)] = "test gathering entry"
    try:
        assert has_handler(9999, 2) is True
    finally:
        _GATHERING_DESCRIPTIONS.pop((9999, 2), None)


def test_noop_takes_precedence_over_gathering() -> None:
    """A (svc, type) pair listed in both tables routes through noop, not gathering."""
    _NOOP_DESCRIPTIONS[(8888, 1)] = "noop wins"
    _GATHERING_DESCRIPTIONS[(8888, 1)] = "gathering loses"
    sink = io.StringIO()
    sink_id = loguru_logger.add(sink, level="DEBUG")
    try:
        dispatch_ws_event(8888, 1, {})
    finally:
        loguru_logger.remove(sink_id)
        _NOOP_DESCRIPTIONS.pop((8888, 1), None)
        _GATHERING_DESCRIPTIONS.pop((8888, 1), None)
    output = sink.getvalue()
    assert "known event" in output
    assert "noop wins" in output
    assert "gathering loses" not in output
