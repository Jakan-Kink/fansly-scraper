"""Unit tests for the daemon's sweep-free incremental Stash path.

The full scan -> find_one(basename) -> adjudicate -> flush flow is exercised
end-to-end against real Stash in the integration suite (it needs the whole
GraphQL pipeline). These unit tests cover the pieces that stand alone with real
objects: the ``local_path`` seam (the transient survives onto the indexed media
the incremental pass reads), the daemon wiring's gating, and the override-config
guard that the per-media lookup matches by basename, never by a mapped-root path.
"""

import json
from datetime import UTC, datetime
from pathlib import Path, PurePath

import httpx
import pytest
import respx
from stash_graphql_client import StashContext

from config import FanslyConfig
from daemon.runner import _daemon_stash_context, _run_incremental_stash
from download.core import DownloadState
from metadata import ContentType
from tests.fixtures.metadata.metadata_factories import (
    AccountFactory,
    AccountMediaFactory,
    AttachmentFactory,
    MediaFactory,
    PostFactory,
)
from tests.fixtures.stash import find_files_response
from tests.fixtures.stash.stash_api_fixtures import dump_graphql_calls
from tests.fixtures.stash.stash_graphql_fixtures import create_graphql_response
from tests.fixtures.stash.stash_type_factories import PerformerFactory, StudioFactory
from tests.fixtures.utils.test_isolation import snowflake_id


_GRAPHQL_URL = "http://localhost:9999/graphql"


def _finished_job() -> dict:
    """A FINISHED metadataScan job payload for the wait_for_job poll."""
    return {
        "id": "job_1",
        "status": "FINISHED",
        "description": "Scanning metadata",
        "progress": 100.0,
        "subTasks": [],
        "addTime": datetime.now(UTC).isoformat(),
    }


class TestIncrementalStashGating:
    """daemon._run_incremental_stash short-circuits before constructing Stash."""

    @pytest.mark.parametrize(
        "creator_name",
        ["someone", None],
        ids=["stash_inactive", "creator_unresolved"],
    )
    @pytest.mark.asyncio
    async def test_noop_gating(
        self,
        config: FanslyConfig,
        creator_name: str | None,
    ) -> None:
        """Both gates -> clean no-op (the guard returns before from_config).

        stash_inactive: no Stash config; from_config would raise without a
        Stash context, so a clean return is proof the gate fired first.
        creator_unresolved: creator_name None -> no-op; cannot resolve account.
        """
        config.stash_context_conn = None
        assert config.stash_active is False
        await _run_incremental_stash(config, DownloadState(creator_name=creator_name))

    @pytest.mark.asyncio
    async def test_active_path_runs_real_processor_without_closing_client(
        self, config, respx_stash_processor, monkeypatch
    ):
        """Lines 626-632: Stash active + creator resolved → run the REAL
        incremental pass on a respx-backed StashProcessing.

        Only the construction seam is spied: ``StashProcessing.from_config`` is
        patched to capture the (config, state) the runner hands it and to return
        the real ``respx_stash_processor``. ``process_creator_incremental`` runs
        for real over the mocked GraphQL edge — the blanket ``{"data": {}}``
        responder resolves no performer, so the pass returns via its own
        no-performer guard. The pass must NOT close the shared singleton client
        (that is owned by the daemon-lifetime context hold).
        """
        config.stash_context_conn = {"host": "localhost", "port": 9999}
        config.stash_require_stash_only_mode = False
        assert config.stash_active is True

        received: list = []

        def _spy_from_config(cfg, state):
            received.append((cfg, state))
            return respx_stash_processor

        monkeypatch.setattr("stash.StashProcessing.from_config", _spy_from_config)

        state = DownloadState(creator_name="active_creator")
        await _run_incremental_stash(config, state)

        # Runner wired its own config + state into the construction seam, and the
        # per-item pass left the shared client open (no cleanup/close).
        assert received == [(config, state)]
        assert respx_stash_processor.context._client is not None

    @pytest.mark.asyncio
    async def test_active_path_error_swallowed_and_client_kept_open(
        self, config, respx_stash_processor, monkeypatch
    ):
        """Lines 631-632: process_creator_incremental raises → _run_incremental_stash
        swallows it (a Stash hiccup must not fail the work item) and, crucially,
        does NOT tear down the shared client — that is the regression guard for the
        per-item-close bug.

        Uses the real respx-backed processor; only the one collaborator method the
        runner awaits is stubbed to raise.
        """
        config.stash_context_conn = {"host": "localhost", "port": 9999}
        config.stash_require_stash_only_mode = False

        received: list = []

        def _spy_from_config(cfg, state):
            received.append((cfg, state))
            return respx_stash_processor

        monkeypatch.setattr("stash.StashProcessing.from_config", _spy_from_config)

        async def _raise_process() -> None:
            raise RuntimeError("incremental boom")

        monkeypatch.setattr(
            respx_stash_processor, "process_creator_incremental", _raise_process
        )

        state = DownloadState(creator_name="err_creator")
        # Must NOT raise — the runner swallows Stash errors.
        await _run_incremental_stash(config, state)

        assert received == [(config, state)]
        # Regression: a failed per-item pass leaves the shared client intact.
        assert respx_stash_processor.context._client is not None


