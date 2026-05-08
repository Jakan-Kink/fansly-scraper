"""Daemon creator-filter utilities."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from loguru import logger

from metadata.models import MonitorState, get_store


if TYPE_CHECKING:
    from config import FanslyConfig


# Maximum number of timeline pages fetched while looking for a non-pinned post.
# 3 pages covers ~45 posts (15 per page), which is enough to detect even
# heavily-pinned creators without burning excessive API quota. Creators with
# more than 45 consecutive pinned posts are treated as pinned-heavy and skipped.
MAX_FILTER_PAGES = 3


def _parse_created_at(raw: Any) -> datetime | None:
    """Coerce a post's ``createdAt`` value to a UTC datetime, or None.

    Fansly returns ``createdAt`` as milliseconds since epoch in raw API
    responses, and ``convert_ids_to_int`` preserves that int form. The
    ``_parse_timestamp`` heuristic (``> 1e10`` -- ms) is mirrored here so
    we can reuse the exact same boundary the metadata-model validators
    use, without importing the private helper.
    """
    if isinstance(raw, datetime):
        return raw
    if isinstance(raw, (int, float)):
        ts = raw / 1000 if raw > 1e10 else raw
        return datetime.fromtimestamp(ts, UTC)
    return None


def _is_newer_than_baseline(
    post: dict,
    baseline: datetime,
    creator_id: int,
) -> bool:
    """Compare a post's createdAt to the baseline.

    Returns True when the post is strictly newer than the baseline.
    Returns True conservatively when the timestamp is unparseable so we
    do not miss content.
    """
    latest = _parse_created_at(post.get("createdAt", 0))
    if latest is None:
        logger.warning(
            "daemon.filters: unrecognised createdAt for creator {} -- processing anyway",
            creator_id,
        )
        return True
    if latest > baseline:
        return True
    logger.debug(
        "daemon.filters: skipping creator {} -- latest post {} not newer than baseline {}",
        creator_id,
        latest,
        baseline,
    )
    return False


def _examine_page(
    posts: list[dict],
    baseline: datetime,
    creator_id: int,
) -> bool | None:
    """Classify one timeline page against the baseline.

    Returns:
        True -- non-pinned post found and it is newer than baseline.
        False -- empty page, or non-pinned post found but not newer.
        None -- all posts on this page are pinned; caller should fetch next page.
    """
    if not posts:
        return False
    non_pinned = [p for p in posts if not p.get("pinned", False)]
    if non_pinned:
        return _is_newer_than_baseline(non_pinned[0], baseline, creator_id)
    return None  # all pinned -- keep paginating


async def should_process_creator(
    config: FanslyConfig,
    creator_id: int,
    *,
    session_baseline: datetime | None = None,
    prefetched_posts: list[dict] | None = None,
) -> bool:
    """Return True if the creator should be processed this daemon tick.

    Pinned posts are excluded — their ``createdAt`` is original publish time, not recent activity.
    Returns True conservatively on API failure or first run (no state row).
    Baseline priority: ``session_baseline`` > ``MonitorState.lastCheckedAt`` > None (first run).
    """
    store = get_store()

    # -- Determine effective baseline -----------------------------------------
    effective_baseline: datetime | None = session_baseline
    if effective_baseline is None:
        try:
            state: MonitorState | None = await store.get(MonitorState, creator_id)
            effective_baseline = state.lastCheckedAt if state is not None else None
        except Exception as exc:
            logger.warning(
                "daemon.filters: could not load MonitorState {} -- processing anyway: {}",
                creator_id,
                exc,
            )
            return True

    if effective_baseline is None:
        # First run for this creator -- no baseline to compare against.
        return True

    # -- Handle prefetched first page (F1 prep) --------------------------------
    if prefetched_posts is not None:
        decision = _examine_page(prefetched_posts, effective_baseline, creator_id)
        if decision is not None:
            return decision
        # All pinned in prefetched -- derive cursor from oldest post
        cursor = str(prefetched_posts[-1].get("id", "0")) if prefetched_posts else "0"
    else:
        cursor = "0"

    # -- Paginate until non-pinned found or cap reached -----------------------
    for _page in range(MAX_FILTER_PAGES):
        try:
            response = await config._api.get_timeline(creator_id, cursor)
            data = config._api.get_json_response_contents(response)
            posts: list[dict] = data.get("posts", [])
        except Exception as exc:
            logger.warning(
                "daemon.filters: timeline fetch failed for creator {} page cursor={} -- processing anyway: {}",
                creator_id,
                cursor,
                exc,
            )
            return True

        decision = _examine_page(posts, effective_baseline, creator_id)
        if decision is not None:
            return decision

        # All pinned on this page -- advance cursor to oldest post's id
        cursor = str(posts[-1].get("id", "0"))

    # All MAX_FILTER_PAGES pages were pinned -- legit pinned-heavy creator
    logger.debug(
        "daemon.filters: skipping creator {} after {} all-pinned pages",
        creator_id,
        MAX_FILTER_PAGES,
    )
    return False
