"""Reusable infrastructure for ``fansly_downloader_ng.main`` integration tests.

The main entrypoint — ``main_integration_env`` — is a pytest fixture that
yields a fully-configured ``MainIntegrationEnv`` dataclass inside:

- an active ``respx.mock`` context (all Fansly HTTP requests are intercepted)
- an active ``fake_websocket_session`` context (``websockets.client.connect``
  returns a FakeSocket whose first recv() completes auth)
- baseline respx routes registered (CORS preflight, device/id, account/me,
  account-by-username for clientuser + testcreator)

Tests receive the environment ready-to-use and add their own mode-specific
routes (timelinenew, messaging/groups, mediastoriesnew, account/media/orders,
account/{id}/walls, post) for the scenarios they verify. The design mirrors
``tests/fixtures/stash/stash_integration_fixtures.py::respx_stash_processor``
— the fixture owns the ``with respx.mock:`` so tests don't need
``@respx.mock`` decorators.

Quick usage::

    @pytest.mark.parametrize("mode", [DownloadMode.TIMELINE, ...])
    async def test_xxx(main_integration_env, mode):
        env = main_integration_env
        env.config.download_mode = mode
        init_logging_config(env.config)
        env.register_empty_content()  # or specific per-test routes
        result = await run_main_and_cleanup(env.config)
"""

from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass
from typing import Any

import httpx
import pytest_asyncio
import respx

from fansly_downloader_ng import main
from tests.fixtures.api.fake_websocket import FakeSocket, fake_websocket_session
from tests.fixtures.utils.test_isolation import snowflake_id


def fansly_json(payload: Any) -> dict[str, Any]:
    """Wrap a payload in Fansly's ``{"success": "true", "response": ...}`` envelope.

    FanslyApi validates responses by asserting ``success == "true"``
    (api/fansly.py:980); every mock response needs this wrapper.
    """
    return {"success": "true", "response": payload}


async def run_main_and_cleanup(config) -> int:
    """Run ``main(config)`` and explicitly clean up the DB pool afterward.

    ``_async_main`` normally owns cleanup; when a test calls ``main()`` directly
    it takes over that responsibility so the asyncpg pool and related state
    don't leak across tests. The caller can still assert on anything ``main()``
    did (DB state, log messages, etc.) after this returns — cleanup is done
    in a ``finally`` block so it runs even when main raises.
    """
    try:
        return await main(config)
    finally:
        if getattr(config, "_database", None) is not None:
            with suppress(Exception):
                await config._database.cleanup()


