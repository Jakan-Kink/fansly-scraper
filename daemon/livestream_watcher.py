"""Livestream watcher.

Polls ``GET /api/v1/streaming/followingstreams/online`` every
``monitoring_livestream_poll_interval_seconds`` (default 30 s), diffs the
result against the active recording set, and starts / signals stop on
``download.livestream._record_stream`` tasks accordingly.

Architecture
------------
``start_livestream_watcher(config, stop_event)``
    Creates the long-running asyncio task and returns it.  Callers are
    responsible for awaiting / cancelling it on shutdown.

``_watcher_loop(config, stop_event)``
    Poll → diff against ``_active_recordings`` → spawn/stop recording tasks.

The actual recording lifecycle (master/variant resolution, segment poll
loop, PyAV mux) lives in ``download.livestream``.
"""

from __future__ import annotations

import asyncio
import contextlib
import threading

from loguru import logger

from config.fanslyconfig import FanslyConfig
from download.livestream import _record_stream, _salvage_orphan_segments
from metadata.models import StreamChannel, StreamingInfo


# ── Internal state ─────────────────────────────────────────────────────────

# Maps creator_id → (recording_task, per-recording stop event).
_active_recordings: dict[int, tuple[asyncio.Task, asyncio.Event]] = {}
_recordings_lock = threading.Lock()


# Seconds to wait for active recordings to finalize during shutdown.
_SHUTDOWN_TIMEOUT = 60.0


# ── Public API ──────────────────────────────────────────────────────────────


def start_livestream_watcher(
    config: FanslyConfig,
    stop_event: asyncio.Event,
) -> asyncio.Task:
    """Launch the long-running watcher loop as an asyncio Task.

    Args:
        config: FanslyConfig with ``setup_api()`` already called.
        stop_event: Set this to stop the watcher (and all active recordings).

    Returns:
        The running asyncio.Task — callers should await it on shutdown.
    """
    task = asyncio.create_task(
        _watcher_loop(config, stop_event),
        name="livestream-watcher",
    )
    logger.info(
        "daemon.livestream_watcher: watcher started (poll interval {}s)",
        config.monitoring_livestream_poll_interval_seconds,
    )
    return task


async def stop_all_recordings() -> None:
    """Signal all active recordings to stop and wait for them.

    Called during daemon shutdown to give recorders time to flush and
    finalize the MP4 moov atom before the process exits.  Waits up to
    ``_SHUTDOWN_TIMEOUT`` seconds before cancelling stragglers.
    """
    with _recordings_lock:
        items = list(_active_recordings.values())

    if not items:
        return

    logger.info(
        "daemon.livestream_watcher: stopping {} active recording(s)…",
        len(items),
    )
    for _, rec_stop in items:
        rec_stop.set()

    tasks = [task for task, _ in items]
    try:
        async with asyncio.timeout(_SHUTDOWN_TIMEOUT):
            await asyncio.gather(*tasks, return_exceptions=True)
    except TimeoutError:
        pending = [t for t in tasks if not t.done()]
        logger.warning(
            "daemon.livestream_watcher: {} recording(s) did not finish "
            "within {}s — cancelling",
            len(pending),
            _SHUTDOWN_TIMEOUT,
        )
        for task in pending:
            task.cancel()


# ── Watcher loop ───────────────────────────────────────────────────────────


async def _watcher_loop(
    config: FanslyConfig,
    stop_event: asyncio.Event,
) -> None:
    """Poll followingstreams/online and manage recording tasks."""
    interval = config.monitoring_livestream_poll_interval_seconds

    # Recover any segment dirs left behind by prior aborted recordings.
    await _salvage_orphan_segments(config)

    while not stop_event.is_set():
        try:
            await _poll_and_diff(config, stop_event)
        except Exception as exc:
            logger.warning(
                "daemon.livestream_watcher: poll error — {}; will retry in {}s",
                exc,
                interval,
            )

        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(
                asyncio.shield(stop_event.wait()),
                timeout=float(interval),
            )

    # Global stop: signal all recordings.
    await stop_all_recordings()
    logger.info("daemon.livestream_watcher: stopped")


async def _poll_and_diff(
    config: FanslyConfig,
    stop_event: asyncio.Event,
) -> None:
    """One poll cycle: fetch live creators, start/stop recording tasks."""
    if not config.monitoring_livestream_recording_enabled:
        return

    api = config.get_api()
    try:
        response = await api.get_following_streams_online()
        data = api.get_json_response_contents(response)
    except Exception as exc:
        logger.warning(
            "daemon.livestream_watcher: followingstreams/online failed — {}",
            exc,
        )
        return

    if not isinstance(data, dict):
        return

    # Parse accounts from aggregationData (they carry the streaming.channel).
    accounts_raw: list[dict] = data.get("aggregationData", {}).get("accounts", [])

    # Build a mapping of creator_id → StreamChannel for everyone currently
    # live AND in scope for this invocation. Scope-filter using the username
    # from the API payload directly — no metadata-store roundtrip per check,
    # because the str-based predicate ``config.is_username_in_scope`` lives
    # entirely off ``config.user_names`` + ``config.use_following``.
    #
    # Fixes the #94 livestream half: pre-fix, the watcher spawned a
    # recording task for every creator returned by
    # ``/streaming/followingstreams/online`` (every followed creator who
    # was live), ignoring the operator's ``targeted_creator.usernames``
    # / ``-u`` whitelist.
    currently_live: dict[int, tuple[str, StreamChannel]] = {}
    for raw_account in accounts_raw:
        streaming_raw = raw_account.get("streaming")
        if not streaming_raw:
            continue
        try:
            streaming_info = StreamingInfo.model_validate(streaming_raw)
        except Exception as exc:
            logger.debug(
                "daemon.livestream_watcher: skip malformed streaming payload — {}",
                exc,
            )
            continue
        if streaming_info.channel is None or streaming_info.channel.status != 2:
            continue
        creator_id = int(streaming_info.accountId)
        username = raw_account.get("username", str(creator_id))
        if not config.is_username_in_scope(username):
            logger.debug(
                "daemon.livestream_watcher: live creator {} ({}) out of scope "
                "— skipping",
                username,
                creator_id,
            )
            continue
        currently_live[creator_id] = (username, streaming_info.channel)

    with _recordings_lock:
        active_ids = set(_active_recordings.keys())

    live_ids = set(currently_live.keys())

    # New live creators → spawn recording tasks.
    for creator_id in live_ids - active_ids:
        username, channel = currently_live[creator_id]
        logger.info(
            "daemon.livestream_watcher: {} ({}) went live — starting recording",
            username,
            creator_id,
        )
        rec_stop = asyncio.Event()
        task = asyncio.create_task(
            _record_stream(config, creator_id, username, channel, rec_stop, stop_event),
            name=f"livestream-record-{creator_id}",
        )
        with _recordings_lock:
            _active_recordings[creator_id] = (task, rec_stop)

    # Ended streams → signal their recording tasks to stop.
    for creator_id in active_ids - live_ids:
        logger.info(
            "daemon.livestream_watcher: creator {} dropped from live list — "
            "signalling recorder to stop",
            creator_id,
        )
        with _recordings_lock:
            entry = _active_recordings.get(creator_id)
        if entry is not None:
            _, rec_stop = entry
            rec_stop.set()

    # Reap finished tasks.
    with _recordings_lock:
        finished = [cid for cid, (task, _) in _active_recordings.items() if task.done()]
        for cid in finished:
            _active_recordings.pop(cid, None)
