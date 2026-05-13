"""API fixtures for testing Fansly API client using respx for edge mocking.

This module provides fixtures for testing the Fansly API client through
its production seam — ``FanslyConfig.setup_api()`` — with all four
edge boundaries faked at the appropriate layer:

- ``apiv3.fansly.com`` HTTP — respx host-scoped routes
- Signed CDN media URLs — respx per-test ``url__startswith`` routes
- ``*.live-video.net`` IVS — respx host-scoped routes (``respx_ivs_cdn``)
- ``wss://wsv3.fansly.com`` WebSocket — ``fake_websocket_session()``
  patches ``api.websocket.ws_client.connect`` so real ``FanslyWebSocket``
  code runs against an auto-authenticating ``FakeSocket``

Reference pattern: ``tests/fixtures/stash/stash_api_fixtures.py``'s
``respx_stash_client`` and ``respx_stash_processor`` (yields a fully
configured subject reached through the production construction seam,
with respx active inside the fixture body).

Branch policy (``tests-to-100/CLAUDE.md``): edges only — no internal
mocks. ``respx_fansly_api`` runs the real ``setup_api()`` bootstrap
(``update_device_id`` + ``setup_session`` + WS auth), surfacing real
production behavior to every consumer test.

Usage:
    @pytest.mark.asyncio
    async def test_collections(respx_fansly_api):
        # respx_fansly_api yields a bootstrapped FanslyApi.
        # mock_config._api is wired automatically.
        respx.get("https://apiv3.fansly.com/api/v1/account/media/orders/").mock(
            side_effect=[httpx.Response(200, json={"success": True, "response": {...}})]
        )
        # Use the yielded api directly OR mock_config._api — same instance.
        result = await respx_fansly_api.get_media_collections()
"""

from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
import pytest_asyncio
import respx
from loguru import logger

from api.fansly import FanslyApi

from .fake_websocket import fake_websocket_session


# ───────────────────────────────────────────────────────────────────────
# Sample response dictionaries (used by tests as JSON bodies)
# ───────────────────────────────────────────────────────────────────────


