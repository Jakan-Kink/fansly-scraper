"""Tests for api/websocket.py — FanslyWebSocket protocol handler.

External boundary: websockets.client.connect (patched with FakeSocket).
Everything else — message dispatch, ping logic, reconnect, auth — runs real code.
"""

import asyncio
import json
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
        self.closed = False

    async def send(self, message: str) -> None:
        self.sent.append(message)

    async def recv(self) -> str:
        if self._recv_queue:
            return self._recv_queue.pop(0)
        # Block forever (simulates waiting for server message)
        await asyncio.sleep(999)
        return ""  # unreachable

    async def close(self) -> None:
        self.closed = True


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


class TestContextManager:
    """Lines 571-583: async context manager."""

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
