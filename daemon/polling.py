"""Daemon polling loops for detecting new content across followed creators.

Provides two async polling functions:

``poll_home_timeline`` — calls ``GET /api/v1/timeline/home`` once and uses the
identity-map cache to identify posts that are not yet known. Returns a tuple of
(new_creator_ids, posts_by_creator) where posts_by_creator maps every creator
that appeared on the timeline page to their post dicts (including creators with
only known posts), and new_creator_ids is the subset with at least one unknown
post. No MonitorState interaction.

``poll_story_states`` — calls ``GET /api/v1/mediastories/following`` once and
compares each creator's active-story signal against the previously persisted
``MonitorState.lastHasActiveStories``. A creator is considered active when
``hasActiveStories`` is truthy OR ``storyCount > 0``. Returns the list of
creator IDs that just flipped from inactive/unknown to active.

Both functions swallow all exceptions from API or store operations and return
an empty result so the daemon loop can continue uninterrupted.
"""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
from loguru import logger

from config.fanslyconfig import FanslyConfig
from metadata.models import MonitorState, Post, get_store


async def poll_home_timeline(
    config: FanslyConfig,
) -> tuple[set[int], dict[int, list[dict]]]:
    """Poll /timeline/home once. Return (new_creator_ids, posts_by_creator).

    ``posts_by_creator`` maps every creator that appeared on the timeline page
    to their list of post dicts, including creators whose posts are all already
    cached. ``new_creator_ids`` is the subset of creators that have at least
    one post absent from the local cache.

    The runner can pass ``posts_by_creator[cid]`` to ``should_process_creator``'s
    ``prefetched_posts`` kwarg to skip the redundant per-creator fetch.

    Uses ``store.get_from_cache(Post, id)`` to identify which posts are
    already known — no MonitorState needed for this loop. The identity
    map is preloaded at store init.

    Args:
        config: FanslyConfig instance with an initialised API client.

    Returns:
        Tuple of (new_creator_ids, posts_by_creator). Both are empty on API
        failure. posts_by_creator contains ALL creators seen on the page;
        new_creator_ids is only those with at least one unknown post.
    """
    try:
        response = await config.get_api().get_home_timeline()
        data = config.get_api().get_json_response_contents(response)
    except httpx.HTTPError as exc:
        logger.warning("daemon.polling: home timeline API call failed — {}", exc)
        return set(), {}
    except Exception as exc:
        logger.warning(
            "daemon.polling: unexpected error fetching home timeline — {}", exc
        )
        return set(), {}

    store = get_store()
    new_creator_ids: set[int] = set()
    posts_by_creator: dict[int, list[dict]] = {}

    posts: list[dict] = data.get("posts", []) if isinstance(data, dict) else []

    for post in posts:
        post_id: int = post["id"]
        creator_id: int = post["accountId"]
        posts_by_creator.setdefault(creator_id, []).append(post)
        if store.get_from_cache(Post, post_id) is None:
            new_creator_ids.add(creator_id)

    return new_creator_ids, posts_by_creator


async def poll_story_states(
    config: FanslyConfig,
) -> list[int]:
    """Poll /mediastories/following once. Return the list of creator IDs
    whose hasActiveStories flipped from False (or None) to True since
    the last poll.

    Reads ``MonitorState.lastHasActiveStories`` for each creator in the
    response. Updates ``MonitorState.lastHasActiveStories`` to the new
    value (so the next poll sees the current state). Commits the updates
    to the store.

    Args:
        config: FanslyConfig instance with an initialised API client.

    Returns:
        List of creator IDs (ints) whose story state flipped from
        inactive/unknown to active. Empty list on API failure.
    """
    try:
        response = await config.get_api().get_story_states_following()
        states: list[dict] = config.get_api().get_json_response_contents(response)
    except httpx.HTTPError as exc:
        logger.warning("daemon.polling: story states API call failed — {}", exc)
        return []
    except Exception as exc:
        logger.warning(
            "daemon.polling: unexpected error fetching story states — {}", exc
        )
        return []

    if not isinstance(states, list):
        logger.warning(
            "daemon.polling: unexpected story states response shape — {!r}",
            type(states),
        )
        return []

    store = get_store()
    creators_with_new_stories: list[int] = []

    for state in states:
        creator_id: int = state["accountId"]
        is_active: bool = bool(
            state.get("hasActiveStories", False) or state.get("storyCount", 0) > 0
        )

        try:
            existing: MonitorState | None = await store.get(MonitorState, creator_id)
            was_active: bool | None = (
                existing.lastHasActiveStories if existing is not None else None
            )

            # Detect flip: newly active where previously inactive or unknown
            if is_active and not was_active:
                creators_with_new_stories.append(creator_id)

            # Persist the new state regardless of direction so the next poll
            # sees a consistent baseline (including creators who went False).
            if existing is not None:
                existing.lastHasActiveStories = is_active
                existing.updatedAt = datetime.now(UTC)
                await store.save(existing)
            else:
                new_state = MonitorState(
                    creatorId=creator_id,
                    lastHasActiveStories=is_active,
                )
                await store.save(new_state)

        except Exception as exc:
            logger.warning(
                "daemon.polling: could not save MonitorState for creator {} — {}; "
                "skipping (state not persisted, creator not returned)",
                creator_id,
                exc,
            )

    return creators_with_new_stories
