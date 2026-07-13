"""Wall-filter resolution against the creator's live wall list."""

import httpx

from config import FanslyConfig
from config.wall_filters import WallFilterSpec, is_snowflake_token
from download.downloadstate import DownloadState
from errors import ApiError
from helpers.common import expect_dict, expect_list, str_or_none
from metadata import Wall
from metadata.models import get_store
from textio import print_warning


def _spec_for_creator(
    config: FanslyConfig, state: DownloadState
) -> WallFilterSpec | None:
    if not config.wall_filters or not state.creator_name:
        return None
    return config.wall_filters.get(state.creator_name.lower())


def _match_tokens(
    tokens: list[str],
    wall_ids: set[int],
    ids_by_name: dict[str, set[int]],
    walls_display: str,
    creator: str | None,
) -> set[int]:
    matched: set[int] = set()
    for token in tokens:
        tok = token.strip()
        hit: set[int] = set()
        if is_snowflake_token(tok) and int(tok) in wall_ids:
            hit = {int(tok)}
        if not hit:
            hit = ids_by_name.get(tok.casefold(), set())
        if hit:
            matched |= hit
        else:
            print_warning(
                f"wall_filters: no wall matching '{token}' for creator "
                f"'{creator}'. Available walls: {walls_display}"
            )
    return matched


async def resolve_wall_filter(config: FanslyConfig, state: DownloadState) -> set[int]:
    """Resolve the creator's wall_filters spec against state.walls.

    Args:
        config: Active configuration carrying wall_filters.
        state: Per-creator download state with walls populated.

    Returns:
        The wall IDs to download; all of state.walls when the filter is
        inactive for this creator.
    """
    spec = _spec_for_creator(config, state)
    if spec is None or spec.all_walls:
        return set(state.walls)

    store = get_store()
    wall_ids = set(state.walls)
    sorted_ids = sorted(wall_ids)
    walls = await store.get_many(Wall, sorted_ids)
    walls_by_id = {wall.id: wall for wall in walls}
    ids_by_name: dict[str, set[int]] = {}
    display_parts: list[str] = []
    for wall_id in sorted_ids:
        wall = walls_by_id.get(wall_id)
        name = wall.name if wall and wall.name else None
        if name:
            ids_by_name.setdefault(name.casefold(), set()).add(wall_id)
            display_parts.append(f"'{name}' ({wall_id})")
        else:
            display_parts.append(str(wall_id))
    walls_display = ", ".join(display_parts)

    if spec.includes:
        resolved = _match_tokens(
            spec.includes, wall_ids, ids_by_name, walls_display, state.creator_name
        )
    else:
        resolved = set(wall_ids)
    resolved -= _match_tokens(
        spec.excludes, wall_ids, ids_by_name, walls_display, state.creator_name
    )

    if not resolved:
        print_warning(
            f"No walls matched wall_filters for creator "
            f"'{state.creator_name}' - nothing to download."
        )
    return resolved


async def resolve_wall_filter_id_keys(config: FanslyConfig) -> None:
    """Re-key snowflake account-ID wall_filters entries to usernames.

    Makes one batched account lookup; unknown IDs are dropped from both
    wall_filters and user_names with a warning. IDs that resolve to a
    username already present in wall_filters are dropped too, rather than
    clobbering that entry.
    """
    id_keys = [key for key in config.wall_filters if is_snowflake_token(key)]
    if not id_keys:
        return

    config._ephemeral_overrides.add("wall_filters")

    batch_size = config.account_ids_batch_size
    accounts = []
    for start in range(0, len(id_keys), batch_size):
        chunk = id_keys[start : start + batch_size]
        try:
            response = await config.get_api().get_account_info_by_id(chunk)
            response.raise_for_status()
        except httpx.HTTPStatusError as e:
            raise ApiError(f"wall_filters: account lookup by ID failed: {e}") from e
        accounts.extend(
            expect_list(
                config.get_api().get_json_response_contents(response), "accounts"
            )
        )
    username_by_id: dict[str, str] = {}
    for account_value in accounts:
        account = expect_dict(account_value, "account")
        username = str_or_none(account.get("username"))
        if username is not None:
            username_by_id[str(account["id"])] = username.lower()

    for key in id_keys:
        username = username_by_id.get(key)
        if username is None:
            print_warning(
                f"wall_filters: no account found for ID {key} - skipping this entry."
            )
            config.wall_filters.pop(key)
            if config.user_names:
                config.user_names.discard(key)
            continue
        if username in config.wall_filters:
            print_warning(
                f"wall_filters: ID {key} resolves to '{username}', which "
                "already has its own wall_filters entry - skipping the "
                "duplicate ID entry."
            )
            config.wall_filters.pop(key)
            if config.user_names:
                config.user_names.discard(key)
            continue
        config.wall_filters[username] = config.wall_filters.pop(key)
        if config.user_names:
            config.user_names.discard(key)
            config.user_names.add(username)
