"""Tests for api/websocket.py — FanslyWebSocket protocol handler.

External boundary: websockets.client.connect (patched with FakeSocket).
Everything else — message dispatch, ping logic, reconnect, auth — runs real code.
"""

import asyncio
import json
from contextlib import suppress
from unittest.mock import patch

import pytest

from api.websocket import FanslyWebSocket


class FakeSocket:
    """Test double for a websockets connection.

    Records all sent messages and feeds back scripted recv responses.
    No mocks — just a list-based message queue.
    """

    def __init__(self, recv_messages: list[str] | None = None):
        self.sent: list[str] = []
        self._recv_queue = list(recv_messages or [])
        self._block_event = asyncio.Event()
        self.closed = False

    async def send(self, message: str) -> None:
        self.sent.append(message)

    async def recv(self) -> str:
        if self._recv_queue:
            return self._recv_queue.pop(0)
        # Block until unblocked (simulates waiting for server message)
        await self._block_event.wait()
        return ""

    async def close(self) -> None:
        self.closed = True
        self._block_event.set()  # Unblock any pending recv


def _make_ws(*, enable_logging=False, on_unauthorized=None, on_rate_limited=None):
    return FanslyWebSocket(
        token="test_token",  # noqa: S106
        user_agent="TestAgent/1.0",
        cookies={"sess": "abc"},
        enable_logging=enable_logging,
        on_unauthorized=on_unauthorized,
        on_rate_limited=on_rate_limited,
    )


def _msg(t, d):
    """Build a WebSocket message string."""
    return json.dumps({"t": t, "d": d})


def _auth_response(session_id="123", ws_session_id="456", account_id="789"):
    """Build a type 1 auth response."""
    return _msg(
        1,
        json.dumps(
            {
                "session": {
                    "id": session_id,
                    "token": "tok",
                    "accountId": account_id,
                    "websocketSessionId": ws_session_id,
                    "status": 2,
                }
            }
        ),
    )


class TestMessageHelpers:
    """Lines 101-127: auth message, cookie header, SSL context."""

    def test_create_auth_message(self):
        ws = _make_ws()
        msg = json.loads(ws._create_auth_message())
        assert msg["t"] == 1
        inner = json.loads(msg["d"])
        assert inner["token"] == "test_token"
        assert inner["v"] == 3

    def test_create_cookie_header(self):
        ws = _make_ws()
        assert ws._create_cookie_header() == "sess=abc"

    def test_create_cookie_header_empty(self):
        ws = FanslyWebSocket(token="t", user_agent="ua")  # noqa: S106
        assert ws._create_cookie_header() == ""

    def test_create_ssl_context(self):
        ws = _make_ws()
        ctx = ws._create_ssl_context()
        import ssl

        assert ctx.check_hostname is False
        assert ctx.verify_mode == ssl.CERT_NONE


