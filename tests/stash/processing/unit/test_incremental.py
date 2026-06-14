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

from daemon.runner import _run_incremental_stash
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


class TestLocalPathSeam:
    """media.local_path (download-time transient) reaches the incremental index."""

    @pytest.mark.asyncio
    async def test_local_path_survives_onto_indexed_media(
        self, entity_store, respx_stash_processor
    ):
        """The media in the built index is the live object carrying local_path.

        The incremental pass derives its scan paths from ``media.local_path`` on
        the index media. Those objects come from the identity map the download
        just populated, so the transient path must be present (not lost to a
        fresh DB load). Without this, the scan would target nothing.
        """
        acct_id = snowflake_id()
        account = AccountFactory.build(id=acct_id, username="test_user")
        await entity_store.save(account)

        media = MediaFactory.build(
            id=snowflake_id(),
            accountId=acct_id,
            mimetype="video/mp4",
            type=2,
            is_downloaded=True,
            local_filename="clip_id_42.mp4",
        )
        media.local_path = "/dl/test_user/Videos/clip_id_42.mp4"
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

        index = await respx_stash_processor._build_media_index([post])

        leaf = PurePath(media.local_filename).name
        indexed_media, _owners = index[leaf]
        assert indexed_media is media  # same identity-map object
        assert indexed_media.local_path == "/dl/test_user/Videos/clip_id_42.mp4"


class TestIncrementalStashGating:
    """daemon._run_incremental_stash short-circuits before constructing Stash."""

    @pytest.mark.asyncio
    async def test_noop_when_stash_inactive(self, config):
        """No Stash config -> clean no-op (the guard returns before from_config).

        from_config would raise without a Stash context, so a clean return is
        proof the gate fired first.
        """
        config.stash_context_conn = None
        assert config.stash_active is False
        await _run_incremental_stash(config, DownloadState(creator_name="someone"))

    @pytest.mark.asyncio
    async def test_noop_when_creator_unresolved(self, config):
        """Unresolved creator (creator_name None) -> no-op; cannot resolve account."""
        config.stash_context_conn = None
        await _run_incremental_stash(config, DownloadState(creator_name=None))


class TestOverrideMatchesByBasename:
    """Regression guard: under override_dldir_w_mapped the per-media lookup must
    match by basename, never by a converted path.

    Under override the Stash library is reorganized, so local paths do not
    correspond to Stash paths and get_stash_path collapses to the bare mapped
    root. A path-based find_one would degrade to ``path=<mapped root>`` — the
    whole managed area, not one file (the too-wide gate). This pins the lookup
    filter to ``basename`` so a future edit back to ``path=`` is caught.
    """

    @pytest.mark.asyncio
    async def test_incremental_lookup_uses_basename_not_mapped_root_path(
        self, respx_stash_processor, entity_store
    ):
        processor = respx_stash_processor
        processor.config.stash_scan_settle_s = 0.0
        # Override on: get_stash_path() now collapses any file path to the root.
        processor.config.stash_override_dldir_w_mapped = True
        processor.config.stash_mapped_path = Path("/stash/lib")

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
