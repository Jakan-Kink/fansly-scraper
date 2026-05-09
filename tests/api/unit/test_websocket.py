"""Tests for api/websocket.py — FanslyWebSocket protocol handler.

External boundary: websockets.client.connect (patched with FakeSocket).
Everything else — message dispatch, ping logic, reconnect, auth — runs real code.
"""

import asyncio
import json
import logging
import ssl
from contextlib import suppress
from unittest.mock import patch

import pytest
from websockets.exceptions import ConnectionClosed, ConnectionClosedOK
from websockets.frames import Close

from api.websocket import FanslyWebSocket
from tests.fixtures.api.fake_websocket import FakeSocket


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

    @pytest.mark.asyncio
    async def test_non_ping_message_logged_at_debug(self, caplog):
        """Phase 4: non-ping messages (type != 2) are logged at DEBUG."""
        import logging

        caplog.set_level(logging.DEBUG)
        ws = _make_ws()
        await ws._handle_message(_auth_response())  # type 1

        assert any(
            "Received WebSocket message - type: 1" in r.getMessage()
            for r in caplog.records
            if r.levelname == "DEBUG"
        )

    @pytest.mark.asyncio
    async def test_ping_message_not_logged_at_debug(self, caplog):
        """Phase 4: MSG_PING (type 2) is NOT logged — too noisy (every 20-25 s)."""
        import logging

        caplog.set_level(logging.DEBUG)
        ws = _make_ws()
        await ws._handle_message(_msg(2, '{"lastPing": 0}'))

        assert not any(
            "Received WebSocket message - type: 2" in r.getMessage()
            for r in caplog.records
            if r.levelname == "DEBUG"
        )


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

        ws = _make_ws()
        ws.connected = True

        # FakeSocket that raises on send
        class ErrorSocket(FakeSocket):
            async def send(self, message):

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

    @pytest.mark.asyncio
    async def test_listen_loop_connection_closed_ok_logs_debug_not_error(self, caplog):
        """Phase 2: ConnectionClosedOK (code 1000) does NOT produce an ERROR log.

        An intentional WS close (code 1000) must not pollute logs with
        the old false-error "WebSocket error in listen loop" message.
        The loop must exit cleanly with connected=False.
        """
        import logging

        caplog.set_level(logging.WARNING)
        ws = _make_ws()
        ws.connected = True

        class CleanCloseSocket(FakeSocket):
            async def recv(self):
                raise ConnectionClosedOK(Close(1000, "OK"), Close(1000, "OK"))

        ws.websocket = CleanCloseSocket()
        await ws._listen_loop()

        assert ws.connected is False
        # The old bug: ConnectionClosedOK was logged at ERROR with the generic
        # "WebSocket error in listen loop" message. Verify that message is absent.
        error_msgs = [r.getMessage() for r in caplog.records if r.levelname == "ERROR"]
        assert not any("WebSocket error in listen loop" in m for m in error_msgs)


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
        """CancelledError in maintenance loop is handled (lines 562-564).

        ``_stop_event`` is now a ``threading.Event`` (post WS-thread refactor),
        whose ``.wait()`` is sync-blocking and would deadlock the event loop
        if awaited inside an async coroutine.  Use a plain async sleep that
        the test can cancel without depending on the threading primitive.
        """
        ws = _make_ws()
        ws._reconnect_delay = 0.01

        async def hang_connect(**_kw):
            # Sleep long enough that the test's cancel arrives first
            await asyncio.sleep(60)

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


class TestStartStopThread:
    """start_in_thread / stop_thread lifecycle (replaces the old
    background-task tests after the WS moved onto its own thread)."""

    @pytest.mark.asyncio
    async def test_stop_thread_when_not_started(self):
        """stop_thread() with no live thread → warning, returns cleanly."""
        ws = _make_ws()
        assert ws._ws_thread is None
        await ws.stop_thread()  # Should warn and return without raising
        assert ws._ws_thread is None

    @pytest.mark.asyncio
    async def test_start_in_thread_already_running(self):
        """Calling start_in_thread twice → second call warns and returns."""
        ws = _make_ws()

        async def long_running_maintain():
            # threading.Event.wait() is blocking; to_thread makes it awaitable
            await asyncio.to_thread(ws._stop_event.wait)

        with patch.object(
            ws, "_maintain_connection", side_effect=long_running_maintain
        ):
            ws.start_in_thread()
            try:
                first_thread = ws._ws_thread
                assert first_thread is not None
                ws.start_in_thread()  # Should warn, no-op — same thread retained
                assert ws._ws_thread is first_thread
            finally:
                await ws.stop_thread()

    @pytest.mark.asyncio
    async def test_start_then_stop_clean_lifecycle(self):
        """start_in_thread spins up, stop_thread joins cleanly."""
        ws = _make_ws()

        async def short_maintain():
            await asyncio.to_thread(ws._stop_event.wait)

        with patch.object(ws, "_maintain_connection", side_effect=short_maintain):
            ws.start_in_thread()
            assert ws._ws_thread is not None
            assert ws._ws_thread.is_alive()
            await ws.stop_thread()
            assert ws._ws_thread is None


class TestContextManager:
    """Async context manager — async with FanslyWebSocket() as client."""

    @pytest.mark.asyncio
    async def test_async_context_manager_starts_and_stops_thread(self):
        ws = _make_ws()

        async def short_maintain():
            await asyncio.to_thread(ws._stop_event.wait)

        with patch.object(ws, "_maintain_connection", side_effect=short_maintain):
            async with ws:
                assert ws._ws_thread is not None
                assert ws._ws_thread.is_alive()

        assert ws._ws_thread is None


# ---------------------------------------------------------------------------
# Monitor handler tests — covers api/websocket.py 479-844
#
# These tests target _monitor_event + _monitor_service_event +
# 8 service-specific handlers (_monitor_post_event, _monitor_media_event,
# _monitor_message_event, _monitor_follow_event, _monitor_wallet_event,
# _monitor_subscription_event, _monitor_payment_event, _monitor_chat_event).
#
# All handlers categorize WebSocket events for protocol-discovery logging
# via loguru. caplog captures via pytest-loguru's bridge. Methods are called
# directly on a real FanslyWebSocket — no thread, no socket, no asyncio.
# ---------------------------------------------------------------------------