class TestLocalPathSeamAndOverrideLookup:
    """One seeded graph covers two standalone incremental-pass guards.

    Seam: ``media.local_path`` (download-time transient) reaches the
    incremental index — the media in the built index is the live identity-map
    object carrying ``local_path``; without it the scan would target nothing.

    Override regression guard: under ``override_dldir_w_mapped`` the Stash
    library is reorganized, so local paths do not correspond to Stash paths and
    get_stash_path collapses to the bare mapped root. A path-based find_one
    would degrade to ``path=<mapped root>`` — the whole managed area, not one
    file (the too-wide gate). This pins the lookup filter to ``basename`` so a
    future edit back to ``path=`` is caught.
    """

    @pytest.mark.asyncio
    async def test_local_path_survives_and_lookup_uses_basename_not_path(
        self, respx_stash_processor, entity_store
    ):
        processor = respx_stash_processor

        acct_id = snowflake_id()
        account = AccountFactory.build(id=acct_id, username="ovr_user")
        await entity_store.save(account)

        leaf = "2026-01-01_at_00-00_UTC_id_42.mp4"
        media = MediaFactory.build(
            id=snowflake_id(),
            accountId=acct_id,
            mimetype="video/mp4",
            type=2,
            is_downloaded=True,
            local_filename=leaf,
        )
        media.local_path = f"/dl/ovr_user/Videos/{leaf}"
        await entity_store.save(media)

        account_media = AccountMediaFactory.build(
            id=snowflake_id(), accountId=acct_id, mediaId=media.id
        )
        await entity_store.save(account_media)

        post = PostFactory.build(id=snowflake_id(), accountId=acct_id)
        post.attachments = [
            AttachmentFactory.build(
                postId=post.id,
                contentId=account_media.id,
                contentType=ContentType.ACCOUNT_MEDIA,
                pos=0,
            )
        ]

        # === Seam: the built index carries the live identity-map media (and
        # its transient local_path), under default (non-override) config. ===
        index = await processor._build_media_index([post])
        index_leaf = PurePath(media.local_filename).name
        indexed_media, _owners = index[index_leaf]
        assert indexed_media is media  # same identity-map object
        assert indexed_media.local_path == f"/dl/ovr_user/Videos/{leaf}"

        # === Override regression guard: lookup by basename, never by path. ===
        processor.config.stash_scan_settle_s = 0.0
        # Override on: get_stash_path() now collapses any file path to the root.
        processor.config.stash_override_dldir_w_mapped = True
        processor.config.stash_mapped_path = Path("/stash/lib")

        performer = PerformerFactory(id="123", name="ovr_user", scenes=[], images=[])
        studio = StudioFactory(id="200", name="ovr_user (Fansly)")

        # connect -> metadataScan -> findJob(finished) -> findFiles(empty, so no
        # adjudication follows). Trailing empties absorb any benign tail call.
        route = respx.post(_GRAPHQL_URL).mock(
            side_effect=[
                httpx.Response(200, json={"data": {}}),
                httpx.Response(200, json={"data": {"metadataScan": "job_1"}}),
                httpx.Response(
                    200, json=create_graphql_response("findJob", _finished_job())
                ),
                find_files_response(),
                find_files_response(),
            ]
        )
        await processor.context.get_client()
        try:
            await processor._run_file_first_incremental(account, performer, studio)
        finally:
            dump_graphql_calls(route.calls, "override_lookup_by_basename")

        find_files_reqs = [
            json.loads(c.request.content)
            for c in route.calls
            if "findFiles" in json.loads(c.request.content).get("query", "")
        ]
        assert find_files_reqs, "incremental pass issued no findFiles lookup"
        file_filter = find_files_reqs[0].get("variables", {}).get("file_filter") or {}
        # Matched by basename (the snowflake-bearing leaf), not by path.
        assert "basename" in file_filter, f"lookup not by basename: {file_filter}"
        assert file_filter["basename"]["value"] == leaf
        # The mapped root must NEVER appear as a path criterion (the too-wide gate).
        assert "path" not in file_filter, (
            f"lookup regressed to a path filter under override: {file_filter}"
        )


