"""MediaStory processing module.

Handles ephemeral media stories — thin wrappers linking accounts to
AccountMedia items. Stories are fetched via GET /api/v1/mediastoriesnew
and the media lives in aggregationData.accountMedia (not top-level like
timeline responses).
"""

from __future__ import annotations

import copy
from typing import TYPE_CHECKING, Any

from textio import json_output

from .media import process_media_info
from .models import MediaStory, get_store


if TYPE_CHECKING:
    from config import FanslyConfig


async def process_media_stories(
    config: FanslyConfig,
    stories_response: dict[str, Any],
) -> list[MediaStory]:
    """Persist MediaStory records and their AccountMedia from a stories response.

    The stories response nests accountMedia under aggregationData:
        {
            "mediaStories": [{id, accountId, contentType, contentId, ...}],
            "aggregationData": {
                "accountMedia": [{id, mediaId, media: {...}, ...}]
            }
        }

    Args:
        config: Application configuration
        stories_response: Unwrapped response from GET /api/v1/mediastoriesnew

    Returns:
        List of persisted MediaStory objects
    """
    store = get_store()
    data = copy.deepcopy(stories_response)

    media_stories_raw = data.get("mediaStories", [])
    if not media_stories_raw:
        return []

    # Extract and persist accountMedia from aggregationData
    aggregation = data.get("aggregationData", {})
    account_media = aggregation.get("accountMedia", [])

    batch_size = 15
    for i in range(0, len(account_media), batch_size):
        batch = account_media[i : i + batch_size]
        await process_media_info(config, {"batch": batch})

    # Persist each MediaStory wrapper
    saved: list[MediaStory] = []
    for story_dict in media_stories_raw:
        if "accountId" not in story_dict:
            json_output(
                1,
                "meta/story - missing_accountId",
                {"storyId": story_dict.get("id")},
            )
            continue

        story = MediaStory.model_validate(story_dict)
        await store.save(story)
        saved.append(story)

    return saved