@dataclass
class MainIntegrationEnv:
    """Fully-configured environment for a main() integration test.

    Yielded by the ``main_integration_env`` fixture. All fields are set up
    before the test body runs; tests can mutate any of them before calling
    ``await run_main_and_cleanup(env.config)``.

    Attributes:
        config: Ready-to-run FanslyConfig with DB attached, secrets set,
            retries disabled (for fast empty-content paths), non-interactive.
        client_id: Snowflake ID advertised as the client's account.
        creator_id: Snowflake ID advertised for ``testcreator`` (the default
            single creator pre-registered by the fixture).
        creator_name: The default creator name (``testcreator``).
        fake_ws: The FakeSocket backing the WebSocket — inspect ``fake_ws.sent``
            to verify auth/subscribe messages the real code transmitted.
        accounts_by_username: Dict backing the pivoting ``/api/v1/account?usernames=``
            responder. Tests add entries via ``env.add_creator(name, account_id)``
            to support multi-creator and following-list scenarios.
    """

    config: Any
    client_id: int
    creator_id: int
    creator_name: str
    fake_ws: FakeSocket
    accounts_by_username: dict[str, dict[str, Any]]

    def add_creator(self, name: str, account_id: int | None = None) -> int:
        """Register an additional creator for multi-creator / following-list tests.

        The ``/api/v1/account?usernames=<name>`` responder is backed by
        ``accounts_by_username`` — adding an entry here makes that name
        resolve to a valid creator account in subsequent HTTP calls from
        ``main()``. Does NOT add the creator to ``config.user_names``; the
        test controls that separately.

        Args:
            name: Username to register.
            account_id: Snowflake ID to advertise. Defaults to a fresh
                ``snowflake_id()``. Callers often want the returned ID to
                reference in subsequent per-creator respx routes.

        Returns:
            The account_id used (either the one passed in or a generated one).
        """
        if account_id is None:
            account_id = snowflake_id()
        self.accounts_by_username[name] = {
            "id": str(account_id),
            "username": name,
            "displayName": name.title(),
            "createdAt": 1700000100000,
            "following": True,
            "subscribed": True,
            "timelineStats": {
                "accountId": str(account_id),
                "imageCount": 0,
                "videoCount": 0,
                "fetchedAt": 1700000200000,
            },
        }
        return account_id

    def register_following_list(self, account_ids: list[int]) -> None:
        """Register a response for the ``/account/{id}/following`` endpoint.

        ``get_following_accounts`` issues two sequential calls per page:

        1. ``GET /api/v1/account/{user_id}/following`` → list of
           ``{"accountId": "<id>"}`` dicts
        2. ``GET /api/v1/account?ids=<comma-sep ids>`` → account details

        This helper registers both. To test the empty-following path (where
        ``get_following_accounts`` returns ``[]`` and main() exits with 1),
        call with an empty list — only the first call is made then.

        Args:
            account_ids: Snowflake IDs to advertise as followed creators.
                Each must already have a matching entry in
                ``accounts_by_username`` (typically via ``add_creator``)
                so the subsequent account-details lookup resolves.
        """
        # Page 1: the following relationships. Second page is empty so the
        # paginator terminates.
        respx.get(
            url__startswith=f"https://apiv3.fansly.com/api/v1/account/{self.client_id}/following"
        ).mock(
            side_effect=[
                httpx.Response(
                    200,
                    json=fansly_json([{"accountId": str(aid)} for aid in account_ids]),
                ),
                httpx.Response(200, json=fansly_json([])),  # end of pagination
            ]
        )
        # Page 1 account details (only called if account_ids is non-empty)
        if account_ids:
            id_set = {str(aid) for aid in account_ids}
            accounts_by_id = {
                data["id"]: data
                for data in self.accounts_by_username.values()
                if data["id"] in id_set
            }

            def _account_by_id_lookup(request):
                ids_param = request.url.params.get("ids", "")
                requested = ids_param.split(",") if ids_param else []
                results = [accounts_by_id[i] for i in requested if i in accounts_by_id]
                return httpx.Response(200, json=fansly_json(results))

            respx.get(
                url__startswith="https://apiv3.fansly.com/api/v1/account?ids="
            ).mock(side_effect=_account_by_id_lookup)

    def register_empty_content(self, response_count: int = 10) -> None:
        """Register every download-mode endpoint with an empty-result response.

        For integration tests that verify orchestration (main()'s per-mode
        dispatch) rather than data handling, one empty response per endpoint
        lets every download function terminate on its no-content path.

        Covers:
        - /api/v1/timelinenew/{creator_id}                (Timeline, Normal)
        - /api/v1/messaging/groups                        (Messages, Normal)
        - /api/v1/mediastoriesnew                         (Stories, Normal)
        - /api/v1/account/media/orders/                   (Collection)
        - /api/v1/post                                    (Single)
        - /api/v1/account/{id}/walls (broad prefix match) (Wall, Normal)

        Tests that need non-empty content for a specific mode should register
        their own mode-specific route BEFORE calling this (respx matches the
        most specific registered route first).
        """
        respx.get(url__startswith="https://apiv3.fansly.com/api/v1/timelinenew/").mock(
            side_effect=[
                httpx.Response(
                    200,
                    json=fansly_json(
                        {
                            "posts": [],
                            "accountMedia": [],
                            "accounts": [],
                            "media": [],
                        }
                    ),
                )
            ]
            * response_count
        )
        # messages.py:41 indexes into ``aggregationData.groups`` unconditionally.
        respx.get("https://apiv3.fansly.com/api/v1/messaging/groups").mock(
            side_effect=[
                httpx.Response(
                    200,
                    json=fansly_json({"data": [], "aggregationData": {"groups": []}}),
                )
            ]
            * response_count
        )
        respx.get("https://apiv3.fansly.com/api/v1/mediastoriesnew").mock(
            side_effect=[httpx.Response(200, json=fansly_json({"stories": []}))]
            * response_count
        )
        respx.get(
            url__startswith="https://apiv3.fansly.com/api/v1/account/media/orders"
        ).mock(
            side_effect=[
                httpx.Response(200, json=fansly_json({"accountMediaOrders": []}))
            ]
            * response_count
        )
        # Single post lookup — download/single.py:67 reads
        # ``accountMediaBundles`` and ``accountMedia`` unconditionally.
        respx.get(url__startswith="https://apiv3.fansly.com/api/v1/post").mock(
            side_effect=[
                httpx.Response(
                    200,
                    json=fansly_json(
                        {
                            "posts": [],
                            "accountMediaBundles": [],
                            "accountMedia": [],
                            "accounts": [],
                        }
                    ),
                )
            ]
            * response_count
        )
        # Catch-all for account-scoped endpoints (walls, etc.) used by Wall mode.
        # This is registered LAST so more specific account/... routes win.
        respx.get(url__startswith="https://apiv3.fansly.com/api/v1/account/").mock(
            side_effect=[httpx.Response(200, json=fansly_json([]))] * response_count
        )