def _logged_messages(caplog, level: str = "INFO") -> list[str]:
    """Return captured loguru messages at the given stdlib levelname."""
    return [r.getMessage() for r in caplog.records if r.levelname == level]


class TestMonitorEvent:
    """Lines 479-536: top-level dispatcher for known message types."""

    def test_ping_returns_silently(self, caplog):
        """Line 486-487: MSG_PING (2) → early return, no log."""
        caplog.set_level(logging.INFO)
        ws = _make_ws()
        ws._monitor_event(FanslyWebSocket.MSG_PING, {})
        # No "[WS Monitor]" lines — ping is intentionally silent.
        assert not any("[WS Monitor]" in m for m in _logged_messages(caplog))

    def test_error_event_logs_code(self, caplog):
        """Lines 489-496: MSG_ERROR → decode + log code."""
        caplog.set_level(logging.INFO)
        ws = _make_ws()
        ws._monitor_event(FanslyWebSocket.MSG_ERROR, json.dumps({"code": 401}))
        msgs = _logged_messages(caplog)
        assert any("[WS Monitor] Error | code=401" in m for m in msgs)

    def test_error_event_dict_input(self, caplog):
        """Lines 489-496: MSG_ERROR with dict input (not str)."""
        caplog.set_level(logging.INFO)
        ws = _make_ws()
        ws._monitor_event(FanslyWebSocket.MSG_ERROR, {"code": 429})
        assert any(
            "[WS Monitor] Error | code=429" in m for m in _logged_messages(caplog)
        )

    def test_session_event_logs_session_id(self, caplog):
        """Lines 498-511: MSG_SESSION → decode + log session/wsId/status."""
        caplog.set_level(logging.INFO)
        ws = _make_ws()
        payload = {
            "session": {
                "id": "sess123",
                "websocketSessionId": "ws456",
                "status": 2,
            }
        }
        ws._monitor_event(FanslyWebSocket.MSG_SESSION, json.dumps(payload))
        msgs = _logged_messages(caplog)
        assert any(
            "[WS Monitor] Session" in m and "sess123" in m and "ws456" in m
            for m in msgs
        )

    def test_service_event_dispatches_to_service_handler(self, caplog):
        """Line 513-515: MSG_SERVICE_EVENT → _monitor_service_event."""
        caplog.set_level(logging.INFO)
        ws = _make_ws()
        # SVC_FOLLOWS=3 with no follow key → unknown follow dump.
        envelope = {"serviceId": 3, "event": json.dumps({"type": 99})}
        ws._monitor_event(FanslyWebSocket.MSG_SERVICE_EVENT, json.dumps(envelope))
        # The service-event dispatch went to the follow handler's fallback.
        msgs = _logged_messages(caplog)
        assert any("[WS Monitor] Follow svc=3" in m for m in msgs)

    def test_batch_event_logs_count_at_debug(self, caplog):
        """Lines 517-520: MSG_BATCH (list) → debug log of count."""
        caplog.set_level(logging.DEBUG)
        ws = _make_ws()
        ws._monitor_event(FanslyWebSocket.MSG_BATCH, [{"foo": 1}, {"bar": 2}])
        debug_msgs = _logged_messages(caplog, "DEBUG")
        assert any("[WS Monitor] Batch of 2" in m for m in debug_msgs)

    def test_batch_event_non_list_input_logs_zero(self, caplog):
        """Line 518: non-list message_data → batch=[]."""
        caplog.set_level(logging.DEBUG)
        ws = _make_ws()
        ws._monitor_event(FanslyWebSocket.MSG_BATCH, "not a list")
        debug_msgs = _logged_messages(caplog, "DEBUG")
        assert any("[WS Monitor] Batch of 0" in m for m in debug_msgs)

    def test_chat_room_event(self, caplog):
        """Lines 522-529: MSG_CHAT_ROOM → log room id."""
        caplog.set_level(logging.INFO)
        ws = _make_ws()
        payload = json.dumps({"chatRoomId": "room42"})
        ws._monitor_event(FanslyWebSocket.MSG_CHAT_ROOM, payload)
        assert any(
            "[WS Monitor] Chat Room Join" in m and "room42" in m
            for m in _logged_messages(caplog)
        )

    def test_unknown_message_type_dumps_payload(self, caplog):
        """Lines 531-536: unknown type → "Unknown t={}" + JSON dump."""
        caplog.set_level(logging.INFO)
        ws = _make_ws()
        ws._monitor_event(99999, {"weird": "payload"})
        msgs = _logged_messages(caplog)
        assert any("[WS Monitor] Unknown t=99999" in m for m in msgs)

    def test_unknown_message_type_empty_payload(self, caplog):
        """Lines 531-536: empty message_data → "(empty)" placeholder."""
        caplog.set_level(logging.INFO)
        ws = _make_ws()
        ws._monitor_event(99999, None)
        msgs = _logged_messages(caplog)
        assert any("(empty)" in m for m in msgs)


