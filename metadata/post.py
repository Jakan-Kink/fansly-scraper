"""Post processing module."""

from __future__ import annotations

import copy
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from textio import json_output

from .account import process_account_data, process_media_bundles_data
from .hashtag import process_post_hashtags
from .media import process_media_info
from .models import Account as AccountModel
from .models import Post, get_store


if TYPE_CHECKING:
    from config import FanslyConfig
    from download.core import DownloadState

    from .models import Account


async def process_pinned_posts(
    config: FanslyConfig,  # noqa: ARG001
    account: Account,
    posts: list[dict],
) -> None:
    """Process pinned posts using sync_junction."""
    store = get_store()
    posts = copy.deepcopy(posts)

    if not posts:
        return

    junction_rows = []
    for post in posts:
        post_id = post["postId"]
        existing = await store.get(Post, post_id)
        if existing is None:
            json_output(
                1,
                "meta/post - p_p_p - skipping_missing_post",
                {"postId": post_id, "accountId": account.id},
            )
            continue

        junction_rows.append(
            {
                "postId": post_id,
                "pos": post["pos"],
                "createdAt": datetime.fromtimestamp(post["createdAt"] / 1000, UTC),
            }
        )

    if junction_rows:
        await store.sync_junction(
            "pinned_posts", "accountId", account.id, junction_rows
        )


async def process_timeline_posts(
    config: FanslyConfig,
    state: DownloadState,
    posts_data: dict[str, Any],
) -> None:
    """Process timeline posts and related data."""
    store = get_store()
    posts = copy.deepcopy(posts_data)

    # Process accounts
    if state.creator_id:
        account = await store.get(AccountModel, state.creator_id)
        if not account and "account" in posts:
            await process_account_data(config, data=posts["account"])

    for account_data in posts.get("accounts", []):
        await process_account_data(config, data=account_data)

    # Process posts
    for post in posts.get("posts", []):
        await _process_timeline_post(post)

    for post in posts.get("aggregatedPosts", []):
        await _process_timeline_post(post)

    # Process media in batches
    account_media = posts.get("accountMedia", [])
    batch_size = 15
    for i in range(0, len(account_media), batch_size):
        batch = account_media[i : i + batch_size]
        await process_media_info(config, {"batch": batch})

    # Process media bundles
    await process_media_bundles_data(config, posts, id_fields=["accountId"])


async def _process_timeline_post(post: dict) -> None:
    """Process a single timeline post.

    Post._prepare_post_data handles:
    - accountMentions → mentions rename + dedup
    - Attachment contentType filtering (TIP, TIP_GOALS, unknown)

    _process_nested_cache_lookups handles:
    - Resolving attachment/mention dicts → objects with FK injection

    store.save() handles:
    - Persisting Post + _sync_associations for attachments, mentions, hashtags, walls
    """
    store = get_store()

    if "accountId" not in post:
        json_output(
            1,
            "meta/post - missing_required_field",
            {"postId": post.get("id"), "missing_field": "accountId"},
        )
        return

    post_obj = Post.model_validate(post)

    # Hashtags are extracted from content text, not nested in the API dict —
    # needs async store lookups, so can't be in a sync model validator
    if post_obj.content:
        await process_post_hashtags(post_obj, post_obj.content)

    await store.save(post_obj)
