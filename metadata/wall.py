"""Wall management module."""

from __future__ import annotations

import copy
from typing import TYPE_CHECKING

from pydantic import JsonValue

from helpers.common import JsonDict, expect_dict, expect_int, expect_list
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
    walls_data: list[JsonValue],
) -> None:
    """Process walls data for an account."""
    store = get_store()
    walls_data = copy.deepcopy(walls_data)

    current_wall_ids = set()
    for raw_wall in walls_data:
        wall_data = expect_dict(raw_wall, "wall")
        if "id" not in wall_data:
            json_output(1, "meta/wall - missing id", {})
            continue

        current_wall_ids.add(wall_data["id"])
        wall_data.setdefault("accountId", account.id)
        wall = Wall.model_validate(wall_data)
        await store.save(wall)

    # Delete walls no longer in the data
    if current_wall_ids:
        existing_walls = await store.find(Wall, accountId=account.id)
        for wall in existing_walls:
            if wall.id not in current_wall_ids:
                await store.delete(wall)


async def process_wall_posts(
    config: FanslyConfig,
    state: DownloadState,
    wall_id: int | str,
    posts_data: JsonDict,
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
    posts_list = expect_list(posts_data["posts"], "wall posts")
    post_ids = [expect_int(expect_dict(p, "post")["id"], "post id") for p in posts_list]
    for pid in post_ids:
        post = await store.get(Post, pid)
        if post and post not in wall.posts:
            wall.posts.append(post)

    await store.save(wall)
