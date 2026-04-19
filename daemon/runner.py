"""Monitoring daemon orchestrator.

Coordinates the five concurrent loops that make up the post-batch monitoring
phase:

  - _timeline_poll_loop     -- calls poll_home_timeline every timeline_interval
  - _story_poll_loop        -- calls poll_story_states every story_interval
  - _worker_loop            -- drains the work queue produced by polling and WS
  - _simulator_tick_loop    -- periodically advances the ActivitySimulator
  - _following_refresh_loop -- refreshes the following list every 5 minutes
                               (only when config.use_following is True)

A FanslyWebSocket is registered with a MSG_SERVICE_EVENT handler that decodes
incoming envelopes and enqueues WorkItems via dispatch_ws_event.

Entry point: ``run_daemon(config)``.  Returns an exit code:
  - ``EXIT_SUCCESS`` (0) on clean SIGINT shutdown
  - ``DAEMON_UNRECOVERABLE`` (-8) when ErrorBudget detects hard-fatal HTTP
    status codes (401, 418) or a prolonged gap with no successful operation

The ErrorBudget class tracks the last successful API operation. Soft errors
(anything other than the hard-fatal set) are measured against a configurable
timeout window. Hard-fatal HTTP status codes cause an immediate raise regardless
of elapsed time.

Intended to be called AFTER a normal batch download.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import signal
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, ClassVar

import httpx
from loguru import logger

from api.websocket import FanslyWebSocket
from config.fanslyconfig import FanslyConfig
from config.logging import websocket_logger as ws_logger
from daemon.dashboard import (
    TASK_FOLLOWING,
    TASK_SIMULATOR,
    TASK_STORY,
    TASK_TIMELINE,
    DaemonDashboard,
    NullDashboard,
    make_dashboard,
)
from daemon.filters import should_process_creator
from daemon.handlers import (
    CheckCreatorAccess,
    DownloadMessagesForGroup,
    DownloadStoriesOnly,
    DownloadTimelineOnly,
    FullCreatorDownload,
    RedownloadCreatorMedia,
    WorkItem,
    dispatch_ws_event,
)
from daemon.polling import poll_home_timeline, poll_story_states
from daemon.simulator import ActivitySimulator
from daemon.state import mark_creator_processed
from download.core import (
    DownloadState,
    download_messages,
    download_stories,
    download_timeline,
    download_wall,
    get_creator_account_info,
    get_following_accounts,
)
from errors import DAEMON_UNRECOVERABLE, EXIT_SUCCESS, DaemonUnrecoverableError
from metadata.models import Account, get_store


# ---------------------------------------------------------------------------
# ErrorBudget
# ---------------------------------------------------------------------------


@dataclass
class ErrorBudget:
    """Track error frequency to detect unrecoverable daemon states.

    Hard-fatal HTTP status codes escalate immediately. Soft errors are
    measured against a configurable timeout window. Successful operations
    reset the clock.

    Args:
        timeout_seconds: Elapsed seconds without success before raising
            DaemonUnrecoverableError on the next error.
        last_success_at: Wall-clock timestamp of the last known-good operation
            (defaults to now at construction time).
    """

    timeout_seconds: int
    last_success_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    _hard_fatal_statuses: ClassVar[set[int]] = {401, 418}

    def on_success(self) -> None:
        """Reset the error clock to now after a successful operation."""
        self.last_success_at = datetime.now(UTC)

    def on_error(self, exc: BaseException) -> None:
        """Decide whether this error is unrecoverable.

        Hard-fatal cases escalate immediately. Soft errors compare against
        the budget window. Successful operations reset the clock.

        Args:
            exc: The exception to evaluate.

        Raises:
            DaemonUnrecoverableError: when the error is hard-fatal or the
                budget window has been exhausted.
        """
        if isinstance(exc, httpx.HTTPStatusError):
            status = exc.response.status_code
            if status in self._hard_fatal_statuses:
                raise DaemonUnrecoverableError(f"Fatal HTTP {status}") from exc
        elapsed = (datetime.now(UTC) - self.last_success_at).total_seconds()
        if elapsed > self.timeout_seconds:
            raise DaemonUnrecoverableError(
                f"No successful operation in {elapsed:.0f}s "
                f"(threshold {self.timeout_seconds}s)"
            ) from exc


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _make_ws(config: FanslyConfig) -> FanslyWebSocket:
    """Construct a FanslyWebSocket from config credentials.

    Args:
        config: FanslyConfig instance with token and user_agent set.

    Returns:
        A new FanslyWebSocket ready for ``start_background()``.
    """
    return FanslyWebSocket(
        token=config.token or "",
        user_agent=config.user_agent or "",
    )


async def _resolve_creator_name(creator_id: int) -> str | None:
    """Resolve a creator's username from the identity-map cache or store.

    Args:
        creator_id: Fansly account ID.

    Returns:
        The creator's username string, or None if not found.
    """
    store = get_store()
    account: Account | None = store.get_from_cache(Account, creator_id)
    if account is None:
        try:
            account = await store.get(Account, creator_id)
        except Exception as exc:
            logger.warning(
                "daemon.runner: could not load Account {} - {}", creator_id, exc
            )
    return account.username if account is not None else None


async def _is_creator_in_scope(config: FanslyConfig, creator_id: int) -> bool:
    """Return True when the creator is in scope for this daemon invocation.

    When daemon was invoked with ``-u user1,user2`` (not ``-uf``), only process
    WorkItems for those specific creators. For ``-uf`` mode, all following-list
    creators are in scope.

    Args:
        config: FanslyConfig instance.
        creator_id: Fansly account ID to check.

    Returns:
        True when the creator is in scope; False otherwise.
    """
    if config.use_following:
        return True
    if not config.user_names:
        return True  # unrestricted edge case
    store = get_store()
    account: Account | None = await store.get(Account, creator_id)
    if account is None or not account.username:
        return False  # unknown creator — skip
    return account.username.lower() in {n.lower() for n in config.user_names}


async def _handle_messages_item(
    config: FanslyConfig, item: DownloadMessagesForGroup
) -> None:
    """Execute a DownloadMessagesForGroup work item.

    Args:
        config: FanslyConfig instance.
        item: Work item specifying which DM group to download.
    """
    logger.info("daemon.runner: downloading messages for group {}", item.group_id)
    state = DownloadState()
    try:
        await download_messages(config, state)
    except Exception as exc:
        logger.opt(exception=exc).error(
            "daemon.runner: download_messages failed for group {} - {}",
            item.group_id,
            exc,
        )
        raise


async def _handle_full_creator_item(
    config: FanslyConfig, item: FullCreatorDownload
) -> None:
    """Execute a FullCreatorDownload work item.

    Downloads timeline, stories (mark_viewed=False), messages, and wall for
    a newly-accessible creator.

    Args:
        config: FanslyConfig instance.
        item: Work item specifying the creator to download.
    """
    creator_name = await _resolve_creator_name(item.creator_id)
    if creator_name is None:
        logger.warning(
            "daemon.runner: skipping FullCreatorDownload - unknown creator {}",
            item.creator_id,
        )
        return

    logger.info(
        "daemon.runner: full creator download for {} ({})",
        creator_name,
        item.creator_id,
    )
    state = DownloadState(creator_name=creator_name)
    try:
        await get_creator_account_info(config, state)
        await download_timeline(config, state)
        await download_stories(config, state, mark_viewed=False)
        await download_messages(config, state)
        await download_wall(config, state)
    except Exception as exc:
        logger.opt(exception=exc).error(
            "daemon.runner: FullCreatorDownload failed for {} - {}",
            creator_name,
            exc,
        )
        raise


async def _handle_redownload_item(
    config: FanslyConfig, item: RedownloadCreatorMedia
) -> None:
    """Execute a RedownloadCreatorMedia work item (PPV purchase).

    Re-downloads timeline and messages for a creator after a PPV purchase
    makes previously locked content accessible.

    Args:
        config: FanslyConfig instance.
        item: Work item specifying the creator to re-download.
    """
    creator_name = await _resolve_creator_name(item.creator_id)
    if creator_name is None:
        logger.warning(
            "daemon.runner: skipping RedownloadCreatorMedia - unknown creator {}",
            item.creator_id,
        )
        return

    logger.info(
        "daemon.runner: PPV re-download for {} ({})", creator_name, item.creator_id
    )
    state = DownloadState(creator_name=creator_name)
    try:
        await get_creator_account_info(config, state)
        await download_timeline(config, state)
        await download_messages(config, state)
    except Exception as exc:
        logger.opt(exception=exc).error(
            "daemon.runner: RedownloadCreatorMedia failed for {} - {}",
            creator_name,
            exc,
        )
        raise


async def _handle_check_access_item(
    config: FanslyConfig, item: CheckCreatorAccess
) -> None:
    """Execute a CheckCreatorAccess work item (new follow).

    Refreshes account info to check for newly-accessible content.

    Args:
        config: FanslyConfig instance.
        item: Work item specifying the creator to check.
    """
    creator_name = await _resolve_creator_name(item.creator_id)
    if creator_name is None:
        logger.warning(
            "daemon.runner: skipping CheckCreatorAccess - unknown creator {}",
            item.creator_id,
        )
        return

    logger.info(
        "daemon.runner: checking access for {} ({})", creator_name, item.creator_id
    )
    state = DownloadState(creator_name=creator_name)
    try:
        await get_creator_account_info(config, state)
    except Exception as exc:
        logger.opt(exception=exc).error(
            "daemon.runner: CheckCreatorAccess failed for {} - {}",
            creator_name,
            exc,
        )
        raise


async def _handle_stories_only_item(
    config: FanslyConfig, item: DownloadStoriesOnly
) -> None:
    """Execute a DownloadStoriesOnly work item.

    Runs story download for the creator with mark_viewed=False so the
    daemon does not affect the user's real Fansly UX.

    Args:
        config: FanslyConfig instance.
        item: Work item specifying the creator whose stories to download.
    """
    creator_name = await _resolve_creator_name(item.creator_id)
    if creator_name is None:
        logger.warning(
            "daemon.runner: skipping DownloadStoriesOnly - unknown creator {}",
            item.creator_id,
        )
        return

    logger.info(
        "daemon.runner: story download for {} ({}) mark_viewed=False",
        creator_name,
        item.creator_id,
    )
    state = DownloadState(creator_name=creator_name)
    try:
        await get_creator_account_info(config, state)
        await download_stories(config, state, mark_viewed=False)
    except Exception as exc:
        logger.opt(exception=exc).error(
            "daemon.runner: DownloadStoriesOnly failed for {} - {}",
            creator_name,
            exc,
        )
        raise


async def _handle_timeline_only_item(
    config: FanslyConfig, item: DownloadTimelineOnly
) -> None:
    """Download ONLY the creator's timeline (from home-timeline poll hit).

    Narrower than FullCreatorDownload: only fetches the timeline, not
    stories, messages, or wall. FullCreatorDownload is reserved for
    subscription-confirmed events (svc=15 type=5 status=3).

    Args:
        config: FanslyConfig instance.
        item: Work item specifying the creator whose timeline to download.
    """
    state = DownloadState(creator_id=item.creator_id, creator_name="")
    try:
        await get_creator_account_info(config, state)
        await download_timeline(config, state)
    except Exception as exc:
        logger.opt(exception=exc).error(
            "daemon.runner: timeline-only download failed for {}: {}",
            item.creator_id,
            exc,
        )
        raise


# Dispatch table: WorkItem type -> handler coroutine factory
_WORK_DISPATCH: dict[type[WorkItem], Any] = {
    DownloadMessagesForGroup: _handle_messages_item,
    FullCreatorDownload: _handle_full_creator_item,
    RedownloadCreatorMedia: _handle_redownload_item,
    CheckCreatorAccess: _handle_check_access_item,
    DownloadStoriesOnly: _handle_stories_only_item,
    DownloadTimelineOnly: _handle_timeline_only_item,
}


async def _handle_work_item(config: FanslyConfig, item: WorkItem) -> None:
    """Route a WorkItem to the appropriate download handler.

    Args:
        config: FanslyConfig instance.
        item: The WorkItem to execute.
    """
    handler = _WORK_DISPATCH.get(type(item))
    if handler is None:
        logger.warning("daemon.runner: unhandled WorkItem type {}", type(item).__name__)
        return
    await handler(config, item)


# ---------------------------------------------------------------------------
# Five daemon loop tasks
# ---------------------------------------------------------------------------


async def _process_timeline_candidate(
    config: FanslyConfig,
    creator_id: int,
    prefetched: list[dict],
    session_baseline: datetime | None,
    baseline_consumed: set[int],
    queue: asyncio.Queue[WorkItem],
    budget: ErrorBudget,
) -> None:
    """Evaluate one creator from the timeline poll and enqueue if needed.

    Checks scope, applies should_process_creator, and enqueues a
    DownloadTimelineOnly item when the creator has new content.

    Args:
        config: FanslyConfig instance.
        creator_id: Fansly account ID of the candidate.
        prefetched: Post dicts already fetched for this creator (may be empty).
        session_baseline: Per-run baseline override (consumed on first use).
        baseline_consumed: Set of creator IDs already past their first check.
        queue: Work queue to push DownloadTimelineOnly items onto.
        budget: ErrorBudget to call on_success after a successful evaluation.
    """
    if not await _is_creator_in_scope(config, creator_id):
        logger.debug("daemon.runner: creator {} out of scope — skipping", creator_id)
        return

    baseline = session_baseline if creator_id not in baseline_consumed else None
    baseline_consumed.add(creator_id)

    try:
        should = await should_process_creator(
            config,
            creator_id,
            session_baseline=baseline,
            prefetched_posts=prefetched,
        )
    except Exception as exc:
        logger.warning(
            "daemon.runner: should_process_creator error for {} - {}",
            creator_id,
            exc,
        )
        should = True

    if should:
        await queue.put(DownloadTimelineOnly(creator_id=creator_id))
        budget.on_success()


async def _timeline_poll_loop(
    config: FanslyConfig,
    simulator: ActivitySimulator,
    queue: asyncio.Queue[WorkItem],
    session_baseline: datetime | None,
    baseline_consumed: set[int],
    stop_event: asyncio.Event,
    budget: ErrorBudget,
    refresh_event: asyncio.Event,
    dashboard: DaemonDashboard | NullDashboard,
) -> None:
    """Continuously poll the home timeline and enqueue DownloadTimelineOnly items.

    Skips the poll when the simulator is in the hidden state
    (simulator.should_poll is False). Calls simulator.on_new_content() when
    the poll returns at least one creator with new posts. Triggers a following
    list refresh (via refresh_event) when on_new_content() signals a transition
    from idle/hidden to active.

    Args:
        config: FanslyConfig instance.
        simulator: ActivitySimulator governing poll cadence.
        queue: Work queue to push DownloadTimelineOnly items onto.
        session_baseline: Per-run baseline override for should_process_creator.
        baseline_consumed: Set of creator IDs already past their first check.
        stop_event: Set to stop the loop.
        budget: ErrorBudget to track API health.
        refresh_event: Event to set when an active-state transition occurs.
    """
    while not stop_event.is_set():
        interval = simulator.timeline_interval
        if interval <= 0.0:
            # Hidden state - wait briefly then re-check
            await dashboard.wait_with_countdown(
                TASK_TIMELINE,
                "Timeline poll (paused — hidden)",
                10.0,
                stop_event,
            )
            continue

        await dashboard.wait_with_countdown(
            TASK_TIMELINE,
            "Timeline poll",
            interval,
            stop_event,
        )

        if stop_event.is_set():
            break

        if not simulator.should_poll:
            continue

        try:
            new_creator_ids, posts_by_creator = await poll_home_timeline(config)
            budget.on_success()
        except DaemonUnrecoverableError:
            raise
        except Exception as exc:
            logger.warning("daemon.runner: timeline poll error - {}", exc)
            budget.on_error(exc)
            continue

        if new_creator_ids:
            transitioned = simulator.on_new_content()
            if transitioned:
                refresh_event.set()
            for creator_id in new_creator_ids:
                prefetched = posts_by_creator.get(creator_id, [])
                await _process_timeline_candidate(
                    config,
                    creator_id,
                    prefetched,
                    session_baseline,
                    baseline_consumed,
                    queue,
                    budget,
                )


async def _story_poll_loop(
    config: FanslyConfig,
    simulator: ActivitySimulator,
    queue: asyncio.Queue[WorkItem],
    stop_event: asyncio.Event,
    budget: ErrorBudget,
    refresh_event: asyncio.Event,
    dashboard: DaemonDashboard | NullDashboard,
) -> None:
    """Continuously poll story states and enqueue DownloadStoriesOnly items.

    Skips the poll when the simulator is in the hidden state. Calls
    simulator.on_new_content() when the poll returns at least one creator
    with newly-active stories. Sets refresh_event when on_new_content()
    signals a transition from idle/hidden to active.

    Args:
        config: FanslyConfig instance.
        simulator: ActivitySimulator governing poll cadence.
        queue: Work queue to push DownloadStoriesOnly items onto.
        stop_event: Set to stop the loop.
        budget: ErrorBudget to track API health.
        refresh_event: Event to set when an active-state transition occurs.
    """
    while not stop_event.is_set():
        interval = simulator.story_interval
        if interval <= 0.0:
            await dashboard.wait_with_countdown(
                TASK_STORY,
                "Story poll (paused — hidden)",
                10.0,
                stop_event,
            )
            continue

        await dashboard.wait_with_countdown(
            TASK_STORY,
            "Story poll",
            interval,
            stop_event,
        )

        if stop_event.is_set():
            break

        if not simulator.should_poll:
            continue

        try:
            creator_ids = await poll_story_states(config)
            budget.on_success()
        except DaemonUnrecoverableError:
            raise
        except Exception as exc:
            logger.warning("daemon.runner: story poll error - {}", exc)
            budget.on_error(exc)
            continue

        if creator_ids:
            transitioned = simulator.on_new_content()
            if transitioned:
                refresh_event.set()
            for creator_id in creator_ids:
                if not await _is_creator_in_scope(config, creator_id):
                    logger.debug(
                        "daemon.runner: story creator {} out of scope — skipping",
                        creator_id,
                    )
                    continue
                await queue.put(DownloadStoriesOnly(creator_id=creator_id))


async def _worker_loop(
    config: FanslyConfig,
    queue: asyncio.Queue[WorkItem],
    stop_event: asyncio.Event,
    use_following: bool,
    budget: ErrorBudget | None = None,
) -> None:
    """Drain the work queue by executing each WorkItem in order.

    When a FullCreatorDownload or DownloadTimelineOnly completes and
    use_following is True, refreshes the following list so newly-subscribed
    creators are included in future polls. Calls mark_creator_processed after
    each creator-scoped download.

    The loop exits when stop_event is set AND the queue is empty, so all
    in-flight items are processed before shutdown. The outer caller caps total
    wait time with asyncio.wait_for.

    Args:
        config: FanslyConfig instance.
        queue: Work queue of pending WorkItems.
        stop_event: Set to stop the loop after draining in-flight items.
        use_following: Whether -uf mode is active (triggers following refresh).
        budget: Optional ErrorBudget to call on_success after each item.
    """
    while True:
        if stop_event.is_set() and queue.empty():
            break

        try:
            item = await asyncio.wait_for(queue.get(), timeout=1.0)
        except TimeoutError:
            continue
        except asyncio.CancelledError:
            break

        try:
            await _handle_work_item(config, item)
        except DaemonUnrecoverableError:
            # ErrorBudget decided we're done — propagate to asyncio.gather
            # so the daemon exits cleanly. task_done() still fires via finally.
            raise
        except Exception as exc:
            # Handler already .opt(exception=exc).error()'d its own traceback
            # with creator context. This is a one-line summary — no need to
            # dump the same traceback twice. Register with ErrorBudget so a
            # prolonged burst escalates to DaemonUnrecoverableError. Do NOT
            # fall through to the else branch — a failed download must not
            # call mark_creator_processed (that would advance lastCheckedAt
            # and cause the next poll to skip the creator, silently losing
            # new content) or budget.on_success (which would mask failure).
            logger.error(
                "daemon.runner: worker error on {} - {}", type(item).__name__, exc
            )
            if budget is not None:
                budget.on_error(exc)
        else:
            if budget is not None:
                budget.on_success()

            # Post-processing only runs on clean handler success (try/else).
            if isinstance(item, (FullCreatorDownload, DownloadTimelineOnly)):
                if use_following:
                    await _refresh_following(config)
                await mark_creator_processed(item.creator_id)

            elif isinstance(
                item, (RedownloadCreatorMedia, CheckCreatorAccess, DownloadStoriesOnly)
            ):
                await mark_creator_processed(item.creator_id)
        finally:
            queue.task_done()


async def _refresh_following(config: FanslyConfig) -> None:
    """Refresh the following list when a new subscription is confirmed.

    Only called when config.use_following is True.  On error, logs and
    returns without modifying config.user_names.

    Args:
        config: FanslyConfig instance to update user_names on.
    """
    try:
        state = DownloadState()
        new_names = await get_following_accounts(config, state)
        if new_names:
            config.user_names = new_names
            logger.info(
                "daemon.runner: following list refreshed - {} creators",
                len(new_names),
            )
    except Exception as exc:
        logger.warning("daemon.runner: following list refresh failed - {}", exc)


async def _following_refresh_loop(
    config: FanslyConfig,
    simulator: ActivitySimulator,
    stop_event: asyncio.Event,
    refresh_event: asyncio.Event,
    budget: ErrorBudget,
    dashboard: DaemonDashboard | NullDashboard,
) -> None:
    """Periodically refresh the following list while the daemon is active.

    Only runs when config.use_following is True. Sleeps when the simulator
    is in idle or hidden state. Refreshes immediately when refresh_event is
    set (triggered by on_new_content() transitions or unhide ticks). Then
    refreshes every 5 minutes while active.

    Args:
        config: FanslyConfig instance to update user_names on.
        simulator: ActivitySimulator to check state before polling.
        stop_event: Set to stop the loop.
        refresh_event: Set externally to trigger an immediate refresh.
        budget: ErrorBudget to call on_error on failures.
    """
    if not config.use_following:
        return

    while not stop_event.is_set():
        # dashboard.wait_with_countdown composites stop_event + refresh_event
        # + timeout into one wait, driving the countdown bar along the way.
        # This also fixes the SIGINT-blindness bug that was in the previous
        # single-event wait (up to 300s delay on shutdown).
        await dashboard.wait_with_countdown(
            TASK_FOLLOWING,
            "Following refresh",
            300.0,
            stop_event,
            refresh_event,
        )

        if refresh_event.is_set():
            refresh_event.clear()

        if stop_event.is_set():
            break

        if simulator.state == "hidden":
            # Still hidden — don't poll, wait again
            continue

        try:
            state = DownloadState()
            new_names = await get_following_accounts(config, state)
            if new_names:
                config.user_names = new_names
                logger.info(
                    "daemon.runner: following list refreshed (periodic) - {} creators",
                    len(new_names),
                )
            budget.on_success()
        except DaemonUnrecoverableError:
            raise
        except Exception as exc:
            logger.warning(
                "daemon.runner: following list refresh failed (periodic) - {}", exc
            )
            budget.on_error(exc)


async def _simulator_tick_loop(
    simulator: ActivitySimulator,
    stop_event: asyncio.Event,
    ws: Any,
    refresh_event: asyncio.Event,
    dashboard: DaemonDashboard | NullDashboard,
) -> None:
    """Periodically advance the ActivitySimulator state machine.

    Logs state transitions so operators can observe daemon activity cadence.
    On an ``"unhide"`` transition, attempts to reassert the WebSocket
    connection before polling resumes, and triggers a following list refresh.

    Args:
        simulator: ActivitySimulator to tick.
        stop_event: Set to stop the loop.
        ws: FanslyWebSocket instance (or compatible stub) for reconnection.
        refresh_event: Event to set on unhide to trigger following refresh.
        dashboard: Dashboard to drive the countdown bar and status line.
    """
    # Seed the state line with the initial simulator state so the operator
    # doesn't see "initializing" for 30s until the first tick.
    dashboard.set_simulator_state(simulator.state)

    while not stop_event.is_set():
        await dashboard.wait_with_countdown(
            TASK_SIMULATOR,
            "Simulator tick",
            30.0,
            stop_event,
        )

        if stop_event.is_set():
            break

        transition = simulator.tick()
        if transition is not None:
            logger.info(
                "daemon.runner: activity state -> {} ({})",
                simulator.state,
                transition,
            )
            dashboard.set_simulator_state(simulator.state)

        if transition == "unhide":
            refresh_event.set()
            try:
                await ws.stop()
                await ws.start_background()
                dashboard.set_ws_state(True)
                ws_logger.info("daemon.runner: WebSocket reconnected after unhide")
            except Exception as exc:
                dashboard.set_ws_state(False)
                ws_logger.warning(
                    "daemon.runner: WebSocket reconnect failed after unhide - {}",
                    exc,
                )


# ---------------------------------------------------------------------------
# WS event routing
# ---------------------------------------------------------------------------


def _make_ws_handler(
    simulator: ActivitySimulator,
    queue: asyncio.Queue[WorkItem],
    budget: ErrorBudget | None = None,
) -> Any:
    """Build an async callback suitable for FanslyWebSocket.register_handler.

    The returned coroutine decodes the ServiceEvent envelope and calls
    dispatch_ws_event to translate it into a WorkItem.  Unknown envelopes and
    JSON errors are logged and swallowed so a bad frame never kills the daemon.

    Args:
        simulator: ActivitySimulator to notify of interrupt events.
        queue: Work queue to push WorkItems onto.
        budget: Optional ErrorBudget to call on_success when an item is queued.

    Returns:
        An async callable compatible with register_handler(MSG_SERVICE_EVENT, ...).
    """

    async def _on_service_event(event_data: Any) -> None:
        """Handle a decoded MSG_SERVICE_EVENT envelope from the WebSocket.

        Args:
            event_data: Decoded envelope dict with keys 'serviceId' and 'event'.
        """
        if not isinstance(event_data, dict):
            return

        service_id = event_data.get("serviceId")
        raw_event = event_data.get("event")

        if service_id is None:
            return

        try:
            inner: dict[str, Any] = (
                json.loads(raw_event)
                if isinstance(raw_event, str)
                else (raw_event or {})
            )
        except (json.JSONDecodeError, TypeError) as exc:
            ws_logger.warning(
                "daemon.runner: WS envelope decode error svc={} - {}", service_id, exc
            )
            return

        event_type = inner.get("type")
        if event_type is None:
            return

        ws_logger.debug(
            "daemon.runner: WS service event svc={} type={}",
            service_id,
            event_type,
        )

        # Let interrupt events wake the simulator even during hidden
        simulator.on_ws_event_during_hidden(service_id, event_type)

        item = dispatch_ws_event(service_id, event_type, inner)
        if item is None:
            ws_logger.debug(
                "daemon.runner: WS event not mapped to work item (svc={} type={})",
                service_id,
                event_type,
            )
            return
        ws_logger.info(
            "daemon.runner: WS event → {} (svc={} type={})",
            type(item).__name__,
            service_id,
            event_type,
        )
        await queue.put(item)
        if budget is not None:
            budget.on_success()

    return _on_service_event


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


async def run_daemon(
    config: FanslyConfig,
    *,
    ws_factory: Any = None,
    stop_event: asyncio.Event | None = None,
) -> int:
    """Enter the monitoring loop.

    Returns an integer exit code:
      - ``EXIT_SUCCESS`` (0) on clean SIGINT shutdown.
      - ``DAEMON_UNRECOVERABLE`` (-8) when ErrorBudget trips on a hard-fatal
        HTTP status (401, 418) or when no successful operation has been
        recorded for longer than ``config.unrecoverable_error_timeout_seconds``.

    Intended to be called AFTER a normal batch download completes.

    Responsibilities:
    - Own a FanslyWebSocket with dispatcher registered for MSG_SERVICE_EVENT
    - Run five concurrent loops: home timeline, story states, following refresh,
      worker, and simulator tick
    - Drain a work queue fed by WS events AND polling discoveries
    - Respect config.user_names (-u) OR config.use_following (-uf) for
      creator scope.  When -uf, refresh the following list on subscription
      confirmed events (svc=15 type=5 status=3) and periodically.
    - Honor config.monitoring_session_baseline for the first
      should_process_creator call per creator this session.
    - Clean shutdown on SIGINT: cancel pollers, drain in-flight worker items
      (up to 30 seconds), stop ws, return exit code.

    Args:
        config: FanslyConfig instance with API credentials and monitoring config.
        ws_factory: Optional callable returning a FanslyWebSocket-compatible
            object.  Defaults to creating a real FanslyWebSocket from config.
            Inject a stub in tests.

    Returns:
        Exit code: EXIT_SUCCESS (0) or DAEMON_UNRECOVERABLE (-8).
    """
    logger.info("daemon.runner: starting monitoring loop")
    logger.info(
        "daemon.runner: session baseline = {}",
        config.monitoring_session_baseline,
    )

    # stop_event accepted as a parameter so tests can drive shutdown by
    # calling .set() directly, without having to patch asyncio.get_running_loop
    # to intercept the SIGINT handler registration. Production callers pass
    # nothing and get a fresh Event.
    if stop_event is None:
        stop_event = asyncio.Event()
    refresh_event = asyncio.Event()
    queue: asyncio.Queue[WorkItem] = asyncio.Queue()

    simulator = ActivitySimulator()
    budget = ErrorBudget(
        timeout_seconds=config.unrecoverable_error_timeout_seconds,
    )

    session_baseline = config.monitoring_session_baseline
    baseline_consumed: set[int] = set()

    # ---- Dashboard ----
    # Enter the dashboard first so WebSocket startup/failure shows up on
    # the status line. make_dashboard returns a NullDashboard when the
    # user has opted out via schema.monitoring.dashboard_enabled=False.
    dashboard = make_dashboard(config.monitoring_dashboard_enabled)
    async with dashboard:
        return await _run_daemon_body(
            config=config,
            ws_factory=ws_factory,
            simulator=simulator,
            budget=budget,
            stop_event=stop_event,
            refresh_event=refresh_event,
            queue=queue,
            session_baseline=session_baseline,
            baseline_consumed=baseline_consumed,
            dashboard=dashboard,
        )


async def _run_daemon_body(
    *,
    config: FanslyConfig,
    ws_factory: Any,
    simulator: ActivitySimulator,
    budget: ErrorBudget,
    stop_event: asyncio.Event,
    refresh_event: asyncio.Event,
    queue: asyncio.Queue[WorkItem],
    session_baseline: datetime | None,
    baseline_consumed: set[int],
    dashboard: DaemonDashboard | NullDashboard,
) -> int:
    """Daemon body — extracted from ``run_daemon`` so the dashboard's async
    context manager can wrap the entire lifecycle cleanly. All parameters are
    already-constructed collaborators, not user-facing config.
    """
    # ---- WebSocket setup ----
    ws: Any = (ws_factory or _make_ws)(config)
    ws.register_handler(
        FanslyWebSocket.MSG_SERVICE_EVENT,
        _make_ws_handler(simulator, queue, budget),
    )
    try:
        await ws.start_background()
        dashboard.set_ws_state(True)
        ws_logger.info("daemon.runner: WebSocket started")
    except Exception as exc:
        dashboard.set_ws_state(False)
        ws_logger.warning(
            "daemon.runner: WebSocket failed to start (continuing without WS) - {}",
            exc,
        )

    # ---- SIGINT handler ----
    # loop.add_signal_handler replaces Python's default SIGINT handler
    # (and the main program's signal.signal registration). If the loop's
    # handler just sets stop_event, repeated Ctrl+C presses do nothing
    # extra — they all set the same already-set flag. Escalate on the
    # second press: uninstall our handler so Python's default raises
    # KeyboardInterrupt, which propagates through asyncio.gather and
    # forces immediate shutdown of any task still ignoring stop_event.
    loop = asyncio.get_running_loop()
    sigint_count = 0

    def _sigint_handler() -> None:
        nonlocal sigint_count
        sigint_count += 1
        if sigint_count == 1:
            logger.info("daemon.runner: SIGINT received - shutting down")
            stop_event.set()
        else:
            logger.warning(
                "daemon.runner: second SIGINT - forcing shutdown via KeyboardInterrupt"
            )
            with contextlib.suppress(NotImplementedError, OSError):
                loop.remove_signal_handler(signal.SIGINT)
            # Re-raise as KeyboardInterrupt on the next scheduler tick so
            # any task still blocked in a wait unblocks via cancellation.
            raise KeyboardInterrupt("daemon: force shutdown")

    with contextlib.suppress(NotImplementedError, OSError):
        loop.add_signal_handler(signal.SIGINT, _sigint_handler)

    # ---- Spawn the five loops ----
    timeline_task = asyncio.create_task(
        _timeline_poll_loop(
            config,
            simulator,
            queue,
            session_baseline,
            baseline_consumed,
            stop_event,
            budget,
            refresh_event,
            dashboard,
        ),
        name="daemon-timeline-poll",
    )
    story_task = asyncio.create_task(
        _story_poll_loop(
            config,
            simulator,
            queue,
            stop_event,
            budget,
            refresh_event,
            dashboard,
        ),
        name="daemon-story-poll",
    )
    worker_task = asyncio.create_task(
        _worker_loop(config, queue, stop_event, config.use_following, budget),
        name="daemon-worker",
    )
    sim_tick_task = asyncio.create_task(
        _simulator_tick_loop(simulator, stop_event, ws, refresh_event, dashboard),
        name="daemon-simulator-tick",
    )
    following_refresh_task = asyncio.create_task(
        _following_refresh_loop(
            config, simulator, stop_event, refresh_event, budget, dashboard
        ),
        name="daemon-following-refresh",
    )

    poller_tasks = [timeline_task, story_task, sim_tick_task, following_refresh_task]
    all_tasks = [*poller_tasks, worker_task]

    logger.info("daemon.runner: all tasks running - state={}", simulator.state)

    exit_code = EXIT_SUCCESS

    try:
        await asyncio.gather(*all_tasks, return_exceptions=False)
    except DaemonUnrecoverableError as exc:
        logger.error("daemon.runner: exiting (unrecoverable): {}", exc)
        exit_code = DAEMON_UNRECOVERABLE
    except (asyncio.CancelledError, KeyboardInterrupt):
        pass
    finally:
        logger.info("daemon.runner: stopping tasks")
        stop_event.set()
        refresh_event.set()  # wake _following_refresh_loop so it can exit

        # Cancel pollers FIRST (stop producing new work)
        for task in poller_tasks:
            task.cancel()
        await asyncio.gather(*poller_tasks, return_exceptions=True)

        # Drain worker — let it process remaining queue items, but cap the wait
        try:
            await asyncio.wait_for(worker_task, timeout=30.0)
        except TimeoutError:
            logger.warning("daemon.runner: worker did not drain in 30s; cancelling")
            worker_task.cancel()
            await asyncio.gather(worker_task, return_exceptions=True)

        # Stop the WebSocket connection
        try:
            await ws.stop()
        except Exception as exc:
            ws_logger.warning("daemon.runner: error stopping WebSocket - {}", exc)

        logger.info(
            "daemon.runner: shutdown complete at {}",
            datetime.now(UTC).isoformat(),
        )

    return exit_code
