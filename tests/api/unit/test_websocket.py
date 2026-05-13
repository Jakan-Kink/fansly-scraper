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

from api.websocket import _ChildWebSocket as FanslyWebSocket
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

        assert ctx.check_hostname is True
        assert ctx.verify_mode == ssl.CERT_REQUIRED
        assert ctx.protocol == ssl.PROTOCOL_TLS_CLIENT


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
        """Ping timeout detection disconnects (lines 457-467).

        Note: ``_start_ping_loop()`` resets ``_last_ping_response = now`` to
        avoid spurious timeout on a fresh connection. The test must set the
        stale-response value AFTER that reset, not before.
        """
        ws = _make_ws()
        fake = FakeSocket()
        ws.websocket = fake
        ws.connected = True
        ws._last_connection_reset = 0.0  # > 15s ago → bypasses cooldown gate

        with patch("api.websocket.timing_jitter", return_value=0.01):
            ws._start_ping_loop()
            # Override AFTER _start_ping_loop's reset to simulate
            # "no ping response in a long time" — first iter fires timeout.
            ws._last_ping_response = 0.0
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
        """CancelledError in maintenance loop is handled (lines 562-564)."""
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


class TestDispatchEvent:
    """_dispatch_event invokes the handler directly on the running loop."""

    @pytest.mark.asyncio
    async def test_sync_handler_invoked_inline(self):
        ws = _make_ws()
        captured: list = []

        def handler(event):
            captured.append(event)

        await ws._dispatch_event(handler, {"k": "v"})
        assert captured == [{"k": "v"}]

    @pytest.mark.asyncio
    async def test_async_handler_awaited_inline(self):
        ws = _make_ws()
        captured: list = []

        async def handler(event):
            captured.append(event)

        await ws._dispatch_event(handler, {"async": "yes"})
        assert captured == [{"async": "yes"}]


# ---------------------------------------------------------------------------
# Wave 6 item #3: edge coverage to push api/websocket.py from 88% toward 95%
#
# Targets:
#   - _absorb_response_cookies (lines 257-283, 27 lines — biggest single block)
#   - _handle_message JSON decode error path (394-395)
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


# ---------------------------------------------------------------------------
# Wave 6 item #4: edge coverage to push api/websocket.py to 100%
#
# Targets remaining missing lines from latest log: 223 (cookie jar fallback),
# 394-395 (generic exception in _handle_message),
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
