"""Fansly WebSocket client for anti-detection and real-time updates.

This module implements a persistent WebSocket connection to wss://wsv3.fansly.com
for anti-detection purposes. Real browser sessions maintain an active WebSocket
connection for real-time notifications and session management.

The client maintains a background connection while the main session performs
downloads and other operations, mimicking real browser behavior.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import ssl
import threading
from collections.abc import Callable
from http.cookies import SimpleCookie
from types import TracebackType
from typing import TYPE_CHECKING, Any, ClassVar

from websockets import client as ws_client
from websockets.exceptions import WebSocketException

from config.logging import websocket_logger as logger
from helpers.timer import timing_jitter


if TYPE_CHECKING:
    import httpx


class FanslyWebSocket:
    """Fansly WebSocket client for maintaining persistent connection.

    This client maintains a WebSocket connection to wss://wsv3.fansly.com
    for anti-detection purposes and real-time event processing. It handles
    authentication, ping/pong, and event notifications.

    Attributes:
        token: Fansly authentication token
        user_agent: User agent string for the connection
        websocket: Active WebSocket connection instance
        connected: Connection status flag
        session_id: Active session ID (obtained from initial handshake)
        _ws_thread: Dedicated thread running the WS event loop
        _ws_loop: The asyncio loop owned by the WS thread
        _main_loop: Caller-provided loop where event handlers run
        _stop_event: threading.Event signalling shutdown across threads
        _event_handlers: Dictionary of event type handlers
    """

    WEBSOCKET_URL = "wss://wsv3.fansly.com"
    WEBSOCKET_VERSION = 3
    PING_INTERVAL_MIN = 20.0  # Minimum ping interval (seconds)
    PING_INTERVAL_MAX = 25.0  # Maximum ping interval (seconds)

    # Protocol message types (from main.js EventService.handleText)
    MSG_ERROR = 0  # ErrorEvent — server error with code (401, 429, etc.)
    MSG_SESSION = 1  # SessionVerifyRequest (client) / SessionVerifiedEvent (server)
    MSG_PING = 2  # PingResponseEvent — response to "p" ping
    MSG_SERVICE_EVENT = 10000  # ServiceEvent — real-time notifications
    MSG_BATCH = 10001  # Batch — array of messages, recursively unpacked
    MSG_CHAT_ROOM = 46001  # Chat room join (chatws only)

    # Known service IDs within ServiceEvent (from observed traffic)
    SVC_POST = 1  # Post interactions (likes, etc.)
    SVC_MEDIA = 2  # Media/content interactions (likes, etc.)
    SVC_FOLLOWS = 3  # Follow/unfollow events
    SVC_MESSAGING = 4  # Message delivery and acknowledgments
    SVC_MSG_INTERACT = 5  # Message interactions (new messages, likes)
    SVC_WALLET = 6  # Wallet balance updates and transactions
    SVC_NOTIFICATIONS = 9  # Notification events (created, read)
    SVC_SUBSCRIPTIONS = 15  # Subscription lifecycle (created → confirmed)
    SVC_PAYMENTS = 16  # External payment processing (card charges, 3DS)
    SVC_POLLS = 42  # Poll viewport subscriptions (auto sub/unsub on scroll)
    SVC_CHAT = 46  # Livestream chat room messages

    # Notification type codes (serviceId * 1000 + N).
    # ClassVar so ruff RUF012 doesn't flag the mutable-default warning —
    # this is shared class-level lookup data, not per-instance state.
    NOTIFICATION_TYPES: ClassVar[dict[int, str]] = {
        1002: "Post Like",
        1003: "Post Reply",
        1004: "Post Reply",
        1005: "Post Quote",
        2002: "Media Like",
        2007: "Media Purchase",
        2008: "Bundle Purchase",
        3002: "New Follower",
        3003: "Unfollowed",
        5003: "Message Reaction",
        7001: "Tip",
        15006: "New Subscriber",
        15007: "Sub Expired",
        15011: "Promotion",
        15016: "New Subscriber",
        32007: "Locked Text Purchase",
        45012: "Stream Ticket Purchase",
    }

    def __init__(
        self,
        token: str,
        user_agent: str,
        cookies: dict[str, str] | None = None,
        enable_logging: bool = False,
        on_unauthorized: Callable[[], Any] | None = None,
        on_rate_limited: Callable[[], Any] | None = None,
        monitor_events: bool = False,
        base_url: str | None = None,
        http_client: httpx.Client | None = None,
    ) -> None:
        """Initialize Fansly WebSocket client.

        Args:
            token: Fansly authentication token
            user_agent: User agent string to use for the connection
            cookies: Optional cookies dict to send with connection. Used as
                the backing store when ``http_client`` is not provided
                (test path). When ``http_client`` IS provided, this dict
                is ignored — cookies are read live from the client's jar
                on every connect/reconnect (HTTP → WS direction).
            enable_logging: Enable detailed debug logging (default: False)
            on_unauthorized: Callback function to call on 401 error (logout)
            on_rate_limited: Callback function to call on 429 error (rate limit)
            monitor_events: Log all received events for protocol discovery (default: False)
            base_url: WebSocket server URL (default: wss://wsv3.fansly.com)
            http_client: Optional shared ``httpx.Client`` whose cookie jar
                is used bidirectionally. On connect/reconnect the
                Cookie header is rebuilt from the jar (HTTP → WS
                direction). Incoming Set-Cookie headers on the
                WebSocket upgrade response are written back into the
                same jar (WS → HTTP direction), so the API and WS stay
                in sync as Fansly rotates session/check-key cookies.
                Pass ``None`` in tests to use the static ``cookies`` dict.
        """
        self.token = token
        self.user_agent = user_agent
        self.http_client = http_client
        # Frozen dict path — only consulted when http_client is None.
        # When http_client is set, _current_cookies() reads fresh from
        # the jar on every call, so this value becomes unused.
        self.cookies = cookies or {}
        self.enable_logging = enable_logging
        self.on_unauthorized = on_unauthorized
        self.on_rate_limited = on_rate_limited
        self.monitor_events = monitor_events
        self.base_url = base_url or self.WEBSOCKET_URL
        self.connected = False
        self.session_id: str | None = None
        self.websocket_session_id: str | None = None
        self.account_id: str | None = None
        self.websocket = None
        self._ping_task: asyncio.Task | None = None
        # threading.Event (not asyncio.Event) so it can be set from the main
        # thread and observed from the WS thread without cross-loop plumbing.
        # All current uses are .is_set()/.set()/.clear() — no .wait().
        self._stop_event = threading.Event()
        self._event_handlers: dict[int, Callable[[dict[str, Any]], Any]] = {}
        self._reconnect_attempts = 0
        self._max_reconnect_attempts = 5
        self._reconnect_delay = 1.5  # JS: reconnect_timeout_ = 1500
        self._max_reconnect_delay = 15.0  # JS: caps at 15000ms
        self._last_ping_response = 0.0  # JS: lastPingResponse_
        self._last_connection_reset = 0.0  # JS: lastConnectionReset_

        # Thread infrastructure: WS owns its own event loop so the spec-
        # mandated 1.2*pingInterval timeout (api/websocket.py ping_worker)
        # is insulated from main-loop drift. Inbound service events are
        # marshaled back to ``_main_loop`` for handler execution.
        self._ws_thread: threading.Thread | None = None
        self._ws_loop: asyncio.AbstractEventLoop | None = None
        self._main_loop: asyncio.AbstractEventLoop | None = None
        self._thread_ready = threading.Event()
        self._thread_exc: BaseException | None = None

    def _create_ssl_context(self) -> ssl.SSLContext:
        """Create SSL context for WebSocket connection.

        Returns:
            Configured SSL context
        """
        ssl_context = ssl.SSLContext()
        ssl_context.verify_mode = ssl.CERT_NONE
        ssl_context.check_hostname = False
        return ssl_context

    def _create_auth_message(self) -> str:
        """Create authentication message for initial handshake.

        Returns:
            JSON string containing authentication message

        Message format (observed from browser):
            {"t": 1, "d": "{\\"token\\": \\"<TOKEN>\\", \\"v\\": 3}"}
        """
        # Fansly WebSocket expects:
        # t=1 is the message type for authentication
        # d contains the JSON-stringified token object with version
        message = {
            "t": 1,
            "d": json.dumps({"token": self.token, "v": self.WEBSOCKET_VERSION}),
        }
        return json.dumps(message)

    def _current_cookies(self) -> dict[str, str]:
        """Return the cookie snapshot to use for the next connect/reconnect.

        When ``http_client`` is set (production path) this reads live from
        the shared httpx jar, so cookie rotations made by HTTP requests
        during the session are reflected in the next WS handshake.
        When ``http_client`` is None (test path) it falls back to the
        frozen ``cookies`` dict passed at construction.

        Returns:
            {cookie_name: cookie_value} for the current connection.
        """
        if self.http_client is None:
            return dict(self.cookies)
        # httpx.Cookies iteration yields Cookie objects via .jar —
        # mirror the pattern api/fansly.py uses to build its snapshot.
        return {c.name: c.value for c in self.http_client.cookies.jar}

    def _create_cookie_header(self) -> str:
        """Create Cookie header from the current cookie snapshot.

        Returns:
            Cookie header string (e.g., "key1=value1; key2=value2")
        """
        current = self._current_cookies()
        if not current:
            return ""
        return "; ".join(f"{k}={v}" for k, v in current.items())

    def _absorb_response_cookies(self, response_headers: Any) -> None:
        """Propagate Set-Cookie headers from the WS upgrade into the HTTP jar.

        websockets' ClientProtocol exposes the HTTP response headers that
        completed the upgrade. If Fansly rotates a cookie as part of the
        upgrade (e.g., refreshing a session-binding cookie), we push
        those values back into the shared httpx jar so subsequent HTTP
        requests send the updated values. This is the WS → HTTP leg of
        bidirectional cookie sync.

        No-op when ``http_client`` is None (test path) or when the
        response headers object does not expose Set-Cookie entries.

        Args:
            response_headers: Mapping-like object from the WebSocket
                upgrade response (``websocket.response_headers``).
        """
        if self.http_client is None or response_headers is None:
            return
        # websockets < 12 exposes response_headers as a multi-dict; use
        # get_all if available, else fall back to a single get() lookup.
        get_all = getattr(response_headers, "get_all", None)
        raw_values = (
            get_all("Set-Cookie")
            if callable(get_all)
            else [response_headers.get("Set-Cookie")]
        )
        for raw in raw_values or []:
            if not raw:
                continue
            # SimpleCookie parses a single Set-Cookie header line into
            # morsels we can then push into the httpx jar. This handles
            # the attribute-laden form (path, domain, expires, ...)
            # that websockets won't pre-parse for us.
            parsed = SimpleCookie()
            try:
                parsed.load(raw)
            except Exception as exc:
                logger.debug("WS Set-Cookie parse failed: {} ({})", raw, exc)
                continue
            for name, morsel in parsed.items():
                self.http_client.cookies.set(
                    name,
                    morsel.value,
                    domain=morsel["domain"] or "fansly.com",
                    path=morsel["path"] or "/",
                )
                logger.trace("WS absorbed cookie {}={}", name, morsel.value)

    def register_handler(
        self,
        message_type: int,
        handler: Callable[[dict[str, Any]], Any],
    ) -> None:
        """Register a custom event handler for specific message types.

        Use this to add custom processing for WebSocket events.

        Args:
            message_type: Message type identifier (from Fansly WebSocket protocol)
            handler: Async function to handle the event data

        Example:
            async def handle_notification(data):
                print(f"Notification: {data}")

            client.register_handler(2, handle_notification)
        """
        self._event_handlers[message_type] = handler
        logger.info("Registered custom handler for message type: {}", message_type)

    async def _handle_message(self, message: str | bytes) -> None:
        """Handle incoming WebSocket message.

        Dispatch order matches main.js EventService.handleText:
        0 → ErrorEvent, 1 → SessionVerifiedEvent, 2 → PingResponseEvent,
        10000 → ServiceEvent, 10001 → Batch (recursive unpack).

        Args:
            message: Raw message string or bytes from WebSocket
        """
        try:
            if isinstance(message, bytes):
                message = message.decode("utf-8")

            data = json.loads(message)
            message_type = data.get("t")
            message_data = data.get("d")

            logger.trace(
                "Received WebSocket message - type: {}, data: {}",
                message_type,
                message_data,
            )

            # Event monitor — categorized logging for protocol discovery
            if self.monitor_events:
                self._monitor_event(message_type, message_data)

            # JS: 0 === r → handleErrorEvent(decodeMessage("ErrorEvent", t.d))
            if message_type == self.MSG_ERROR:
                error_data = (
                    json.loads(message_data)
                    if isinstance(message_data, str)
                    else message_data
                )
                await self._handle_error_event(error_data)

            # JS: 1 === r → handleSessionVerifiedEvent
            elif message_type == self.MSG_SESSION:
                await self._handle_auth_response(message_data)

            # JS: 2 === r → handlePingResponseEvent
            elif message_type == self.MSG_PING:
                self._last_ping_response = asyncio.get_event_loop().time()
                logger.trace("Received ping response: {}", message_data)

            # JS: 1e4 === r → handleServiceEvent(decodeMessage("ServiceEvent", t.d))
            elif message_type == self.MSG_SERVICE_EVENT:
                event_data = (
                    json.loads(message_data)
                    if isinstance(message_data, str)
                    else message_data
                )
                if self.MSG_SERVICE_EVENT in self._event_handlers:
                    handler = self._event_handlers[self.MSG_SERVICE_EVENT]
                    # Marshal to main loop so handler-side state (EntityStore,
                    # StashClient, asyncpg pool) stays single-threaded.
                    await self._dispatch_event(handler, event_data)

            # JS: 10001 === r → iterate t.d array, recursively handleText each
            elif message_type == self.MSG_BATCH:
                batch = message_data or []
                for sub_message in batch:
                    await self._handle_message(
                        json.dumps(sub_message)
                        if isinstance(sub_message, dict)
                        else sub_message
                    )

            # Handle other registered message types
            elif message_type in self._event_handlers:
                handler = self._event_handlers[message_type]
                # Marshal to main loop — same rationale as MSG_SERVICE_EVENT.
                await self._dispatch_event(handler, message_data)

            # Unknown message types silently discarded (anti-detection).
            # Logged at DEBUG so enabling the websocket level surfaces them
            # during protocol reverse-engineering without triggering any
            # user-visible behavior change in production.
            else:
                logger.debug(
                    "Received unhandled message type {} (discarded)",
                    message_type,
                )

        except json.JSONDecodeError as e:
            logger.error("Failed to decode WebSocket message: {}", e)
        except Exception as e:
            logger.error("Error handling WebSocket message: {}", e)

    async def _handle_error_event(self, error_data: dict[str, Any]) -> None:
        """Handle error events from WebSocket.

        Args:
            error_data: Dictionary containing error information with 'code' field

        Based on main.js behavior:
            - Code 401: Unauthorized - triggers logout and disconnect
            - Code 429: Rate Limited - triggers adaptive backoff (out-of-band)
        """
        error_code = error_data.get("code")

        if error_code == 401:
            logger.warning("WebSocket received 401 Unauthorized - triggering logout")
            self.connected = False
            self.session_id = None

            # Call the unauthorized callback if provided
            if self.on_unauthorized:
                if asyncio.iscoroutinefunction(self.on_unauthorized):
                    await self.on_unauthorized()
                else:
                    self.on_unauthorized()

            # Disconnect WebSocket
            await self.disconnect()
        elif error_code == 429:
            logger.warning(
                "WebSocket received 429 Rate Limited - triggering out-of-band rate limiter backoff"
            )

            # Call the rate limited callback if provided (triggers rate limiter)
            if self.on_rate_limited:
                if asyncio.iscoroutinefunction(self.on_rate_limited):
                    await self.on_rate_limited()
                else:
                    self.on_rate_limited()
        else:
            logger.warning("WebSocket received error code: {}", error_code)

    async def _handle_auth_response(self, data: str) -> None:
        """Handle authentication response from WebSocket.

        Args:
            data: JSON string containing session data

        Expected response format (from browser):
            {
                "session": {
                    "id": "721574688668528640",
                    "token": "...",
                    "accountId": "720167541418237953",
                    "deviceId": null,
                    "status": 2,
                    "websocketSessionId": "838561520299290624",
                    ...
                }
            }
        """
        try:
            response_data = json.loads(data)
            session_info = response_data.get("session", {})

            self.session_id = session_info.get("id")
            self.websocket_session_id = session_info.get("websocketSessionId")
            self.account_id = session_info.get("accountId")

            if self.session_id:
                logger.info(
                    "WebSocket authenticated - session: {}, ws_session: {}, account: {}",
                    self.session_id,
                    self.websocket_session_id,
                    self.account_id,
                )
            else:
                logger.warning("Authentication response missing session ID")

        except json.JSONDecodeError as e:
            logger.error("Failed to decode auth response: {}", e)

    # region Event Monitor

    def _monitor_event(self, message_type: int, message_data: Any) -> None:
        """Log received WebSocket events for protocol discovery.

        Categorizes known event types with structured output.
        Dumps unknown types as decoded JSON for analysis.
        Skips ping (type 2) and messaging ACK (serviceId 4) events.
        """
        if message_type == self.MSG_PING:
            return

        if message_type == self.MSG_ERROR:
            error = (
                json.loads(message_data)
                if isinstance(message_data, str)
                else (message_data or {})
            )
            logger.info("[WS Monitor] Error | code={}", error.get("code"))
            return

        if message_type == self.MSG_SESSION:
            session = (
                json.loads(message_data)
                if isinstance(message_data, str)
                else (message_data or {})
            )
            sess = session.get("session", {})
            logger.info(
                "[WS Monitor] Session | id={} wsId={} status={}",
                sess.get("id"),
                sess.get("websocketSessionId"),
                sess.get("status"),
            )
            return

        if message_type == self.MSG_SERVICE_EVENT:
            self._monitor_service_event(message_data)
            return

        if message_type == self.MSG_BATCH:
            batch = message_data if isinstance(message_data, list) else []
            logger.debug("[WS Monitor] Batch of {} events", len(batch))
            return

        if message_type == self.MSG_CHAT_ROOM:
            room = (
                json.loads(message_data)
                if isinstance(message_data, str)
                else (message_data or {})
            )
            logger.info("[WS Monitor] Chat Room Join | room={}", room.get("chatRoomId"))
            return

        # Unknown top-level message type — full dump
        logger.info(
            "[WS Monitor] Unknown t={}\n{}",
            message_type,
            json.dumps(message_data, indent=2) if message_data else "(empty)",
        )

    def _monitor_service_event(self, message_data: Any) -> None:
        """Decode and categorize a ServiceEvent (type 10000).

        Fully decodes the triple-JSON nesting
        (envelope -> serviceId/event -> payload).
        Known services get structured single-line output.
        Unknown services dump the decoded payload as indented JSON.
        """
        try:
            envelope = (
                json.loads(message_data)
                if isinstance(message_data, str)
                else (message_data or {})
            )
        except json.JSONDecodeError:
            logger.warning("[WS Monitor] ServiceEvent decode error: {}", message_data)
            return

        service_id = envelope.get("serviceId")
        raw_event = envelope.get("event")

        try:
            event = (
                json.loads(raw_event)
                if isinstance(raw_event, str)
                else (raw_event or {})
            )
        except json.JSONDecodeError:
            logger.warning(
                "[WS Monitor] ServiceEvent inner decode error | svc={}: {}",
                service_id,
                raw_event,
            )
            return

        event_type = event.get("type")

        # Skip messaging ACK/read-receipt events (serviceId=4)
        if service_id == self.SVC_MESSAGING:
            return

        # Notifications — noise, only log at debug
        if service_id == self.SVC_NOTIFICATIONS:
            if "notification" in event:
                notif = event["notification"]
                ntype = notif.get("type")
                nlabel = self.NOTIFICATION_TYPES.get(ntype, f"unknown({ntype})")
                logger.debug(
                    "[WS Monitor] Notification | {} corr={} | id={}",
                    nlabel,
                    notif.get("correlationId"),
                    notif.get("id"),
                )
            elif "data" in event:
                logger.debug(
                    "[WS Monitor] Notification Read | beforeAnd={}",
                    event["data"].get("beforeAnd"),
                )
            return

        # Poll viewport subs are noise — only log at debug
        if service_id == self.SVC_POLLS:
            ps = event.get("pollSubscription", {})
            action = "Sub" if event_type == 20 else "Unsub"
            logger.debug(
                "[WS Monitor] Poll {} | poll={} | id={}",
                action,
                ps.get("pollId"),
                ps.get("id"),
            )
            return

        # Dispatch to known service handlers
        handlers = {
            self.SVC_POST: lambda: self._monitor_post_event(event_type, event),
            self.SVC_MEDIA: lambda: self._monitor_media_event(event_type, event),
            self.SVC_FOLLOWS: lambda: self._monitor_follow_event(event),
            self.SVC_MSG_INTERACT: lambda: self._monitor_message_event(
                event_type, event
            ),
            self.SVC_WALLET: lambda: self._monitor_wallet_event(event),
            self.SVC_SUBSCRIPTIONS: lambda: self._monitor_subscription_event(event),
            self.SVC_PAYMENTS: lambda: self._monitor_payment_event(event),
            self.SVC_CHAT: lambda: self._monitor_chat_event(event),
        }

        handler = handlers.get(service_id)
        if handler:
            handler()
        else:
            # Unknown service — full dump for discovery
            logger.info(
                "[WS Monitor] serviceId={} type={}\n{}",
                service_id,
                event_type,
                json.dumps(event, indent=2),
            )

    def _monitor_post_event(self, event_type: int, event: dict) -> None:
        """Categorize post service events (serviceId=1)."""
        if "like" in event:
            like = event["like"]
            logger.info(
                "[WS Monitor] Post Like | post={} account={} | id={}",
                like.get("postId"),
                like.get("accountId"),
                like.get("id"),
            )
            return

        # Unknown post event — dump for discovery
        logger.info(
            "[WS Monitor] Post svc=1 type={}\n{}",
            event_type,
            json.dumps(event, indent=2),
        )

    def _monitor_media_event(self, event_type: int, event: dict) -> None:
        """Categorize media service events (serviceId=2)."""
        if "like" in event:
            like = event["like"]
            logger.info(
                "[WS Monitor] Media Like | media={} bundle={} access={} | id={} at={}",
                like.get("accountMediaId"),
                like.get("accountMediaBundleId"),
                like.get("accountMediaAccess"),
                like.get("id"),
                like.get("createdAt"),
            )
            return

        if "order" in event:
            order = event["order"]
            logger.info(
                "[WS Monitor] Media Purchase | media={} from={} | order={}",
                order.get("accountMediaId"),
                order.get("correlationAccountId"),
                order.get("orderId"),
            )
            return

        # Unknown media event — dump for discovery
        logger.info(
            "[WS Monitor] Media svc=2 type={}\n{}",
            event_type,
            json.dumps(event, indent=2),
        )

    def _monitor_message_event(self, event_type: int, event: dict) -> None:
        """Categorize message interaction events (serviceId=5)."""
        # Typing indicators — extremely noisy, DEBUG-only so they only
        # surface when the websocket logger is explicitly set to DEBUG.
        if "typingAnnounceEvent" in event:
            ta = event["typingAnnounceEvent"]
            logger.debug(
                "[WS Monitor] Typing | account={} group={}",
                ta.get("accountId"),
                ta.get("groupId"),
            )
            return

        if "message" in event:
            msg = event["message"]
            content = msg.get("content", "")
            preview = (content[:60] + "...") if len(content) > 60 else content
            attachments = msg.get("attachments", [])
            logger.info(
                "[WS Monitor] New Message | from={} group={} attachments={} | id={}"
                ' content="{}"',
                msg.get("senderId"),
                msg.get("groupId"),
                len(attachments),
                msg.get("id"),
                preview,
            )
            return

        if "like" in event:
            like = event["like"]
            logger.info(
                "[WS Monitor] Message Like | msg={} group={} like_type={} | id={}",
                like.get("messageId"),
                like.get("groupId"),
                like.get("type"),
                like.get("id"),
            )
            return

        # Unknown message event — dump for discovery
        logger.info(
            "[WS Monitor] Message svc=5 type={}\n{}",
            event_type,
            json.dumps(event, indent=2),
        )

    def _monitor_follow_event(self, event: dict) -> None:
        """Categorize follow events (serviceId=3)."""
        if "follow" in event:
            follow = event["follow"]
            logger.info(
                "[WS Monitor] Follow | account={} follower={} | id={}",
                follow.get("accountId"),
                follow.get("followerId"),
                follow.get("id"),
            )
            return

        logger.info(
            "[WS Monitor] Follow svc=3\n{}",
            json.dumps(event, indent=2),
        )

    def _monitor_wallet_event(self, event: dict) -> None:
        """Categorize wallet events (serviceId=6)."""
        if "wallet" in event:
            w = event["wallet"]
            logger.info(
                "[WS Monitor] Wallet | balance={} version={} | id={}",
                w.get("balance"),
                w.get("walletVersion"),
                w.get("id"),
            )
            return

        if "transaction" in event:
            tx = event["transaction"]
            logger.info(
                "[WS Monitor] Wallet Tx | type={} amount={} status={} | id={} corr={}",
                tx.get("type"),
                tx.get("amount"),
                tx.get("status"),
                tx.get("id"),
                tx.get("correlationId"),
            )
            return

        logger.info(
            "[WS Monitor] Wallet svc=6\n{}",
            json.dumps(event, indent=2),
        )

    def _monitor_subscription_event(self, event: dict) -> None:
        """Categorize subscription events (serviceId=15)."""
        if "subscription" in event:
            sub = event["subscription"]
            tier = sub.get("subscriptionTierName", "")
            label = f' "{tier}"' if tier else ""
            logger.info(
                "[WS Monitor] Subscription | account={} status={}{} price={} | id={}",
                sub.get("accountId"),
                sub.get("status"),
                label,
                sub.get("price"),
                sub.get("id"),
            )
            return

        logger.info(
            "[WS Monitor] Subscription svc=15\n{}",
            json.dumps(event, indent=2),
        )

    def _monitor_payment_event(self, event: dict) -> None:
        """Categorize payment events (serviceId=16)."""
        if "transaction" in event:
            tx = event["transaction"]
            logger.info(
                "[WS Monitor] Payment | type={} amount={} status={} 3ds={} | id={}",
                tx.get("type"),
                tx.get("amount"),
                tx.get("status"),
                tx.get("threeDSecure"),
                tx.get("id"),
            )
            return

        logger.info(
            "[WS Monitor] Payment svc=16\n{}",
            json.dumps(event, indent=2),
        )

    def _monitor_chat_event(self, event: dict) -> None:
        """Categorize chat room events (serviceId=46, chatws only)."""
        if "chatRoomMessage" in event:
            msg = event["chatRoomMessage"]
            meta = msg.get("metadata", "{}")
            if isinstance(meta, str):
                meta = json.loads(meta) if meta else {}
            creator = meta.get("senderIsCreator", False)
            tag = " [creator]" if creator else ""
            content = msg.get("content", "")
            preview = (content[:60] + "...") if len(content) > 60 else content
            logger.info(
                '[WS Monitor] Chat{} | @{} ({}): "{}" | room={}',
                tag,
                msg.get("username"),
                msg.get("displayname"),
                preview,
                msg.get("chatRoomId"),
            )
            return

        logger.info(
            "[WS Monitor] Chat svc=46\n{}",
            json.dumps(event, indent=2),
        )

    # endregion

    async def connect(self) -> None:
        """Connect to Fansly WebSocket server.

        Establishes WebSocket connection with authentication.
        Connection URL: wss://wsv3.fansly.com/?v=3

        Raises:
            WebSocketException: If connection fails
            RuntimeError: If authentication fails
        """
        if self.connected:
            logger.warning("Already connected to WebSocket")
            return

        # Build connection URL with version parameter
        connection_url = f"{self.base_url}/?v={self.WEBSOCKET_VERSION}"
        logger.info("Connecting to WebSocket: {}", connection_url)

        try:
            ssl_context = self._create_ssl_context()

            # Prepare extra headers (matching browser request)
            extra_headers = {
                "User-Agent": self.user_agent,
                "Origin": "https://fansly.com",
                "Sec-Fetch-Dest": "empty",
                "Sec-Fetch-Mode": "websocket",
                "Sec-Fetch-Site": "same-site",
                "DNT": "1",
                "Sec-GPC": "1",
            }

            # Add cookies if provided
            cookie_header = self._create_cookie_header()
            if cookie_header:
                extra_headers["Cookie"] = cookie_header

            # Connect to WebSocket
            self.websocket = await ws_client.connect(
                uri=connection_url,
                extra_headers=extra_headers,
                ssl=ssl_context,
            )

            self.connected = True
            self._last_ping_response = (
                asyncio.get_event_loop().time()
            )  # JS: lastPingResponse_ = Date.now()

            # WS → HTTP cookie sync: absorb any Set-Cookie headers from
            # the upgrade response back into the shared httpx jar so the
            # API layer sees the latest values on its next HTTP request.
            self._absorb_response_cookies(
                getattr(self.websocket, "response_headers", None)
            )

            logger.info("WebSocket connection established")

            # Send authentication message
            auth_message = self._create_auth_message()
            await self.websocket.send(auth_message)

            # Wait for authentication response
            response = await self.websocket.recv()
            await self._handle_message(response)

            if not self.session_id:
                raise RuntimeError("Failed to authenticate WebSocket connection")

            # Start ping loop
            self._start_ping_loop()

            self._reconnect_attempts = 0  # Reset on successful connection

        except Exception as e:
            self.connected = False
            logger.error("Failed to connect to WebSocket: {}", e)
            raise

    async def disconnect(self) -> None:
        """Disconnect from WebSocket server.

        Gracefully closes the WebSocket connection and stops ping loop.
        """
        if not self.connected or not self.websocket:
            logger.warning("Not connected to WebSocket")
            return

        logger.info("Disconnecting from WebSocket")

        # Stop ping loop
        self._stop_ping_loop()

        try:
            await self.websocket.close()
        except Exception as e:
            logger.error("Error during WebSocket disconnect: {}", e)
        finally:
            self.connected = False
            self.websocket = None
            self.session_id = None
            self.websocket_session_id = None
            self.account_id = None

    def _start_ping_loop(self) -> None:
        """Start the ping loop task.

        Matches JS behavior: sends 'p' every 20-25s (randomized).
        If no ping response within 1.2x the interval, resets the connection
        (JS: lastPingResponse_ > pingTimeout_ → resetWebsocket).
        """
        # Clear stale ref if the previous worker self-exited on timeout.
        if self._ping_task is not None and self._ping_task.done():
            self._ping_task = None
        if self._ping_task is not None:
            logger.warning("Ping loop already running")
            return

        self._last_ping_response = asyncio.get_event_loop().time()

        async def ping_worker() -> None:
            """Worker to send periodic pings with timeout detection."""
            try:
                while self.connected and not self._stop_event.is_set():
                    try:
                        ping_interval = timing_jitter(
                            self.PING_INTERVAL_MIN, self.PING_INTERVAL_MAX
                        )
                        await asyncio.sleep(ping_interval)

                        if not self.connected or not self.websocket:
                            break

                        now = asyncio.get_event_loop().time()
                        ping_timeout = (
                            1.2 * ping_interval
                        )  # JS: pingTimeout_ = 1.2 * pingInterval_

                        # JS: if now - lastPingResponse_ > pingTimeout_ → reset
                        if (
                            now - self._last_ping_response > ping_timeout
                            and now - self._last_connection_reset > 15.0
                        ):
                            logger.warning(
                                "Ping timeout ({:.1f}s since last response), resetting connection",
                                now - self._last_ping_response,
                            )
                            self._last_connection_reset = now
                            self.connected = False
                            break

                        await self.websocket.send("p")

                        logger.trace("Sent ping (next in {:.1f}s)", ping_interval)

                    except WebSocketException as e:
                        logger.error("Error sending ping: {}", e)
                        self.connected = False
                        break
                    except Exception as e:
                        logger.error("Unexpected error in ping loop: {}", e)
                        break

            except asyncio.CancelledError:
                logger.debug("Ping loop cancelled")

        self._ping_task = asyncio.create_task(ping_worker())
        logger.debug("Ping loop started")

    def _stop_ping_loop(self) -> None:
        """Stop the ping loop task."""
        if self._ping_task is None:
            return

        self._ping_task.cancel()
        self._ping_task = None

        logger.debug("Ping loop stopped")

    async def _listen_loop(self) -> None:
        """Listen for incoming WebSocket messages.

        This loop runs continuously while connected, processing
        incoming messages until disconnection or error.
        """
        try:
            while self.connected and self.websocket:
                try:
                    message = await asyncio.wait_for(
                        self.websocket.recv(),
                        timeout=60.0,  # Timeout to allow periodic checks
                    )
                    await self._handle_message(message)
                except TimeoutError:
                    # Timeout is normal - just continue listening
                    logger.debug("WebSocket listen timeout - continuing")
                    continue
                except WebSocketException as e:
                    logger.error("WebSocket error in listen loop: {}", e)
                    self.connected = False
                    break

        except asyncio.CancelledError:
            logger.info("WebSocket listen loop cancelled")
        except Exception as e:
            logger.error("Unexpected error in listen loop: {}", e)
            self.connected = False

    async def _maintain_connection(self) -> None:
        """Maintain WebSocket connection with reconnection logic."""
        while not self._stop_event.is_set():
            try:
                if not self.connected:
                    if self._reconnect_attempts >= self._max_reconnect_attempts:
                        logger.error(
                            "Max reconnection attempts reached ({})",
                            self._max_reconnect_attempts,
                        )
                        break

                    if self._reconnect_attempts > 0:
                        # JS: reconnect_timeout_ *= 2, capped at 15s
                        delay = min(
                            self._reconnect_delay * (2**self._reconnect_attempts),
                            self._max_reconnect_delay,
                        )
                        logger.info("Reconnecting in {:.1f} seconds...", delay)
                        await asyncio.sleep(delay)

                    self._reconnect_attempts += 1
                    await self.connect()

                # Listen for messages
                await self._listen_loop()

                # If we get here, connection was lost
                if not self._stop_event.is_set():
                    logger.warning("WebSocket connection lost, will attempt reconnect")
                    await self.disconnect()

            except asyncio.CancelledError:
                logger.info("Connection maintenance cancelled")
                break
            except Exception as e:
                logger.error("Error in connection maintenance: {}", e)
                await asyncio.sleep(self._reconnect_delay)

    def start_in_thread(
        self,
        main_loop: asyncio.AbstractEventLoop | None = None,
        ready_timeout: float = 5.0,
    ) -> None:
        """Start the WebSocket on a dedicated thread with its own event loop.

        Insulates the spec-mandated ``1.2 * pingInterval`` timeout (see
        ``ping_worker``) from any work on the caller's event loop.  Service
        events received on the WS thread are marshaled back to ``main_loop``
        for handler execution, so ``EntityStore``, ``StashClient``, etc.
        (which expect to live on the main loop) are not touched cross-thread.

        Args:
            main_loop: The asyncio loop where registered event handlers
                should run.  Defaults to the running loop at call time.
            ready_timeout: Seconds to wait for the WS thread to spin up
                its event loop.  Raises ``RuntimeError`` on timeout.

        Raises:
            RuntimeError: If the WS thread fails to start within
                ``ready_timeout`` or if the thread's setup raised.

        Example:
            client = FanslyWebSocket(token, user_agent)
            client.start_in_thread()
            # ... do other work ...
            await client.stop_thread()
        """
        if self._ws_thread is not None and self._ws_thread.is_alive():
            logger.warning("WebSocket thread already running")
            return

        self._main_loop = main_loop or asyncio.get_running_loop()
        self._thread_ready.clear()
        self._thread_exc = None
        self._stop_event.clear()

        logger.info("Starting WebSocket thread")
        self._ws_thread = threading.Thread(
            target=self._thread_main,
            daemon=True,
            name="fansly-ws",
        )
        self._ws_thread.start()

        if not self._thread_ready.wait(timeout=ready_timeout):
            raise RuntimeError(
                f"WebSocket thread failed to initialize within {ready_timeout}s"
            )

        # Brief grace window: the ready event fires once the loop is alive,
        # but ``_maintain_connection`` may crash on its first iteration
        # (programming error, missing dep, etc.). Wait a short moment for
        # such crashes to surface in ``_thread_exc`` before declaring success.
        self._ws_thread.join(timeout=0.1)
        if self._thread_exc is not None:
            raise RuntimeError(
                f"WebSocket thread failed during startup: {self._thread_exc}"
            ) from self._thread_exc

        logger.info("WebSocket thread started")

    def _thread_main(self) -> None:
        """Entry point for the WS thread — owns its own event loop.

        Sets ``_thread_ready`` once the loop is up so ``start_in_thread``
        can return.  Captures any exception into ``_thread_exc`` for the
        starter to re-raise.
        """
        try:
            self._ws_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._ws_loop)
            self._thread_ready.set()
            self._ws_loop.run_until_complete(self._maintain_connection())
        except BaseException as exc:
            self._thread_exc = exc
            logger.error("WebSocket thread crashed: {}", exc)
            # Ensure the starter unblocks even on early crash
            self._thread_ready.set()
        finally:
            try:
                if self._ws_loop is not None and not self._ws_loop.is_closed():
                    self._ws_loop.close()
            except Exception as exc:  # pragma: no cover — defensive: asyncio loop.close() rarely raises
                logger.warning("Error closing WS loop: {}", exc)
            self._ws_loop = None

    async def stop_thread(self, join_timeout: float = 10.0) -> None:
        """Signal the WS thread to stop, then join it.

        Must be called from the main loop (the one passed to
        ``start_in_thread``).  Sets the cross-thread stop event, then
        awaits the thread join via ``asyncio.to_thread`` so the main
        loop is not blocked while waiting.

        Args:
            join_timeout: Seconds to wait for the thread to exit.  An
                exceeded timeout logs an error but does not raise; the
                ``daemon=True`` thread will be cleaned up at process exit.
        """
        if self._ws_thread is None or not self._ws_thread.is_alive():
            logger.warning("No WebSocket thread running")
            self._ws_thread = None
            return

        logger.info("Stopping WebSocket thread")
        self._stop_event.set()

        # Wake the listen loop now. Without this it's blocked in
        # ``asyncio.wait_for(self.websocket.recv(), timeout=60.0)`` and
        # won't observe ``_stop_event`` until that 60s recv timeout fires
        # — well past ``join_timeout``, producing a misleading "orphan
        # thread" warning. Closing the websocket from the WS thread's
        # own loop causes the in-flight ``recv()`` to return immediately,
        # so the listen loop exits, _maintain_connection observes
        # ``_stop_event`` on its next while-check, and the thread joins
        # promptly.
        ws_loop = self._ws_loop
        ws = self.websocket
        if ws_loop is not None and not ws_loop.is_closed() and ws is not None:

            async def _close_ws() -> None:
                with contextlib.suppress(Exception):
                    await ws.close()

            with contextlib.suppress(RuntimeError, Exception):
                asyncio.run_coroutine_threadsafe(_close_ws(), ws_loop)

        await asyncio.to_thread(self._ws_thread.join, join_timeout)
        if self._ws_thread.is_alive():  # pragma: no cover — defensive: requires a hung thread that ignores stop_event for join_timeout (10s default)
            logger.error(
                "WebSocket thread did not exit within {}s — orphan thread",
                join_timeout,
            )

        self._ws_thread = None
        self._stop_event.clear()
        self._reconnect_attempts = 0
        logger.info("WebSocket thread stopped")

    async def _dispatch_event(
        self,
        handler: Callable[[Any], Any],
        event: Any,
    ) -> None:
        """Dispatch a WS event to a handler.

        When the WS runs on its own thread (production: ``start_in_thread``),
        handlers are marshaled to ``_main_loop`` so EntityStore/StashClient
        state stays single-threaded:
          * Async handlers → ``run_coroutine_threadsafe`` (fire-and-forget;
            WS thread never awaits, so a slow handler can't starve ping/pong)
          * Sync handlers → ``call_soon_threadsafe``

        When ``_handle_message`` is called from the same loop already running
        (no separate WS thread, or test harness driving the message dispatch
        directly), fall back to inline invocation — there's no thread
        boundary to cross, and run_coroutine_threadsafe across the same loop
        deadlocks.

        ``event`` is typed as ``Any`` because the message handler dispatch
        covers both ``MSG_SERVICE_EVENT`` (decoded dicts) and arbitrary
        registered message types whose payload shape varies by type.
        """
        # Same-loop fast path: no thread boundary → just invoke directly.
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:  # pragma: no cover — defensive: dispatch is always called from an async context
            current_loop = None

        no_thread_boundary = (
            self._main_loop is None
            or self._main_loop.is_closed()
            or self._main_loop is current_loop
        )
        if no_thread_boundary:
            if asyncio.iscoroutinefunction(handler):
                await handler(event)
            else:
                handler(event)
            return

        # Cross-thread path: marshal to the main loop, fire-and-forget.
        try:
            if asyncio.iscoroutinefunction(handler):
                asyncio.run_coroutine_threadsafe(handler(event), self._main_loop)
            else:
                self._main_loop.call_soon_threadsafe(handler, event)
        except RuntimeError as exc:  # pragma: no cover — defensive: only fires if target loop is closed mid-dispatch
            logger.error("Failed to dispatch WS event to main loop: {}", exc)

    async def send_message(self, message_type: int, data: Any) -> None:
        """Send a message through the WebSocket connection.

        Args:
            message_type: Message type identifier
            data: Message data (will be JSON-stringified)

        Raises:
            RuntimeError: If not connected
        """
        if not self.connected or not self.websocket:
            raise RuntimeError("WebSocket not connected")

        message = {
            "t": message_type,
            "d": json.dumps(data) if not isinstance(data, str) else data,
        }

        await self.websocket.send(json.dumps(message))

        logger.debug("Sent WebSocket message - type: {}", message_type)

    async def __aenter__(self) -> FanslyWebSocket:
        """Async context manager entry — starts the WS thread."""
        self.start_in_thread()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """Async context manager exit — joins the WS thread."""
        await self.stop_thread()
