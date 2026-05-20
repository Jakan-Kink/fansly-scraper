"""In-process WebSocket test server for the Fansly t/d JSON protocol.

Spins up a real ``websockets`` server on an ephemeral local port so the
production ``_ChildWebSocket`` client can connect, authenticate, ping, and
receive scripted events without touching the network.

Usage (the ``ws_server`` fixture is auto-loaded via
``tests/fixtures/__init__.py`` → ``tests/conftest.py``'s wildcard import;
tests just take it as a function argument):

    @pytest.mark.asyncio
    async def test_subscription_event_dispatches(ws_server):
        ws_server.set_session(id="42", account_id="100")

        client = _ChildWebSocket(
            token="t",
            user_agent="ua",
            base_url=ws_server.base_url,        # ws://127.0.0.1:<port>
            ping_timeout_enabled=False,
        )
        seen: list[dict] = []
        client.register_handler(MSG_SERVICE_EVENT, lambda d: seen.append(d))

        await client.connect()
        await ws_server.wait_for_auth()

        ws_server.push_service_event(
            service_id=15, inner={"type": 5, "subscription": {"accountId": "9"}}
        )
        await asyncio.sleep(0.05)

        assert seen[-1]["serviceId"] == 15
        await client.disconnect()

Gotchas:
    * The production client passes ``ssl=ssl_context`` to ``connect()``
      unconditionally. For a ``ws://`` URI ``websockets`` ignores it,
      so no SSL gymnastics are needed here. If a future version starts
      rejecting that combo, monkey-patch ``_create_ssl_context`` to
      return ``None`` in your test setup.
    * Requires ``websockets`` >= 13 for ``websockets.asyncio.server`` and
      the ``process_response`` callback signature used below.
    * Uses ``pytest-asyncio``. Set ``asyncio_mode = "auto"`` in your
      ``pyproject.toml`` or decorate tests with ``@pytest.mark.asyncio``.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections import deque
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import pytest_asyncio
from loguru import logger
from websockets.asyncio.server import ServerConnection, serve

from tests.fixtures.utils.test_isolation import snowflake_id


_BIND_HOST = "127.0.0.1"


# Top-level message types — duplicated here so the test server has no
# import dependency on production ``api/websocket_protocol.py``. Tests
# can then exercise the protocol even when that module is mid-refactor.
# Numbers MUST stay in sync with the protocol spec.
T_ERROR = 0
T_SESSION = 1
T_PING = 2
T_SERVICE_EVENT = 10000
T_BATCH = 10001
T_CHAT_ROOM = 46001


@dataclass
class _SessionInfo:
    id: str = "test-session-id"
    websocket_session_id: str = "test-ws-session-id"
    account_id: str = "test-account-id"


@dataclass
class FakeFanslyWSServer:
    """In-process WebSocket server speaking Fansly's ``t``/``d`` JSON protocol.

    One instance handles arbitrarily many client connections in sequence
    (reconnect tests), or in parallel (multi-client tests). State is
    per-fixture, reset between tests.

    Tests interact through:

      * ``push_*`` methods            — queue an outbound frame; the next
                                        connected client receives it.
      * ``received``                  — deque of every frame the server
                                        has received from any client.
      * ``connections``               — list of currently-open
                                        ``ServerConnection`` instances.
      * ``auth_event``                — ``asyncio.Event`` set when a
                                        ``t=1`` auth frame arrives.
                                        Clear it manually between
                                        reconnect cycles if you reuse
                                        the same server across them.
      * ``auto_ack`` / ``auto_pong``  — defaults; flip to False to drive
                                        the handshake or ping path
                                        manually.
      * ``set_session(...)``          — override the auto-ack response.
      * ``upgrade_set_cookies``       — raw ``Set-Cookie`` header lines
                                        appended to every upgrade
                                        response (cookie-absorption
                                        tests).
      * ``last_request_headers``      — headers from the most recent
                                        WS upgrade request, for
                                        asserting ``Cookie``,
                                        ``User-Agent``, ``Origin``, etc.
    """

    base_url: str = ""
    port: int = 0
    received: deque[dict[str, Any]] = field(default_factory=deque)
    # Parallel raw-text capture. Includes non-JSON frames (e.g., bare "p"
    # ping strings) that the parsed ``received`` deque drops. Tests that
    # need to observe pings or other non-JSON wire traffic read from here.
    received_raw: deque[str] = field(default_factory=deque)
    connections: list[ServerConnection] = field(default_factory=list)
    upgrade_set_cookies: list[str] = field(default_factory=list)
    last_request_headers: dict[str, str] = field(default_factory=dict)
    auto_ack: bool = True
    auto_pong: bool = True
    # When True, close every incoming connection immediately after accept,
    # before any frames are exchanged. Client sees a clean disconnect
    # (``ConnectionClosedOK``) on its first ``recv()``. Use to test
    # client behaviour on server-initiated disconnect.
    close_on_connect: bool = False
    _session: _SessionInfo = field(default_factory=_SessionInfo)
    _outbound: asyncio.Queue[str] = field(default_factory=asyncio.Queue)
    _pusher_tasks: list[asyncio.Task[None]] = field(default_factory=list)
    auth_event: asyncio.Event = field(default_factory=asyncio.Event)

    # ---- session / handshake config ----------------------------------

    def set_session(
        self,
        *,
        id: str | None = None,
        websocket_session_id: str | None = None,
        account_id: str | None = None,
    ) -> None:
        """Override fields of the auto-ack session response."""
        if id is not None:
            self._session.id = id
        if websocket_session_id is not None:
            self._session.websocket_session_id = websocket_session_id
        if account_id is not None:
            self._session.account_id = account_id

    # ---- frame push helpers ------------------------------------------

    def push_raw(self, payload: str) -> None:
        """Queue a pre-serialized JSON string for the next outbound send."""
        self._outbound.put_nowait(payload)

    def push(self, frame: dict[str, Any]) -> None:
        """Queue a frame (dict) for the next outbound send."""
        self.push_raw(json.dumps(frame))

    def push_service_event(
        self,
        *,
        service_id: int,
        inner: dict[str, Any],
        inner_as_string: bool = True,
    ) -> None:
        """Queue a ``t=10000`` service event.

        Fansly double-encodes both the ``d`` envelope and the inner
        ``event`` field as JSON strings. ``inner_as_string=False`` skips
        the inner re-encode if you're testing a server variant that
        sends the dict shape directly.
        """
        event_field: Any = json.dumps(inner) if inner_as_string else inner
        d_dict = {"serviceId": service_id, "event": event_field}
        self.push({"t": T_SERVICE_EVENT, "d": json.dumps(d_dict)})

    def push_batch(self, sub_frames: list[dict[str, Any]]) -> None:
        """Queue a ``t=10001`` batch wrapping pre-built sub-frames.

        Sub-frames are passed as dicts; the production client's
        recursive handler will re-serialize them on the way in.
        """
        self.push({"t": T_BATCH, "d": sub_frames})

    def push_error(self, code: int, **extra: Any) -> None:
        """Queue a ``t=0`` error frame (e.g. ``code=401`` or ``code=429``)."""
        payload = {"code": code, **extra}
        self.push({"t": T_ERROR, "d": json.dumps(payload)})

    def push_session_response(self, session: dict[str, Any] | None = None) -> None:
        """Queue a ``t=1`` session-verified frame.

        Only needed when ``auto_ack`` is False; otherwise the handler
        sends this automatically on receiving the client's ``t=1``.
        """
        s = session or {
            "id": self._session.id,
            "websocketSessionId": self._session.websocket_session_id,
            "accountId": self._session.account_id,
        }
        self.push({"t": T_SESSION, "d": json.dumps({"session": s})})

    # ---- canonical service-event builders ----------------------------
    #
    # Each method below constructs a documented payload shape per
    # docs/reference/Fansly-WebSocket-Protocol.md and queues it via
    # push_service_event. Test authors pass the semantic args they
    # care about; everything else gets a sensible default. The wire
    # shape stays visible at the call site — these are payload
    # constructors, not scenario macros.

    # ── PostService (svc=1) ──────────────────────────────────────────

    def push_post_like(
        self, *, account_id: str, post_id: str, like_id: str | None = None
    ) -> None:
        """Queue a ``(1, 2)`` post-like event."""
        self.push_service_event(
            service_id=1,
            inner={
                "type": 2,
                "like": {
                    "accountId": account_id,
                    "postId": post_id,
                    "id": like_id or str(snowflake_id()),
                },
            },
        )

    # ── MediaService (svc=2) ─────────────────────────────────────────

    def push_media_like(
        self,
        *,
        account_id: str,
        account_media_id: str,
        like_id: str | None = None,
        account_media_access: bool = True,
        location_type: str | None = None,
        location_correlation_id: str | None = None,
        location_metadata: str | None = None,
        created_at: int | None = None,
    ) -> None:
        """Queue a ``(2, 2)`` media-like event.

        Pass ``location_type``/``location_correlation_id``/``location_metadata``
        to construct the discovery-breadcrumb form; leave None for the
        non-discovery (profile-page / post-detail) variant.
        """
        self.push_service_event(
            service_id=2,
            inner={
                "type": 2,
                "like": {
                    "accountId": account_id,
                    "accountMediaId": account_media_id,
                    "accountMediaBundleId": None,
                    "accountMediaAccess": account_media_access,
                    "locationType": location_type,
                    "locationCorrelationId": location_correlation_id,
                    "locationMetadata": location_metadata,
                    "id": like_id or str(snowflake_id()),
                    "createdAt": created_at or (snowflake_id() % 10**13),
                },
            },
        )

    def push_ppv_order(
        self,
        *,
        creator_id: str,
        buyer_id: str,
        account_media_id: str,
        order_type: int,
        order_id: str | None = None,
        account_media_bundle_id: str | None = None,
    ) -> None:
        """Queue a ``(2, 7)`` PPV order event.

        ``order_type=1`` for child media within a bundle (set
        ``account_media_bundle_id`` to the parent bundle's id).
        ``order_type=2`` for the bundle (or stand-alone item) itself.
        """
        order: dict[str, Any] = {
            "orderId": order_id or str(snowflake_id()),
            "accountMediaId": account_media_id,
            "correlationAccountId": creator_id,
            "accountId": buyer_id,
            "type": order_type,
        }
        if account_media_bundle_id is not None:
            order["accountMediaBundleId"] = account_media_bundle_id
        self.push_service_event(
            service_id=2,
            inner={"type": 7, "order": order},
        )

    def push_ppv_order_complete(
        self,
        *,
        creator_id: str,
        buyer_id: str,
        account_media_id: str,
        order_type: int = 2,
    ) -> None:
        """Queue a ``(2, 8)`` PPV order-completion event."""
        self.push_service_event(
            service_id=2,
            inner={
                "type": 8,
                "order": {
                    "accountMediaId": account_media_id,
                    "correlationAccountId": creator_id,
                    "accountId": buyer_id,
                    "type": order_type,
                },
            },
        )

    # ── FollowerService (svc=3) ──────────────────────────────────────

    def push_follow(
        self, *, follower_id: str, creator_id: str, follow_id: str | None = None
    ) -> None:
        """Queue a ``(3, 2)`` follow event."""
        self.push_service_event(
            service_id=3,
            inner={
                "type": 2,
                "follow": {
                    "followerId": follower_id,
                    "accountId": creator_id,
                    "id": follow_id or str(snowflake_id()),
                },
            },
        )

    def push_unfollow(
        self,
        *,
        follower_id: str,
        creator_id: str,
        follow_id: str,
        created_at: int | None = None,
    ) -> None:
        """Queue a ``(3, 3)`` unfollow event.

        ``follow_id`` MUST match the matching ``(3, 2)`` event's id
        (lifecycle id-reuse, per the protocol convention).
        """
        self.push_service_event(
            service_id=3,
            inner={
                "type": 3,
                "follow": {
                    "followerId": follower_id,
                    "accountId": creator_id,
                    "id": follow_id,
                    "createdAt": created_at or (snowflake_id() % 10**13),
                },
            },
        )

    # ── GroupService (svc=4) ─────────────────────────────────────────

    def push_message_ack(
        self,
        *,
        group_id: str,
        message_ids: list[str],
        user_id: str,
        ack_type: int,
        recipients: list[dict[str, Any]] | None = None,
        user_read_receipts_enabled: bool = True,
    ) -> None:
        """Queue a ``(4, 2)`` ackCommand event.

        ``ack_type``: 1=Delivered, 2=Read, 3=observed-rare. ``user_id``
        is the ACK-emitter (i.e., receiver of the messages — see doc).
        """
        self.push_service_event(
            service_id=4,
            inner={
                "type": 2,
                "ackCommand": {
                    "groupId": group_id,
                    "messageIds": message_ids,
                    "userId": user_id,
                    "userReadReceiptsEnabled": user_read_receipts_enabled,
                    "type": ack_type,
                    "recipients": recipients or [],
                },
            },
        )

    # ── MessageService (svc=5) ───────────────────────────────────────

    def push_new_message(
        self,
        *,
        group_id: str,
        sender_id: str,
        content: str,
        message_id: str | None = None,
        attachments: list[dict[str, Any]] | None = None,
        in_reply_to: str = "",
        recipient_id: str | None = None,
        created_at: float | None = None,
    ) -> None:
        """Queue a ``(5, 1)`` new-message event."""
        msg_id = message_id or str(snowflake_id())
        msg: dict[str, Any] = {
            "type": 1,
            "content": content,
            "attachments": attachments or [],
            "groupId": group_id,
            "senderId": sender_id,
            "inReplyTo": in_reply_to,
            "interactions": [
                {
                    "groupId": group_id,
                    "userId": recipient_id or self._session.account_id,
                    "readAt": 0,
                    "deliveredAt": 0,
                    "messageId": msg_id,
                },
            ],
            "id": msg_id,
            "createdAt": created_at or (snowflake_id() % 10**10) + 0.001,
            "embeds": [],
        }
        self.push_service_event(
            service_id=5,
            inner={"type": 1, "message": msg},
        )

    def push_message_deleted(
        self,
        *,
        group_id: str,
        sender_id: str,
        message_id: str,
        deleted_at: int,
        content: str = "",
        in_reply_to: str = "",
        created_at: int | None = None,
    ) -> None:
        """Queue a ``(5, 10)`` message-deletion event.

        The deletion event carries the full message body — pass
        ``content`` and ``in_reply_to`` to populate them if a test
        cares about reconstructing the original message from the
        deletion event.
        """
        self.push_service_event(
            service_id=5,
            inner={
                "type": 10,
                "message": {
                    "id": message_id,
                    "type": 1,
                    "dataVersion": 1,
                    "content": content,
                    "groupId": group_id,
                    "senderId": sender_id,
                    "correlationId": "0",
                    "inReplyTo": in_reply_to,
                    "inReplyToRoot": in_reply_to,
                    "createdAt": created_at or (deleted_at - 60),
                    "attachments": [],
                    "embeds": [],
                    "interactions": [],
                    "likes": [],
                    "deletedAt": deleted_at,
                },
            },
        )

    def push_typing_announce(
        self, *, group_id: str, account_id: str, last_announce: int | None = None
    ) -> None:
        """Queue a ``(5, 22)`` typing-announce event.

        Production silences these at the WS layer via
        ``SILENT_SERVICE_EVENTS``. Useful for testing that silencing.
        """
        self.push_service_event(
            service_id=5,
            inner={
                "type": 22,
                "typingAnnounceEvent": {
                    "accountId": account_id,
                    "groupId": group_id,
                    "lastAnnounce": last_announce or (snowflake_id() % 10**13),
                },
            },
        )

    # ── WalletService (svc=6) ────────────────────────────────────────

    def push_wallet_update(
        self,
        *,
        account_id: str,
        balance: int,
        balance64: int,
        wallet_id: str | None = None,
        wallet_version: int = 1,
        updated_at: int | None = None,
    ) -> None:
        """Queue a ``(6, 2)`` wallet-update event.

        ``balance64 - balance`` is the in-flight pending debit amount
        (verified empirically). For a settled wallet, set them equal.
        """
        self.push_service_event(
            service_id=6,
            inner={
                "type": 2,
                "wallet": {
                    "id": wallet_id or str(snowflake_id()),
                    "balance": balance,
                    "balance64": balance64,
                    "accountId": account_id,
                    "type": 1,
                    "flags": 0,
                    "walletVersion": wallet_version,
                    "updatedAt": updated_at or (snowflake_id() % 10**13),
                },
            },
        )

    def push_wallet_transaction(
        self,
        *,
        amount: int,
        correlation_id: str,
        origin_wallet_id: str,
        transaction_id: str | None = None,
        transaction_type: int = 58000,
        status: int = 2,
        destination_wallet_id: str | None = None,
    ) -> None:
        """Queue a ``(6, 3)`` wallet-transaction event.

        ``transaction_type=58000`` is the creator-content debit code
        (subscription/PPV). ``correlation_id`` joins back to the
        originating business event (e.g., subscription.historyId).
        """
        self.push_service_event(
            service_id=6,
            inner={
                "type": 3,
                "transaction": {
                    "id": transaction_id or str(snowflake_id()),
                    "type": transaction_type,
                    "originWalletId": origin_wallet_id,
                    "destinationWalletId": destination_wallet_id,
                    "status": status,
                    "amount": amount,
                    "correlationId": correlation_id,
                },
            },
        )

    # ── NotificationService (svc=9) ──────────────────────────────────

    def push_notification_created(
        self,
        *,
        account_id: str,
        type_code: int,
        notification_id: str | None = None,
    ) -> None:
        """Queue a ``(9, 1)`` notification-created event.

        ``type_code`` is the compound `serviceId * 1000 + N` form
        (e.g., 15011 for a subscription-promotion notification).
        """
        self.push_service_event(
            service_id=9,
            inner={
                "type": 1,
                "notification": {
                    "id": notification_id or str(snowflake_id()),
                    "accountId": account_id,
                    "type": type_code,
                },
            },
        )

    def push_notification_read_sync(
        self,
        *,
        account_id: str,
        before_and: str,
        notification_class: str = "",
    ) -> None:
        """Queue a ``(9, 2)`` notification read-state-sync event.

        ``before_and``: highwater notification id; all notifications
        with id <= this are now marked read.
        ``notification_class``: filter slot (empty string = all classes).
        """
        self.push_service_event(
            service_id=9,
            inner={
                "type": 2,
                "data": {
                    "accountId": account_id,
                    "type": notification_class,
                    "beforeAnd": before_and,
                },
            },
        )

    # ── SubscriptionService (svc=15) ─────────────────────────────────

    def push_subscription_event(
        self,
        *,
        subscription_id: str,
        history_id: str,
        subscriber_id: str,
        creator_id: str,
        status: int,
        price_mills: int,
        version: int = 1,
        billing_cycle: int = 30,
        ends_at: int = 0,
        subscription_streak: int = 0,
        subscription_total_days: int = 0,
    ) -> None:
        """Queue a ``(15, 5)`` subscription event.

        ``status=2`` = pending, ``status=3`` = confirmed. Daemon only
        dispatches FullCreatorDownload on status=3. For a new
        subscription, ``subscription_streak=0`` and
        ``subscription_total_days=0`` on the confirmation; for a
        renewal, both are non-zero.
        """
        self.push_service_event(
            service_id=15,
            inner={
                "type": 5,
                "subscription": {
                    "id": subscription_id,
                    "historyId": history_id,
                    "subscriberId": subscriber_id,
                    "accountId": creator_id,
                    "status": status,
                    "version": version,
                    "billingCycle": billing_cycle,
                    "price": price_mills,
                    "renewPrice": price_mills,
                    "endsAt": ends_at,
                    "subscriptionStreak": subscription_streak,
                    "subscriptionTotalDays": subscription_total_days,
                },
            },
        )

    # ── PollsService (svc=42) ────────────────────────────────────────

    def push_poll_subscribe(
        self,
        *,
        account_id: str,
        poll_id: str,
        subscription_id: str | None = None,
        created_at: int | None = None,
    ) -> None:
        """Queue a ``(42, 20)`` poll-subscribe event."""
        self.push_service_event(
            service_id=42,
            inner={
                "type": 20,
                "pollSubscription": {
                    "accountId": account_id,
                    "pollId": poll_id,
                    "id": subscription_id or str(snowflake_id()),
                    "createdAt": created_at or (snowflake_id() % 10**13),
                },
            },
        )

    def push_poll_vote(
        self,
        *,
        account_id: str,
        poll_id: str,
        option_id: str,
        vote_id: str | None = None,
    ) -> None:
        """Queue a ``(42, 10)`` poll-vote event."""
        self.push_service_event(
            service_id=42,
            inner={
                "type": 10,
                "pollVote": {
                    "accountId": account_id,
                    "pollId": poll_id,
                    "optionId": option_id,
                    "id": vote_id or str(snowflake_id()),
                },
            },
        )

    def push_poll_unsubscribe(
        self,
        *,
        account_id: str,
        poll_id: str,
        subscription_id: str,
    ) -> None:
        """Queue a ``(42, 21)`` poll-unsubscribe event.

        ``subscription_id`` MUST match the matching ``(42, 20)`` event's
        id (lifecycle id-reuse, per the protocol convention).
        """
        self.push_service_event(
            service_id=42,
            inner={
                "type": 21,
                "pollSubscription": {
                    "id": subscription_id,
                    "accountId": account_id,
                    "pollId": poll_id,
                },
            },
        )

    # ---- assertion helpers -------------------------------------------

    async def wait_for_auth(self, timeout: float = 2.0) -> dict[str, Any]:  # noqa: ASYNC109 - public test helper; timeout kwarg is idiomatic
        """Block until the server has received a ``t=1`` auth frame.

        Returns the auth frame. Raises ``asyncio.TimeoutError`` if no
        auth message arrives within ``timeout`` seconds.
        """
        await asyncio.wait_for(self.auth_event.wait(), timeout)
        for frame in reversed(self.received):
            if frame.get("t") == T_SESSION:
                return frame
        raise AssertionError("auth_event was set but no t=1 frame in received")

    def frames_of_type(self, t: int) -> list[dict[str, Any]]:
        """All received frames matching ``t``, in arrival order."""
        return [f for f in self.received if f.get("t") == t]

    def last_received(self) -> dict[str, Any] | None:
        return self.received[-1] if self.received else None

    # ---- failure-injection helpers -----------------------------------

    async def force_close_connections(self) -> None:
        """Forcibly close all currently-open connections.

        Use to simulate mid-test server-initiated disconnect (e.g.,
        Fansly closes the WS because of a network event). The client
        sees the close as ``ConnectionClosedOK`` on its next ``recv()``.
        """
        for conn in list(self.connections):
            with contextlib.suppress(Exception):
                await conn.close()

    # ---- internal connection handler ---------------------------------

    async def _handle(self, connection: ServerConnection) -> None:
        if self.close_on_connect:
            # Reject before any framing — client's first recv() sees the
            # clean close. Used to test ``ConnectionClosedOK`` handling.
            await connection.close()
            return
        self.connections.append(connection)
        pusher = asyncio.create_task(self._pump_outbound(connection))
        self._pusher_tasks.append(pusher)
        try:
            async for raw in connection:
                text = raw.decode("utf-8") if isinstance(raw, bytes) else raw
                self.received_raw.append(text)
                try:
                    frame = json.loads(text)
                except json.JSONDecodeError:
                    # Production client logs and drops bad JSON; mirror —
                    # but raw text is already captured above.
                    continue
                self.received.append(frame)
                t = frame.get("t")
                if t == T_SESSION:
                    self.auth_event.set()
                    if self.auto_ack:
                        self.push_session_response()
                elif t == T_PING and self.auto_pong:
                    # Bare pong — same wire shape the client expects in
                    # ``_handle_message`` for MSG_PING (no ``d`` field).
                    self.push_raw(json.dumps({"t": T_PING}))
        finally:
            pusher.cancel()
            if connection in self.connections:
                self.connections.remove(connection)

    async def _pump_outbound(self, connection: ServerConnection) -> None:
        """Drain the outbound queue into one specific connection.

        Each connection gets its own pumper so the queue is consumed
        fairly when more than one client is connected (reconnect/handover
        tests). On send failure the frame is re-queued so the next
        connection gets it instead.
        """
        try:
            while True:
                payload = await self._outbound.get()
                try:
                    await connection.send(payload)
                except Exception:
                    self._outbound.put_nowait(payload)
                    return
        except asyncio.CancelledError:
            return


def dump_ws_server_state(
    ws_server: FakeFanslyWSServer,
    *,
    daemon_task: asyncio.Task | None = None,
    captured_logs: list[str] | None = None,
    tail_logs: int = 60,
) -> None:
    """Log the scripted-responder server's wire state for debugging.

    Counterpart to ``dump_fansly_calls`` (which dumps respx HTTP routes).
    Call this from a test's failure path to surface what the WS subprocess
    actually did: did a connection land, what frames arrived, did the
    daemon_task crash with an exception, what were the recent log lines.

    Args:
        ws_server: The fixture under inspection.
        daemon_task: Optional ``asyncio.Task`` running the daemon. When
            provided, ``done()`` / ``exception()`` are dumped so a silent
            crash inside the daemon (e.g., kwarg mismatch, circular
            import in spawn subprocess) surfaces immediately.
        captured_logs: Optional list previously populated by a loguru
            ``logger.add(lambda m: captured_logs.append(str(m)), ...)``
            sink — the last ``tail_logs`` entries are dumped.
        tail_logs: How many recent log lines to dump (default 60).
    """
    lines: list[str] = [
        f"ws_server.base_url: {ws_server.base_url}",
        f"ws_server.connections: {len(ws_server.connections)}",
        f"ws_server.received ({len(ws_server.received)} frames):",
    ]
    lines.extend(f"    {f}" for f in ws_server.received)
    if daemon_task is not None:
        lines.append(
            f"daemon_task.done()={daemon_task.done()} "
            f"cancelled={daemon_task.cancelled()}"
        )
        if daemon_task.done():
            try:
                exc = daemon_task.exception()
                lines.append(f"daemon_task.exception(): {exc!r}")
            except (asyncio.CancelledError, asyncio.InvalidStateError) as e:
                lines.append(f"daemon_task.exception() raised: {e!r}")
    if captured_logs:
        shown = min(tail_logs, len(captured_logs))
        lines.append(f"captured logs (last {shown} of {len(captured_logs)} lines):")
        lines.extend(line.rstrip() for line in captured_logs[-tail_logs:])
    logger.info("\n--- ws_server dump ---\n" + "\n".join(lines))


def make_child_ws_for(
    server_url: str,
    *,
    token: str = "test-token",  # noqa: S107
    user_agent: str = "test-ua",
    cookies: dict[str, str] | None = None,
    enable_logging: bool = False,
    on_unauthorized: Any = None,
    on_rate_limited: Any = None,
) -> Any:
    """Construct an ``_ChildWebSocket`` aimed at ``server_url`` directly.

    Sibling to ``make_ws_factory_for`` — that one returns a factory
    callable producing the public ``FanslyWebSocket`` wrapper (spawn
    subprocess), for use with ``run_daemon(ws_factory=...)``. This one
    returns a bare ``_ChildWebSocket`` instance (no subprocess, runs in
    the test's asyncio loop), for unit tests that exercise the WS
    protocol layer directly without the wrapper.
    """
    from api.websocket import _ChildWebSocket

    return _ChildWebSocket(
        token=token,
        user_agent=user_agent,
        cookies=cookies if cookies is not None else {"sess": "abc"},
        enable_logging=enable_logging,
        on_unauthorized=on_unauthorized,
        on_rate_limited=on_rate_limited,
        base_url=server_url,
    )


def make_ws_factory_for(server_url: str, *, enable_logging: bool = False) -> Any:
    """Return a ``ws_factory`` callable producing a ``FanslyWebSocket``
    aimed at ``server_url`` (typically ``ws_server.base_url``).

    Counterpart to ``make_fake_ws_factory`` — that one returns a stub for
    tests that don't care about transport. This one returns a factory that
    builds the production class against the in-process scripted-responder
    server (the WS-protocol equivalent of respx — real TCP, real
    triple-JSON encoding, but scripted server-side payloads). The test
    exercises the real connect / authenticate / decode / dispatch path
    instead of bypassing it with synthetic handler calls.

    The scripted responder's ``auto_pong`` echoes the bare ``{"t": 2}``
    wire shape, which matches what ``_ChildWebSocket._handle_message``
    looks for to update ``_last_ping_response`` — so the ping watchdog
    stays satisfied during the test even though we're not connected to
    real Fansly.

    Set ``enable_logging=True`` to forward subprocess DEBUG/TRACE logs back
    through the parent's loguru sink (via ``_reemit_child_log``). Useful
    for debugging subprocess connect / auth issues during test conversion.
    """
    from api.websocket import FanslyWebSocket

    def _factory(config: Any) -> Any:
        return FanslyWebSocket(
            token=config.token or "test-token",
            user_agent=config.user_agent or "test-ua",
            base_url=server_url,
            enable_logging=enable_logging,
        )

    return _factory


@pytest_asyncio.fixture
async def ws_server() -> AsyncIterator[FakeFanslyWSServer]:
    """Start a ``FakeFanslyWSServer`` on ``127.0.0.1:<ephemeral>`` per-test."""
    state = FakeFanslyWSServer()

    def process_response(connection, request, response):  # type: ignore[no-untyped-def]
        # Capture the upgrade request headers for later assertion.
        state.last_request_headers = dict(request.headers.raw_items())
        # Inject Set-Cookie lines the test asked for. Repeated assignment
        # to the same multi-mapping key produces multiple Set-Cookie
        # response headers, matching real-world cookie writes.
        for cookie_line in state.upgrade_set_cookies:
            response.headers["Set-Cookie"] = cookie_line
        return response

    async with serve(
        state._handle,
        _BIND_HOST,
        0,
        process_response=process_response,
        # Fansly's WS does not negotiate a subprotocol.
        subprotocols=None,
    ) as server:
        sock = next(iter(server.sockets))
        state.port = sock.getsockname()[1]
        state.base_url = f"ws://{_BIND_HOST}:{state.port}"
        try:
            yield state
        finally:
            for task in state._pusher_tasks:
                task.cancel()
            await asyncio.gather(*state._pusher_tasks, return_exceptions=True)
