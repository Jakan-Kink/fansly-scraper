"""Unit tests for download.wallfilters resolution."""

import httpx
import pytest
import respx

from config.wall_filters import WallFilterSpec
from download.downloadstate import DownloadState
from download.wallfilters import resolve_wall_filter, resolve_wall_filter_id_keys
from errors import ApiError
from tests.fixtures.api import dump_fansly_calls
from tests.fixtures.metadata import AccountFactory, WallFactory
from tests.fixtures.utils.test_isolation import snowflake_id


async def _seed_walls(entity_store, creator_id, names):
    """Persist an Account plus one Wall per name; returns {name: wall_id}."""
    account = AccountFactory(id=creator_id, username="creator1")
    await entity_store.save(account)
    ids = {}
    for name in names:
        wall = WallFactory(id=snowflake_id(), accountId=creator_id, name=name)
        await entity_store.save(wall)
        ids[name] = wall.id
    return ids


@pytest.mark.asyncio
class TestResolveWallFilter:
    async def test_inactive_filter_returns_all(self, mock_config, entity_store):
        creator_id = snowflake_id()
        ids = await _seed_walls(entity_store, creator_id, ["Promos", "FULL VIDEOS"])
        state = DownloadState(creator_name="creator1")
        state.creator_id = creator_id
        state.walls = set(ids.values())
        mock_config.wall_filters = {}
        assert await resolve_wall_filter(mock_config, state) == set(ids.values())

    async def test_name_match_case_insensitive(self, mock_config, entity_store):
        creator_id = snowflake_id()
        ids = await _seed_walls(entity_store, creator_id, ["Promos", "FULL VIDEOS"])
        state = DownloadState(creator_name="creator1")
        state.creator_id = creator_id
        state.walls = set(ids.values())
        mock_config.wall_filters = {
            "creator1": WallFilterSpec(includes=["full videos"])
        }
        assert await resolve_wall_filter(mock_config, state) == {ids["FULL VIDEOS"]}

    async def test_id_match_and_mixed_spec(self, mock_config, entity_store):
        creator_id = snowflake_id()
        ids = await _seed_walls(
            entity_store, creator_id, ["Promos", "FULL VIDEOS", "Extras"]
        )
        state = DownloadState(creator_name="creator1")
        state.creator_id = creator_id
        state.walls = set(ids.values())
        mock_config.wall_filters = {
            "creator1": WallFilterSpec(includes=[str(ids["Promos"]), "extras"])
        }
        assert await resolve_wall_filter(mock_config, state) == {
            ids["Promos"],
            ids["Extras"],
        }

    async def test_excludes_only_and_overlap(self, mock_config, entity_store):
        creator_id = snowflake_id()
        ids = await _seed_walls(entity_store, creator_id, ["Promos", "FULL VIDEOS"])
        state = DownloadState(creator_name="creator1")
        state.creator_id = creator_id
        state.walls = set(ids.values())
        mock_config.wall_filters = {"creator1": WallFilterSpec(excludes=["promos"])}
        assert await resolve_wall_filter(mock_config, state) == {ids["FULL VIDEOS"]}
        # include+exclude overlap -> excluded wins
        mock_config.wall_filters = {
            "creator1": WallFilterSpec(
                includes=["Promos", "FULL VIDEOS"], excludes=["Promos"]
            )
        }
        assert await resolve_wall_filter(mock_config, state) == {ids["FULL VIDEOS"]}

    async def test_duplicate_names_all_match(self, mock_config, entity_store):
        creator_id = snowflake_id()
        ids_a = await _seed_walls(entity_store, creator_id, ["PPV"])
        wall_b = WallFactory(id=snowflake_id(), accountId=creator_id, name="PPV")
        await entity_store.save(wall_b)
        state = DownloadState(creator_name="creator1")
        state.creator_id = creator_id
        state.walls = {ids_a["PPV"], wall_b.id}
        mock_config.wall_filters = {"creator1": WallFilterSpec(includes=["ppv"])}
        assert await resolve_wall_filter(mock_config, state) == {
            ids_a["PPV"],
            wall_b.id,
        }

    async def test_no_match_returns_empty(self, mock_config, entity_store):
        creator_id = snowflake_id()
        ids = await _seed_walls(entity_store, creator_id, ["Promos"])
        state = DownloadState(creator_name="creator1")
        state.creator_id = creator_id
        state.walls = set(ids.values())
        mock_config.wall_filters = {
            "creator1": WallFilterSpec(includes=["renamed-away"])
        }
        assert await resolve_wall_filter(mock_config, state) == set()

    async def test_all_walls_spec_unfiltered(self, mock_config, entity_store):
        creator_id = snowflake_id()
        ids = await _seed_walls(entity_store, creator_id, ["Promos", "FULL VIDEOS"])
        state = DownloadState(creator_name="creator1")
        state.creator_id = creator_id
        state.walls = set(ids.values())
        mock_config.wall_filters = {"creator1": WallFilterSpec(all_walls=True)}
        assert await resolve_wall_filter(mock_config, state) == set(ids.values())

    async def test_unnamed_wall_shown_by_id_and_excluded_from_match(
        self, mock_config, entity_store
    ):
        creator_id = snowflake_id()
        ids = await _seed_walls(entity_store, creator_id, ["Promos"])
        unnamed_wall = WallFactory(id=snowflake_id(), accountId=creator_id, name=None)
        await entity_store.save(unnamed_wall)
        state = DownloadState(creator_name="creator1")
        state.creator_id = creator_id
        state.walls = {ids["Promos"], unnamed_wall.id}
        mock_config.wall_filters = {"creator1": WallFilterSpec(includes=["promos"])}
        assert await resolve_wall_filter(mock_config, state) == {ids["Promos"]}


