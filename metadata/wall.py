"""Wall management module."""

from __future__ import annotations

import copy
from typing import TYPE_CHECKING

from textio import json_output

from .models import Post, Wall, get_store
from .post import process_timeline_posts


if TYPE_CHECKING:
    from config import FanslyConfig
    from download.core import DownloadState

    from .models import Account


async def process_account_walls(
    config: FanslyConfig,  # noqa: ARG001
    account: Account,
    walls_data: list[dict],
) -> None:
    """Process walls data for an account."""
    store = get_store()
    walls_data = copy.deepcopy(walls_data)

    for wall_data in walls_data:
        if "id" not in wall_data:
            json_output(1, "meta/wall - missing id", {})
            continue

        wall_data.setdefault("accountId", account.id)
        wall = Wall.model_validate(wall_data)
        await store.save(wall)

    # Delete walls no longer in the data
    current_wall_ids = {wd["id"] for wd in walls_data if "id" in wd}
    if current_wall_ids:
        existing_walls = await store.find(Wall, accountId=account.id)
        for wall in existing_walls:
            if wall.id not in current_wall_ids:
                await store.delete(wall)


async def process_wall_posts(
    config: FanslyConfig,
    state: DownloadState,
    wall_id: str,
    posts_data: dict,
) -> None:
    """Process posts from a specific wall."""
    store = get_store()
    posts_data = copy.deepcopy(posts_data)

    await process_timeline_posts(config, state, posts_data)

    wall, _created = await store.get_or_create(
        Wall,
        defaults={"accountId": state.creator_id},
        id=int(wall_id),
    )

    # Add posts to wall via relationship — dirty tracking handles junction sync
    post_ids = [post["id"] for post in posts_data["posts"]]
    for pid in post_ids:
        post = await store.get(Post, pid)
        if post and post not in wall.posts:
            wall.posts.append(post)

    await store.save(wall)
