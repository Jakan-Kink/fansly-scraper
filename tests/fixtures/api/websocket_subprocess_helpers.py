"""Test doubles for api.websocket_subprocess parent + child surfaces."""

from __future__ import annotations

import multiprocessing as mp
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from api.websocket import FanslyWebSocket
from api.websocket_subprocess import FanslyWebSocketProxy


def make_proxy(**overrides: Any) -> FanslyWebSocketProxy:
    """Build a FanslyWebSocketProxy with sensible test defaults."""
    defaults = {
        "token": "test_token",
        "user_agent": "TestAgent/1.0",
        "cookies": {"sess": "abc"},
    }
    defaults.update(overrides)
    return FanslyWebSocketProxy(**defaults)


def spawn_ctx_with_mock_process(mock_process: MagicMock) -> MagicMock:
    """Stand-in for mp.get_context('spawn') whose Process returns mock_process."""
    # Queue stays real so tests can pre-load commands and read events.
    ctx = MagicMock(name="spawn_ctx")
    ctx.Queue = mp.Queue
    ctx.Process.return_value = mock_process
    return ctx


def build_mock_ws_class() -> tuple[MagicMock, MagicMock]:
    """Return (mock_class, instance) for patching api.websocket_subprocess.FanslyWebSocket."""
    mock_class = MagicMock(name="FanslyWebSocket_class")
    mock_class.MSG_SERVICE_EVENT = FanslyWebSocket.MSG_SERVICE_EVENT

    instance = MagicMock(name="ws_instance")
    instance.start_in_thread = MagicMock()
    instance.stop_thread = AsyncMock()
    instance.send_message = AsyncMock()
    instance.register_handler = MagicMock()
    instance.connected = False
    instance.session_id = None
    instance.websocket_session_id = None
    instance.account_id = None

    mock_class.return_value = instance
    return mock_class, instance
