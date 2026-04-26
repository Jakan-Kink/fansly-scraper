"""WebSocket test doubles — two layers of fakes for different test surfaces.

Two distinct stubs live here, one per test surface:

* ``FakeSocket`` is the **transport-level** fake — it stands in for the
  ``websockets.client`` connection object that ``FanslyWebSocket.connect``
  receives. Use it when the test wants real ``FanslyWebSocket`` behavior
  (auth flow, ping/pong, message decoding) but no actual network.

* ``FakeWS`` is the **client-level** fake — it stands in for the entire
  ``FanslyWebSocket`` instance. Use it when the test wants to inject a
  pre-made WS into something like ``daemon/runner.py`` via ``ws_factory``
  and drive service events synchronously via ``fire(svc, type, payload)``
  without exercising the real client at all.

Both belong here per the project-wide rule (CLAUDE.md): "All pytest
fixtures, fakes, factories, and reusable test helpers MUST live in
``tests/fixtures/``" — never in ``conftest.py`` or ``test_*.py`` files.

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
from collections.abc import Callable
from contextlib import contextmanager
from typing import Any
from unittest.mock import patch

import pytest


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


class FakeWS:
    """Client-level WebSocket stub — stands in for an entire FanslyWebSocket.

    No real network connection. Suitable for tests that inject a fake into
    components expecting a ``FanslyWebSocket``-shaped object (e.g.,
    ``daemon/runner.py`` via its ``ws_factory`` injection seam).

    Mirrors the post-thread-refactor surface:
      * ``register_handler(msg_type, handler)`` — record a handler
      * ``start_in_thread(*_, **_)`` — sync, sets ``started`` flag
      * ``stop_thread(*_, **_)`` — async, sets ``stopped`` flag
      * ``fire(service_id, event_type, inner_dict)`` — synchronously invoke
        the registered ``MSG_SERVICE_EVENT`` handler with the envelope
        shape the real ``_handle_message`` produces after type-10000
        dispatch: ``{"serviceId": svc, "event": json.dumps({type, **inner})}``

    The ``*_args, **_kwargs`` on the lifecycle methods absorb the real
    signatures' optional arguments (``main_loop``, ``ready_timeout``,
    ``join_timeout``) so callers don't need a special-case path.
    """

    MSG_SERVICE_EVENT = 10000

    def __init__(self) -> None:
        self.started = False
        self.stopped = False
        self._handlers: dict[int, Callable] = {}

    def register_handler(self, message_type: int, handler: Callable) -> None:
        """Record the handler for the given message type."""
        self._handlers[message_type] = handler

    def start_in_thread(self, *_args: Any, **_kwargs: Any) -> None:
        """Simulate thread start without network. Sync to match production."""
        self.started = True

    async def stop_thread(self, *_args: Any, **_kwargs: Any) -> None:
        """Simulate thread stop. Async to match production."""
        self.stopped = True

    async def fire(
        self, service_id: int, event_type: int, inner: dict[str, Any]
    ) -> None:
        """Fire a MSG_SERVICE_EVENT with the given service/event/payload.

        Constructs the envelope dict that ``_handle_message`` passes to the
        registered handler after type-10000 dispatch.

        Args:
            service_id: Fansly ``serviceId`` field.
            event_type: The ``type`` field inside the inner event dict.
            inner: The inner event dict (will have ``type`` merged in).
        """
        envelope = {
            "serviceId": service_id,
            "event": json.dumps({"type": event_type, **inner}),
        }
        handler = self._handlers.get(self.MSG_SERVICE_EVENT)
        if handler is None:
            return
        if asyncio.iscoroutinefunction(handler):
            await handler(envelope)
        else:
            handler(envelope)


@pytest.fixture
def fake_ws() -> FakeWS:
    """Provide a fresh ``FakeWS`` stub for each test.

    Auto-discovered via the master conftest's wildcard import from
    ``tests.fixtures``, so any test in the suite can simply request the
    ``fake_ws`` argument and receive an isolated stub instance.
    """
    return FakeWS()


def make_fake_ws_factory(fake_ws: FakeWS) -> Callable[[Any], FakeWS]:
    """Return a ``ws_factory`` callable that always yields *fake_ws*.

    The signature ``(config) -> FakeWS`` matches the ``ws_factory`` seam in
    ``daemon/runner.py`` and equivalents.

    Args:
        fake_ws: The FakeWS instance to return on every call.

    Returns:
        A factory callable suitable for the ``ws_factory`` parameter.
    """

    def _factory(_config: Any) -> FakeWS:
        return fake_ws

    return _factory


__all__ = [
    "FakeSocket",
    "FakeWS",
    "auth_response",
    "fake_websocket_session",
    "fake_ws",
    "make_fake_ws_factory",
    "ws_message",
]
