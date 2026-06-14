"""Unit tests for MediaProcessingMixin._process_media_fast_path (incremental).

The fast-path is the daemon/incremental entry point: a Media already carries a
``stash_id`` from a prior run, so instead of the full file sweep we
``store.get`` the stored Scene/Image directly and RE-VERIFY our file is still
its primary (a pHash re-merge could have demoted us since the id was stored).
Still owned -> stamp; demoted -> re-adjudicate via the not-owned path (scenes)
or detect-and-log (images).

These tests run the REAL ``store.get`` (a ``findScene``/``findImage`` by id) and
the REAL force-refetch ``populate`` (the uncapable-server ``findScenes`` path
fallback), routed over respx. Entities are built with ``performers=[]`` (scenes
also ``tags=[]``) so ``_stamp_metadata``'s add_performer/add_tag stay in-memory.
"""

import io

import httpx
import pytest
import respx
from loguru import logger as loguru_logger
from stash_graphql_client.types import Scene

from tests.fixtures.metadata.metadata_factories import MediaFactory
from tests.fixtures.stash import seed_processor_caches, stash_creator_root
from tests.fixtures.stash.stash_api_fixtures import dump_graphql_calls
from tests.fixtures.stash.stash_graphql_fixtures import (
    create_find_scenes_result,
    create_graphql_response,
    create_image_dict,
    create_image_file_dict,
    create_scene_dict,
    create_video_file_dict,
)


_GRAPHQL_URL = "http://localhost:9999/graphql"


