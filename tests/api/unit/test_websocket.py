"""Tests for api/websocket.py — FanslyWebSocket protocol handler.

External boundary: websockets.asyncio.client.connect (patched with FakeSocket).
Everything else — message dispatch, ping logic, reconnect, auth — runs real code.
"""

import asyncio
import json
import logging
import ssl
from contextlib import suppress
from unittest.mock import patch

import pytest
from pydantic import JsonValue
from websockets.exceptions import ConnectionClosed, ConnectionClosedOK
from websockets.frames import Close

from api.websocket import _ChildWebSocket as FanslyWebSocket
from tests.fixtures.api import make_child_ws_for
from tests.fixtures.api.fake_websocket import FakeSocket


def _make_ws(*, enable_logging=False, on_unauthorized=None, on_rate_limited=None):
    return FanslyWebSocket(
        token="test_token",
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
        ws = FanslyWebSocket(token="t", user_agent="ua")
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
        received: list[JsonValue] = []
        ws.register_handler(10000, received.append)

        event = {"serviceId": 1, "action": "create", "data": {"id": "99"}}
        await ws._handle_message(_msg(10000, json.dumps(event)))

        assert len(received) == 1
        assert isinstance(received[0], dict)
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
        received: list[JsonValue] = []
        ws.register_handler(10000, received.append)

        batch = [
            {"t": 10000, "d": json.dumps({"event": "a"})},
            {"t": 10000, "d": json.dumps({"event": "b"})},
            {"t": 2, "d": '{"lastPing": 0}'},  # ping mixed in
        ]
        await ws._handle_message(_msg(10001, batch))

        assert len(received) == 2
        assert isinstance(received[0], dict)
        assert isinstance(received[1], dict)
        assert received[0]["event"] == "a"
        assert received[1]["event"] == "b"

    @pytest.mark.asyncio
    async def test_custom_handler_sync_and_async(self):
        """Custom registered handlers — sync and async."""
        ws = _make_ws()
        sync_received: list[JsonValue] = []
        async_received: list[JsonValue] = []

        ws.register_handler(99, sync_received.append)

        async def async_handler(data: JsonValue) -> None:
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
    @pytest.mark.parametrize(
        ("code", "callback_kind", "expect_disconnect", "expect_fired"),
        [
            pytest.param(401, "sync", True, True, id="401-sync-callback"),
            pytest.param(401, "async", True, True, id="401-async-callback"),
            pytest.param(401, "none", True, False, id="401-no-callback"),
            pytest.param(429, "sync", False, True, id="429-sync-callback"),
            pytest.param(429, "async", False, True, id="429-async-callback"),
            pytest.param(429, "none", False, False, id="429-no-callback"),
        ],
    )
    async def test_handle_error_event(
        self, code, callback_kind, expect_disconnect, expect_fired
    ):
        """401 disconnects and fires on_unauthorized; 429 stays connected and
        fires on_rate_limited; sync/async/missing callbacks all handled."""
        called = []

        def _sync_cb():
            called.append("fired")

        async def _async_cb():
            called.append("fired")

        callback = {"sync": _sync_cb, "async": _async_cb, "none": None}[callback_kind]
        kwargs = (
            {"on_unauthorized": callback}
            if code == 401
            else {"on_rate_limited": callback}
        )
        ws = _make_ws(**kwargs)
        ws.connected = True
        ws.session_id = "s"

        await ws._handle_error_event({"code": code})

        assert called == (["fired"] if expect_fired else [])
        assert ws.connected is (not expect_disconnect)


class TestConnectDisconnect:
    """connect/disconnect — happy path + auth-failure run against the
    scripted-responder ``ws_server``; connection-injection failures
    (raise on connect) stay patched because the scripted server can't
    simulate them without a bogus port.
    """

    @pytest.mark.asyncio
    async def test_connect_and_disconnect(self, ws_server):
        """Full connect → auth → ping loop start → disconnect cycle."""
        ws_server.set_session(id="123", websocket_session_id="456", account_id="789")
        ws = make_child_ws_for(ws_server.base_url)

        await ws.connect()

        assert ws.connected is True
        assert ws.session_id == "123"
        assert ws._ping_task is not None

        # Auth frame observable on the scripted responder
        assert ws_server.auth_event.is_set()
        auth_frames = ws_server.frames_of_type(1)
        assert len(auth_frames) == 1
        assert auth_frames[0]["t"] == 1

        await ws.disconnect()
        assert ws.connected is False
        assert ws.session_id is None

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
    async def test_connect_auth_failure(self, ws_server):
        """Auth response without session ID → RuntimeError."""
        ws_server.auto_ack = False
        # Manually queue a malformed t=1 with empty session dict
        ws_server.push({"t": 1, "d": json.dumps({"session": {}})})

        ws = make_child_ws_for(ws_server.base_url)

        with pytest.raises(RuntimeError, match="Failed to authenticate"):
            await ws.connect()

        assert ws.connected is False

    @pytest.mark.asyncio
    async def test_connect_exception(self):
        """Connection failure → connected=False, exception propagates.

        Stays patched: simulating ``raise OSError`` from the connect call
        is failure-injection that the scripted responder doesn't provide.
        """
        ws = _make_ws()

        async def fail_connect(**kwargs):
            raise OSError("refused")

        with (
            patch("api.websocket.ws_connect", side_effect=fail_connect),
            pytest.raises(OSError),
        ):
            await ws.connect()

        assert ws.connected is False


class TestSendMessage:
    """Lines 558-569: send_message."""

    @pytest.mark.asyncio
    async def test_send_message(self, ws_server):
        """send_message encodes the envelope and writes it to the wire.

        Drives a real connect + send through the scripted responder so
        the encoded frame is observable via ``ws_server.received``.
        """
        ws = make_child_ws_for(ws_server.base_url, enable_logging=True)
        await ws.connect()

        await ws.send_message(5, {"hello": "world"})

        # Allow a tick for the send to land on the server
        await asyncio.sleep(0.05)

        type5_frames = ws_server.frames_of_type(5)
        assert len(type5_frames) == 1
        assert json.loads(type5_frames[0]["d"]) == {"hello": "world"}

        await ws.disconnect()

    @pytest.mark.asyncio
    async def test_send_message_not_connected(self):
        ws = _make_ws()
        with pytest.raises(RuntimeError, match="not connected"):
            await ws.send_message(1, "data")


class TestPingLoop:
    """Lines 425-498: ping worker, start/stop, timeout detection."""

    @pytest.mark.asyncio
    async def test_ping_sends_p(self, ws_server):
        """Ping loop sends bare 'p' to websocket (line 469).

        Connect via the scripted responder so the ping loop runs against
        the real wire; observe the bare 'p' via ``received_raw`` (non-JSON
        frames don't land in the parsed ``received`` deque).
        """
        with patch("api.websocket.timing_jitter", return_value=0.05):
            ws = make_child_ws_for(ws_server.base_url, enable_logging=True)
            await ws.connect()

            # connect() starts the ping loop. A second start should warn
            # but not create a second task (lines 433-434).
            ws._start_ping_loop()

            await asyncio.sleep(0.15)

            await ws.disconnect()

        # Verify at least one bare 'p' was sent to the wire
        assert "p" in ws_server.received_raw

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
    @pytest.mark.parametrize(
        ("make_exc", "forbid_error_log"),
        [
            pytest.param(
                lambda: ConnectionClosed(Close(1006, "gone"), None),
                False,
                id="websocket-error",
            ),
            pytest.param(lambda: RuntimeError("boom"), False, id="unexpected-error"),
            pytest.param(
                lambda: ConnectionClosedOK(Close(1000, "OK"), Close(1000, "OK")),
                True,
                id="connection-closed-ok",
            ),
        ],
    )
    async def test_listen_loop_recv_raises(self, caplog, make_exc, forbid_error_log):
        """recv raising exits the loop with connected=False (lines 519-528).

        Covers the WebSocketException branch (ConnectionClosed), the generic
        Exception branch (RuntimeError), and the Phase 2 regression guard:
        an intentional close (ConnectionClosedOK, code 1000) must NOT produce
        the old false-error "WebSocket error in listen loop" ERROR log.
        """
        caplog.set_level(logging.WARNING)
        ws = _make_ws()
        ws.connected = True

        class RaisingSocket(FakeSocket):
            async def recv(self):
                raise make_exc()

        ws.websocket = RaisingSocket()
        await ws._listen_loop()

        assert ws.connected is False
        if forbid_error_log:
            error_msgs = [
                r.getMessage() for r in caplog.records if r.levelname == "ERROR"
            ]
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

        with patch("api.websocket.ws_connect", side_effect=fail_connect):
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

        with patch("api.websocket.ws_connect", side_effect=hang_connect):
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

    def set(
        self,
        name: str,
        value: str,
        domain: str | None = None,
        path: str | None = None,
    ) -> None:
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
        """Real _listen_loop returns (connection lost) → warning + real disconnect.

        No method-replacement: the REAL ``_listen_loop``, ``_maintain_connection``
        and ``disconnect`` all run. The transport ``FakeSocket`` is the only edge —
        its ``recv`` raises ``ConnectionClosedOK`` immediately (as the real
        ``websockets`` client does when the peer closes), so the real
        ``_listen_loop`` takes its ``WebSocketException`` branch, sets
        ``connected = False`` and returns. Back in ``_maintain_connection`` the
        ``stop_event`` is unset, so it logs "WebSocket connection lost" and calls
        the real ``disconnect``. The loop is bounded by pre-setting
        ``_reconnect_attempts`` to ``_max_reconnect_attempts``: the next iteration
        finds the connection down and hits the max-attempts break. Mirrors the
        real ``ws_connect``/``FakeSocket`` transport-edge pattern used elsewhere
        in this file.
        """
        caplog.set_level(logging.WARNING)

        class _PeerClosedSocket(FakeSocket):
            """recv immediately reports the peer closed — natural listen-loop exit."""

            async def recv(self) -> str:
                raise ConnectionClosedOK(Close(1000, "OK"), Close(1000, "OK"))

        ws = _make_ws()
        ws.connected = True
        ws.websocket = _PeerClosedSocket()
        # Already at max attempts: after the lost-connection disconnect, the next
        # maintain iteration hits the max-attempts break and exits cleanly.
        ws._reconnect_attempts = ws._max_reconnect_attempts

        await asyncio.wait_for(ws._maintain_connection(), timeout=5.0)

        warnings = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
        assert any("WebSocket connection lost" in m for m in warnings)
        # Real _listen_loop's WebSocketException branch already flipped
        # connected → False; the real disconnect then takes its "not connected"
        # early-return branch (also a real code path). Either way the loop
        # exited via the max-attempts break, not a hang.
        assert ws.connected is False