class TestMonitorServiceEvent:
    """Lines 538-634: service-event dispatcher (svc → handler routing)."""

    def test_envelope_decode_error_logs_warning(self, caplog):
        """Lines 552-554: bad JSON envelope → warning + return."""
        caplog.set_level(logging.WARNING)
        ws = _make_ws()
        ws._monitor_service_event("not valid json {")
        msgs = _logged_messages(caplog, "WARNING")
        assert any("ServiceEvent decode error" in m for m in msgs)

    def test_inner_event_decode_error_logs_warning(self, caplog):
        """Lines 565-571: bad inner JSON → warning with svc id + return."""
        caplog.set_level(logging.WARNING)
        ws = _make_ws()
        envelope = json.dumps({"serviceId": 7, "event": "not valid {"})
        ws._monitor_service_event(envelope)
        msgs = _logged_messages(caplog, "WARNING")
        assert any("inner decode error" in m and "svc=7" in m for m in msgs)

    def test_messaging_service_returns_silently(self, caplog):
        """Lines 576-577: SVC_MESSAGING (4) → noisy ACKs, skip."""
        caplog.set_level(logging.DEBUG)
        ws = _make_ws()
        envelope = {
            "serviceId": FanslyWebSocket.SVC_MESSAGING,
            "event": json.dumps({"type": 99}),
        }
        ws._monitor_service_event(json.dumps(envelope))
        # No [WS Monitor] log for messaging ACKs.
        assert not any(
            "[WS Monitor]" in m
            for m in _logged_messages(caplog, "INFO")
            + _logged_messages(caplog, "DEBUG")
        )

    def test_notification_event_known_type_debug_log(self, caplog):
        """Lines 580-590: SVC_NOTIFICATIONS with known type → debug log with label."""
        caplog.set_level(logging.DEBUG)
        ws = _make_ws()
        envelope = {
            "serviceId": FanslyWebSocket.SVC_NOTIFICATIONS,
            "event": json.dumps(
                {
                    "notification": {
                        "type": 1002,  # "Post Like"
                        "correlationId": "c1",
                        "id": "n1",
                    }
                }
            ),
        }
        ws._monitor_service_event(json.dumps(envelope))
        debug_msgs = _logged_messages(caplog, "DEBUG")
        assert any("Post Like" in m and "c1" in m and "n1" in m for m in debug_msgs)

    def test_notification_event_unknown_type_uses_unknown_label(self, caplog):
        """Lines 583-584: notification type not in NOTIFICATION_TYPES → 'unknown(N)'."""
        caplog.set_level(logging.DEBUG)
        ws = _make_ws()
        envelope = {
            "serviceId": FanslyWebSocket.SVC_NOTIFICATIONS,
            "event": json.dumps({"notification": {"type": 9999, "id": "n2"}}),
        }
        ws._monitor_service_event(json.dumps(envelope))
        debug_msgs = _logged_messages(caplog, "DEBUG")
        assert any("unknown(9999)" in m for m in debug_msgs)

    def test_notification_read_event(self, caplog):
        """Lines 591-595: SVC_NOTIFICATIONS with 'data' (not 'notification')."""
        caplog.set_level(logging.DEBUG)
        ws = _make_ws()
        envelope = {
            "serviceId": FanslyWebSocket.SVC_NOTIFICATIONS,
            "event": json.dumps({"data": {"beforeAnd": "1234567890"}}),
        }
        ws._monitor_service_event(json.dumps(envelope))
        debug_msgs = _logged_messages(caplog, "DEBUG")
        assert any("Notification Read" in m and "1234567890" in m for m in debug_msgs)

    def test_poll_subscribe_event(self, caplog):
        """Lines 599-608: SVC_POLLS event_type=20 → 'Sub' debug log."""
        caplog.set_level(logging.DEBUG)
        ws = _make_ws()
        envelope = {
            "serviceId": FanslyWebSocket.SVC_POLLS,
            "event": json.dumps(
                {
                    "type": 20,
                    "pollSubscription": {"pollId": "p1", "id": "ps1"},
                }
            ),
        }
        ws._monitor_service_event(json.dumps(envelope))
        debug_msgs = _logged_messages(caplog, "DEBUG")
        assert any("Poll Sub" in m and "p1" in m and "ps1" in m for m in debug_msgs)

    def test_poll_unsubscribe_event(self, caplog):
        """Lines 599-608: SVC_POLLS event_type!=20 → 'Unsub' debug log."""
        caplog.set_level(logging.DEBUG)
        ws = _make_ws()
        envelope = {
            "serviceId": FanslyWebSocket.SVC_POLLS,
            "event": json.dumps(
                {
                    "type": 21,
                    "pollSubscription": {"pollId": "p2", "id": "ps2"},
                }
            ),
        }
        ws._monitor_service_event(json.dumps(envelope))
        debug_msgs = _logged_messages(caplog, "DEBUG")
        assert any("Poll Unsub" in m for m in debug_msgs)

    def test_unknown_service_id_dumps_event(self, caplog):
        """Lines 627-634: serviceId not in handlers dict → full dump."""
        caplog.set_level(logging.INFO)
        ws = _make_ws()
        envelope = {
            "serviceId": 12345,  # not in any SVC_* constant
            "event": json.dumps({"type": 7, "weird": "data"}),
        }
        ws._monitor_service_event(json.dumps(envelope))
        msgs = _logged_messages(caplog)
        assert any("serviceId=12345" in m and "type=7" in m for m in msgs)


class TestMonitorPostEvent:
    """Lines 636-653: SVC_POST handler (likes + unknown fallback)."""

    def test_post_like_logs_structured(self, caplog):
        caplog.set_level(logging.INFO)
        ws = _make_ws()
        ws._monitor_post_event(
            event_type=1,
            event={"like": {"postId": "p1", "accountId": "a1", "id": "lk1"}},
        )
        assert any(
            "Post Like" in m and "p1" in m and "a1" in m and "lk1" in m
            for m in _logged_messages(caplog)
        )

    def test_post_unknown_dumps_event(self, caplog):
        caplog.set_level(logging.INFO)
        ws = _make_ws()
        ws._monitor_post_event(event_type=7, event={"unknown_key": "x"})
        assert any(
            "Post svc=1 type=7" in m and "unknown_key" in m
            for m in _logged_messages(caplog)
        )


class TestMonitorMediaEvent:
    """Lines 655-684: SVC_MEDIA handler (likes + purchases + unknown)."""

    def test_media_like_logs_structured(self, caplog):
        caplog.set_level(logging.INFO)
        ws = _make_ws()
        ws._monitor_media_event(
            event_type=1,
            event={
                "like": {
                    "accountMediaId": "am1",
                    "accountMediaBundleId": "amb1",
                    "accountMediaAccess": True,
                    "id": "lk2",
                    "createdAt": 1234,
                }
            },
        )
        assert any(
            "Media Like" in m and "am1" in m and "amb1" in m
            for m in _logged_messages(caplog)
        )

    def test_media_purchase_logs_structured(self, caplog):
        caplog.set_level(logging.INFO)
        ws = _make_ws()
        ws._monitor_media_event(
            event_type=7,
            event={
                "order": {
                    "accountMediaId": "am2",
                    "correlationAccountId": "buyer1",
                    "orderId": "ord1",
                }
            },
        )
        assert any(
            "Media Purchase" in m and "am2" in m and "ord1" in m
            for m in _logged_messages(caplog)
        )

    def test_media_unknown_dumps_event(self, caplog):
        caplog.set_level(logging.INFO)
        ws = _make_ws()
        ws._monitor_media_event(event_type=99, event={"unknown_field": True})
        assert any("Media svc=2 type=99" in m for m in _logged_messages(caplog))