class TestHandleMessage:
    """Lines 157-207: message dispatch — error, session, ping, service, batch."""

    @pytest.mark.asyncio
    async def test_type_0_error_event(self):
        """MSG_ERROR (0) → _handle_error_event with decoded data."""
        ws = _make_ws()
        ws.connected = True

        error_data = {"code": 500, "message": "internal"}
        await ws._handle_message(_msg(0, json.dumps(error_data)))
        # Unknown error code → just logs, no crash

    @pytest.mark.asyncio
    async def test_type_0_error_401_disconnects(self):
        """MSG_ERROR (0) with code 401 → disconnects, calls on_unauthorized."""
        called = []

        async def on_unauth():
            called.append("unauthorized")

        ws = _make_ws(on_unauthorized=on_unauth)
        ws.connected = True
        ws.session_id = "sess"

        await ws._handle_message(_msg(0, json.dumps({"code": 401})))

        assert ws.connected is False
        assert called == ["unauthorized"]

    @pytest.mark.asyncio
    async def test_type_0_error_429_calls_rate_limited(self):
        """MSG_ERROR (0) with code 429 → calls on_rate_limited."""
        called = []
        ws = _make_ws(on_rate_limited=lambda: called.append("rate_limited"))
        ws.connected = True

        await ws._handle_message(_msg(0, json.dumps({"code": 429})))

        assert called == ["rate_limited"]

    @pytest.mark.asyncio
    async def test_type_1_session_verified(self):
        """MSG_SESSION (1) → _handle_auth_response sets session fields."""
        ws = _make_ws()
        await ws._handle_message(_auth_response())
        assert ws.session_id == "123"
        assert ws.websocket_session_id == "456"
        assert ws.account_id == "789"

    @pytest.mark.asyncio
    async def test_type_1_missing_session_id(self):
        """Auth response without session ID → logs warning."""
        ws = _make_ws()
        await ws._handle_message(_msg(1, json.dumps({"session": {}})))
        assert ws.session_id is None

    @pytest.mark.asyncio
    async def test_type_1_invalid_json(self):
        """Auth response with invalid JSON in d → logs error."""
        ws = _make_ws()
        await ws._handle_message(_msg(1, "not json"))
        assert ws.session_id is None

    @pytest.mark.asyncio
    async def test_type_2_ping_response(self):
        """MSG_PING (2) → updates _last_ping_response."""
        ws = _make_ws()
        before = ws._last_ping_response
        await ws._handle_message(_msg(2, '{"lastPing": 1234}'))
        assert ws._last_ping_response > before

    @pytest.mark.asyncio
    async def test_type_10000_service_event(self):
        """MSG_SERVICE_EVENT (10000) → dispatches to registered handler."""
        ws = _make_ws()
        received = []
        ws.register_handler(10000, received.append)

        event = {"serviceId": 1, "action": "create", "data": {"id": "99"}}
        await ws._handle_message(_msg(10000, json.dumps(event)))

        assert len(received) == 1
        assert received[0]["serviceId"] == 1

    @pytest.mark.asyncio
    async def test_type_10000_async_handler(self):
        """Service event with async handler."""
        ws = _make_ws()
        received = []

        async def handler(data):
            received.append(data)

        ws.register_handler(10000, handler)
        await ws._handle_message(_msg(10000, json.dumps({"x": 1})))
        assert len(received) == 1

    @pytest.mark.asyncio
    async def test_type_10001_batch(self):
        """MSG_BATCH (10001) → recursively unpacks array of messages."""
        ws = _make_ws()
        received = []
        ws.register_handler(10000, received.append)

        batch = [
            {"t": 10000, "d": json.dumps({"event": "a"})},
            {"t": 10000, "d": json.dumps({"event": "b"})},
            {"t": 2, "d": '{"lastPing": 0}'},  # ping mixed in
        ]
        await ws._handle_message(_msg(10001, batch))

        assert len(received) == 2
        assert received[0]["event"] == "a"
        assert received[1]["event"] == "b"

    @pytest.mark.asyncio
    async def test_custom_handler_sync_and_async(self):
        """Custom registered handlers — sync and async."""
        ws = _make_ws()
        sync_received = []
        async_received = []

        ws.register_handler(99, sync_received.append)

        async def async_handler(data):
            async_received.append(data)

        ws.register_handler(100, async_handler)

        await ws._handle_message(_msg(99, "sync_data"))
        await ws._handle_message(_msg(100, "async_data"))

        assert sync_received == ["sync_data"]
        assert async_received == ["async_data"]

    @pytest.mark.asyncio
    async def test_unknown_type_discarded(self):
        """Unknown message type with logging enabled → debug log, no crash."""
        ws = _make_ws(enable_logging=True)
        await ws._handle_message(_msg(99999, "unknown"))

    @pytest.mark.asyncio
    async def test_bytes_message(self):
        """Bytes input → decoded to string first."""
        ws = _make_ws()
        await ws._handle_message(_auth_response().encode("utf-8"))
        assert ws.session_id == "123"

    @pytest.mark.asyncio
    async def test_invalid_json_message(self):
        """Non-JSON message → JSONDecodeError caught."""
        ws = _make_ws()
        await ws._handle_message("not json at all")