def mount_client_account_me_route(
    client_id: int,
    client_username: str = "clientuser",
    response_count: int = 5,
) -> respx.Route:
    """Register ``GET /api/v1/account/me`` with a minimal client-account response.

    Used by anything that calls ``get_creator_account_info(config, state)``
    with ``state.creator_name=None`` — that hits the client-account-info
    variant rather than ``/account?usernames=``. The daemon's
    ``_refresh_following`` is the canonical caller (it constructs a fresh
    ``DownloadState()`` so creator_name is None).

    The response shape is ``{"success": ..., "response": {"account": {...}}}``
    — ``_extract_account_data`` checks ``"account" in response_data`` to
    distinguish the client variant from the creator-list variant.

    ``timelineStats`` is intentionally OMITTED: ``_update_state_from_account``
    only requires it when ``state.creator_name is not None``, and the client
    variant always has it None.

    Args:
        client_id: Snowflake ID to advertise as the client's account.
        client_username: Display username (default "clientuser").
        response_count: How many sequential calls to satisfy. Default 5
            covers a daemon test that triggers ``_refresh_following``
            once or twice.

    Returns:
        The mounted respx route — assert on ``.call_count`` for "did the
        client account fetch fire?" verification.
    """
    return respx.get("https://apiv3.fansly.com/api/v1/account/me").mock(
        side_effect=[
            httpx.Response(
                200,
                json=fansly_json(
                    {
                        "account": {
                            "id": str(client_id),
                            "username": client_username,
                            "displayName": client_username.title(),
                            "createdAt": 1700000000000,
                        }
                    }
                ),
            )
        ]
        * response_count
    )


def mount_empty_creator_pipeline(
    creator_id: int,
    creator_name: str,
    *,
    walls: list[dict[str, Any]] | None = None,
    response_count: int = 5,
) -> dict[str, respx.Route]:
    """Register the per-creator handler-pipeline routes with empty responses.

    Mounts the four endpoints exercised by ``_handle_full_creator_item``
    (account/timeline/stories/messages). Each response body is empty so
    the handler completes its loops without downloading anything but
    with every code path firing.

    Designed for daemon-dispatch tests in
    ``tests/daemon/unit/test_runner_wiring.py`` — narrower than
    ``MainIntegrationEnv.register_empty_content`` (which also covers
    Collection/Single/Wall modes for ``main()`` integration tests).

    The account response includes ``timelineStats`` (mandatory —
    ``_update_state_from_account`` raises
    ``ApiAccountInfoError("you most likely misspelled it!")`` without
    it). When ``walls`` is None or empty, the account has no walls →
    ``state.walls`` stays unset → wall iteration is skipped. Pass a
    list of wall dicts (e.g., ``[{"id": str(snowflake_id()), "pos": 1,
    "name": "Main"}]``) to populate ``state.walls`` and exercise the
    daemon's wall-iteration branch (each wall triggers another
    ``/timelinenew/{creator_id}?wallId=...`` call which the timeline
    route's ``response_count`` repetition absorbs).

    Args:
        creator_id: Snowflake ID for the creator under test.
        creator_name: Username — must match what the handler uses to
            resolve the URL (typically ``saved_account.username``).
        walls: Optional list of wall dicts to attach to the account
            response. Each wall must have ``id`` (str — Pydantic
            coerces to int via the ID coercer). Default: no walls.
        response_count: How many sequential responses to mount per
            route. Default 5 covers a full FullCreatorDownload run
            with up to a few walls.

    Returns:
        Dict of route name → respx.Route, so callers can assert on
        individual call counts (e.g., ``routes["timeline"].call_count``).
        Keys: ``account``, ``timeline``, ``stories``, ``messages``.
    """
    account_payload: dict[str, Any] = {
        "id": str(creator_id),
        "username": creator_name,
        "createdAt": 1700000000,
        "timelineStats": {
            "accountId": str(creator_id),
            "imageCount": 1,
            "videoCount": 0,
            "bundleCount": 0,
            "bundleImageCount": 0,
            "bundleVideoCount": 0,
            "fetchedAt": 1700000000,
        },
    }
    if walls:
        account_payload["walls"] = walls

    routes: dict[str, respx.Route] = {}
    routes["account"] = respx.get(
        url__startswith="https://apiv3.fansly.com/api/v1/account?usernames="
    ).mock(
        side_effect=[httpx.Response(200, json=fansly_json([account_payload]))]
        * response_count
    )
    routes["timeline"] = respx.get(
        url__startswith=(f"https://apiv3.fansly.com/api/v1/timelinenew/{creator_id}")
    ).mock(
        side_effect=[
            httpx.Response(
                200,
                json=fansly_json(
                    {
                        "posts": [],
                        "aggregationData": {
                            "accountMedia": [],
                            "media": [],
                            "accounts": [],
                        },
                    }
                ),
            )
        ]
        * response_count
    )
    routes["stories"] = respx.get(
        url__startswith="https://apiv3.fansly.com/api/v1/mediastoriesnew"
    ).mock(
        side_effect=[
            httpx.Response(
                200,
                json=fansly_json(
                    {
                        "mediaStories": [],
                        "aggregationData": {
                            "accountMedia": [],
                            "media": [],
                            "accounts": [],
                        },
                    }
                ),
            )
        ]
        * response_count
    )
    routes["messages"] = respx.get(
        url__startswith="https://apiv3.fansly.com/api/v1/messaging/groups"
    ).mock(
        side_effect=[
            httpx.Response(
                200,
                json=fansly_json(
                    {
                        "data": [],
                        "aggregationData": {"groups": [], "accounts": []},
                    }
                ),
            )
        ]
        * response_count
    )
    return routes


