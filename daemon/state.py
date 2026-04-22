"""Daemon-side persistence of per-creator monitoring state.

Thin wrapper around MonitorState rows in the identity map / DB, used
by the daemon runner to record when each creator was last processed.
Split from daemon/filters.py per the planning doc's Optimization 3
section (which distinguishes filter DECISIONS from state PERSISTENCE).
"""

from __future__ import annotations

from datetime import UTC, datetime

from loguru import logger

from metadata.models import Account, MonitorState, get_store


async def mark_creator_processed(creator_id: int) -> None:
    """Record the current time as MonitorState.lastCheckedAt after a run.

    Records:
    - ``lastCheckedAt``: wall-clock time of this call (baseline for the next
      call to should_process_creator)
    - ``lastRunAt``: same wall-clock time
    - ``updatedAt``: same wall-clock time

    Creates a new MonitorState row if none exists yet. Logs and swallows
    exceptions so a failed snapshot never aborts the daemon loop.

    Args:
        creator_id: Fansly account ID of the creator that was just processed.
    """
    store = get_store()

    # Guard: Account must exist before we can create a MonitorState row (FK).
    account: Account | None = store.get_from_cache(Account, creator_id)
    if account is None:
        try:
            account = await store.get(Account, creator_id)
        except Exception as exc:
            logger.warning(
                "daemon.state: could not load Account {} for snapshot — skipping: {}",
                creator_id,
                exc,
            )
            return

    if account is None:
        logger.debug(
            "daemon.state: Account {} not found — skipping MonitorState update",
            creator_id,
        )
        return

    now = datetime.now(UTC)

    try:
        state: MonitorState | None = await store.get(MonitorState, creator_id)
        if state is not None:
            state.lastCheckedAt = now
            state.lastRunAt = now
            state.updatedAt = now
            await store.save(state)
        else:
            new_state = MonitorState(
                creatorId=creator_id,
                lastCheckedAt=now,
                lastRunAt=now,
                updatedAt=now,
            )
            await store.save(new_state)
    except Exception as exc:
        logger.warning(
            "daemon.state: could not save MonitorState for creator {} — skipping: {}",
            creator_id,
            exc,
        )