class TestErrorEvent:
    """Lines 212-240: _handle_error_event — 401, 429, unknown codes."""

    @pytest.mark.asyncio
    async def test_401_sync_callback(self):
        """401 with sync on_unauthorized callback."""
        called = []
        ws = _make_ws(on_unauthorized=lambda: called.append("unauth"))
        ws.connected = True
        ws.session_id = "s"

        await ws._handle_error_event({"code": 401})
        assert called == ["unauth"]
        assert ws.connected is False

    @pytest.mark.asyncio
    async def test_429_async_callback(self):
        """429 with async on_rate_limited callback."""
        called = []

        async def on_rate():
            called.append("rate")

        ws = _make_ws(on_rate_limited=on_rate)

        await ws._handle_error_event({"code": 429})
        assert called == ["rate"]

    @pytest.mark.asyncio
    async def test_429_no_callback(self):
        """429 without callback → just logs."""
        ws = _make_ws()
        await ws._handle_error_event({"code": 429})

    @pytest.mark.asyncio
    async def test_401_no_callback(self):
        """401 without callback → disconnects but no crash."""
        ws = _make_ws()
        ws.connected = True
        ws.session_id = "s"
        await ws._handle_error_event({"code": 401})
        assert ws.connected is False


class TestConnectDisconnect:
    """Lines 282-373: connect/disconnect with mocked websockets.client.connect."""

    @pytest.mark.asyncio
    async def test_connect_and_disconnect(self):
        """Full connect → auth → ping loop start → disconnect cycle."""
        ws = _make_ws()
        fake = FakeSocket(recv_messages=[_auth_response()])

        async def fake_connect(**kwargs):
            return fake

        with patch("api.websocket.ws_client.connect", side_effect=fake_connect):
            await ws.connect()

        assert ws.connected is True
        assert ws.session_id == "123"
        assert ws._ping_task is not None
        # Auth message was sent
        assert len(fake.sent) == 1
        auth_sent = json.loads(fake.sent[0])
        assert auth_sent["t"] == 1

        await ws.disconnect()
        assert ws.connected is False
        assert ws.session_id is None
        assert fake.closed is True

    @pytest.mark.asyncio
    async def test_connect_already_connected(self):
        """connect() when already connected → warning, no-op."""
        ws = _make_ws()
        ws.connected = True
        await ws.connect()  # no crash

    @pytest.mark.asyncio
    async def test_disconnect_not_connected(self):
        """disconnect() when not connected → warning, no-op."""
        ws = _make_ws()
        await ws.disconnect()  # no crash

    @pytest.mark.asyncio
    async def test_connect_auth_failure(self):
        """Auth response without session ID → RuntimeError."""
        ws = _make_ws()
        fake = FakeSocket(recv_messages=[_msg(1, json.dumps({"session": {}}))])

        async def fake_connect(**kwargs):
            return fake

        with (
            patch("api.websocket.ws_client.connect", side_effect=fake_connect),
            pytest.raises(RuntimeError, match="Failed to authenticate"),
        ):
            await ws.connect()

        assert ws.connected is False

    @pytest.mark.asyncio
    async def test_connect_exception(self):
        """Connection failure → connected=False, exception propagates."""
        ws = _make_ws()

        async def fail_connect(**kwargs):
            raise OSError("refused")

        with (
            patch("api.websocket.ws_client.connect", side_effect=fail_connect),
            pytest.raises(OSError),
        ):
            await ws.connect()

        assert ws.connected is False


class TestSendMessage:
    """Lines 558-569: send_message."""

    @pytest.mark.asyncio
    async def test_send_message(self):
        ws = _make_ws(enable_logging=True)
        ws.connected = True
        fake = FakeSocket()
        ws.websocket = fake

        await ws.send_message(5, {"hello": "world"})

        assert len(fake.sent) == 1
        sent = json.loads(fake.sent[0])
        assert sent["t"] == 5
        assert json.loads(sent["d"]) == {"hello": "world"}

    @pytest.mark.asyncio
    async def test_send_message_not_connected(self):
        ws = _make_ws()
        with pytest.raises(RuntimeError, match="not connected"):
            await ws.send_message(1, "data")


