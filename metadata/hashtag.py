"""Hashtag processing module."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from .models import Hashtag, get_store


if TYPE_CHECKING:
    from .models import Post


def extract_hashtags(content: str) -> list[str]:
    """Extract hashtags from post content.

    Returns:
        List of hashtag values (without #), deduplicated and lowercased.
    """
    if not content:
        return []

    pattern = r"#+([\w]+)"
    hashtags = []
    for raw_tag in re.findall(pattern, content):
        tag = raw_tag.strip()
        if not tag:
            continue
        hashtags.append(tag.lower())

    # Remove duplicates while preserving order
    seen: set[str] = set()
    return [tag for tag in hashtags if not (tag in seen or seen.add(tag))]


async def process_post_hashtags(
    post_obj: Post,
    content: str,
) -> None:
    """Process hashtags for a post.

    Extracts hashtags from content, creates missing hashtag records,
    and adds them to the post's hashtags relationship.
    """
    if not content:
        return

    hashtag_values = extract_hashtags(content)
    if not hashtag_values:
        return

    store = get_store()

    for value in hashtag_values:
        # Case-insensitive lookup
        existing = await store.find_one(Hashtag, value__iexact=value)
        if existing is None:
            existing, _created = await store.get_or_create(
                Hashtag, defaults={"value": value}, value=value
            )

        if existing and existing not in post_obj.hashtags:
            post_obj.hashtags.append(existing)

    # Dirty tracking on post_obj.hashtags triggers _sync_associations on save