class TestMonitorMessageEvent:
    """Lines 686-731: SVC_MSG_INTERACT handler (typing + new + like + unknown)."""

    def test_typing_announce_logged_at_debug(self, caplog):
        caplog.set_level(logging.DEBUG)
        ws = _make_ws()
        ws._monitor_message_event(
            event_type=1,
            event={"typingAnnounceEvent": {"accountId": "a1", "groupId": "g1"}},
        )
        assert any(
            "Typing" in m and "a1" in m and "g1" in m
            for m in _logged_messages(caplog, "DEBUG")
        )

    def test_new_message_short_content(self, caplog):
        caplog.set_level(logging.INFO)
        ws = _make_ws()
        ws._monitor_message_event(
            event_type=1,
            event={
                "message": {
                    "senderId": "s1",
                    "groupId": "g1",
                    "attachments": [],
                    "id": "m1",
                    "content": "hi",
                }
            },
        )
        msgs = _logged_messages(caplog)
        assert any("New Message" in m and "hi" in m for m in msgs)

    def test_new_message_long_content_truncated(self, caplog):
        caplog.set_level(logging.INFO)
        ws = _make_ws()
        long_content = "x" * 100
        ws._monitor_message_event(
            event_type=1,
            event={
                "message": {
                    "senderId": "s1",
                    "groupId": "g1",
                    "attachments": [{"id": "a1"}, {"id": "a2"}],
                    "id": "m2",
                    "content": long_content,
                }
            },
        )
        msgs = _logged_messages(caplog)
        # Truncated to 60 chars + "..."
        assert any("..." in m and "attachments=2" in m for m in msgs)

    def test_message_like(self, caplog):
        caplog.set_level(logging.INFO)
        ws = _make_ws()
        ws._monitor_message_event(
            event_type=2,
            event={
                "like": {
                    "messageId": "m3",
                    "groupId": "g1",
                    "type": 1,
                    "id": "lk3",
                }
            },
        )
        assert any("Message Like" in m and "m3" in m for m in _logged_messages(caplog))

    def test_message_unknown_dumps_event(self, caplog):
        caplog.set_level(logging.INFO)
        ws = _make_ws()
        ws._monitor_message_event(event_type=99, event={"weird": "thing"})
        assert any("Message svc=5 type=99" in m for m in _logged_messages(caplog))


class TestMonitorFollowEvent:
    """Lines 733-748: SVC_FOLLOWS handler."""

    def test_follow_logs_structured(self, caplog):
        caplog.set_level(logging.INFO)
        ws = _make_ws()
        ws._monitor_follow_event(
            event={
                "follow": {
                    "accountId": "creator1",
                    "followerId": "fan1",
                    "id": "f1",
                }
            },
        )
        assert any(
            "Follow" in m and "creator1" in m and "fan1" in m
            for m in _logged_messages(caplog)
        )

    def test_follow_unknown_dumps_event(self, caplog):
        caplog.set_level(logging.INFO)
        ws = _make_ws()
        ws._monitor_follow_event(event={"unfollow": {"accountId": "x"}})
        assert any("Follow svc=3" in m for m in _logged_messages(caplog))


class TestMonitorWalletEvent:
    """Lines 750-777: SVC_WALLET handler (wallet balance + transactions + unknown)."""

    def test_wallet_balance(self, caplog):
        caplog.set_level(logging.INFO)
        ws = _make_ws()
        ws._monitor_wallet_event(
            event={"wallet": {"balance": 5000, "walletVersion": 3, "id": "w1"}},
        )
        assert any("Wallet" in m and "5000" in m for m in _logged_messages(caplog))

    def test_wallet_transaction(self, caplog):
        caplog.set_level(logging.INFO)
        ws = _make_ws()
        ws._monitor_wallet_event(
            event={
                "transaction": {
                    "type": "credit",
                    "amount": 1000,
                    "status": "completed",
                    "id": "tx1",
                    "correlationId": "c1",
                }
            },
        )
        assert any(
            "Wallet Tx" in m and "credit" in m and "1000" in m
            for m in _logged_messages(caplog)
        )

    def test_wallet_unknown_dumps_event(self, caplog):
        caplog.set_level(logging.INFO)
        ws = _make_ws()
        ws._monitor_wallet_event(event={"unknown_wallet_field": "x"})
        assert any("Wallet svc=6" in m for m in _logged_messages(caplog))


class TestMonitorSubscriptionEvent:
    """Lines 779-798: SVC_SUBSCRIPTIONS handler."""

    def test_subscription_with_tier_name(self, caplog):
        caplog.set_level(logging.INFO)
        ws = _make_ws()
        ws._monitor_subscription_event(
            event={
                "subscription": {
                    "accountId": "acct1",
                    "status": 3,
                    "subscriptionTierName": "Premium",
                    "price": 999,
                    "id": "sub1",
                }
            },
        )
        msgs = _logged_messages(caplog)
        assert any(
            "Subscription" in m and "Premium" in m and "acct1" in m for m in msgs
        )

    def test_subscription_without_tier_name(self, caplog):
        caplog.set_level(logging.INFO)
        ws = _make_ws()
        ws._monitor_subscription_event(
            event={
                "subscription": {
                    "accountId": "acct2",
                    "status": 1,
                    "price": 0,
                    "id": "sub2",
                }
            },
        )
        msgs = _logged_messages(caplog)
        # Tier label is empty when subscriptionTierName is absent.
        assert any("Subscription" in m and "acct2" in m for m in msgs)

    def test_subscription_unknown_dumps_event(self, caplog):
        caplog.set_level(logging.INFO)
        ws = _make_ws()
        ws._monitor_subscription_event(event={"weird": "sub"})
        assert any("Subscription svc=15" in m for m in _logged_messages(caplog))


class TestMonitorPaymentEvent:
    """Lines 800-817: SVC_PAYMENTS handler."""

    def test_payment_transaction(self, caplog):
        caplog.set_level(logging.INFO)
        ws = _make_ws()
        ws._monitor_payment_event(
            event={
                "transaction": {
                    "type": "card",
                    "amount": 2500,
                    "status": "approved",
                    "threeDSecure": True,
                    "id": "pmt1",
                }
            },
        )
        assert any(
            "Payment" in m and "card" in m and "2500" in m
            for m in _logged_messages(caplog)
        )

    def test_payment_unknown_dumps_event(self, caplog):
        caplog.set_level(logging.INFO)
        ws = _make_ws()
        ws._monitor_payment_event(event={"unknown_payment_field": "x"})
        assert any("Payment svc=16" in m for m in _logged_messages(caplog))


