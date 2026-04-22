"""WebSocket event handlers for the monitoring daemon.

This module is stateless: it translates decoded ServiceEvent dicts into typed
WorkItem values that the daemon runner executes.  No downloads, no API calls,
no side effects — pure input → output functions.

Event shapes come from the ``_monitor_*`` methods in ``api/websocket.py``.
The ``dispatch_ws_event`` entry point mirrors the dispatch table used by
``FanslyWebSocket._monitor_service_event``.

Service IDs referenced here (from ``FanslyWebSocket`` constants):
    SVC_MEDIA        = 2
    SVC_FOLLOWS      = 3
    SVC_MSG_INTERACT = 5
    SVC_WALLET       = 6
    SVC_SUBSCRIPTIONS = 15
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from config.logging import textio_logger as logger


# ---------------------------------------------------------------------------
# WorkItem hierarchy
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class WorkItem:
    """Base class for all daemon work items.

    Each concrete subclass represents one actionable unit of work the daemon
    runner will execute in response to a WebSocket event.  Frozen dataclasses
    give value equality and hashability out of the box.
    """


@dataclass(frozen=True, slots=True)
class DownloadMessagesForGroup(WorkItem):
    """Trigger a message-attachment download for the specified DM group.

    Produced by svc=5 type=1 (new message) when the message payload includes
    at least one attachment entry.
    """

    group_id: int


@dataclass(frozen=True, slots=True)
class FullCreatorDownload(WorkItem):
    """Trigger a full download pass for a newly-accessible creator.

    Produced by svc=15 type=5 status=3 (subscription confirmed).
    """

    creator_id: int


@dataclass(frozen=True, slots=True)
class RedownloadCreatorMedia(WorkItem):
    """Re-download a creator's media after a PPV purchase makes it accessible.

    Produced by svc=2 type=7 (PPV media purchased) and type=8 (PPV bundle
    purchased).
    """

    creator_id: int


@dataclass(frozen=True, slots=True)
class CheckCreatorAccess(WorkItem):
    """Check whether a newly-followed creator has accessible content.

    Produced by svc=3 type=2 (new follow).
    """

    creator_id: int


@dataclass(frozen=True, slots=True)
class DownloadStoriesOnly(WorkItem):
    """Trigger a story-only download pass for a creator whose story state flipped.

    Produced by the story-state polling loop when hasActiveStories flips from
    False (or unknown) to True.  The runner MUST pass mark_viewed=False so
    the daemon does not affect the user's real Fansly UX.
    """

    creator_id: int


@dataclass(frozen=True, slots=True)
class DownloadTimelineOnly(WorkItem):
    """Download ONLY the creator's timeline.

    Emitted by the home-timeline poll (``daemon/polling.py::poll_home_timeline``)
    when a new post is detected for a followed creator.  Narrower than
    ``FullCreatorDownload`` (which runs timeline+stories+messages+wall for
    subscription events) — this item triggers only a timeline pass.

    NOT produced by any WebSocket event.  ``dispatch_ws_event`` will never
    return an instance of this class.
    """

    creator_id: int


@dataclass(frozen=True, slots=True)
class MarkMessagesDeleted(WorkItem):
    """Mark already-downloaded DM rows as deleted in the local archive.

    Produced by svc=5 type=10 (message deleted). We preserve the file on
    disk and any Attachment rows — only the Message row's ``deleted`` /
    ``deletedAt`` fields are flipped so the archive reflects the
    creator's state change without losing content.

    ``deleted_at_epoch`` is the server-provided unix timestamp on the
    deletion payload when available; the runner falls back to "now"
    only if the server did not supply one.
    """

    message_ids: tuple[int, ...]
    deleted_at_epoch: int | None = None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _safe_int(value: Any) -> int | None:
    """Convert *value* to int, returning None on failure.

    Handles None, empty strings, and values that cannot be coerced without
    raising. Fansly IDs commonly arrive as numeric strings from JSON, so
    coercion is the expected path for well-formed events. Booleans are
    rejected explicitly — ``bool`` is a subclass of ``int`` in Python, so
    a payload containing ``true``/``false`` would otherwise silently turn
    into 1/0 which would be an unsafe WorkItem target.

    Args:
        value: The raw value from the event dict (may be str, int, or None).

    Returns:
        Integer representation of *value*, or None if conversion is impossible.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Per-(svc, type) handler callables
# ---------------------------------------------------------------------------


def _handle_new_message(event: dict[str, Any]) -> WorkItem | None:
    """Handle svc=5 type=1 — new message with possible attachments.

    Returns:
        DownloadMessagesForGroup when the message has at least one attachment.
        None for pure text messages or malformed payloads.
    """
    message = event.get("message")
    if not isinstance(message, dict):
        return None

    attachments = message.get("attachments", [])
    if not attachments:
        return None

    group_id = _safe_int(message.get("groupId"))
    if group_id is None:
        logger.warning(
            "daemon.handlers: svc=5 type=1 — missing/invalid groupId in message"
        )
        return None

    return DownloadMessagesForGroup(group_id=group_id)


