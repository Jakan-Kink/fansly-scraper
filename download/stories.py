"""Story Downloads

Downloads ephemeral media stories for a creator. Stories are single-page
(no pagination) — one GET returns all active stories.
"""

from __future__ import annotations

from config import FanslyConfig
from metadata import MediaStoryState, get_store, process_media_stories
from textio import print_info, print_warning

from .common import get_unique_media_ids, process_download_accessible_media
from .core import DownloadState
from .media import fetch_and_process_media
from .types import DownloadType


async def download_stories(
    config: FanslyConfig,
    state: DownloadState,
    *,
    mark_viewed: bool = True,
) -> None:
    """Download all active media stories for a creator.

    Stories are ephemeral — no pagination, no cursor. A single API call
    returns all active stories with their media in aggregationData.

    Skips early if MediaStoryState.hasActiveStories is False (already
    known from the account profile fetch).

    Args:
        config: FanslyConfig instance.
        state: Current download state for the creator.
        mark_viewed: If True (default, normal download), POST to
            `/api/v1/mediastory/view` for each saved story so the
            creator sees the view. The monitoring daemon must pass
            False — it shouldn't affect the user's real Fansly UX by
            marking stories watched in the background.
    """
    store = get_store()

    # Early exit if we already know there are no active stories
    if state.creator_id:
        story_state = await store.get(MediaStoryState, state.creator_id)
        if story_state and not story_state.hasActiveStories:
            return

    print_info("Checking for active Stories...")

    state.download_type = DownloadType.STORIES

    try:
        response = await config.get_api().get_media_stories(state.creator_id)
        response.raise_for_status()

        stories_response = config.get_api().get_json_response_contents(response)
        media_stories = stories_response.get("mediaStories", [])

        if not media_stories:
            print_info("No active stories found.")
            return

        print_info(
            f"Found {len(media_stories)} active stor{'y' if len(media_stories) == 1 else 'ies'}."
        )

        # Persist metadata (MediaStory records + AccountMedia)
        saved_stories = await process_media_stories(config, stories_response)

        # Extract media IDs from aggregationData (not top-level)
        aggregation = stories_response.get("aggregationData", {})
        account_media = aggregation.get("accountMedia", [])
        media_ids = get_unique_media_ids({"accountMedia": account_media})

        if not media_ids:
            print_info("No downloadable media in stories.")
            return

        # Download accessible media using the standard pipeline
        state.start_batch()
        accessible = await fetch_and_process_media(config, state, media_ids)
        await process_download_accessible_media(config, state, accessible)

        # The monitoring daemon passes mark_viewed=False so it doesn't
        # affect the user's real Fansly UX by marking stories watched
        # in the background.
        if mark_viewed:
            await _mark_stories_viewed(config, saved_stories)

    except Exception as e:
        print_warning(f"Error downloading stories: {e}")


async def _mark_stories_viewed(config: FanslyConfig, saved_stories: list) -> None:
    """POST mediastory/view for each saved story, best-effort.

    Failures are logged and swallowed — marking a story viewed is not
    critical to the download completing successfully.
    """
    api = config.get_api()
    for story in saved_stories:
        try:
            await api.mark_story_viewed(story.id)
        except Exception as e:
            print_warning(f"Failed to mark story {story.id} as viewed: {e}")