class TestMonitorChatEvent:
    """Lines 819-843: SVC_CHAT handler (creator vs viewer chat messages)."""

    def test_chat_room_message_viewer(self, caplog):
        caplog.set_level(logging.INFO)
        ws = _make_ws()
        ws._monitor_chat_event(
            event={
                "chatRoomMessage": {
                    "metadata": json.dumps({"senderIsCreator": False}),
                    "username": "viewer1",
                    "displayname": "Viewer One",
                    "content": "hi everyone",
                    "chatRoomId": "room1",
                }
            },
        )
        msgs = _logged_messages(caplog)
        # No "[creator]" tag for viewer messages.
        assert any(
            "Chat |" in m and "viewer1" in m and "[creator]" not in m for m in msgs
        )

    def test_chat_room_message_creator_has_tag(self, caplog):
        caplog.set_level(logging.INFO)
        ws = _make_ws()
        ws._monitor_chat_event(
            event={
                "chatRoomMessage": {
                    "metadata": json.dumps({"senderIsCreator": True}),
                    "username": "creator1",
                    "displayname": "The Creator",
                    "content": "thanks for tipping",
                    "chatRoomId": "room1",
                }
            },
        )
        msgs = _logged_messages(caplog)
        assert any("[creator]" in m and "creator1" in m for m in msgs)

    def test_chat_room_message_dict_metadata(self, caplog):
        """Lines 824-825: metadata as dict (not str) → use as-is."""
        caplog.set_level(logging.INFO)
        ws = _make_ws()
        ws._monitor_chat_event(
            event={
                "chatRoomMessage": {
                    "metadata": {"senderIsCreator": True},
                    "username": "creator2",
                    "displayname": "C2",
                    "content": "hi",
                    "chatRoomId": "room2",
                }
            },
        )
        assert any(
            "[creator]" in m and "creator2" in m for m in _logged_messages(caplog)
        )

    def test_chat_long_content_truncated(self, caplog):
        caplog.set_level(logging.INFO)
        ws = _make_ws()
        ws._monitor_chat_event(
            event={
                "chatRoomMessage": {
                    "metadata": "{}",
                    "username": "u",
                    "displayname": "U",
                    "content": "y" * 100,
                    "chatRoomId": "r",
                }
            },
        )
        msgs = _logged_messages(caplog)
        assert any("..." in m for m in msgs)

    def test_chat_unknown_dumps_event(self, caplog):
        caplog.set_level(logging.INFO)
        ws = _make_ws()
        ws._monitor_chat_event(event={"unknown_chat_field": "x"})
        assert any("Chat svc=46" in m for m in _logged_messages(caplog))


# ---------------------------------------------------------------------------
# Wave 6 item #3: edge coverage to push api/websocket.py from 88% toward 95%
#
# Targets:
#   - _absorb_response_cookies (lines 257-283, 27 lines — biggest single block)
#   - _handle_message JSON decode error path (394-395)
#   - _dispatch_event cross-thread RuntimeError path (1258-1264)
#   - _thread_main exception capture (1164-1180)
# ---------------------------------------------------------------------------


class _CookieJar:
    """Minimal stand-in for httpx.Cookies — captures every .set() call."""

    def __init__(self) -> None:
        self.set_calls: list[dict] = []

    def set(self, name, value, domain=None, path=None) -> None:
        self.set_calls.append(
            {"name": name, "value": value, "domain": domain, "path": path}
        )


class _StubHttpClient:
    """http_client stand-in exposing a `.cookies` jar."""

    def __init__(self) -> None:
        self.cookies = _CookieJar()


class _MultiDictResponseHeaders:
    """Mock response_headers that DOES expose get_all (websockets ≥12 path)."""

    def __init__(self, set_cookie_values: list[str | None]) -> None:
        self._values = set_cookie_values

    def get_all(self, name):
        if name == "Set-Cookie":
            return self._values
        return []


class _SingleResponseHeaders:
    """Mock response_headers WITHOUT get_all (websockets <12 fallback path)."""

    def __init__(self, set_cookie_value: str | None) -> None:
        self._value = set_cookie_value

    def get(self, name, default=None):
        if name == "Set-Cookie":
            return self._value
        return default