def create_mock_json_response(
    status_code: int = 200,
    json_data: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    """Create a real httpx.Response for use with respx mocking."""
    if json_data is None:
        json_data = {}

    if headers is None:
        headers = {"content-type": "application/json"}

    return httpx.Response(
        status_code=status_code,
        json=json_data,
        headers=headers,
    )


@pytest.fixture
def mock_fansly_account_response():
    """Provide a sample Fansly account API response for testing."""
    return {
        "success": True,
        "response": {
            "id": "123456789",
            "username": "testuser",
            "displayName": "Test User",
            "about": "Test account",
            "location": "Test Location",
            "following": False,
            "subscribed": False,
            "flags": 0,
            "version": 1,
            "createdAt": int(datetime.now(UTC).timestamp() * 1000),
        },
        "aggregationData": {},
    }


@pytest.fixture
def mock_fansly_timeline_response():
    """Provide a sample Fansly timeline API response for testing."""
    return {
        "success": True,
        "response": [
            {
                "id": "post_123",
                "accountId": "123456789",
                "content": "Test post content",
                "createdAt": int(datetime.now(UTC).timestamp() * 1000),
                "likeCount": 5,
                "replyCount": 2,
                "attachments": [],
            }
        ],
        "aggregationData": {},
    }


# ───────────────────────────────────────────────────────────────────────
# FanslyApi factory (for tests that build their own api instances)
# ───────────────────────────────────────────────────────────────────────


@pytest.fixture
def fansly_api_factory():
    """Factory fixture for creating FanslyApi instances with custom parameters.

    For tests that need multiple api instances or custom constructor args.
    Most tests should use ``respx_fansly_api`` instead — it constructs the
    api through the production ``FanslyConfig.setup_api()`` seam.
    """

    def _create_api(
        token: str = "test_token",  # noqa: S107 # Test fixture default token
        user_agent: str = "test_user_agent",
        check_key: str = "test_check_key",
        device_id: str = "test_device_id",
        device_id_timestamp: int | None = None,
        on_device_updated=None,
    ):
        """Create a FanslyApi instance with specified parameters."""
        if device_id_timestamp is None:
            device_id_timestamp = int(datetime.now(UTC).timestamp() * 1000)

        return FanslyApi(
            token=token,
            user_agent=user_agent,
            check_key=check_key,
            device_id=device_id,
            device_id_timestamp=device_id_timestamp,
            on_device_updated=on_device_updated,
        )

    return _create_api


# ───────────────────────────────────────────────────────────────────────
# Bootstrap-route helper for setup_api()
# ───────────────────────────────────────────────────────────────────────


def _mount_apiv3_bootstrap_routes() -> None:
    """Mount the routes ``FanslyConfig.setup_api()`` needs to complete.

    Called inside ``respx.mock`` before ``await mock_config.setup_api()``.
    Covers ``update_device_id`` → ``GET /api/v1/device/id?ngsw-bypass=true``
    and ``setup_session`` preflight → ``GET /api/v1/account/me?ngsw-bypass=true``,
    plus the OPTIONS preflight that ``cors_options_request`` fires before
    each GET (``api/fansly.py:362``).

    The OPTIONS responder is host-scoped to ``apiv3.fansly.com`` so a
    misdirected OPTIONS to a different host raises through
    ``assert_all_mocked=True`` instead of getting a silent 200.
    """
    # URL prefixes terminate with ``?`` (the query-string separator) so
    # ``account/me?`` is NOT a string prefix of ``account/media/orders/?``.
    # Without the terminator, respx's naive prefix matching catches
    # ``me`` ⊂ ``media`` and the bootstrap route intercepts test routes.
    #
    # OPTIONS uses ``return_value`` because every test fires a CORS
    # preflight (``cors_options_request`` at ``api/fansly.py:362``); a
    # one-shot OPTIONS responder would exhaust after the first GET.
    # The endpoint GETs use ``side_effect=[...]`` (one-shot): they fire
    # exactly once during ``setup_api()`` bootstrap and then exhaust, so
    # tests can mount their own routes on the same endpoints without
    # the bootstrap routes intercepting (respx skips exhausted routes
    # and matches later-registered ones for the same URL).
    respx.route(
        method="OPTIONS",
        url__startswith="https://apiv3.fansly.com",
    ).mock(return_value=httpx.Response(200))
    respx.get(url__startswith="https://apiv3.fansly.com/api/v1/device/id?").mock(
        side_effect=[
            httpx.Response(
                200,
                json={"success": True, "response": "test-device-id-bootstrap"},
            )
        ]
    )
    respx.get(url__startswith="https://apiv3.fansly.com/api/v1/account/me?").mock(
        side_effect=[
            httpx.Response(
                200,
                json={
                    "success": True,
                    "response": {
                        "account": {
                            "id": "100000001",
                            "username": "testuser",
                            "displayName": "Test User",
                        }
                    },
                },
            )
        ]
    )


# ───────────────────────────────────────────────────────────────────────
# respx_fansly_api — apiv3.fansly.com edge, bootstrapped via setup_api()
# ───────────────────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def respx_fansly_api(
    mock_config,
    no_display,
) -> AsyncGenerator[FanslyApi, None]:
    """Get a bootstrapped FanslyApi via FanslyConfig.setup_api().

    Mirrors production: the api is reached through the same FanslyConfig
    path real code uses (``get_api`` → ``setup_api`` → ``update_device_id``
    + ``setup_session``). Bootstrap HTTP runs inside the respx context;
    WebSocket auth runs against a ``FakeSocket`` that synthesizes a
    session_id (``fake_websocket_session()``).

    Yields:
        FanslyApi: bootstrapped, session_id populated, ready to use.
        ``mock_config._api`` is wired to the same instance.

    Tests register per-call routes on ``https://apiv3.fansly.com/...``
    URLs with ``side_effect=[]`` (per branch CLAUDE.md respx rules).
    The bootstrap routes are reset between bootstrap and serve phases
    so test-side ``route.calls`` assertions start clean.

    OPTIONS preflight is host-scoped to ``apiv3.fansly.com`` only —
    a misdirected OPTIONS to a different host fails through
    ``assert_all_mocked=True`` instead of getting a silent 200.

    Example:
        @pytest.mark.asyncio
        async def test_timeline(respx_fansly_api):
            route = respx.get(
                url__startswith="https://apiv3.fansly.com/api/v1/timeline"
            ).mock(side_effect=[httpx.Response(200, json={...})])
            try:
                result = await respx_fansly_api.get_home_timeline()
            finally:
                dump_fansly_calls(route.calls)
    """
    with respx.mock, fake_websocket_session() as _ws:
        _mount_apiv3_bootstrap_routes()

        # Real production seam: get_api() → setup_api() → update_device_id
        # + setup_session (which spins up FanslyWebSocket and waits for
        # session_id from the FakeSocket auth_response).
        api = await mock_config.setup_api()

        # ``respx.clear()`` (not ``reset()``) removes the bootstrap routes
        # from the registry — otherwise a test mounting a route on the
        # same endpoint (e.g., ``account/me``) hits the bootstrap route
        # first; since its ``side_effect=[]`` is exhausted, respx raises
        # ``StopIteration`` instead of falling through to the test's
        # route. ``reset()`` only clears call history, not routes.
        respx.clear()
        respx.route(
            method="OPTIONS",
            url__startswith="https://apiv3.fansly.com",
        ).mock(return_value=httpx.Response(200))

        try:
            yield api
        finally:
            # Close the WebSocket thread + httpx session to prevent
            # socket warnings on test teardown.
            await api.aclose()
            await api.http_session.aclose()