def mount_empty_following_route(client_id: int) -> respx.Route:
    """Register ``GET /api/v1/account/{client_id}/following`` returning an empty page.

    For tests that verify the refresh boundary (did the daemon's
    ``_refresh_following`` actually call the following endpoint?) without
    needing a real list. Returns an empty list response so
    ``_get_following_page`` short-circuits at ``if not account_ids:
    return [], 0`` and the outer ``get_following_accounts`` loop exits
    on the first page (``if not accounts: break``).

    Args:
        client_id: The client's account ID — must match what
            ``get_creator_account_info`` (client variant) returned via
            ``mount_client_account_me_route``, since
            ``get_following_accounts`` uses ``state.creator_id`` to build
            the URL.

    Returns:
        The mounted respx route — assert on ``.call_count`` for "did the
        following refresh fire?" verification.
    """
    return respx.get(
        url__startswith=(
            f"https://apiv3.fansly.com/api/v1/account/{client_id}/following"
        )
    ).mock(side_effect=[httpx.Response(200, json=fansly_json([]))])


def _register_baseline_routes(
    *,
    client_id: int,
    client_username: str,
    client_display_name: str,
    device_id: str,
    options_response_count: int,
    get_response_count: int,
) -> None:
    """Register the three endpoints every main() run hits (CORS, device, me).

    Internal helper used by ``main_integration_env``. Uses ``return_value`` for
    the blanket CORS OPTIONS route — intentional fixture-level responder
    that must answer every GET's preflight regardless of path or count. This
    matches the pattern at ``tests/fixtures/stash/stash_api_fixtures.py:265``
    where the stash fixture also uses a blanket default responder.
    """
    respx.route(method="OPTIONS", url__startswith="https://apiv3.fansly.com/").mock(
        side_effect=[httpx.Response(200)] * options_response_count
    )
    respx.get(url__startswith="https://apiv3.fansly.com/api/v1/device/id").mock(
        side_effect=[httpx.Response(200, json=fansly_json(device_id))]
        * get_response_count
    )
    respx.get("https://apiv3.fansly.com/api/v1/account/me").mock(
        side_effect=[
            httpx.Response(
                200,
                json=fansly_json(
                    {
                        "account": {
                            "id": str(client_id),
                            "username": client_username,
                            "displayName": client_display_name,
                            "createdAt": 1700000000000,
                        }
                    }
                ),
            )
        ]
        * get_response_count
    )