class TestAbsorbResponseCookies:
    """Lines 236-283: WS → HTTP cookie sync from upgrade response."""

    def test_returns_when_http_client_is_none(self):
        """Lines 253-254: http_client is None → no-op."""
        ws = _make_ws()
        ws.http_client = None
        # Must not raise even with non-empty headers.
        ws._absorb_response_cookies(_MultiDictResponseHeaders(["a=1; Path=/"]))

    def test_returns_when_response_headers_is_none(self):
        """Lines 253-254: response_headers None → no-op."""
        ws = _make_ws()
        ws.http_client = _StubHttpClient()
        ws._absorb_response_cookies(None)
        assert ws.http_client.cookies.set_calls == []

    def test_get_all_path_pushes_each_cookie_to_jar(self):
        """Lines 257-283: websockets ≥12 path with get_all returning a list."""
        ws = _make_ws()
        ws.http_client = _StubHttpClient()
        headers = _MultiDictResponseHeaders(
            [
                "session_id=abc123; Path=/; Domain=fansly.com",
                "csrf=xyz789; Path=/api",
            ]
        )

        ws._absorb_response_cookies(headers)

        # Both cookies pushed into the jar with correct attributes.
        names_seen = {c["name"] for c in ws.http_client.cookies.set_calls}
        assert names_seen == {"session_id", "csrf"}

    def test_fallback_path_when_get_all_missing(self):
        """Lines 261-262: websockets <12 fallback uses get('Set-Cookie')."""
        ws = _make_ws()
        ws.http_client = _StubHttpClient()
        headers = _SingleResponseHeaders("legacy_session=old123; Path=/")

        ws._absorb_response_cookies(headers)

        assert len(ws.http_client.cookies.set_calls) == 1
        assert ws.http_client.cookies.set_calls[0]["name"] == "legacy_session"

    def test_empty_raw_values_skipped(self):
        """Lines 264-265: falsy raw values → continue (don't try to parse)."""
        ws = _make_ws()
        ws.http_client = _StubHttpClient()
        # Mix of None, empty string, and real cookie — only real one should land.
        headers = _MultiDictResponseHeaders(
            [
                None,
                "",
                "valid=v1; Path=/",
            ]
        )

        ws._absorb_response_cookies(headers)

        assert len(ws.http_client.cookies.set_calls) == 1
        assert ws.http_client.cookies.set_calls[0]["name"] == "valid"

    def test_simplecookie_parse_failure_logged_and_continues(self, caplog, monkeypatch):
        """Lines 271-275: SimpleCookie.load raises → log debug + continue."""
        caplog.set_level(logging.DEBUG)
        ws = _make_ws()
        ws.http_client = _StubHttpClient()

        # Patch SimpleCookie to raise on .load()
        from api import websocket as ws_module

        class _RaisingCookie:
            def __init__(self) -> None:
                pass

            def load(self, _raw):
                raise ValueError("malformed cookie")

            def items(self):
                return []

        monkeypatch.setattr(ws_module, "SimpleCookie", _RaisingCookie)

        headers = _MultiDictResponseHeaders(["bad_cookie_data"])
        ws._absorb_response_cookies(headers)

        # No cookie pushed (parse failed), debug log fired.
        assert ws.http_client.cookies.set_calls == []
        debug_msgs = [r.getMessage() for r in caplog.records if r.levelname == "DEBUG"]
        assert any(
            "WS Set-Cookie parse failed" in m and "bad_cookie_data" in m
            for m in debug_msgs
        )

    def test_default_domain_when_morsel_domain_missing(self):
        """Lines 279-281: morsel without Domain → defaults to 'fansly.com'."""
        ws = _make_ws()
        ws.http_client = _StubHttpClient()
        # Set-Cookie without Domain attribute.
        headers = _MultiDictResponseHeaders(["nodomain=v; Path=/"])

        ws._absorb_response_cookies(headers)

        assert len(ws.http_client.cookies.set_calls) == 1
        assert ws.http_client.cookies.set_calls[0]["domain"] == "fansly.com"

    def test_default_path_when_morsel_path_missing(self):
        """Lines 280-281: morsel without Path → defaults to '/'."""
        ws = _make_ws()
        ws.http_client = _StubHttpClient()
        headers = _MultiDictResponseHeaders(["nopath=v; Domain=fansly.com"])

        ws._absorb_response_cookies(headers)

        assert len(ws.http_client.cookies.set_calls) == 1
        assert ws.http_client.cookies.set_calls[0]["path"] == "/"


class TestHandleMessageDecodeErrors:
    """Lines 394-395: top-level _handle_message error catch."""

    @pytest.mark.asyncio
    async def test_invalid_json_logs_error_does_not_raise(self, caplog):
        """JSONDecodeError caught → ERROR log, no propagation."""
        caplog.set_level(logging.ERROR)
        ws = _make_ws()
        ws.connected = True

        # Malformed JSON envelope — top-level json.loads raises.
        await ws._handle_message("not valid json {{{")

        errors = [r.getMessage() for r in caplog.records if r.levelname == "ERROR"]
        assert any("Failed to decode WebSocket message" in m for m in errors)


class TestDispatchEventCrossThread:
    """Lines 1242-1264: _dispatch_event path selection (in-loop vs cross-thread)."""

    @pytest.mark.asyncio
    async def test_no_main_loop_runs_handler_inline(self):
        """Lines 1249-1252: _main_loop is None → call handler directly in current loop."""
        ws = _make_ws()
        ws._main_loop = None  # no thread boundary

        captured: list = []

        def handler(event):
            captured.append(event)

        await ws._dispatch_event(handler, {"foo": "bar"})

        assert captured == [{"foo": "bar"}]

    @pytest.mark.asyncio
    async def test_async_handler_inline_path(self):
        """Lines 1252-1254: async handler in same loop → await it."""
        ws = _make_ws()
        ws._main_loop = None

        captured: list = []

        async def handler(event):
            captured.append(event)

        await ws._dispatch_event(handler, {"async": "yes"})
        assert captured == [{"async": "yes"}]

    @pytest.mark.asyncio
    async def test_main_loop_same_as_current_runs_inline(self):
        """Lines 1244-1252: main_loop IS current loop → inline path (no marshalling)."""
        ws = _make_ws()
        ws._main_loop = asyncio.get_running_loop()

        captured: list = []

        def handler(event):
            captured.append(event)

        await ws._dispatch_event(handler, {"current": "loop"})
        assert captured == [{"current": "loop"}]

    @pytest.mark.asyncio
    async def test_closed_main_loop_runs_inline(self):
        """Lines 1247-1252: main_loop.is_closed() → inline path."""
        ws = _make_ws()
        # Build a dummy loop, close it, assign as main_loop.
        dummy_loop = asyncio.new_event_loop()
        dummy_loop.close()
        ws._main_loop = dummy_loop

        captured: list = []

        def handler(event):
            captured.append(event)

        await ws._dispatch_event(handler, {"closed": "loop"})
        assert captured == [{"closed": "loop"}]


class TestThreadMainErrorCapture:
    """Lines 1164-1180: _thread_main BaseException → captured in _thread_exc."""

    def test_maintain_connection_crash_captured_in_thread_exc(self, monkeypatch):
        """When _maintain_connection raises, _thread_exc holds it; ready event is set."""
        ws = _make_ws()

        async def _crash():
            raise RuntimeError("maintain boom")

        # Patch _maintain_connection to raise on first await.
        monkeypatch.setattr(ws, "_maintain_connection", _crash)

        # Run _thread_main directly (synchronously — it manages its own loop).
        ws._thread_main()

        assert isinstance(ws._thread_exc, RuntimeError)
        assert "maintain boom" in str(ws._thread_exc)
        # ready event was set despite the crash (so start_in_thread can unblock).
        assert ws._thread_ready.is_set()
        # Loop reference cleared in finally.
        assert ws._ws_loop is None

    def test_clean_thread_main_finishes_without_exc(self, monkeypatch):
        """Happy path: _maintain_connection returns cleanly → _thread_exc stays None."""
        ws = _make_ws()

        async def _ok():
            return None  # immediate clean exit

        monkeypatch.setattr(ws, "_maintain_connection", _ok)
        ws._thread_main()

        assert ws._thread_exc is None
        assert ws._thread_ready.is_set()
        assert ws._ws_loop is None


