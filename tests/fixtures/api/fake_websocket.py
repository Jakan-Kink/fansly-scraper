"""FakeSocket — shared test double for ``websockets.client.connect``.

Used by any test that drives code calling into ``api.websocket.FanslyWebSocket``
without a real Fansly WebSocket server. The pattern originated in
``tests/api/unit/test_websocket.py``; moving it into a shared fixture module
makes it reusable for the Wave 2.2 main() integration tests and any other
test that needs to bypass real WebSocket connections.

Usage::

    from unittest.mock import patch
    from tests.fixtures.api.fake_websocket import FakeSocket, auth_response

    fake = FakeSocket(recv_messages=[auth_response()])

    async def fake_connect(**kwargs):
        return fake

    with patch("api.websocket.ws_client.connect", side_effect=fake_connect):
        # call code that opens a WebSocket
        ...

The FanslyApi → FanslyWebSocket auth flow (verified in api/fansly.py:548-584
and api/websocket.py:449) expects the first recv() to return a type-1 message
whose ``d`` payload is a JSON string containing a ``session`` object with at
least ``id`` and ``websocketSessionId`` fields. ``auth_response()`` builds
that by default; pass different args to test auth-failure paths.
"""

from __future__ import annotations

import asyncio
import json
from contextlib import contextmanager
from unittest.mock import patch


class FakeSocket:
    """Test double for a websockets.client connection.

    Records all sent messages and feeds back scripted recv responses from a
    queue. Once the queue is drained, ``recv()`` blocks until ``close()`` is
    called — this matches the real WebSocket behavior of waiting for the
    next server message, so downstream code that expects a long-lived
    connection does not exit prematurely.

    Attributes:
        sent: Every message sent via ``send()`` in call order.
        closed: True after ``close()`` has been called.
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
        # Block until close() is called — simulates waiting for next server
        # message on a live connection.
        await self._block_event.wait()
        return ""

    async def close(self) -> None:
        self.closed = True
        self._block_event.set()  # Unblock any pending recv.


def ws_message(msg_type: int, data: str) -> str:
    """Build a WebSocket message string in Fansly's envelope format.

    Args:
        msg_type: Integer message type (1=auth, 2=ping, etc.).
        data: Already-JSON-encoded payload string (Fansly's ``d`` field is
              a string, not an object — it contains nested JSON).

    Returns:
        A JSON string ready to feed into FakeSocket's recv_messages list.
    """
    return json.dumps({"t": msg_type, "d": data})


def auth_response(
    session_id: str = "test-session-id-1",
    ws_session_id: str = "test-ws-session-id-1",
    account_id: str = "100000001",
) -> str:
    """Build a successful type-1 (auth) response.

    Matches api/websocket.py:449 which reads ``session.id`` and
    ``session.websocketSessionId`` from the response to populate the
    ``FanslyWebSocket.session_id`` / ``websocket_session_id`` attributes.
    ``FanslyApi.get_active_session_async`` waits up to 1 second for
    ``session_id`` to be populated (api/fansly.py:564-567).

    Args:
        session_id: The session ID the FakeSocket's auth response advertises.
        ws_session_id: The WebSocket-specific session ID.
        account_id: The account ID associated with the session.

    Returns:
        A WebSocket message string to put at the start of
        FakeSocket's recv_messages list.
    """
    return ws_message(
        1,
        json.dumps(
            {
                "session": {
                    "id": session_id,
                    "token": "fake-ws-token",
                    "accountId": account_id,
                    "websocketSessionId": ws_session_id,
                    "status": 2,
                }
            }
        ),
    )


@contextmanager
def fake_websocket_session(
    session_id: str = "test-session-id-1",
    ws_session_id: str = "test-ws-session-id-1",
    account_id: str = "100000001",
):
    """Patch ``api.websocket.ws_client.connect`` to return an auto-authing FakeSocket.

    The Fansly WebSocket auth flow (api/fansly.py:548-584, api/websocket.py:449)
    reads ``session.id`` + ``session.websocketSessionId`` from the first
    type-1 message and populates ``FanslyWebSocket.session_id``. This helper
    scripts that response so ``get_active_session_async`` succeeds.

    Use this from any test (integration or unit) that drives code through
    ``FanslyApi.setup_api`` or ``FanslyWebSocket.connect`` without a real
    Fansly WebSocket server.

    Yields:
        The FakeSocket instance — callers can inspect ``fake.sent`` to
        verify what auth / subscribe messages were transmitted.

    Example::

        with fake_websocket_session() as fake:
            await config.setup_api()
        # fake.sent contains the auth message the real code produced
    """
    fake = FakeSocket(
        recv_messages=[
            auth_response(
                session_id=session_id,
                ws_session_id=ws_session_id,
                account_id=account_id,
            )
        ]
    )

    async def _fake_connect(**_kwargs):
        return fake

    with patch("api.websocket.ws_client.connect", side_effect=_fake_connect):
        yield fake


__all__ = ["FakeSocket", "auth_response", "fake_websocket_session", "ws_message"]