def _handle_subscription_event(event: dict[str, Any]) -> WorkItem | None:
    """Handle svc=15 type=5 — subscription lifecycle event.

    Returns:
        FullCreatorDownload when status=3 (confirmed).
        None for status=2 (pending) or any other non-confirmed status.
    """
    subscription = event.get("subscription")
    if not isinstance(subscription, dict):
        return None

    status = subscription.get("status")
    if status != 3:
        return None

    creator_id = _safe_int(subscription.get("accountId"))
    if creator_id is None:
        logger.warning(
            "daemon.handlers: svc=15 type=5 status=3 — missing/invalid accountId"
        )
        return None

    return FullCreatorDownload(creator_id=creator_id)


def _handle_ppv_purchase(event: dict[str, Any]) -> WorkItem | None:
    """Handle svc=2 type=7/8 and svc=32 type=7 — PPV media / bundle / story purchased.

    Returns:
        RedownloadCreatorMedia when correlationAccountId is present and valid.
        None on malformed payloads.
    """
    order = event.get("order")
    if not isinstance(order, dict):
        return None

    creator_id = _safe_int(order.get("correlationAccountId"))
    if creator_id is None:
        logger.warning(
            "daemon.handlers: PPV event — missing/invalid correlationAccountId"
        )
        return None

    return RedownloadCreatorMedia(creator_id=creator_id)


def _handle_message_deleted(event: dict[str, Any]) -> WorkItem | None:
    """Handle svc=5 type=10 — message deleted.

    Returns:
        MarkMessagesDeleted with the affected message_ids and the
        server-provided ``deletedAt`` epoch when present. None if the
        payload carries no ids we can act on.
    """
    message = event.get("message")
    if not isinstance(message, dict):
        return None

    raw_ids = message.get("ids") or message.get("messageIds") or []
    if not isinstance(raw_ids, list):
        return None

    ids: list[int] = []
    for raw_id in raw_ids:
        coerced = _safe_int(raw_id)
        if coerced is not None:
            ids.append(coerced)

    # Fallback: single ``id`` field on the message object (the shape Fansly
    # actually emits per observed traffic).
    single = _safe_int(message.get("id"))
    if single is not None and single not in ids:
        ids.append(single)

    if not ids:
        return None

    return MarkMessagesDeleted(
        message_ids=tuple(ids),
        deleted_at_epoch=_safe_int(message.get("deletedAt")),
    )


def _handle_account_profile_updated(event: dict[str, Any]) -> WorkItem | None:
    """Handle svc=12 type=2 — account profile updated.

    Observation-only for now. Documentation is ambiguous about whether this
    fires for other creators' profiles or just our own; log the accountId
    so we can decide empirically before wiring a real state-sync action.
    """
    account = event.get("account")
    if not isinstance(account, dict):
        return None

    account_id = _safe_int(account.get("id"))
    logger.info(
        "daemon.handlers: svc=12 type=2 profile update observed (accountId={})",
        account_id,
    )
    return None


def _handle_new_follow(event: dict[str, Any]) -> WorkItem | None:
    """Handle svc=3 type=2 — new follow event.

    Returns:
        CheckCreatorAccess with the followed creator's account ID.
        None on malformed payloads.
    """
    follow = event.get("follow")
    if not isinstance(follow, dict):
        return None

    creator_id = _safe_int(follow.get("accountId"))
    if creator_id is None:
        logger.warning(
            "daemon.handlers: svc=3 type=2 — missing/invalid accountId in follow"
        )
        return None

    return CheckCreatorAccess(creator_id=creator_id)


def _handle_wallet_event(_event: dict[str, Any]) -> WorkItem | None:
    """Handle svc=6 type=2 — wallet credited.

    Informational only — no download action required.

    Returns:
        Always None.
    """
    return None


# ---------------------------------------------------------------------------
# Dispatch table
# ---------------------------------------------------------------------------

# Maps (service_id, event_type) → handler callable.
# Handlers receive the decoded event dict and return WorkItem | None.
_DISPATCH: dict[tuple[int, int], Any] = {
    (5, 1): _handle_new_message,
    (5, 10): _handle_message_deleted,
    (15, 5): _handle_subscription_event,
    (2, 7): _handle_ppv_purchase,
    (2, 8): _handle_ppv_purchase,
    (3, 2): _handle_new_follow,
    (6, 2): _handle_wallet_event,
    (12, 2): _handle_account_profile_updated,
    (32, 7): _handle_ppv_purchase,
}


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def dispatch_ws_event(
    service_id: int,
    event_type: int,
    event: dict[str, Any],
) -> WorkItem | None:
    """Translate a decoded ServiceEvent into a WorkItem, or None if no action needed.

    ``event`` is the already-decoded payload (after the envelope → serviceId/event
    → payload triple-JSON unpacking done by FanslyWebSocket._monitor_service_event).

    Args:
        service_id: The ``serviceId`` field from the decoded envelope.
        event_type: The ``type`` field from the inner event dict.
        event: The fully-decoded inner event dict.

    Returns:
        A WorkItem subclass instance describing the required action, or None
        when the event is informational, irrelevant, or malformed.
    """
    handler = _DISPATCH.get((service_id, event_type))
    if handler is None:
        return None
    return handler(event)
