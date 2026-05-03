"""Pre-sync bootstrap for the monitoring daemon's WebSocket handler.

This module attaches a delta-capture handler to the WebSocket that
``api/fansly.py`` already opens for anti-detection. The goal: service
events (PPV purchases, new follows, incoming messages, subscription
confirms, etc.) that arrive during initial sync land in a queue rather
than being dropped on the floor.

Think of the initial sync as a base snapshot and this module's queue as
a change-delta log — after sync finishes the caller drains the queue
via :func:`drain_backfill` to apply any deltas that landed mid-snapshot.

Before this module existed there were two WebSockets at different points
in the lifecycle: the API's anti-detection WS (no handler, events
dropped) during sync, then the daemon's WS (with handler) after sync.
Now there is exactly one WS — the API's — and we just swap which
handler is registered on it as the lifecycle progresses:

  * After ``config.setup_api()``:   bootstrap attaches a *buffering*
                                    handler (queue only, no budget).
  * During ``run_daemon``:          the handler is re-registered with
                                    a budget-aware closure that also
                                    notifies ``ErrorBudget.on_success``.

Both handlers share the same queue / simulator / baseline_consumed so
state accumulated during sync transfers cleanly into the daemon phase.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from api.websocket import FanslyWebSocket
from config.logging import textio_logger as logger
from config.logging import websocket_logger as ws_logger

from .handlers import WorkItem
from .runner import _handle_work_item, _make_simulator, _make_ws_handler
from .simulator import ActivitySimulator


if TYPE_CHECKING:
    from config.fanslyconfig import FanslyConfig


# Re-export internal runner helpers so the test suite can patch them.
__all__ = [
    "DaemonBootstrap",
    "_handle_work_item",
    "bootstrap_daemon_ws",
    "drain_backfill",
    "shutdown_bootstrap",
]


@dataclass
class DaemonBootstrap:
    """Collaborators established pre-sync, handed to ``run_daemon`` post-sync.

    Attributes:
        ws: The FanslyWebSocket the handler is attached to. ``None`` if
            the API's WS was not available when bootstrap ran (e.g. WS
            auth failed during ``setup_api``).
        queue: Work queue being filled by the handler as events arrive.
        simulator: ActivitySimulator tracking WS events. Starts in "active".
        baseline_consumed: Set of creator IDs whose session-baseline check
            has already been applied. Empty at bootstrap; daemon reuses it.
        ws_started: True if a handler was successfully attached to a
            connected WS. False means no delta capture is happening.
    """

    ws: FanslyWebSocket | None
    queue: asyncio.Queue[WorkItem]
    simulator: ActivitySimulator
    baseline_consumed: set[int]
    ws_started: bool


async def bootstrap_daemon_ws(config: FanslyConfig) -> DaemonBootstrap:
    """Attach a delta-capture handler to the API's already-running WebSocket.

    Call this *after* ``config.setup_api()`` — the API has by then built
    a fully-authenticated WebSocket (with session cookies) and started
    it in the background. We simply register a MSG_SERVICE_EVENT handler
    on that same WS so incoming service events enqueue WorkItems.

    The handler is registered with ``budget=None`` because during
    bootstrap there is no daemon context — we only *capture* events.
    ``run_daemon`` re-registers a budget-aware handler when it takes
    over, reusing the same queue and simulator.

    Args:
        config: FanslyConfig with ``setup_api()`` already called.

    Returns:
        DaemonBootstrap with queue/simulator populated. ``ws`` is the
        API's WS (same instance as ``config._api._websocket_client``) on
        success, or ``None`` if no WS was available to attach to.
    """
    queue: asyncio.Queue[WorkItem] = asyncio.Queue()
    simulator = _make_simulator(config)
    baseline_consumed: set[int] = set()

    api = getattr(config, "_api", None)
    ws = getattr(api, "_websocket_client", None) if api is not None else None

    if ws is None or not getattr(ws, "connected", False):
        ws_logger.warning(
            "daemon.bootstrap: API WebSocket not available; "
            "running without delta capture (events during sync will be dropped)"
        )
        return DaemonBootstrap(
            ws=None,
            queue=queue,
            simulator=simulator,
            baseline_consumed=baseline_consumed,
            ws_started=False,
        )

    ws.register_handler(
        FanslyWebSocket.MSG_SERVICE_EVENT,
        _make_ws_handler(simulator, queue, budget=None),
    )
    ws_logger.info(
        "daemon.bootstrap: delta-capture handler attached (session_id={})",
        getattr(ws, "session_id", None),
    )

    return DaemonBootstrap(
        ws=ws,
        queue=queue,
        simulator=simulator,
        baseline_consumed=baseline_consumed,
        ws_started=True,
    )


async def drain_backfill(
    config: FanslyConfig,
    bootstrap: DaemonBootstrap,
) -> int:
    """Process WorkItems accumulated during initial sync.

    Runs one-shot through whatever is currently in the queue, executing
    each item via ``_handle_work_item`` (the same routine the daemon's
    worker loop uses). Downstream handlers themselves dedup against the
    database, so items referring to content that sync already grabbed
    are idempotent — the filesystem writer sees the hash already exists
    and skips.

    No poll loops, no budget, no simulator ticks. This is a pure
    snapshot-then-delta apply.

    Args:
        config: FanslyConfig with API + database ready.
        bootstrap: The DaemonBootstrap returned by ``bootstrap_daemon_ws``.

    Returns:
        Number of WorkItems successfully processed. Items that raise are
        logged and counted as failures (not re-queued).
    """
    processed = 0
    failures = 0
    initial_size = bootstrap.queue.qsize()

    if initial_size == 0:
        logger.debug("daemon.bootstrap: no delta items captured during sync")
        return 0

    logger.info(
        "daemon.bootstrap: draining {} delta item(s) captured during sync",
        initial_size,
    )

    while not bootstrap.queue.empty():
        item = bootstrap.queue.get_nowait()
        try:
            await _handle_work_item(config, item)
        except Exception as exc:
            failures += 1
            logger.error(
                "daemon.bootstrap: backfill error on {} - {}",
                type(item).__name__,
                exc,
            )
        else:
            processed += 1

    logger.info(
        "daemon.bootstrap: backfill complete — {} processed, {} failed",
        processed,
        failures,
    )
    return processed


async def shutdown_bootstrap(bootstrap: DaemonBootstrap) -> None:
    """Detach the delta-capture handler when no daemon will take over.

    Called when ``config.daemon_mode`` is False — the delta-capture phase
    ran during sync, backfill applied the captured events, and now
    there is no consumer for further events. Detaching the handler
    stops queue growth; the WS itself stays owned by
    ``api/fansly.py`` (its normal shutdown path tears it down).

    Args:
        bootstrap: The DaemonBootstrap to release.
    """
    if not bootstrap.ws_started or bootstrap.ws is None:
        return
    # Remove our handler so leftover events between now and WS shutdown
    # don't balloon the orphaned queue.
    bootstrap.ws._event_handlers.pop(FanslyWebSocket.MSG_SERVICE_EVENT, None)
    ws_logger.info("daemon.bootstrap: delta-capture handler detached")


def _unused_import_keeper() -> Any:
    """Silence unused-import warnings for names exported for tests."""
    return _handle_work_item