class TestDaemonStashContext:
    """daemon.runner._daemon_stash_context — the daemon-lifetime hold that keeps
    the shared Stash client open so per-item passes never close it (the belt for
    the _run_incremental_stash no-close fix)."""

    @pytest.mark.asyncio
    async def test_inactive_is_noop(self, config):
        """Stash inactive → no context entered; a clean no-op."""
        config.stash_context_conn = None
        assert config.stash_active is False
        async with _daemon_stash_context(config):
            pass  # nothing held; must not raise

    @pytest.mark.asyncio
    async def test_active_holds_then_releases_shared_client(
        self, config, stash_context, respx_stash_client
    ):
        """Stash active → entering increments the context ref-count and keeps the
        already-initialized singleton open; exiting releases it (ref → 0) and the
        client is closed once.

        respx_stash_client initializes stash_context's client over the mocked
        GraphQL edge; the helper's enter/exit then make no further HTTP (cached
        client on enter, transport close on exit).
        """
        config._stash = stash_context
        config.stash_context_conn = {
            "Scheme": "http",
            "Host": "localhost",
            "Port": 9999,
        }
        config.stash_require_stash_only_mode = False
        assert config.stash_active is True

        assert stash_context.ref_count == 0
        async with _daemon_stash_context(config):
            assert stash_context.ref_count == 1  # held for the daemon's lifetime
            assert stash_context._client is not None
        assert stash_context.ref_count == 0  # released at exit
        assert stash_context._client is None  # closed once the last ref releases

    @pytest.mark.asyncio
    async def test_active_connect_failure_logged_and_continues(self, config, caplog):
        """A failed initial connect is caught + logged; the daemon continues
        (incremental passes fall back to lazy per-call connect). The real connect
        attempt is refused/blocked, exercising the except arm without mocking.
        """
        caplog.set_level("WARNING")
        config.stash_context_conn = {"Scheme": "http", "Host": "127.0.0.1", "Port": 1}
        config.stash_require_stash_only_mode = False
        config._stash = StashContext(conn=config.stash_context_conn)
        assert config.stash_active is True

        async with _daemon_stash_context(config):
            pass  # connect fails → caught → continue without holding

        warnings = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
        assert any("could not pre-open Stash context" in m for m in warnings)