class TestPingLoop:
    """Lines 425-498: ping worker, start/stop, timeout detection."""

    @pytest.mark.asyncio
    async def test_ping_sends_p(self):
        """Ping loop sends 'p' to websocket (line 469)."""
        ws = _make_ws(enable_logging=True)
        fake = FakeSocket()
        ws.websocket = fake
        ws.connected = True

        ws._start_ping_loop()
        # Let the ping worker run one iteration
        # Override the timing to be fast
        with patch("api.websocket.timing_jitter", return_value=0.05):
            ws._start_ping_loop()  # Already running → warning (line 433-434)
            await asyncio.sleep(0.15)

        ws.connected = False  # Signal the loop to exit
        ws._stop_ping_loop()
        await asyncio.sleep(0.05)

        # Verify at least one 'p' was sent
        assert "p" in fake.sent

    @pytest.mark.asyncio
    async def test_ping_timeout_resets_connection(self):
        """Ping timeout detection disconnects (lines 457-467)."""
        ws = _make_ws()
        fake = FakeSocket()
        ws.websocket = fake
        ws.connected = True
        # Set last ping response far in the past to trigger timeout
        ws._last_ping_response = 0.0
        ws._last_connection_reset = 0.0

        with patch("api.websocket.timing_jitter", return_value=0.01):
            ws._start_ping_loop()
            await asyncio.sleep(0.1)

        # Timeout should have set connected=False
        assert ws.connected is False
        ws._stop_ping_loop()

    @pytest.mark.asyncio
    async def test_ping_websocket_error(self):
        """WebSocket error during ping stops loop (lines 474-477)."""
        from websockets.exceptions import ConnectionClosed

        ws = _make_ws()
        ws.connected = True

        # FakeSocket that raises on send
        class ErrorSocket(FakeSocket):
            async def send(self, message):
                from websockets.frames import Close

                raise ConnectionClosed(Close(1006, "gone"), None)

        ws.websocket = ErrorSocket()

        with patch("api.websocket.timing_jitter", return_value=0.01):
            ws._start_ping_loop()
            await asyncio.sleep(0.1)

        assert ws.connected is False
        ws._stop_ping_loop()

    @pytest.mark.asyncio
    async def test_stop_ping_loop_logging(self):
        """_stop_ping_loop with enable_logging (lines 497-498)."""
        ws = _make_ws(enable_logging=True)
        ws._ping_task = asyncio.create_task(asyncio.sleep(999))
        ws._stop_ping_loop()
        assert ws._ping_task is None

    @pytest.mark.asyncio
    async def test_start_ping_already_running(self):
        """_start_ping_loop when already running warns (lines 432-434)."""
        ws = _make_ws()
        ws.connected = True
        ws.websocket = FakeSocket()

        ws._start_ping_loop()
        first_task = ws._ping_task
        ws._start_ping_loop()  # Should warn, not create a second task
        assert ws._ping_task is first_task
        ws._stop_ping_loop()
        ws.connected = False


class TestListenLoop:
    """Lines 500-528: _listen_loop — recv, timeout, errors."""

    @pytest.mark.asyncio
    async def test_listen_loop_processes_messages(self):
        """_listen_loop receives and dispatches messages (lines 506-513).

        Feed two messages then signal exit. The second recv sets
        connected=False and returns a valid message so the loop exits
        after processing.
        """
        ws = _make_ws()
        ws.connected = True

        call_count = [0]

        class TwoMessageSocket(FakeSocket):
            async def recv(self) -> str:
                call_count[0] += 1
                if call_count[0] == 1:
                    return _msg(2, '{"ping": 1}')
                # Second recv: signal stop and return valid msg
                ws.connected = False
                return _msg(2, '{"ping": 2}')

        ws.websocket = TwoMessageSocket()
        await ws._listen_loop()
        assert call_count[0] >= 2

    @pytest.mark.asyncio
    async def test_listen_loop_timeout_continues(self):
        """Listen timeout is normal — continues then exits (lines 514-518).

        _listen_loop wraps recv() in asyncio.wait_for(..., timeout=60.0).
        We simulate the timeout by raising TimeoutError from recv, which is
        what asyncio.wait_for raises when the inner coroutine times out.
        On second call, we set connected=False and return immediately so the
        while loop exits cleanly.
        """
        ws = _make_ws(enable_logging=True)
        ws.connected = True

        timeout_count = [0]

        class TimeoutOnceSocket(FakeSocket):
            async def recv(self) -> str:
                timeout_count[0] += 1
                if timeout_count[0] == 1:
                    raise TimeoutError
                # Second call: signal exit and return immediately
                ws.connected = False
                return _msg(2, '{"ping": 0}')  # Valid message to process

        ws.websocket = TimeoutOnceSocket()
        await ws._listen_loop()
        assert timeout_count[0] >= 2

    @pytest.mark.asyncio
    async def test_listen_loop_websocket_error(self):
        """WebSocket error in listen → connected=False (lines 519-522)."""
        from websockets.exceptions import ConnectionClosed
        from websockets.frames import Close

        ws = _make_ws()
        ws.connected = True

        class ErrorSocket(FakeSocket):
            async def recv(self):
                raise ConnectionClosed(Close(1006, "gone"), None)

        ws.websocket = ErrorSocket()
        await ws._listen_loop()
        assert ws.connected is False

    @pytest.mark.asyncio
    async def test_listen_loop_unexpected_error(self):
        """Unexpected error in listen → connected=False (lines 526-528)."""
        ws = _make_ws()
        ws.connected = True

        class BrokenSocket(FakeSocket):
            async def recv(self):
                raise RuntimeError("boom")

        ws.websocket = BrokenSocket()
        await ws._listen_loop()
        assert ws.connected is False