@pytest.mark.asyncio
class TestResolveIdKeys:
    async def test_id_key_rekeyed_to_username(self, respx_fansly_api, mock_config):
        account_id = snowflake_id()
        route = respx.get(
            url__startswith=respx_fansly_api.ACCOUNT_BY_ID_ENDPOINT.format("")
        ).mock(
            side_effect=[
                httpx.Response(
                    200,
                    json={
                        "success": True,
                        "response": [{"id": str(account_id), "username": "Creator1"}],
                    },
                )
            ]
        )
        mock_config.wall_filters = {str(account_id): WallFilterSpec(includes=["A"])}
        mock_config.user_names = {str(account_id)}
        try:
            await resolve_wall_filter_id_keys(mock_config)
        finally:
            dump_fansly_calls(route.calls)
        assert route.called
        assert set(mock_config.wall_filters) == {"creator1"}
        assert mock_config.user_names == {"creator1"}

    async def test_unknown_id_dropped_with_warning(self, respx_fansly_api, mock_config):
        account_id = snowflake_id()
        route = respx.get(
            url__startswith=respx_fansly_api.ACCOUNT_BY_ID_ENDPOINT.format("")
        ).mock(
            side_effect=[httpx.Response(200, json={"success": True, "response": []})]
        )
        mock_config.wall_filters = {str(account_id): WallFilterSpec(includes=["A"])}
        mock_config.user_names = {str(account_id)}
        try:
            await resolve_wall_filter_id_keys(mock_config)
        finally:
            dump_fansly_calls(route.calls)
        assert mock_config.wall_filters == {}
        assert mock_config.user_names == set()

    async def test_no_id_keys_is_noop(self, mock_config):
        mock_config.wall_filters = {"creator1": WallFilterSpec(includes=["A"])}
        mock_config.user_names = {"creator1"}
        await resolve_wall_filter_id_keys(mock_config)
        assert mock_config.wall_filters == {"creator1": WallFilterSpec(includes=["A"])}
        assert mock_config.user_names == {"creator1"}

    async def test_unknown_id_dropped_without_user_names_set(
        self, respx_fansly_api, mock_config
    ):
        account_id = snowflake_id()
        route = respx.get(
            url__startswith=respx_fansly_api.ACCOUNT_BY_ID_ENDPOINT.format("")
        ).mock(
            side_effect=[httpx.Response(200, json={"success": True, "response": []})]
        )
        mock_config.wall_filters = {str(account_id): WallFilterSpec(includes=["A"])}
        mock_config.user_names = None
        try:
            await resolve_wall_filter_id_keys(mock_config)
        finally:
            dump_fansly_calls(route.calls)
        assert mock_config.wall_filters == {}
        assert mock_config.user_names is None

    async def test_id_key_rekeyed_without_user_names_set(
        self, respx_fansly_api, mock_config
    ):
        account_id = snowflake_id()
        route = respx.get(
            url__startswith=respx_fansly_api.ACCOUNT_BY_ID_ENDPOINT.format("")
        ).mock(
            side_effect=[
                httpx.Response(
                    200,
                    json={
                        "success": True,
                        "response": [{"id": str(account_id), "username": "Creator1"}],
                    },
                )
            ]
        )
        mock_config.wall_filters = {str(account_id): WallFilterSpec(includes=["A"])}
        mock_config.user_names = None
        try:
            await resolve_wall_filter_id_keys(mock_config)
        finally:
            dump_fansly_calls(route.calls)
        assert route.called
        assert set(mock_config.wall_filters) == {"creator1"}
        assert mock_config.user_names is None

    async def test_id_key_clobber_skipped_when_username_exists(
        self, respx_fansly_api, mock_config
    ):
        """Re-keying that would overwrite an existing username entry is skipped."""
        account_id = snowflake_id()
        route = respx.get(
            url__startswith=respx_fansly_api.ACCOUNT_BY_ID_ENDPOINT.format("")
        ).mock(
            side_effect=[
                httpx.Response(
                    200,
                    json={
                        "success": True,
                        "response": [{"id": str(account_id), "username": "Creator1"}],
                    },
                )
            ]
        )
        mock_config.wall_filters = {
            str(account_id): WallFilterSpec(includes=["A"]),
            "creator1": WallFilterSpec(includes=["B"]),
        }
        mock_config.user_names = {str(account_id), "creator1"}
        try:
            await resolve_wall_filter_id_keys(mock_config)
        finally:
            dump_fansly_calls(route.calls)
        assert route.called
        assert mock_config.wall_filters == {"creator1": WallFilterSpec(includes=["B"])}
        assert mock_config.user_names == {"creator1"}

    async def test_id_key_clobber_skipped_when_username_exists_and_user_names_none(
        self, respx_fansly_api, mock_config
    ):
        """Re-keying that would overwrite an existing username entry is skipped when user_names is None."""
        account_id = snowflake_id()
        route = respx.get(
            url__startswith=respx_fansly_api.ACCOUNT_BY_ID_ENDPOINT.format("")
        ).mock(
            side_effect=[
                httpx.Response(
                    200,
                    json={
                        "success": True,
                        "response": [{"id": str(account_id), "username": "Creator1"}],
                    },
                )
            ]
        )
        mock_config.wall_filters = {
            str(account_id): WallFilterSpec(includes=["A"]),
            "creator1": WallFilterSpec(includes=["B"]),
        }
        mock_config.user_names = None
        try:
            await resolve_wall_filter_id_keys(mock_config)
        finally:
            dump_fansly_calls(route.calls)
        assert route.called
        assert mock_config.wall_filters == {"creator1": WallFilterSpec(includes=["B"])}
        assert mock_config.user_names is None

    async def test_id_key_missing_username_dropped_with_warning(
        self, respx_fansly_api, mock_config
    ):
        """An account payload lacking ``username`` is treated like an unknown ID."""
        account_id = snowflake_id()
        route = respx.get(
            url__startswith=respx_fansly_api.ACCOUNT_BY_ID_ENDPOINT.format("")
        ).mock(
            side_effect=[
                httpx.Response(
                    200,
                    json={"success": True, "response": [{"id": str(account_id)}]},
                )
            ]
        )
        mock_config.wall_filters = {str(account_id): WallFilterSpec(includes=["A"])}
        mock_config.user_names = {str(account_id)}
        try:
            await resolve_wall_filter_id_keys(mock_config)
        finally:
            dump_fansly_calls(route.calls)
        assert route.called
        assert mock_config.wall_filters == {}
        assert mock_config.user_names == set()

    async def test_id_lookup_http_error_raises_api_error(
        self, respx_fansly_api, mock_config
    ):
        """A 5xx from the account-by-ID lookup maps to ApiError, not a raw HTTPStatusError."""
        account_id = snowflake_id()
        route = respx.get(
            url__startswith=respx_fansly_api.ACCOUNT_BY_ID_ENDPOINT.format("")
        ).mock(side_effect=[httpx.Response(500, json={"error": "boom"})] * 6)
        mock_config.wall_filters = {str(account_id): WallFilterSpec(includes=["A"])}
        mock_config.user_names = {str(account_id)}
        try:
            with pytest.raises(ApiError):
                await resolve_wall_filter_id_keys(mock_config)
        finally:
            dump_fansly_calls(route.calls)
        assert route.called