def _register_account_lookup_route(
    accounts_by_username: dict[str, dict[str, Any]],
) -> None:
    """Register ``/api/v1/account?usernames=…`` with a pivoting responder.

    Both ``load_client_account_into_db`` (clientuser) and per-creator lookups
    (testcreator plus anything added via ``env.add_creator``) hit this URL.
    One route with a ``side_effect`` callable reads ``accounts_by_username``
    on each call, so ``add_creator`` entries added mid-test resolve on next
    HTTP request.
    """

    def _account_lookup(request):
        usernames_param = request.url.params.get("usernames", "")
        # Fansly accepts comma-separated usernames; resolve each present.
        requested = [u.strip() for u in usernames_param.split(",") if u.strip()]
        results = [
            accounts_by_username[u] for u in requested if u in accounts_by_username
        ]
        return httpx.Response(200, json=fansly_json(results))

    respx.get(
        url__startswith="https://apiv3.fansly.com/api/v1/account?usernames="
    ).mock(side_effect=_account_lookup)


@pytest_asyncio.fixture
async def main_integration_env(
    config_with_database, bypass_load_config, minimal_argv, fast_timing, tmp_path
):
    """Fully-configured environment for a main() integration test.

    Mirrors the ``respx_stash_processor`` pattern at
    ``tests/fixtures/stash/stash_integration_fixtures.py:260``: the fixture
    owns the ``with respx.mock:`` context and baseline route registration so
    tests don't need ``@respx.mock`` decorators or imperative setup calls.

    Yields:
        MainIntegrationEnv: Composite state object. Tests typically only need
            ``env.config`` (to set download mode etc.) and ``env.register_empty_content()``
            (for orchestration-only tests). ``env.client_id``, ``env.creator_id``,
            and ``env.creator_name`` are exposed for tests that need to register
            their own per-mode routes with matching IDs.

    Example — happy-path test for Timeline mode, empty response::

        async def test_main_timeline_empty(main_integration_env):
            env = main_integration_env
            env.config.download_mode = DownloadMode.TIMELINE
            init_logging_config(env.config)
            env.register_empty_content()
            result = await run_main_and_cleanup(env.config)
            assert result in (0, 4)
    """
    config = config_with_database
    client_id = snowflake_id()
    creator_id = snowflake_id()
    creator_name = "testcreator"

    # Config boilerplate every main() integration run needs.
    config.config_path = tmp_path / "config.yaml"
    config.user_names = {creator_name}
    config.download_directory = tmp_path
    config.interactive = False
    config.use_following = False
    # Valid-shape secrets to pass token/user-agent/check-key validators.
    config.token = "t" * 60
    config.user_agent = "Mozilla/5.0 " + "A" * 60
    config.check_key = "check-key-placeholder-123"
    # Disable retry loops so empty-response paths terminate immediately.
    # Tests that want to exercise retry logic should set these back.
    config.timeline_retries = 0
    config.timeline_delay_seconds = 0
    config.wall_retries = 0
    config.messages_retries = 0
    config.collection_retries = 0
    config.single_retries = 0

    # Seed the account-lookup registry with the client + default creator.
    # Tests can add more via ``env.add_creator(name, id)``.
    accounts_by_username: dict[str, dict[str, Any]] = {
        "clientuser": {
            "id": str(client_id),
            "username": "clientuser",
            "displayName": "Client User",
            "createdAt": 1700000000000,
        },
        creator_name: {
            "id": str(creator_id),
            "username": creator_name,
            "displayName": "Test Creator",
            "createdAt": 1700000100000,
            "following": True,
            "subscribed": True,
            "timelineStats": {
                "accountId": str(creator_id),
                "imageCount": 0,
                "videoCount": 0,
                "fetchedAt": 1700000200000,
            },
        },
    }

    with respx.mock, fake_websocket_session() as fake_ws:
        _register_baseline_routes(
            client_id=client_id,
            client_username="clientuser",
            client_display_name="Client User",
            device_id="test-device-id-12345",
            options_response_count=30,
            get_response_count=5,
        )
        _register_account_lookup_route(accounts_by_username)
        yield MainIntegrationEnv(
            config=config,
            client_id=client_id,
            creator_id=creator_id,
            creator_name=creator_name,
            fake_ws=fake_ws,
            accounts_by_username=accounts_by_username,
        )


__all__ = [
    "MainIntegrationEnv",
    "fansly_json",
    "main_integration_env",
    "run_main_and_cleanup",
]