class TestMaintainConnection:
    """Lines 530-567: _maintain_connection — reconnect logic."""

    @pytest.mark.asyncio
    async def test_max_reconnect_attempts(self):
        """Max reconnect attempts reached → stops (lines 535-540)."""
        ws = _make_ws()
        ws._max_reconnect_attempts = 1
        ws._reconnect_attempts = 1
        ws._reconnect_delay = 0.01

        # Already at max attempts, loop body exits on first check
        await asyncio.wait_for(ws._maintain_connection(), timeout=2.0)

    @pytest.mark.asyncio
    async def test_reconnect_with_delay(self):
        """Reconnect after failure uses exponential backoff (lines 542-549)."""
        ws = _make_ws()
        ws._max_reconnect_attempts = 2
        ws._reconnect_delay = 0.01
        ws._max_reconnect_delay = 0.05

        connect_count = [0]

        async def fail_connect(**_kw):
            connect_count[0] += 1
            raise OSError("refused")

        with patch("api.websocket.ws_client.connect", side_effect=fail_connect):
            await asyncio.wait_for(ws._maintain_connection(), timeout=5.0)

        assert connect_count[0] == 2  # Tried twice then stopped

    @pytest.mark.asyncio
    async def test_maintenance_cancelled(self):
        """CancelledError in maintenance loop is handled (lines 562-564)."""
        ws = _make_ws()
        ws._reconnect_delay = 0.01

        async def hang_connect(**_kw):
            # Wait for stop event instead of sleeping forever
            await ws._stop_event.wait()

        with patch("api.websocket.ws_client.connect", side_effect=hang_connect):
            task = asyncio.create_task(ws._maintain_connection())
            await asyncio.sleep(0.05)
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task


class TestDisconnectEdgeCases:
    """Lines 414-423: disconnect error handling."""

    @pytest.mark.asyncio
    async def test_disconnect_close_error(self):
        """Error during websocket.close is caught (lines 416-417)."""
        ws = _make_ws()
        ws.connected = True

        class ErrorCloseSocket(FakeSocket):
            async def close(self):
                raise RuntimeError("close failed")

        ws.websocket = ErrorCloseSocket()
        await ws.disconnect()
        assert ws.connected is False
        assert ws.websocket is None


class TestStartStopBackground:
    """Lines 569-619: start_background, stop."""

    @pytest.mark.asyncio
    async def test_start_background_already_running(self):
        """start_background when already running → warning (lines 581-583)."""
        ws = _make_ws()
        done = asyncio.Event()
        ws._background_task = asyncio.create_task(done.wait())

        await ws.start_background()  # Should warn and return

        done.set()
        await ws._background_task

    @pytest.mark.asyncio
    async def test_stop_no_background_task(self):
        """stop() when no background task → warning (lines 595-597)."""
        ws = _make_ws()
        ws._background_task = None
        await ws.stop()  # Should warn and return

    @pytest.mark.asyncio
    async def test_stop_timeout_cancels(self):
        """Background task doesn't stop in time → cancel (lines 607-611)."""
        ws = _make_ws()
        never_done = asyncio.Event()

        async def hang():
            await never_done.wait()

        ws._background_task = asyncio.create_task(hang())
        ws._stop_event = asyncio.Event()

        await ws.stop()
        assert ws._background_task is None


class TestContextManager:
    """Lines 644-656: async context manager."""

    @pytest.mark.asyncio
    async def test_async_context_manager(self):
        ws = _make_ws()
        fake = FakeSocket(recv_messages=[_auth_response()])

        async def fake_connect(**kwargs):
            return fake

        with patch("api.websocket.ws_client.connect", side_effect=fake_connect):
            async with ws:
                assert ws._background_task is not None

        assert ws._background_task is None