# ───────────────────────────────────────────────────────────────────────
# respx_ivs_cdn — *.live-video.net edge for IVS livestream tests
# ───────────────────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def respx_ivs_cdn() -> AsyncGenerator[httpx.AsyncClient, None]:
    """Get an httpx.AsyncClient with respx active for IVS CDN tests.

    Use for tests that exercise IVS-host code paths (master/variant
    playlist fetches, ``.ts`` segment downloads) — anything hitting
    ``*.live-video.net``. Distinct from ``respx_fansly_api`` because
    IVS calls do NOT hit a Fansly API, do not need CORS preflight,
    and do not require ``mock_config._api`` wiring.

    Yields a fresh ``httpx.AsyncClient`` ready for IVS calls. Tests
    that consume ``_download_segment(client, ...)`` (which takes an
    injected client) use the yielded one directly; tests of
    ``_resolve_variant_url`` (which constructs its own client
    internally) just need respx active inside the with-block.

    No fixture-level fallback responder — ``assert_all_mocked=True``
    catches any unrouted call as a test failure (preferred to silent
    200s on the wrong host).

    Example:
        async def test_segment_fetch(respx_ivs_cdn, tmp_path):
            url = "https://chan.live-video.net/segment_001.ts"
            route = respx.get(url).mock(
                side_effect=[httpx.Response(200, content=b"TS-DATA")]
            )
            try:
                ok = await _download_segment(
                    respx_ivs_cdn, url, tmp_path / "s.ts", "[t]"
                )
            finally:
                dump_fansly_calls(route.calls)
            assert ok is True
    """
    with respx.mock:
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=15.0,
        ) as client:
            yield client


# ───────────────────────────────────────────────────────────────────────
# dump_fansly_calls — debug helper for try/finally blocks
# ───────────────────────────────────────────────────────────────────────


def dump_fansly_calls(calls, label: str = "Fansly API calls") -> None:
    """Log request/response details for each Fansly API call.

    Works with respx route.calls or respx.calls. Use in try/finally blocks
    when debugging test failures to see exactly what HTTP calls were made.

    Handles three response states per call:

    - **Response present** (normal case): logs status code.
    - **No response** (``side_effect=[<Exception>]`` raised before producing
      a response): logs ``"NO RESPONSE (exception raised)"``. Without this
      branch, accessing ``call.response`` would raise ``ValueError`` from
      respx and the dump itself would crash inside the test's ``finally:`` —
      masking the real failure with a fixture-side error.
    - **Bad call object** (defensive): any other AttributeError/ValueError
      from ``call.request``/``call.response`` is caught + reported so the
      dumper never raises during teardown.

    Args:
        calls: respx route.calls or respx.calls list
        label: Header label for the output
    """
    sep = "=" * 70
    lines = [sep, f"  {label} ({len(calls)} total)", sep]
    for i, call in enumerate(calls):
        try:
            req = call.request
        except Exception as exc:  # pragma: no cover  (defensive — shouldn't happen)
            lines.append(f"  [{i}] <unable to access call.request: {exc!r}>")
            continue

        if getattr(call, "has_response", True):
            resp = call.optional_response
            status = resp.status_code if resp is not None else "NO RESPONSE"
        else:
            status = "NO RESPONSE (exception raised)"

        lines.append(f"  [{i}] {req.method} {req.url}")
        if req.content:
            lines.append(f"      body: {req.content[:200]!r}")
        lines.append(f"      → {status}")
    lines.append(sep)
    logger.info("\n" + "\n".join(lines))


__all__ = [
    "create_mock_json_response",
    "dump_fansly_calls",
    "fansly_api_factory",
    "mock_fansly_account_response",
    "mock_fansly_timeline_response",
    "respx_fansly_api",
    "respx_ivs_cdn",
]
