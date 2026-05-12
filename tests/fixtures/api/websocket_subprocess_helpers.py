"""Test doubles for the parent (``FanslyWebSocket``) and child
(``_ChildWebSocket``) surfaces in ``api/websocket.py``.

``make_proxy`` builds a real ``FanslyWebSocket`` for parent-side tests.
``spawn_ctx_with_mock_process`` substitutes a fake ``mp.get_context``
whose ``Process`` is a MagicMock. ``build_mock_ws_class`` returns a
mocked ``_ChildWebSocket`` class for tests that drive the subprocess
supervisor without spawning a real Python process.
"""

from __future__ import annotations

import asyncio
import multiprocessing as mp
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from api.websocket import FanslyWebSocket
from api.websocket_protocol import MSG_SERVICE_EVENT


async def _hang_until_cancelled() -> None:
    """Stand-in for ``_maintain_connection`` — mirrors production's run-forever
    semantics so the supervisor's FIRST_COMPLETED race waits on the consumer,
    not on an immediately-returning mock. Cancellation propagates cleanly.
    """
    await asyncio.sleep(3600)


def make_proxy(**overrides: Any) -> FanslyWebSocket:
    """Build a FanslyWebSocket with sensible test defaults."""
    defaults = {
        "token": "test_token",
        "user_agent": "TestAgent/1.0",
        "cookies": {"sess": "abc"},
    }
    defaults.update(overrides)
    return FanslyWebSocket(**defaults)


def spawn_ctx_with_mock_process(mock_process: MagicMock) -> MagicMock:
    """Stand-in for mp.get_context('spawn') whose Process returns mock_process."""
    # Queue stays real so tests can pre-load commands and read events.
    ctx = MagicMock(name="spawn_ctx")
    ctx.Queue = mp.Queue
    ctx.Process.return_value = mock_process
    return ctx


def build_mock_ws_class() -> tuple[MagicMock, MagicMock]:
    """Return (mock_class, instance) for patching api.websocket._ChildWebSocket.

    The supervisor's lifecycle is now: create task from ``_maintain_connection()``,
    set ``_stop_event`` to signal shutdown, close ``websocket`` if present.
    Stub those concretely so the supervisor can spin up and tear down in tests.
    """
    mock_class = MagicMock(name="ChildWebSocket_class")
    mock_class.MSG_SERVICE_EVENT = MSG_SERVICE_EVENT

    instance = MagicMock(name="ws_instance")
    instance._maintain_connection = AsyncMock(side_effect=_hang_until_cancelled)
    instance._stop_event = MagicMock()
    instance.websocket = None  # skip the supervisor's close branch
    instance.send_message = AsyncMock()
    instance.register_handler = MagicMock()
    instance.connected = False
    instance.session_id = None
    instance.websocket_session_id = None
    instance.account_id = None

    mock_class.return_value = instance
    return mock_class, instance