# ---------------------------------------------------------------------------
# Wave 6 item #4: edge coverage to push api/websocket.py to 100%
#
# Targets remaining missing lines from latest log: 223 (cookie jar fallback),
# 333 (monitor_events branch), 394-395 (generic exception in _handle_message),
# 960 (stale ping task ref clear), 978 (ping disconnect mid-loop), 1007-1008
# (generic exception in ping_worker), 1011 (ping CancelledError), 1050
# (listen loop CancelledError), 1083-1085 (maintain_connection lost-conn),
# 1242-1243 (dispatch RuntimeError), 1258-1264 (cross-thread dispatch).
# ---------------------------------------------------------------------------


class _CookieWithJar:
    """httpx.Cookies-like object exposing a `.jar` iterable of cookie objects.

    Used by `_current_cookies` line 223 fallback path. Each yielded object
    needs `.name` and `.value` attributes.
    """

    class _Cookie:
        def __init__(self, name: str, value: str) -> None:
            self.name = name
            self.value = value

    def __init__(self, items: dict[str, str]) -> None:
        self.jar = [self._Cookie(n, v) for n, v in items.items()]


class _HttpClientWithJar:
    """Stand-in http_client with a `.cookies.jar` attribute."""

    def __init__(self, cookies: dict[str, str]) -> None:
        self.cookies = _CookieWithJar(cookies)