class TestProcessMediaFastPath:
    """Tests for MediaProcessingMixin._process_media_fast_path."""

    @pytest.mark.asyncio
    async def test_fast_path_scene_still_primary_stamps(
        self, respx_stash_processor, mock_item, mock_account
    ):
        """Our file is still files[0] -> stamp in place + record media.stash_id.

        Real ``store.get`` issues ``findScene``; route it to return a scene whose
        primary file basename-matches our media. Discriminating: the stamped
        fields land and media.stash_id is the int of the scene id.
        """
        media = MediaFactory.build(
            is_downloaded=True, mimetype="video/mp4", local_filename="x_id_42.mp4"
        )
        media.stash_id = 700
        studio = seed_processor_caches(respx_stash_processor, mock_account)

        scene_data = create_scene_dict(
            id="700",
            title="Test Scene",
            performers=[],
            tags=[],
            files=[create_video_file_dict("500", "/dl/test_user/x_id_42.mp4")],
        )
        # One call: the fast-path store.get → findScene.
        route = respx.post(_GRAPHQL_URL).mock(
            side_effect=[
                httpx.Response(
                    200, json=create_graphql_response("findScene", scene_data)
                )
            ]
        )

        try:
            result = await respx_stash_processor._process_media_fast_path(
                media, mock_item, mock_account, studio
            )
        finally:
            dump_graphql_calls(route.calls, "fast_path_scene_still_primary")

        assert len(result) == 1
        scene = result[0]
        assert scene.id == "700"
        assert scene.code == str(media.id)
        assert scene.title
        assert scene.details == mock_item.content
        assert media.stash_id == 700

    @pytest.mark.asyncio
    @pytest.mark.usefixtures("isolate_scene_create_input", "enable_scene_creation")
    async def test_fast_path_scene_demoted_readjudicates_split(
        self, respx_stash_processor, mock_item, mock_account
    ):
        """Demoted (files[0] is foreign) + split=True -> _adjudicate_not_owned SPLITs.

        Our file is files[1]; the fail-safe re-check (the force-refetch populate's
        ``findScenes`` path fallback) confirms we are still secondary, then a new
        owned scene is split off. ``findScene`` (the fast-path get) then
        ``findScenes`` (the re-check) are routed in sequence.
        """
        media = MediaFactory.build(
            is_downloaded=True, mimetype="video/mp4", local_filename="x_id_42.mp4"
        )
        media.stash_id = 700
        studio = seed_processor_caches(respx_stash_processor, mock_account)
        respx_stash_processor.config.stash_enable_scene_split = True

        # files[0] is foreign → our file (id 500) is demoted to secondary.
        files = [
            create_video_file_dict("98", "/other/foreign.mp4"),
            create_video_file_dict("500", "/dl/test_user/x_id_42.mp4"),
        ]
        scene_data = create_scene_dict(id="700", title="Shared", files=files)

        def _scenes_by_path():
            return httpx.Response(
                200,
                json=create_graphql_response(
                    "findScenes",
                    create_find_scenes_result(count=1, scenes=[scene_data]),
                ),
            )

        # Exactly 3 calls: [0] the fast-path store.get → findScene; [1]+[2] the
        # force-refetch populate's path fallback, which issues the identical
        # findScenes-by-path query TWICE (direct fetch + nested file resolution).
        route = respx.post(_GRAPHQL_URL).mock(
            side_effect=[
                httpx.Response(
                    200, json=create_graphql_response("findScene", scene_data)
                ),
                _scenes_by_path(),
                _scenes_by_path(),
            ]
        )

        try:
            result = await respx_stash_processor._process_media_fast_path(
                media, mock_item, mock_account, studio
            )
        finally:
            dump_graphql_calls(route.calls, "fast_path_scene_demoted_split")

        assert len(result) == 1
        new_scene = result[0]
        assert isinstance(new_scene, Scene)
        assert new_scene.is_new() is True
        assert new_scene.code == str(media.id)
        assert [f.id for f in new_scene.files] == ["500"]
        assert new_scene.id != "700"  # distinct from the demoted shared scene

    @pytest.mark.asyncio
    async def test_fast_path_scene_demoted_split_disabled_skips(
        self, respx_stash_processor, mock_item, mock_account
    ):
        """Demoted + split=False -> warn-and-skip, returns [], no stamp, no split."""
        media = MediaFactory.build(
            is_downloaded=True, mimetype="video/mp4", local_filename="x_id_42.mp4"
        )
        media.stash_id = 700
        studio = seed_processor_caches(respx_stash_processor, mock_account)
        respx_stash_processor.config.stash_enable_scene_split = False

        scene_data = create_scene_dict(
            id="700",
            title="Shared",
            files=[
                create_video_file_dict("98", "/other/foreign.mp4"),
                create_video_file_dict("500", "/dl/test_user/x_id_42.mp4"),
            ],
        )
        # One call: store.get → findScene; mode False returns before any populate.
        route = respx.post(_GRAPHQL_URL).mock(
            side_effect=[
                httpx.Response(
                    200, json=create_graphql_response("findScene", scene_data)
                )
            ]
        )

        sink = io.StringIO()
        sink_id = loguru_logger.add(sink, level="WARNING")
        try:
            result = await respx_stash_processor._process_media_fast_path(
                media, mock_item, mock_account, studio
            )
        finally:
            loguru_logger.remove(sink_id)
            dump_graphql_calls(route.calls, "fast_path_scene_demoted_disabled")

        assert result == []
        assert "scene-split disabled" in sink.getvalue()
        assert media.stash_id == 700

    @pytest.mark.asyncio
    async def test_fast_path_image_all_local_stamps(
        self, respx_stash_processor, mock_item, mock_account
    ):
        """All-local image -> stamp in place + record media.stash_id, returns [image]."""
        root = stash_creator_root(respx_stash_processor)
        media = MediaFactory.build(
            is_downloaded=True, mimetype="image/jpeg", local_filename="x_id_42.jpg"
        )
        media.stash_id = 900
        studio = seed_processor_caches(respx_stash_processor, mock_account)

        image_data = create_image_dict(
            id="900",
            title="Test Image",
            performers=[],
            visual_files=[
                create_image_file_dict("800", f"{root}/test_user/x_id_42.jpg")
            ],
        )
        # One call: store.get → findImage (performer caches seeded so the stamp
        # adds no inverse-sync PopulateRelationship).
        route = respx.post(_GRAPHQL_URL).mock(
            side_effect=[
                httpx.Response(
                    200, json=create_graphql_response("findImage", image_data)
                )
            ]
        )

        try:
            result = await respx_stash_processor._process_media_fast_path(
                media, mock_item, mock_account, studio
            )
        finally:
            dump_graphql_calls(route.calls, "fast_path_image_all_local")

        assert len(result) == 1
        image = result[0]
        assert image.id == "900"
        assert image.code == str(media.id)
        assert image.details == mock_item.content
        assert media.stash_id == 900

    @pytest.mark.asyncio
    async def test_fast_path_image_foreign_skips(
        self, respx_stash_processor, mock_item, mock_account
    ):
        """A foreign co-resident visual file -> logger.error + NO stamp, no stash change."""
        root = stash_creator_root(respx_stash_processor)
        media = MediaFactory.build(
            is_downloaded=True, mimetype="image/jpeg", local_filename="x_id_42.jpg"
        )
        media.stash_id = 901
        studio = seed_processor_caches(respx_stash_processor, mock_account)

        image_data = create_image_dict(
            id="901",
            title="Shared",
            visual_files=[
                create_image_file_dict("801", f"{root}/test_user/x_id_42.jpg"),
                create_image_file_dict("802", "/other/scraper/foreign.jpg"),
            ],
        )
        # One call: store.get → findImage; the foreign skip returns before stamp.
        route = respx.post(_GRAPHQL_URL).mock(
            side_effect=[
                httpx.Response(
                    200, json=create_graphql_response("findImage", image_data)
                )
            ]
        )

        sink = io.StringIO()
        sink_id = loguru_logger.add(sink, level="ERROR")
        try:
            result = await respx_stash_processor._process_media_fast_path(
                media, mock_item, mock_account, studio
            )
        finally:
            loguru_logger.remove(sink_id)
            dump_graphql_calls(route.calls, "fast_path_image_foreign")

        output = sink.getvalue()
        assert result == []
        assert "foreign co-resident" in output
        assert "901" in output
        assert media.stash_id == 901

    @pytest.mark.asyncio
    async def test_fast_path_stale_id_returns_empty(
        self, respx_stash_processor, mock_item, mock_account
    ):
        """store.get returns None (stale/deleted id) -> returns [], no error."""
        media = MediaFactory.build(
            is_downloaded=True, mimetype="video/mp4", local_filename="x_id_42.mp4"
        )
        media.stash_id = 700
        studio = seed_processor_caches(respx_stash_processor, mock_account)

        # One call: store.get → findScene returns null → None short-circuit.
        route = respx.post(_GRAPHQL_URL).mock(
            side_effect=[
                httpx.Response(200, json=create_graphql_response("findScene", None))
            ]
        )

        try:
            result = await respx_stash_processor._process_media_fast_path(
                media, mock_item, mock_account, studio
            )
        finally:
            dump_graphql_calls(route.calls, "fast_path_stale_id")

        assert result == []
        # store.get was queried (a findScene for the stored id) before the None
        # short-circuit.
        assert any(b"findScene" in c.request.content for c in route.calls)

    @pytest.mark.asyncio
    async def test_fast_path_file_not_on_entity(
        self, respx_stash_processor, mock_item, mock_account
    ):
        """Entity returned but no file basename-matches local_filename -> returns []."""
        media = MediaFactory.build(
            is_downloaded=True, mimetype="video/mp4", local_filename="x_id_42.mp4"
        )
        media.stash_id = 700
        studio = seed_processor_caches(respx_stash_processor, mock_account)

        # The stored scene's only file has a NON-matching basename.
        scene_data = create_scene_dict(
            id="700",
            title="Test Scene",
            performers=[],
            tags=[],
            files=[create_video_file_dict("999", "/dl/test_user/some_other.mp4")],
        )
        # One call: store.get → findScene; no basename match → short-circuit.
        route = respx.post(_GRAPHQL_URL).mock(
            side_effect=[
                httpx.Response(
                    200, json=create_graphql_response("findScene", scene_data)
                )
            ]
        )

        try:
            result = await respx_stash_processor._process_media_fast_path(
                media, mock_item, mock_account, studio
            )
        finally:
            dump_graphql_calls(route.calls, "fast_path_file_not_on_entity")

        assert result == []
        assert media.stash_id == 700

    @pytest.mark.asyncio
    async def test_fast_path_no_stash_id(
        self, respx_stash_processor, mock_item, mock_account
    ):
        """media.stash_id is None -> returns [] immediately, store.get NOT called."""
        media = MediaFactory.build(
            is_downloaded=True, mimetype="video/mp4", local_filename="x_id_42.mp4"
        )
        media.stash_id = None
        studio = seed_processor_caches(respx_stash_processor, mock_account)

        # Zero calls expected: an empty side_effect makes ANY GraphQL call raise,
        # so the no-id short-circuit is enforced (not merely asserted after).
        route = respx.post(_GRAPHQL_URL).mock(side_effect=[])

        try:
            result = await respx_stash_processor._process_media_fast_path(
                media, mock_item, mock_account, studio
            )
        finally:
            dump_graphql_calls(route.calls, "fast_path_no_stash_id")

        assert result == []
        # Short-circuited before the store boundary: store.get never fired.
        assert len(route.calls) == 0
