"""Shared fixtures for daemon tests (unit and integration).

Provides:
  - FakeWS stub and _fake_ws_factory for injecting a fake WebSocket
  - ``fake_ws`` pytest fixture
  - ``saved_account`` pytest_asyncio fixture
  - ``config_wired`` fixture wiring FanslyApi into config
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import Any
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from tests.fixtures.metadata.metadata_factories import AccountFactory


# ---------------------------------------------------------------------------
# FakeWS -- minimal WebSocket stub for injection
# ---------------------------------------------------------------------------


class FakeWS:
    """Minimal WebSocket stub.  No real network connection.

    Provides:
      - register_handler(msg_type, handler)
      - start_background() -- sets started flag
      - stop() -- sets stopped flag
      - fire(service_id, event_type, inner_dict) -- calls the registered
        MSG_SERVICE_EVENT handler with the envelope dict that the real
        FanslyWebSocket passes after MSG_SERVICE_EVENT dispatch.

    The envelope shape mirrors _handle_message for MSG_SERVICE_EVENT:
      {"serviceId": service_id, "event": json.dumps(inner)}
    """

    MSG_SERVICE_EVENT = 10000

    def __init__(self) -> None:
        self.started = False
        self.stopped = False
        self._handlers: dict[int, Callable] = {}

    def register_handler(self, message_type: int, handler: Callable) -> None:
        """Record the handler for the given message type."""
        self._handlers[message_type] = handler

    async def start_background(self) -> None:
        """Simulate background start without network."""
        self.started = True

    async def stop(self) -> None:
        """Simulate stop."""
        self.stopped = True

    async def fire(
        self, service_id: int, event_type: int, inner: dict[str, Any]
    ) -> None:
        """Fire a MSG_SERVICE_EVENT with the given service/event/payload.

        Constructs the envelope dict that _handle_message passes to the
        registered handler after type-10000 dispatch.

        Args:
            service_id: Fansly serviceId field.
            event_type: The ``type`` field inside the inner event dict.
            inner: The inner event dict (will have ``type`` merged in).
        """
        envelope = {
            "serviceId": service_id,
            "event": json.dumps({"type": event_type, **inner}),
        }
        handler = self._handlers.get(self.MSG_SERVICE_EVENT)
        if handler is not None:
            if asyncio.iscoroutinefunction(handler):
                await handler(envelope)
            else:
                handler(envelope)


def _fake_ws_factory(fake_ws: FakeWS) -> Callable:
    """Return a ws_factory callable that always yields the given FakeWS.

    Args:
        fake_ws: The FakeWS instance to return.

    Returns:
        A callable with signature ``(config) -> FakeWS``.
    """

    def _factory(config: Any) -> FakeWS:
        return fake_ws

    return _factory


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_ws() -> FakeWS:
    """Provide a FakeWS stub for each test."""
    return FakeWS()


@pytest_asyncio.fixture
async def saved_account(entity_store):
    """Create and persist a test Account to satisfy FK constraints."""
    account = AccountFactory.build()
    await entity_store.save(account)
    return account


@pytest.fixture
def config_wired(config, entity_store, fansly_api):
    """Config wired with a real FanslyApi backed by the test entity_store.

    entity_store listed before config_wired so the store singleton is set
    before polling/filter functions call get_store().
    """
    config._api = fansly_api
    return config
