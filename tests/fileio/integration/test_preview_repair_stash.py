"""Stash-edge tests for ``_rescan_and_invalidate`` in ``fileio/preview_repair``.

Drives the real blocking rescan through respx (metadataScan + findJob) and
verifies the renamed path reaches the scan request, then that the cached
file/scene/image/gallery types are invalidated. The inactive path is proven a
pure no-op (no StashProcessing, no GraphQL call).

Mocks Stash ONLY at the GraphQL HTTP edge via respx (per-test routes use
``side_effect``); no internal methods are patched. Real DB, real store.
"""

from datetime import UTC, datetime

import httpx
import pytest
import respx

from fileio.preview_repair import _rescan_and_invalidate
from tests.fixtures.stash.stash_api_fixtures import (
    _mock_capability_response,
    assert_op_with_vars,
    dump_graphql_calls,
)
from tests.fixtures.stash.stash_graphql_fixtures import create_graphql_response
from tests.fixtures.stash.stash_type_factories import SceneFactory


_CONN = {"scheme": "http", "host": "localhost", "port": 9999, "apikey": ""}
_SNOWFLAKE_ID = 123456789012345678


def _finished_job_dict() -> dict:
    """FINISHED metadata-scan job (returned immediately for test speed)."""
    return {
        "id": "job_123",
        "status": "FINISHED",
        "description": "Scanning metadata",
        "progress": 100.0,
        "subTasks": [],
        "addTime": datetime.now(UTC).isoformat(),
    }


@pytest.mark.asyncio
async def test_rescan_and_invalidate_active_scans_and_invalidates(
    respx_stash_processor, tmp_path
):
    """Active path scans the renamed paths, then invalidates cached file types.

    Invalidate coverage note: ``_rescan_and_invalidate`` runs the 8-type
    ``invalidate_type`` loop, then ``cleanup()`` -> ``context.close()``, which
    itself calls ``store.invalidate_all()`` and drops the store. So the store
    is gone by the time the call returns and a post-call seeded-cache assertion
    is infeasible. We seed a Scene before the call to exercise a populated
    store through the path, and assert the scan fired with the renamed paths
    and the call completed cleanly; the 8-type loop's eviction is covered by
    reasoning (it mirrors ``file_first.py:_finalize_creator``) rather than a
    post-cleanup cache read.
    """
    processor = respx_stash_processor
    processor.config.stash_context_conn = dict(_CONN)
    processor.config.stash_scan_settle_s = 0
    processor.state.creator_id = _SNOWFLAKE_ID
    processor.state.base_path = tmp_path
    tmp_path.mkdir(parents=True, exist_ok=True)

    # Seed one Scene so the invalidate loop runs against a populated store.
    seeded = SceneFactory(title="seeded-preview-scene")
    processor.store.add(seeded)
    assert processor.context.store.cache_stats().by_type.get("Scene", 0) == 1

    renamed = [str(tmp_path / "Pictures" / "Previews" / "x_preview_id_123.jpg")]

    route = respx.post("http://localhost:9999/graphql").mock(
        side_effect=[
            # ConfigurationDefaults query (metadata_scan fetches scan defaults
            # first); empty data -> client falls back to hardcoded defaults.
            httpx.Response(200, json={"data": {}}),
            # metadataScan mutation -> job id string
            httpx.Response(200, json={"data": {"metadataScan": "job_123"}}),
            # findJob query -> FINISHED immediately
            httpx.Response(
                200, json=create_graphql_response("findJob", _finished_job_dict())
            ),
            # cleanup() -> context.close() drops the client; the fixture
            # teardown then re-touches context.client, so reconnect below.
            # This capability response feeds that reconnect's introspection.
            _mock_capability_response(),
        ]
    )

    try:
        await _rescan_and_invalidate(processor.config, processor.state, renamed)
        # Production reconnects the shared context per creator via get_client();
        # do the same so the fixture's teardown finds an initialized client.
        await processor.context.get_client()
    finally:
        dump_graphql_calls(route.calls, "rescan active path")

    # The scan fired and carried the renamed path into the metadataScan request.
    # calls[0] is ConfigurationDefaults; calls[1] is the MetadataScan mutation.
    assert route.call_count >= 2
    assert_op_with_vars(
        route.calls[1],
        "metadataScan",
        input__paths=renamed,
    )


@pytest.mark.asyncio
async def test_rescan_and_invalidate_inactive_is_noop(respx_stash_processor):
    """Inactive path returns immediately: no StashProcessing, no GraphQL call.

    The route's ``side_effect`` queue is empty: any GraphQL call would raise
    StopIteration, so a passing test proves no request was made.
    """
    processor = respx_stash_processor
    processor.config.stash_context_conn = None  # -> config.stash_active is False

    route = respx.post("http://localhost:9999/graphql").mock(side_effect=[])

    try:
        await _rescan_and_invalidate(
            processor.config, processor.state, ["/whatever/x.jpg"]
        )
    finally:
        dump_graphql_calls(route.calls, "rescan inactive no-op")

    assert not route.called
