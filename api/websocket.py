"""Fansly WebSocket client for anti-detection and real-time updates.

This module implements a persistent WebSocket connection to wss://wsv3.fansly.com
for anti-detection purposes. Real browser sessions maintain an active WebSocket
connection for real-time notifications and session management.

The client maintains a background connection while the main session performs
downloads and other operations, mimicking real browser behavior.
"""

from __future__ import annotations

import asyncio
import json
import ssl
from collections.abc import Callable
from contextlib import suppress
from types import TracebackType
from typing import Any

from websockets import client as ws_client
from websockets.exceptions import WebSocketException

from config.logging import textio_logger as logger
from helpers.timer import timing_jitter


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
        _background_task: Background task for maintaining connection
        _stop_event: Event to signal shutdown
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
    ):
        """Initialize Fansly WebSocket client.

        Args:
            token: Fansly authentication token
            user_agent: User agent string to use for the connection
            cookies: Optional cookies dict to send with connection
            enable_logging: Enable detailed debug logging (default: False)
            on_unauthorized: Callback function to call on 401 error (logout)
            on_rate_limited: Callback function to call on 429 error (rate limit)
        """
        self.token = token
        self.user_agent = user_agent
        self.cookies = cookies or {}
        self.enable_logging = enable_logging
        self.on_unauthorized = on_unauthorized
        self.on_rate_limited = on_rate_limited
        self.base_url = self.WEBSOCKET_URL
        self.connected = False
        self.session_id: str | None = None
        self.websocket_session_id: str | None = None
        self.account_id: str | None = None
        self.websocket = None
        self._background_task: asyncio.Task | None = None
        self._ping_task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()
        self._event_handlers: dict[int, Callable[[dict[str, Any]], Any]] = {}
        self._reconnect_attempts = 0
        self._max_reconnect_attempts = 5
        self._reconnect_delay = 1.0

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

    def _create_cookie_header(self) -> str:
        """Create Cookie header from cookies dict.

        Returns:
            Cookie header string (e.g., "key1=value1; key2=value2")
        """
        if not self.cookies:
            return ""
        return "; ".join(f"{k}={v}" for k, v in self.cookies.items())

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
        logger.info("Registered custom handler for message type: %d", message_type)

    async def _handle_message(self, message: str | bytes) -> None:
        """Handle incoming WebSocket message.

        Args:
            message: Raw message string or bytes from WebSocket
        """
        try:
            # Handle both string and bytes
            if isinstance(message, bytes):
                message = message.decode("utf-8")

            data = json.loads(message)
            message_type = data.get("t")
            message_data = data.get("d")

            if self.enable_logging:
                logger.debug(
                    "Received WebSocket message - type: %s, data: %s",
                    message_type,
                    message_data,
                )

            # Handle authentication response (type 1)
            if message_type == 1:
                await self._handle_auth_response(message_data)
            # Handle ping response (type 2) - contains lastPing timestamp
            elif message_type == 2:
                if self.enable_logging:
                    logger.debug("Received ping response: %s", message_data)
            # Handle error events (check for error code in message data)
            elif isinstance(message_data, dict) and "code" in message_data:
                await self._handle_error_event(message_data)
            # Handle other registered message types
            elif message_type in self._event_handlers:
                handler = self._event_handlers[message_type]
                if asyncio.iscoroutinefunction(handler):
                    await handler(message_data)
                else:
                    handler(message_data)
            # Silently discard unknown message types (anti-detection)
            elif self.enable_logging:
                logger.debug(
                    "Received unhandled message type %s (discarded)",
                    message_type,
                )

        except json.JSONDecodeError as e:
            logger.error("Failed to decode WebSocket message: %s", e)
        except Exception as e:
            logger.error("Error handling WebSocket message: %s", e)

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
            logger.warning("WebSocket received error code: %s", error_code)

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
                    "WebSocket authenticated - session: %s, ws_session: %s, account: %s",
                    self.session_id,
                    self.websocket_session_id,
                    self.account_id,
                )
            else:
                logger.warning("Authentication response missing session ID")

        except json.JSONDecodeError as e:
            logger.error("Failed to decode auth response: %s", e)

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
        logger.info("Connecting to WebSocket: %s", connection_url)

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
            logger.error("Failed to connect to WebSocket: %s", e)
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
            logger.error("Error during WebSocket disconnect: %s", e)
        finally:
            self.connected = False
            self.websocket = None
            self.session_id = None
            self.websocket_session_id = None
            self.account_id = None

    def _start_ping_loop(self) -> None:
        """Start the ping loop task.

        Sends 'p' (ping) with randomized interval between 20-25 seconds,
        matching browser behavior. Expects type 2 response with lastPing.
        """
        if self._ping_task is not None:
            logger.warning("Ping loop already running")
            return

        async def ping_worker() -> None:
            """Worker to send periodic pings with randomized intervals."""
            try:
                while self.connected and not self._stop_event.is_set():
                    try:
                        # Randomize ping interval between 20-25 seconds (matching browser)
                        ping_interval = timing_jitter(
                            self.PING_INTERVAL_MIN, self.PING_INTERVAL_MAX
                        )
                        await asyncio.sleep(ping_interval)

                        if not self.connected or not self.websocket:
                            break

                        # Send ping (just the letter 'p')
                        await self.websocket.send("p")

                        if self.enable_logging:
                            logger.debug("Sent ping (next in %.1fs)", ping_interval)

                    except WebSocketException as e:
                        logger.error("Error sending ping: %s", e)
                        self.connected = False
                        break
                    except Exception as e:
                        logger.error("Unexpected error in ping loop: %s", e)
                        break

            except asyncio.CancelledError:
                logger.debug("Ping loop cancelled")

        self._ping_task = asyncio.create_task(ping_worker())
        if self.enable_logging:
            logger.debug("Ping loop started")

    def _stop_ping_loop(self) -> None:
        """Stop the ping loop task."""
        if self._ping_task is None:
            return

        self._ping_task.cancel()
        self._ping_task = None

        if self.enable_logging:
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
                    if self.enable_logging:
                        logger.debug("WebSocket listen timeout - continuing")
                    continue
                except WebSocketException as e:
                    logger.error("WebSocket error in listen loop: %s", e)
                    self.connected = False
                    break

        except asyncio.CancelledError:
            logger.info("WebSocket listen loop cancelled")
        except Exception as e:
            logger.error("Unexpected error in listen loop: %s", e)
            self.connected = False

    async def _maintain_connection(self) -> None:
        """Maintain WebSocket connection with reconnection logic."""
        while not self._stop_event.is_set():
            try:
                if not self.connected:
                    if self._reconnect_attempts >= self._max_reconnect_attempts:
                        logger.error(
                            "Max reconnection attempts reached (%d)",
                            self._max_reconnect_attempts,
                        )
                        break

                    if self._reconnect_attempts > 0:
                        delay = self._reconnect_delay * (2**self._reconnect_attempts)
                        logger.info("Reconnecting in %.1f seconds...", delay)
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
                logger.error("Error in connection maintenance: %s", e)
                await asyncio.sleep(self._reconnect_delay)

    async def start_background(self) -> None:
        """Start WebSocket connection in background task.

        Connects to WebSocket and maintains connection until stop() is called.
        Automatically handles reconnection on disconnects.

        Example:
            client = FanslyWebSocket(token, user_agent)
            await client.start_background()
            # ... do other work ...
            await client.stop()
        """
        if self._background_task is not None:
            logger.warning("Background task already running")
            return

        logger.info("Starting WebSocket background task")

        self._background_task = asyncio.create_task(self._maintain_connection())
        logger.info("WebSocket background task started")

    async def stop(self) -> None:
        """Stop background WebSocket connection.

        Signals the background task to disconnect and waits for cleanup.
        """
        if self._background_task is None:
            logger.warning("No background task running")
            return

        logger.info("Stopping WebSocket background task")
        self._stop_event.set()

        # Stop ping loop
        self._stop_ping_loop()

        try:
            await asyncio.wait_for(self._background_task, timeout=5.0)
        except TimeoutError:
            logger.warning("Background task did not stop gracefully, cancelling")
            self._background_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._background_task

        if self.connected:
            await self.disconnect()

        self._background_task = None
        self._stop_event.clear()
        self._reconnect_attempts = 0
        logger.info("WebSocket background task stopped")

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

        if self.enable_logging:
            logger.debug("Sent WebSocket message - type: %d", message_type)

    async def __aenter__(self) -> FanslyWebSocket:
        """Async context manager entry."""
        await self.start_background()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """Async context manager exit."""
        await self.stop()
