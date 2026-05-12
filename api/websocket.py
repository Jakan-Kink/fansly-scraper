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
import inspect
import json
import multiprocessing as mp
import queue
import ssl
from collections.abc import Callable
from http.cookies import SimpleCookie
from typing import TYPE_CHECKING, Any

from websockets import client as ws_client
from websockets.exceptions import ConnectionClosedOK, WebSocketException

from api.websocket_protocol import (
    MSG_BATCH,
    MSG_CHAT_ROOM,
    MSG_ERROR,
    MSG_PING,
    MSG_SERVICE_EVENT,
    MSG_SESSION,
)
from config.logging import websocket_logger as logger
from helpers.timer import timing_jitter


if TYPE_CHECKING:
    from multiprocessing.queues import Queue as MpQueue

    import httpx


# Drain-poll interval for the cmd_q/evt_q blocking get() calls. Short
# enough that shutdown observes cancellation/queue-death promptly, long
# enough that the wakeup overhead is negligible (~2 wakes/sec/thread).
_QUEUE_POLL_INTERVAL_S = 0.5


class _ChildWebSocket:
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
        _stop_event: asyncio.Event signalling shutdown to the run loops
        _event_handlers: Dictionary of event type handlers
    """

    WEBSOCKET_URL = "wss://wsv3.fansly.com"
    WEBSOCKET_VERSION = 3
    PING_INTERVAL_MIN = 20.0  # Minimum ping interval (seconds)
    PING_INTERVAL_MAX = 25.0  # Maximum ping interval (seconds)

    def __init__(
        self,
        token: str,
        user_agent: str,
        cookies: dict[str, str] | None = None,
        enable_logging: bool = False,
        on_unauthorized: Callable[[], Any] | None = None,
        on_rate_limited: Callable[[], Any] | None = None,
        base_url: str | None = None,
        http_client: httpx.AsyncClient | httpx.Client | None = None,
        ping_timeout_enabled: bool = True,
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
            base_url: WebSocket server URL (default: wss://wsv3.fansly.com)
            http_client: Optional shared ``httpx.Client`` whose cookie jar
                is used bidirectionally. On connect/reconnect the
                Cookie header is rebuilt from the jar (HTTP → WS
                direction). Incoming Set-Cookie headers on the
                WebSocket upgrade response are written back into the
                same jar (WS → HTTP direction), so the API and WS stay
                in sync as Fansly rotates session/check-key cookies.
                Pass ``None`` in tests to use the static ``cookies`` dict.
            ping_timeout_enabled: When ``True`` (default), the ping loop
                disconnects if no ``t=2`` response arrives within
                1.2x ping_interval.  Set ``False`` for connections that
                do not echo pings back (e.g. the dedicated chat WS).
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
        self.base_url = base_url or self.WEBSOCKET_URL
        self.connected = False
        self.session_id: str | None = None
        self.websocket_session_id: str | None = None
        self.account_id: str | None = None
        self.websocket = None
        self._ping_task: asyncio.Task | None = None
        # Signalled by the subprocess supervisor on shutdown request.
        # _maintain_connection and ping_worker check .is_set() to exit
        # cleanly. Single-loop access — no cross-thread plumbing.
        self._stop_event = asyncio.Event()
        self._event_handlers: dict[int, Callable[[dict[str, Any]], Any]] = {}
        self._reconnect_attempts = 0
        self._max_reconnect_attempts = 5
        self._reconnect_delay = 1.5  # JS: reconnect_timeout_ = 1500
        self._max_reconnect_delay = 15.0  # JS: caps at 15000ms
        self._last_ping_response = 0.0  # JS: lastPingResponse_
        self._last_connection_reset = 0.0  # JS: lastConnectionReset_
        self.ping_timeout_enabled = ping_timeout_enabled

    def _create_ssl_context(self) -> ssl.SSLContext:
        """Create SSL context for the WebSocket connection.

        Uses the standard-library default: TLS-client purpose,
        ``CERT_REQUIRED``, hostname verification on. Fansly serves
        WS endpoints behind globally-trusted third-party certificates,
        so default verification is the correct posture.

        Returns:
            Configured SSL context
        """
        return ssl.create_default_context()

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
        # Snapshot under the jar's internal lock to avoid
        # `RuntimeError: dictionary changed size during iteration`
        # when the async loop mutates cookies concurrently.
        jar = self.http_client.cookies.jar
        try:
            with jar._cookies_lock:  # type: ignore[attr-defined]
                return {c.name: c.value for c in jar}
        except AttributeError:
            return {c.name: c.value for c in list(jar)}

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

            if message_type != MSG_PING:
                # Type at DEBUG, payload at TRACE — payloads can carry DM
                # bodies, accountIds, and other PII that should not land in
                # operator-shareable DEBUG logs.
                logger.debug("Received WebSocket message - type: {}", message_type)
                logger.trace(
                    "WebSocket payload type={} data={}",
                    message_type,
                    message_data,
                )

            # JS: 0 === r → handleErrorEvent(decodeMessage("ErrorEvent", t.d))
            if message_type == MSG_ERROR:
                error_data = (
                    json.loads(message_data)
                    if isinstance(message_data, str)
                    else message_data
                )
                await self._handle_error_event(error_data)

            # JS: 1 === r → handleSessionVerifiedEvent
            elif message_type == MSG_SESSION:
                await self._handle_auth_response(message_data)

            # JS: 2 === r → handlePingResponseEvent
            elif message_type == MSG_PING:
                self._last_ping_response = asyncio.get_event_loop().time()
                logger.trace("Received ping response: {}", message_data)

            # JS: 1e4 === r → handleServiceEvent(decodeMessage("ServiceEvent", t.d))
            elif message_type == MSG_SERVICE_EVENT:
                event_data = (
                    json.loads(message_data)
                    if isinstance(message_data, str)
                    else message_data
                )
                if MSG_SERVICE_EVENT in self._event_handlers:
                    handler = self._event_handlers[MSG_SERVICE_EVENT]
                    # Marshal to main loop so handler-side state (EntityStore,
                    # StashClient, asyncpg pool) stays single-threaded.
                    await self._dispatch_event(handler, event_data)

            # JS: 10001 === r → iterate t.d array, recursively handleText each
            elif message_type == MSG_BATCH:
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
                if inspect.iscoroutinefunction(self.on_unauthorized):
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
                if inspect.iscoroutinefunction(self.on_rate_limited):
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
                            self.ping_timeout_enabled
                            and now - self._last_ping_response > ping_timeout
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
                    if isinstance(e, ConnectionClosedOK):
                        logger.debug("WebSocket closed cleanly in listen loop: {}", e)
                    else:
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

    async def _dispatch_event(
        self,
        handler: Callable[[Any], Any],
        event: Any,
    ) -> None:
        """Invoke a registered handler with the decoded event payload.

        Runs on the subprocess's single asyncio loop. ``event`` is typed
        as ``Any`` because dispatch covers ``MSG_SERVICE_EVENT`` (decoded
        dicts) and arbitrary registered message types whose payload
        shape varies by type.
        """
        if inspect.iscoroutinefunction(handler):
            await handler(event)
        else:
            handler(event)

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

    async def join_chat_room(self, chat_room_id: int | str) -> None:
        """Join a livestream chat room (sends MSG_CHAT_ROOM / t=46001).

        Subscribes to real-time chat events for *chat_room_id*.  Must be
        called after a successful ``connect()``.

        Args:
            chat_room_id: The chat room ID to join (int or str).
        """
        await self.send_message(
            MSG_CHAT_ROOM,
            {"chatRoomId": str(chat_room_id)},
        )
        logger.debug("Joined chat room: {}", chat_room_id)


# ---------------------------------------------------------------------------
# Child-process entry point — must be a top-level function so the spawn
# start method (macOS / Python 3.8+ default) can pickle ``target=``.
# ---------------------------------------------------------------------------


def _setup_child_logging(evt_q: MpQueue) -> None:
    """Forward all child loguru records to the parent over ``evt_q``.

    Pushes ``{"kind": "log", level, name, function, line, message}`` per
    record. The parent's ``_drain_evt_q`` re-emits each record through its
    own configured loguru sinks so child output lands in the same files
    operators already tail — no separate child log file.
    """

    def _sink(message: Any) -> None:
        rec = message.record
        with contextlib.suppress(BrokenPipeError, OSError):
            evt_q.put(
                {
                    "kind": "log",
                    "level": rec["level"].name,
                    "name": rec["name"],
                    "function": rec["function"],
                    "line": rec["line"],
                    "message": rec["message"],
                }
            )

    logger.remove()
    logger.add(_sink, level="TRACE", backtrace=True, diagnose=True)


def _run_ws_subprocess(
    init_kwargs: dict[str, Any],
    cookies_initial: dict[str, str],
    cmd_q: MpQueue,
    evt_q: MpQueue,
    forward_types: tuple[int, ...],
) -> None:
    """Subprocess entry — runs ``_ChildWebSocket`` inside a child Python process.

    Args:
        init_kwargs: Picklable subset of ``_ChildWebSocket`` constructor args
            (everything except ``http_client`` and the callbacks, which
            either can't cross the boundary or live in the parent).
        cookies_initial: Cookie snapshot taken from the parent's HTTP jar
            at start time. Subsequent rotations arrive via ``cmd_q``.
        cmd_q: Parent → child command queue. Messages: ``{"cmd": "send",
            "type": int, "data": Any}``, ``{"cmd": "cookies", "data":
            dict}``, ``{"cmd": "stop"}``.
        evt_q: Child → parent event queue.
        forward_types: Message types for which the child registers a
            forwarder. The parent decides which types it has handlers
            for and passes the list at startup; ``MSG_SERVICE_EVENT``
            is always included.
    """
    _setup_child_logging(evt_q)

    def _on_unauth() -> None:
        evt_q.put({"kind": "auth_error", "code": 401})

    def _on_rate_limit() -> None:
        evt_q.put({"kind": "rate_limit"})

    ws = _ChildWebSocket(
        cookies=dict(cookies_initial),
        http_client=None,
        on_unauthorized=_on_unauth,
        on_rate_limited=_on_rate_limit,
        **init_kwargs,
    )

    def _forward_response_cookies(response_headers: Any) -> None:
        """Replacement for ``_absorb_response_cookies`` — pushes morsels via evt_q."""
        if response_headers is None:
            return
        get_all = getattr(response_headers, "get_all", None)
        raw_values = (
            get_all("Set-Cookie")
            if callable(get_all)
            else [response_headers.get("Set-Cookie")]
        )
        for raw in raw_values or []:
            if not raw:
                continue
            parsed = SimpleCookie()
            try:
                parsed.load(raw)
            except Exception as exc:
                logger.debug("WS Set-Cookie parse failed in child: {} ({})", raw, exc)
                continue
            for name, morsel in parsed.items():
                evt_q.put(
                    {
                        "kind": "set_cookie",
                        "name": name,
                        "value": morsel.value,
                        "domain": morsel["domain"] or "fansly.com",
                        "path": morsel["path"] or "/",
                    }
                )

    ws._absorb_response_cookies = _forward_response_cookies  # type: ignore[method-assign]

    def _make_forwarder(msg_type: int) -> Callable[[Any], Any]:
        async def _forward(event_data: Any) -> None:
            evt_q.put({"kind": "event", "type": msg_type, "data": event_data})

        return _forward

    types_to_forward = set(forward_types) | {MSG_SERVICE_EVENT}
    for msg_type in types_to_forward:
        ws.register_handler(msg_type, _make_forwarder(msg_type))

    async def _status_publisher() -> None:
        """Publish (connected, session_id, ...) snapshots whenever they change."""
        last: tuple[bool, str | None, str | None, str | None] = (
            False,
            None,
            None,
            None,
        )
        while True:
            try:
                snapshot = (
                    bool(ws.connected),
                    ws.session_id,
                    ws.websocket_session_id,
                    ws.account_id,
                )
                if snapshot != last:
                    evt_q.put(
                        {
                            "kind": "status",
                            "connected": snapshot[0],
                            "session_id": snapshot[1],
                            "websocket_session_id": snapshot[2],
                            "account_id": snapshot[3],
                        }
                    )
                    last = snapshot
                await asyncio.sleep(0.1)
            except asyncio.CancelledError:
                return
            except Exception as exc:
                logger.error("status_publisher error: {}", exc)

    async def _command_consumer() -> bool:
        """Drain cmd_q. Returns True on stop request, False on queue death."""
        loop = asyncio.get_running_loop()
        while True:
            try:
                msg = await loop.run_in_executor(
                    None, cmd_q.get, True, _QUEUE_POLL_INTERVAL_S
                )
            except queue.Empty:
                continue
            except (EOFError, OSError, BrokenPipeError):
                return False
            cmd = msg.get("cmd")
            if cmd == "stop":
                return True
            if cmd == "send":
                try:
                    await ws.send_message(msg["type"], msg["data"])
                except Exception as exc:
                    logger.error("send_message failed in child: {}", exc)
            elif cmd == "cookies":
                ws.cookies = dict(msg["data"])
            elif cmd == "register":
                msg_type = msg["type"]
                if msg_type not in types_to_forward:
                    types_to_forward.add(msg_type)
                    ws.register_handler(msg_type, _make_forwarder(msg_type))
            else:
                logger.warning("Unknown command in child: {}", cmd)

    async def _supervisor() -> None:
        maintain_task = asyncio.create_task(
            ws._maintain_connection(), name="ws-maintain"
        )
        status_task = asyncio.create_task(_status_publisher(), name="ws-status")
        consumer_task = asyncio.create_task(_command_consumer(), name="ws-consumer")
        try:
            # consumer_task ends → parent requested stop (or queue died).
            # maintain_task ends → WS gave up (max reconnects exhausted);
            # subprocess must surface that or the parent sits in the
            # misleading "subprocess up, WS dead" state indefinitely.
            await asyncio.wait(
                {maintain_task, consumer_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if maintain_task.done() and not consumer_task.done():
                with contextlib.suppress(Exception):
                    evt_q.put(
                        {
                            "kind": "status",
                            "connected": False,
                            "session_id": None,
                            "websocket_session_id": None,
                            "account_id": None,
                            "terminal": True,
                        }
                    )
        finally:
            ws._stop_event.set()
            for task in (status_task, consumer_task, maintain_task):
                if not task.done():
                    task.cancel()
            # Close the websocket so any in-flight recv() returns and
            # _listen_loop exits, instead of blocking on the 60s timeout.
            if ws.websocket is not None:
                with contextlib.suppress(Exception):
                    await ws.websocket.close()
            for task in (status_task, consumer_task, maintain_task):
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task

    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(_supervisor())


# ---------------------------------------------------------------------------
# Public WS client — subprocess-isolated parent-side proxy.
# ---------------------------------------------------------------------------


class FanslyWebSocket:
    """Subprocess-backed Fansly WebSocket client.

    All WebSocket I/O happens in a child process; this class is the
    parent-side shim that translates calls into queue messages and
    reflects subprocess state via mirrored attributes. Exposes the same
    surface (``connected``, ``session_id``, ``register_handler``,
    ``start_in_thread``, ``stop_thread``, ``send_message``) callers
    consume — they don't need to know there's a subprocess underneath.

    Each subprocess has its own GIL, so the WS heartbeat is fully
    insulated from main-process CPU bursts (image hashing, large JSON
    decodes, dedupe). Communication uses two ``multiprocessing.Queue``
    instances: ``cmd_q`` (parent→child) and ``evt_q`` (child→parent).

    What does NOT cross the process boundary:
      * ``httpx.Client`` — the parent owns the cookie jar; the child
        receives snapshot dicts via ``cmd_q`` and pushes Set-Cookie
        morsels back via ``evt_q``.
      * Callback closures (``on_unauthorized`` / ``on_rate_limited``) —
        the child sends ``{"kind": "auth_error"|"rate_limit"}`` events;
        the parent invokes the locally-stored callbacks.
      * Handler closures (``register_handler``) — the child registers
        a forwarder for each requested message type; the parent
        dispatches to the actual closure.
    """

    MSG_ERROR = MSG_ERROR
    MSG_SESSION = MSG_SESSION
    MSG_PING = MSG_PING
    MSG_SERVICE_EVENT = MSG_SERVICE_EVENT
    MSG_BATCH = MSG_BATCH
    MSG_CHAT_ROOM = MSG_CHAT_ROOM
    PING_INTERVAL_MIN = _ChildWebSocket.PING_INTERVAL_MIN
    PING_INTERVAL_MAX = _ChildWebSocket.PING_INTERVAL_MAX

    def __init__(
        self,
        token: str,
        user_agent: str,
        cookies: dict[str, str] | None = None,
        enable_logging: bool = False,
        on_unauthorized: Callable[[], Any] | None = None,
        on_rate_limited: Callable[[], Any] | None = None,
        base_url: str | None = None,
        http_client: httpx.Client | None = None,
    ) -> None:
        self.token = token
        self.user_agent = user_agent
        self.cookies = dict(cookies) if cookies else {}
        self.enable_logging = enable_logging
        self.on_unauthorized = on_unauthorized
        self.on_rate_limited = on_rate_limited
        self.base_url = base_url
        self.http_client = http_client

        # Mirrored runtime state — populated by the drain task from "status" events.
        self.connected = False
        self.session_id: str | None = None
        self.websocket_session_id: str | None = None
        self.account_id: str | None = None

        # Same shape as _ChildWebSocket._event_handlers so daemon/bootstrap.py's
        # `bootstrap.ws._event_handlers.pop(...)` keeps working.
        self._event_handlers: dict[int, Callable[[Any], Any]] = {}

        self._proc: mp.process.BaseProcess | None = None
        self._cmd_q: MpQueue | None = None
        self._evt_q: MpQueue | None = None
        self._drain_task: asyncio.Task[None] | None = None
        self._main_loop: asyncio.AbstractEventLoop | None = None

    def register_handler(
        self,
        message_type: int,
        handler: Callable[[Any], Any],
    ) -> None:
        """Register a parent-side handler for a given message type.

        The handler stays in the parent (handlers close over ``asyncio.Queue``
        and other loop-bound state and cannot cross a process boundary).
        If the subprocess is already running, push a "register" command so
        it knows to forward this message type.
        """
        already_known = message_type in self._event_handlers
        self._event_handlers[message_type] = handler
        if not already_known:
            self._send_cmd({"cmd": "register", "type": message_type})

    def start_in_thread(
        self,
        main_loop: asyncio.AbstractEventLoop | None = None,
        ready_timeout: float = 5.0,
    ) -> None:
        """Spawn the WS subprocess and start the parent-side drain task.

        Args:
            main_loop: The asyncio loop to schedule callbacks on. Defaults
                to the running loop at call time.
            ready_timeout: Unused — kept for signature compatibility.
                The subprocess publishes its session_id via ``evt_q``;
                callers poll ``self.session_id`` after ``start_in_thread()``.
        """
        del ready_timeout  # signature compat only
        if self._proc is not None and self._proc.is_alive():
            return

        self._main_loop = main_loop or asyncio.get_running_loop()
        ctx = mp.get_context("spawn")
        self._cmd_q = ctx.Queue()
        self._evt_q = ctx.Queue()

        cookies_initial = self._snapshot_cookies()

        init_kwargs = {
            "token": self.token,
            "user_agent": self.user_agent,
            "enable_logging": self.enable_logging,
            "base_url": self.base_url,
        }

        forward_types = tuple(self._event_handlers.keys())

        self._proc = ctx.Process(
            target=_run_ws_subprocess,
            args=(
                init_kwargs,
                cookies_initial,
                self._cmd_q,
                self._evt_q,
                forward_types,
            ),
            daemon=True,
            name="fansly-ws-subprocess",
        )
        self._proc.start()

        self._drain_task = asyncio.create_task(self._drain_evt_q())

    async def stop_thread(self, join_timeout: float = 10.0) -> None:
        """Stop the WS subprocess and join.

        Sends a stop command, cancels the drain task, joins the
        subprocess, and falls back to ``terminate()`` if the child
        overruns the join timeout.
        """
        if self._proc is None:
            return

        self._send_cmd({"cmd": "stop"})

        if self._drain_task is not None:
            self._drain_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._drain_task
            self._drain_task = None

        await asyncio.to_thread(self._proc.join, join_timeout)
        if self._proc.is_alive():
            self._proc.terminate()
            await asyncio.to_thread(self._proc.join, 1.0)

        self._proc = None
        self._cmd_q = None
        self._evt_q = None
        self.connected = False

    async def send_message(self, message_type: int, data: Any) -> None:
        """Send a message through the WS subprocess.

        Raises:
            RuntimeError: If the subprocess is not running.
        """
        if self._proc is None or not self._proc.is_alive():
            raise RuntimeError("WS subprocess not running")
        self._send_cmd({"cmd": "send", "type": message_type, "data": data})

    async def _drain_evt_q(self) -> None:
        """Pull events from the subprocess and dispatch locally."""
        loop = asyncio.get_running_loop()
        try:
            while True:
                if self._evt_q is None or self._proc is None:
                    return
                try:
                    msg = await loop.run_in_executor(
                        None, self._evt_q.get, True, _QUEUE_POLL_INTERVAL_S
                    )
                except queue.Empty:
                    continue
                except (EOFError, OSError, BrokenPipeError, AttributeError):
                    return
                kind = msg.get("kind")
                if kind == "event":
                    await self._dispatch_event(msg["type"], msg["data"])
                elif kind == "status":
                    self._update_status(msg)
                elif kind == "log":
                    self._reemit_child_log(msg)
                elif kind == "auth_error":
                    await self._dispatch_callback(self.on_unauthorized)
                elif kind == "rate_limit":
                    await self._dispatch_callback(self.on_rate_limited)
                elif kind == "set_cookie":
                    self._absorb_set_cookie(msg)
        except asyncio.CancelledError:
            pass

    async def _dispatch_event(self, msg_type: int, data: Any) -> None:
        handler = self._event_handlers.get(msg_type)
        if handler is None:
            return
        if inspect.iscoroutinefunction(handler):
            await handler(data)
        else:
            handler(data)

    async def _dispatch_callback(self, cb: Callable[[], Any] | None) -> None:
        if cb is None:
            return
        if inspect.iscoroutinefunction(cb):
            await cb()
        else:
            cb()

    def _reemit_child_log(self, msg: dict[str, Any]) -> None:
        """Re-emit a child loguru record through this process's sinks."""
        location = (
            f"{msg.get('name', '?')}:{msg.get('function', '?')}:{msg.get('line', '?')}"
        )
        with contextlib.suppress(Exception):
            logger.log(
                msg.get("level", "INFO"),
                "[ws-child {}] {}",
                location,
                msg.get("message", ""),
            )

    def _update_status(self, msg: dict[str, Any]) -> None:
        prev_connected = self.connected
        self.connected = msg.get("connected", False)
        self.session_id = msg.get("session_id")
        self.websocket_session_id = msg.get("websocket_session_id")
        self.account_id = msg.get("account_id")

        if msg.get("terminal"):
            # Child reports WS exhausted reconnects — subprocess is still
            # alive but the connection can no longer recover on its own.
            # Operators / daemon shutdown will reap the process; this is
            # the structural signal that respawn (not retry) is needed.
            logger.error("WS subprocess: connection lost permanently (max reconnects)")

        # Connected → disconnected transition: push a fresh cookie snapshot
        # so the child's next reconnect uses the latest jar contents.
        if prev_connected and not self.connected:
            self._send_cmd({"cmd": "cookies", "data": self._snapshot_cookies()})

    def _absorb_set_cookie(self, msg: dict[str, Any]) -> None:
        if self.http_client is None:
            self.cookies[msg["name"]] = msg["value"]
            return
        self.http_client.cookies.set(
            msg["name"],
            msg["value"],
            domain=msg.get("domain", "fansly.com"),
            path=msg.get("path", "/"),
        )

    def _snapshot_cookies(self) -> dict[str, str]:
        if self.http_client is None:
            return dict(self.cookies)
        return {c.name: c.value for c in self.http_client.cookies.jar}

    def _send_cmd(self, cmd: dict[str, Any]) -> None:
        if self._cmd_q is None:
            return
        with contextlib.suppress(BrokenPipeError, OSError):
            self._cmd_q.put(cmd)