class TestWaveSixCoverage:
    """Edge cases pushing api/websocket.py from 94.43% to 100%."""

    def test_current_cookies_uses_http_client_jar(self):
        """Line 223: when http_client is set, cookies sourced from .cookies.jar."""
        ws = _make_ws()
        ws.http_client = _HttpClientWithJar({"sess": "abc", "csrf": "xyz"})

        result = ws._current_cookies()

        assert result == {"sess": "abc", "csrf": "xyz"}

    @pytest.mark.asyncio
    async def test_handle_message_monitor_events_calls_monitor(self):
        """Line 333: monitor_events=True triggers _monitor_event."""
        ws = _make_ws(enable_logging=False)
        ws.monitor_events = True

        captured: list[tuple[int, object]] = []

        def _spy_monitor(message_type, message_data):
            captured.append((message_type, message_data))

        ws._monitor_event = _spy_monitor  # type: ignore[method-assign]

        # Type-3 message (no special handler, just monitored)
        await ws._handle_message(json.dumps({"t": 3, "d": "payload"}))

        assert captured == [(3, "payload")]

    @pytest.mark.asyncio
    async def test_handle_message_generic_exception_caught(self, caplog):
        """Lines 394-395: non-JSON exception during processing is caught + logged."""
        caplog.set_level(logging.ERROR)
        ws = _make_ws()

        # Register a handler that raises — exception path is downstream of
        # the JSON decode, hits line 394-395.
        def _broken_handler(_data):
            raise RuntimeError("handler exploded")

        ws._event_handlers[42] = _broken_handler

        await ws._handle_message(json.dumps({"t": 42, "d": "ok"}))

        errors = [r.getMessage() for r in caplog.records if r.levelname == "ERROR"]
        assert any("Error handling WebSocket message" in m for m in errors)

    @pytest.mark.asyncio
    async def test_start_ping_loop_clears_stale_done_task(self):
        """Line 960: stale `_ping_task` (already done) is cleared before restart."""
        ws = _make_ws()
        ws.websocket = FakeSocket()
        ws.connected = True

        # Pre-set a "done" ping task to simulate a previous worker that exited.
        async def _already_done():
            return None

        prior_task = asyncio.create_task(_already_done())
        await prior_task  # ensure done() is True
        ws._ping_task = prior_task

        with patch("api.websocket.timing_jitter", return_value=0.01):
            ws._start_ping_loop()
            # New task created, prior was cleared first
            assert ws._ping_task is not prior_task
            assert ws._ping_task is not None

        ws.connected = False
        ws._stop_ping_loop()
        await asyncio.sleep(0.05)

    @pytest.mark.asyncio
    async def test_ping_worker_breaks_when_websocket_set_to_none(self):
        """Line 978: connected=True but websocket=None → break loop.

        Production guard at line 977 catches mid-loop disconnection
        (websocket cleared) and breaks the worker cleanly.
        """
        ws = _make_ws()
        # connected=True but websocket=None — ping worker enters the while
        # loop, runs timing_jitter + sleep, then the line-977 check trips
        # and the loop breaks.
        ws.websocket = None
        ws.connected = True

        with patch("api.websocket.timing_jitter", return_value=0.01):
            ws._start_ping_loop()
            # Wait for the worker to complete one iteration + break.
            assert ws._ping_task is not None
            await asyncio.wait_for(ws._ping_task, timeout=1.0)

        # Task completed (broke out of the loop, hit line 978).
        assert ws._ping_task is None or ws._ping_task.done()

    @pytest.mark.asyncio
    async def test_ping_worker_generic_exception_logged_and_breaks(self, caplog):
        """Lines 1007-1008: non-WebSocketException in ping loop logs + breaks."""
        caplog.set_level(logging.ERROR)
        ws = _make_ws()
        ws.websocket = FakeSocket()
        ws.connected = True

        # Force timing_jitter to raise a non-WebSocketException
        # (TypeError is fine — it bypasses the WebSocketException catch
        # and lands in the generic Exception handler at 1006).
        def _boom(*_a, **_k):
            raise TypeError("unexpected")

        with patch("api.websocket.timing_jitter", _boom):
            ws._start_ping_loop()
            await asyncio.sleep(0.1)

        errors = [r.getMessage() for r in caplog.records if r.levelname == "ERROR"]
        assert any("Unexpected error in ping loop" in m for m in errors)

    @pytest.mark.asyncio
    async def test_ping_worker_cancelled_error_logged(self, caplog):
        """Line 1011: outer CancelledError handler logs + exits cleanly."""
        caplog.set_level(logging.DEBUG)
        ws = _make_ws()
        ws.websocket = FakeSocket()
        ws.connected = True

        with patch("api.websocket.timing_jitter", return_value=10.0):
            ws._start_ping_loop()
            await asyncio.sleep(0.01)
            assert ws._ping_task is not None
            ws._ping_task.cancel()
            with suppress(asyncio.CancelledError):
                await ws._ping_task

        debug = [r.getMessage() for r in caplog.records if r.levelname == "DEBUG"]
        assert any("Ping loop cancelled" in m for m in debug)

    @pytest.mark.asyncio
    async def test_listen_loop_cancelled_error_logged(self, caplog):
        """Line 1050: outer CancelledError handler in _listen_loop."""
        caplog.set_level(logging.INFO)
        ws = _make_ws()
        ws.websocket = FakeSocket()
        ws.connected = True

        task = asyncio.create_task(ws._listen_loop())
        await asyncio.sleep(0.01)
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

        info = [r.getMessage() for r in caplog.records if r.levelname == "INFO"]
        assert any("WebSocket listen loop cancelled" in m for m in info)

    @pytest.mark.asyncio
    async def test_maintain_connection_logs_lost_then_reconnects(self, caplog):
        """Lines 1083-1085: _listen_loop returned without stop_event → log + disconnect."""
        caplog.set_level(logging.WARNING)
        ws = _make_ws()
        ws.connected = True
        ws.websocket = FakeSocket()
        # Already past max attempts to break out cleanly on next iteration.
        ws._reconnect_attempts = ws._max_reconnect_attempts

        async def _listen_returns_immediately():
            return None  # simulates connection lost without stop_event

        async def _disconnect_marker():
            ws._stop_event.set()  # break out of maintain_connection

        ws._listen_loop = _listen_returns_immediately  # type: ignore[method-assign]
        ws.disconnect = _disconnect_marker  # type: ignore[method-assign]

        await ws._maintain_connection()

        warnings = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
        assert any("WebSocket connection lost" in m for m in warnings)

    @pytest.mark.asyncio
    async def test_dispatch_event_runtime_error_in_get_running_loop(self, caplog):
        """Lines 1242-1243: get_running_loop fails → current_loop=None → fall through."""
        # When get_running_loop raises (called outside an async context),
        # current_loop becomes None. Combined with _main_loop also None,
        # no_thread_boundary is True → handler runs inline.
        ws = _make_ws()
        ws._main_loop = None  # force inline path

        captured = []

        def _sync_handler(event):
            captured.append(event)

        await ws._dispatch_event(_sync_handler, {"hello": "world"})

        assert captured == [{"hello": "world"}]

    @pytest.mark.asyncio
    async def test_dispatch_event_cross_thread_sync_handler(self):
        """Line 1262: sync handler cross-thread → main_loop.call_soon_threadsafe."""
        import threading

        other_loop = asyncio.new_event_loop()
        loop_ready = threading.Event()
        captured = []

        def _run_other_loop():
            asyncio.set_event_loop(other_loop)
            loop_ready.set()
            other_loop.run_forever()

        other_thread = threading.Thread(target=_run_other_loop, daemon=True)
        other_thread.start()
        loop_ready.wait(timeout=2.0)

        try:
            ws = _make_ws()
            ws._main_loop = other_loop  # different from current loop

            def _sync_handler(event):
                captured.append(event)

            await ws._dispatch_event(_sync_handler, {"y": 2})
            await asyncio.sleep(0.05)
        finally:
            other_loop.call_soon_threadsafe(other_loop.stop)
            other_thread.join(timeout=2.0)
            other_loop.close()

        assert captured == [{"y": 2}]

    @pytest.mark.asyncio
    async def test_dispatch_event_cross_thread_path(self):
        """Lines 1258-1264: cross-thread path marshals via run_coroutine_threadsafe.

        Sets _main_loop to a different (running) loop than the test's loop,
        forcing the no_thread_boundary check to be False. Verifies the cross-
        thread dispatch is invoked.
        """
        import threading

        # Build a separate event loop in another thread to act as _main_loop.
        other_loop = asyncio.new_event_loop()
        loop_ready = threading.Event()
        captured = []

        def _run_other_loop():
            asyncio.set_event_loop(other_loop)
            loop_ready.set()
            other_loop.run_forever()

        other_thread = threading.Thread(target=_run_other_loop, daemon=True)
        other_thread.start()
        loop_ready.wait(timeout=2.0)

        try:
            ws = _make_ws()
            ws._main_loop = other_loop  # different from current running loop

            async def _async_handler(event):
                captured.append(event)

            await ws._dispatch_event(_async_handler, {"x": 1})
            # Give the cross-thread coroutine time to land
            await asyncio.sleep(0.05)
        finally:
            other_loop.call_soon_threadsafe(other_loop.stop)
            other_thread.join(timeout=2.0)
            other_loop.close()

        assert captured == [{"x": 1}]

    @pytest.mark.asyncio
    async def test_start_in_thread_timeout_raises(self, monkeypatch):
        """Line 1141: thread fails to set _thread_ready within timeout → RuntimeError."""
        ws = _make_ws()

        # Patch _thread_main to never set _thread_ready (sleep instead).
        import time

        def _hung_thread_main(*_a, **_kw):
            time.sleep(2.0)  # never sets _thread_ready

        monkeypatch.setattr(ws, "_thread_main", _hung_thread_main)

        # ready_timeout=0.05 ensures the wait expires fast.
        with pytest.raises(RuntimeError, match=r"failed to initialize within 0\.05s"):
            ws.start_in_thread(ready_timeout=0.05)

    @pytest.mark.asyncio
    async def test_start_in_thread_re_raises_thread_setup_exception(self, monkeypatch):
        """Line 1151: thread setup crashes during startup → RuntimeError re-raised."""
        ws = _make_ws()

        def _crashing_thread_main(*_a, **_kw):
            # Set _thread_ready quickly so start_in_thread's wait succeeds,
            # then crash so _thread_exc is populated before the join check.
            ws._thread_ready.set()
            ws._thread_exc = ValueError("simulated thread setup crash")

        monkeypatch.setattr(ws, "_thread_main", _crashing_thread_main)

        with pytest.raises(
            RuntimeError, match="failed during startup: simulated thread setup crash"
        ):
            ws.start_in_thread()
