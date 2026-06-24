"""Unit tests for MediaProcessingMixin._owned_scene and _stamp_metadata."""

import json
from pathlib import PurePath
from typing import Any

import httpx
import pytest
import respx
from stash_graphql_client import is_set, present
from stash_graphql_client.types import Scene, SceneCreateInput, VideoFile

from tests.fixtures.metadata.metadata_factories import MediaFactory
from tests.fixtures.stash import graphql_op_fired
from tests.fixtures.stash.stash_api_fixtures import dump_graphql_calls
from tests.fixtures.stash.stash_graphql_fixtures import (
    create_find_scenes_result,
    create_graphql_response,
    create_scene_dict,
    create_video_file_dict,
)
from tests.fixtures.stash.stash_type_factories import (
    PerformerFactory,
    SceneFactory,
    StudioFactory,
    TagFactory,
)


class TestOwnedScene:
    @pytest.mark.asyncio
    async def test_owned_when_our_file_is_primary(self, respx_stash_processor):
        our = VideoFile(id="100", path="/dl/test_user/x_id_42.mp4")
        scene = Scene(id="500", files=[our])
        our.scenes = [scene]
        assert await respx_stash_processor._owned_scene(our) is scene

    @pytest.mark.asyncio
    async def test_not_owned_when_secondary(self, respx_stash_processor):
        foreign = VideoFile(id="99", path="/other/foreign.mp4")
        our = VideoFile(id="100", path="/dl/test_user/x_id_42.mp4")
        scene = Scene(id="500", files=[foreign, our])  # foreign is primary
        our.scenes = [scene]
        assert await respx_stash_processor._owned_scene(our) is None


class TestStampMetadata:
    """Tests for MediaProcessingMixin._stamp_metadata (no-save sibling)."""

    @pytest.mark.asyncio
    async def test_stamp_metadata_basic(
        self, respx_stash_processor, mock_item, mock_account, mock_scene
    ):
        """Stamp basic metadata onto a Scene; no per-object save inside the method.

        Performer + studio are cached/passed and there are no mentions/hashtags,
        so the only mutating call is the sceneUpdate emitted by the TEST's save.
        """
        media = MediaFactory.build(is_downloaded=True)

        # Pre-seed caches so no incidental performer/studio GraphQL fires.
        respx_stash_processor._account = mock_account
        respx_stash_processor._performer = PerformerFactory.build(
            id="123", name=mock_account.username
        )
        studio = StudioFactory.build(id="200", name=f"{mock_account.username} (Fansly)")

        # With a cached performer + passed studio and no mentions/hashtags, the
        # method fires no GraphQL; the only call is the sceneUpdate from save().
        scene_update_result = {
            "id": mock_scene.id,
            "title": "stamped",
            "code": str(media.id),
            "date": mock_item.createdAt.strftime("%Y-%m-%d"),
            "details": mock_item.content,
        }
        graphql_route = respx.post("http://localhost:9999/graphql").mock(
            side_effect=[
                httpx.Response(
                    200,
                    json=create_graphql_response("sceneUpdate", scene_update_result),
                ),
            ]
        )

        try:
            await respx_stash_processor._stamp_metadata(
                mock_scene, media, mock_item, mock_account, studio
            )
            # Core semantic: the method itself does NOT save (no internal flush).
            # All caches are pre-seeded, so it fires zero GraphQL of its own.
            calls_before_save = len(graphql_route.calls)
            await respx_stash_processor.store.save(mock_scene)
        finally:
            dump_graphql_calls(graphql_route.calls, "test_stamp_metadata_basic")

        assert calls_before_save == 0

        calls = graphql_route.calls

        # A sceneUpdate fired (from the test's save), carrying the stamped fields.
        assert graphql_op_fired(calls, "sceneUpdate")
        update_call = next(
            c for c in calls if "sceneUpdate" in json.loads(c.request.content)["query"]
        )
        variables = json.loads(update_call.request.content)["variables"]
        input_vars = variables.get("input", variables)
        assert input_vars.get("title")
        assert input_vars["code"] == str(media.id)
        assert input_vars["date"] == mock_item.createdAt.strftime("%Y-%m-%d")
        # The passed studio survived the dirty-flush onto the wire (ids are
        # serialized as strings). This is the non-tautological replacement for
        # the old "findStudios/studioCreate did not fire" assertions — those
        # could never fire since the studio was PASSED in.
        assert input_vars["studio_id"] == studio.id

        # In-memory mutations.
        assert mock_scene.code == str(media.id)
        assert mock_scene.details == mock_item.content

    @pytest.mark.asyncio
    async def test_stamp_metadata_relationship_flushes(
        self, respx_stash_processor, mock_item, mock_account, mock_scene
    ):
        """A performer relationship set by _stamp_metadata SERIALIZES on flush.

        Headline contract: _stamp_metadata adds the cached primary performer
        via add_performer() (in-place inverse mutation, no internal save), and
        a LATER store.save() must carry that relationship onto the GraphQL
        wire. We assert the cached performer's id lands in the emitted
        sceneUpdate variables (``performer_ids``, ids serialized as strings) —
        NOT merely on the in-memory object. This fails if the relationship were
        not flushed, which the in-memory ``scene.performers`` check cannot
        detect.
        """
        media = MediaFactory.build(is_downloaded=True)

        respx_stash_processor._account = mock_account
        performer = PerformerFactory.build(id="123", name=mock_account.username)
        respx_stash_processor._performer = performer
        studio = StudioFactory.build(id="200", name=f"{mock_account.username} (Fansly)")

        scene_update_result = {"id": mock_scene.id, "code": str(media.id)}
        graphql_route = respx.post("http://localhost:9999/graphql").mock(
            side_effect=[
                httpx.Response(
                    200,
                    json=create_graphql_response("sceneUpdate", scene_update_result),
                ),
            ]
        )

        try:
            await respx_stash_processor._stamp_metadata(
                mock_scene, media, mock_item, mock_account, studio
            )
            await respx_stash_processor.store.save(mock_scene)
        finally:
            dump_graphql_calls(
                graphql_route.calls, "test_stamp_metadata_relationship_flushes"
            )

        calls = graphql_route.calls
        assert graphql_op_fired(calls, "sceneUpdate")
        update_call = next(
            c
            for c in calls
            if graphql_op_fired(respx.models.CallList([c]), "sceneUpdate")
        )
        variables = json.loads(update_call.request.content)["variables"]
        input_vars = variables.get("input", variables)
        # The relationship survived the dirty-flush onto the wire: the cached
        # performer's id appears in performer_ids (serialized as a string).
        assert performer.id in input_vars["performer_ids"]

    @pytest.mark.asyncio
    async def test_stamp_metadata_uses_cached_studio(
        self, respx_stash_processor, mock_item, mock_account, mock_scene
    ):
        """A cached self._studio suppresses the findStudios lookup (media.py:844).

        Studio is NOT passed; instead processor._studio is pre-set. The method
        must use the cache rather than resolving via GraphQL, so findStudios
        does not fire. Performer is also cached so _find_existing_performer
        doesn't muddy the run with its own findPerformers. The lone mutation is
        the test-side save's sceneUpdate, into which the cached studio's id
        serializes — proving the cache branch was taken (non-tautological,
        unlike asserting absence when the studio is passed in).
        """
        media = MediaFactory.build(is_downloaded=True)

        respx_stash_processor._account = mock_account
        respx_stash_processor._performer = PerformerFactory.build(
            id="123", name=mock_account.username
        )
        respx_stash_processor._studio = StudioFactory.build(
            id="200", name=f"{mock_account.username} (Fansly)"
        )

        scene_update_result = {"id": mock_scene.id, "code": str(media.id)}
        graphql_route = respx.post("http://localhost:9999/graphql").mock(
            side_effect=[
                httpx.Response(
                    200,
                    json=create_graphql_response("sceneUpdate", scene_update_result),
                ),
            ]
        )

        try:
            # No studio= argument: the self._studio cache must supply it.
            await respx_stash_processor._stamp_metadata(
                mock_scene, media, mock_item, mock_account
            )
            await respx_stash_processor.store.save(mock_scene)
        finally:
            dump_graphql_calls(
                graphql_route.calls, "test_stamp_metadata_uses_cached_studio"
            )

        calls = graphql_route.calls
        # The cache branch suppressed the studio lookup entirely.
        assert not graphql_op_fired(calls, "findStudios")
        assert not graphql_op_fired(calls, "studioCreate")
        # And the cached studio still landed on the wire.
        update_call = next(
            c
            for c in calls
            if graphql_op_fired(respx.models.CallList([c]), "sceneUpdate")
        )
        variables = json.loads(update_call.request.content)["variables"]
        input_vars = variables.get("input", variables)
        assert input_vars["studio_id"] == respx_stash_processor._studio.id

    @pytest.mark.asyncio
    async def test_stamp_metadata_skips_organized(
        self, respx_stash_processor, mock_item, mock_account, mock_scene
    ):
        """Organized scene short-circuits the method; metadata left untouched.

        The early-return path makes zero GraphQL calls (no respx route needed),
        mirroring the proven test_update_stash_metadata_already_organized idiom.
        We assert in-memory non-mutation rather than save()-then-no-update, since
        the test's own ``organized=True`` setup would itself dirty the object.
        """
        media = MediaFactory.build(is_downloaded=True)
        mock_scene.organized = True
        original_title = mock_scene.title
        original_code = getattr(mock_scene, "code", None)
        original_details = getattr(mock_scene, "details", None)

        await respx_stash_processor._stamp_metadata(
            mock_scene, media, mock_item, mock_account
        )

        assert mock_scene.title == original_title
        assert getattr(mock_scene, "code", None) == original_code
        assert getattr(mock_scene, "details", None) == original_details

    @pytest.mark.asyncio
    async def test_stamp_metadata_skips_later_date(
        self, respx_stash_processor, mock_item, mock_account, mock_scene
    ):
        """Stored date earlier than item → item is later → skip to preserve earliest.

        Early-return path: no GraphQL fires, so no respx route is needed. Assert
        in-memory non-mutation (the test's own ``date`` setup would dirty save()).
        """
        media = MediaFactory.build(is_downloaded=True)
        # mock_item.createdAt is 2024-04-01; stored date is earlier, so item is later.
        mock_scene.date = "2024-03-01"
        original_title = mock_scene.title
        original_code = getattr(mock_scene, "code", None)
        original_details = getattr(mock_scene, "details", None)

        await respx_stash_processor._stamp_metadata(
            mock_scene, media, mock_item, mock_account
        )

        assert mock_scene.date == "2024-03-01"
        assert mock_scene.title == original_title
        assert getattr(mock_scene, "code", None) == original_code
        assert getattr(mock_scene, "details", None) == original_details

    @pytest.mark.asyncio
    async def test_stamp_metadata_forces_on_bad_title(
        self, respx_stash_processor, mock_item, mock_account, mock_scene
    ):
        """Bad title forces an update even when organized=True."""
        media = MediaFactory.build(is_downloaded=True)
        mock_scene.title = "Media from old batch"
        mock_scene.organized = True

        # Cache performer + pass studio so only sceneUpdate (from save) mutates.
        respx_stash_processor._account = mock_account
        respx_stash_processor._performer = PerformerFactory.build(
            id="123", name=mock_account.username
        )
        studio = StudioFactory.build(id="200", name=f"{mock_account.username} (Fansly)")

        graphql_route = respx.post("http://localhost:9999/graphql").mock(
            side_effect=[
                httpx.Response(
                    200,
                    json=create_graphql_response(
                        "sceneUpdate", {"id": mock_scene.id, "code": str(media.id)}
                    ),
                ),
            ]
        )

        try:
            await respx_stash_processor._stamp_metadata(
                mock_scene, media, mock_item, mock_account, studio
            )
            await respx_stash_processor.store.save(mock_scene)
        finally:
            dump_graphql_calls(
                graphql_route.calls, "test_stamp_metadata_forces_on_bad_title"
            )

        assert graphql_op_fired(graphql_route.calls, "sceneUpdate")
        assert mock_scene.title != "Media from old batch"

    @pytest.mark.asyncio
    async def test_stamp_metadata_adds_preview_tag(
        self, respx_stash_processor, mock_item, mock_account, mock_scene
    ):
        """media.is_preview=True adds the cached 'Trailer' tag (in-memory)."""
        media = MediaFactory.build(is_downloaded=True, is_preview=True)

        # Seed the Trailer tag into the store cache so _add_preview_tag's sync
        # filter() hits (no findTags).
        respx_stash_processor.store.add(TagFactory(id="999", name="Trailer"))

        # Cache performer + pass studio to avoid incidental lookups.
        respx_stash_processor._account = mock_account
        respx_stash_processor._performer = PerformerFactory.build(
            id="123", name=mock_account.username
        )
        studio = StudioFactory.build(id="200", name=f"{mock_account.username} (Fansly)")

        # One call: add_performer populates the performer's scenes inverse
        # (findScenes) since this performer's scenes are not pre-seeded.
        graphql_route = respx.post("http://localhost:9999/graphql").mock(
            side_effect=[
                httpx.Response(
                    200,
                    json=create_graphql_response(
                        "findScenes", {"count": 0, "scenes": []}
                    ),
                )
            ]
        )

        try:
            await respx_stash_processor._stamp_metadata(
                mock_scene, media, mock_item, mock_account, studio
            )
        finally:
            dump_graphql_calls(
                graphql_route.calls, "test_stamp_metadata_adds_preview_tag"
            )

        tag_names = [t.name for t in mock_scene.tags]
        assert "Trailer" in tag_names

    @pytest.mark.asyncio
    async def test_stamp_metadata_no_preview_tag_when_false(
        self, respx_stash_processor, mock_item, mock_account, mock_scene
    ):
        """media.is_preview=False does not add a 'Trailer' tag."""
        media = MediaFactory.build(is_downloaded=True, is_preview=False)

        respx_stash_processor.store.add(TagFactory(id="999", name="Trailer"))
        respx_stash_processor._account = mock_account
        respx_stash_processor._performer = PerformerFactory.build(
            id="123", name=mock_account.username
        )
        studio = StudioFactory.build(id="200", name=f"{mock_account.username} (Fansly)")

        # Zero calls: is_preview=False adds no Trailer tag, and the cached
        # performer/studio leave the stamp with no inverse to populate here.
        graphql_route = respx.post("http://localhost:9999/graphql").mock(side_effect=[])

        try:
            await respx_stash_processor._stamp_metadata(
                mock_scene, media, mock_item, mock_account, studio
            )
        finally:
            dump_graphql_calls(
                graphql_route.calls, "test_stamp_metadata_no_preview_tag_when_false"
            )

        tag_names = [t.name for t in mock_scene.tags]
        assert "Trailer" not in tag_names


class TestProcessFileFirst:
    """Tests for MediaProcessingMixin._process_file_first (the dispatcher).

    Covers ONLY the implemented VideoFile owned-update branch plus the
    minimal stubs (unindexed skip, not-owned no-op). The split/dry-run and
    ImageFile branches are later-phase no-ops and asserted as such.
    """

    @pytest.mark.asyncio
    async def test_process_file_first_owned_updates_and_sets_stash_id(
        self, respx_stash_processor, mock_item, mock_account
    ):
        """VideoFile we are primary of → stamp in place + record media.stash_id.

        Mirrors test_stamp_metadata_basic: SceneFactory defaults give empty
        relationship lists, so the in-place stamp fires zero GraphQL. The scene
        is left dirty (no save inside the method).
        """
        file = VideoFile(id="500", path="/dl/test_user/x_id_42.mp4")
        scene = SceneFactory.build(id="700", title="Test Scene")
        scene.files = [file]
        file.scenes = [scene]  # _owned_scene resolves without a populate

        media = MediaFactory.build(
            is_downloaded=True, mimetype="video/mp4", local_filename="x_id_42.mp4"
        )

        # Pre-seed caches + pass studio so _stamp_metadata fires no GraphQL.
        respx_stash_processor._account = mock_account
        respx_stash_processor._performer = PerformerFactory.build(
            id="123", name=mock_account.username
        )
        studio = StudioFactory.build(id="200", name=f"{mock_account.username} (Fansly)")

        index = {PurePath(present(file.path)).name: (media, [mock_item])}
        assert PurePath(present(file.path)).name == "x_id_42.mp4"

        result = await respx_stash_processor._process_file_first(
            file, index, mock_account, studio
        )

        # The owned scene is returned (the entity our file was adjudicated into).
        assert result == [scene]
        assert result[0] is scene
        # media.stash_id recorded as the int of the owned scene's id.
        assert media.stash_id == int(scene.id)
        assert media.stash_id == 700
        # The scene was stamped in place (no save inside the method). Assert on
        # the stamped fields (discriminating — the setup does not set these) and
        # that the scene remains an existing object, so it flushes as an UPDATE.
        # (is_dirty() is non-discriminating here: the setup's `scene.files = [..]`
        # already dirties the scene before _process_file_first runs.)
        assert scene.code == str(media.id)
        assert scene.title
        assert scene.details == mock_item.content
        assert not scene.is_new()

    @pytest.mark.asyncio
    async def test_process_file_first_unindexed_file_skips(
        self, respx_stash_processor, mock_account
    ):
        """A swept file with no matching FDNG download → skip (no exception)."""
        file = VideoFile(id="501", path="/dl/test_user/unknown.mp4")
        media = MediaFactory.build(
            is_downloaded=True, mimetype="video/mp4", local_filename="x_id_42.mp4"
        )
        original_stash_id = media.stash_id

        result = await respx_stash_processor._process_file_first(file, {}, mock_account)

        # Unindexed file → empty adjudication list, no media touched.
        assert result == []
        assert media.stash_id == original_stash_id

    @pytest.mark.asyncio
    async def test_process_file_first_not_owned_is_noop_for_now(
        self, respx_stash_processor, mock_item, mock_account
    ):
        """SECONDARY on a shared scene → Phase-3.3 stub no-op (nothing stamped).

        Asserts in-memory non-mutation (scene.code unchanged, media.stash_id
        still None), documenting the intentional no-op until split/dry-run lands.
        """
        foreign = VideoFile(id="98", path="/other/foreign.mp4")
        our = VideoFile(id="500", path="/dl/test_user/x_id_42.mp4")
        scene = SceneFactory.build(id="700", title="Test Scene")
        scene.files = [foreign, our]  # foreign is primary → we are NOT owned
        our.scenes = [scene]  # _owned_scene resolves without a populate

        media = MediaFactory.build(
            is_downloaded=True, mimetype="video/mp4", local_filename="x_id_42.mp4"
        )
        original_code = getattr(scene, "code", None)

        index = {PurePath(present(our.path)).name: (media, [mock_item])}

        result = await respx_stash_processor._process_file_first(
            our, index, mock_account
        )

        # Default config (enable_scene_split=False) → warn-and-skip → empty list.
        assert result == []
        assert media.stash_id is None
        assert getattr(scene, "code", None) == original_code

    @pytest.mark.asyncio
    async def test_animated_image_videofile_skips_no_split(
        self, respx_stash_processor, mock_item, mock_account
    ):
        """An animated image (image/* media) backed by a VideoFile is skipped.

        VideoFile exposes no ``.images`` reverse, so the backing Image cannot be
        resolved; routing it to the scene path would spuriously SPLIT in True
        mode. The dispatcher detects the image mimetype and skips. Discriminator:
        result == [] AND zero GraphQL — an empty side_effect makes any call
        (e.g. the not-owned force-refetch the old path would issue) raise.
        """
        file = VideoFile(id="100", path="/dl/u/anim_id_42.gif")
        media = MediaFactory.build(
            is_downloaded=True, mimetype="image/gif", local_filename="anim_id_42.gif"
        )
        index = {PurePath(present(file.path)).name: (media, [mock_item])}
        respx_stash_processor.config.stash_enable_scene_split = True

        graphql_route = respx.post("http://localhost:9999/graphql").mock(side_effect=[])
        try:
            result = await respx_stash_processor._process_file_first(
                file, index, mock_account, None
            )
        finally:
            dump_graphql_calls(graphql_route.calls, "animated_image_videofile_skips")

        # Detected by mimetype and skipped before any scene query.
        assert result == []
        assert len(graphql_route.calls) == 0
        assert media.stash_id is None


class TestSplitSceneForFile:
    """Tests for MediaProcessingMixin._split_scene_for_file.

    The split CREATEs a brand-new store-tracked Scene that owns our file
    (``files=[file]`` → temp UUID id → is_new). No GraphQL fires inside the
    method: ``store.add`` is in-memory and ``_stamp_metadata`` does not save.
    A later ``store.save_all()`` (not exercised here) flushes the create.
    """

    @pytest.mark.asyncio
    async def test_split_scene_for_file_creates_owned_new_scene(
        self, respx_stash_processor, mock_item, mock_account
    ):
        """Build a new Scene owning our file, register it, and stamp metadata.

        Mirrors test_stamp_metadata_basic's zero-GraphQL setup (cached
        performer + passed studio, no mentions/hashtags). Asserts: the scene is
        new, owns our file (files[0] is file), is tracked in the store cache by
        its temp id, and carries the stamped fields. No respx route is needed
        because the method fires no GraphQL (store.add is in-memory, no save).
        """
        file = VideoFile(id="500", path="/dl/test_user/x_id_42.mp4")
        media = MediaFactory.build(
            is_downloaded=True, mimetype="video/mp4", local_filename="x_id_42.mp4"
        )

        # Pre-seed caches + pass studio so _stamp_metadata fires no GraphQL.
        respx_stash_processor._account = mock_account
        respx_stash_processor._performer = PerformerFactory.build(
            id="123", name=mock_account.username
        )
        studio = StudioFactory.build(id="200", name=f"{mock_account.username} (Fansly)")

        scene = await respx_stash_processor._split_scene_for_file(
            file, media, mock_item, mock_account, studio
        )

        # A brand-new scene that owns our file.
        assert scene.is_new() is True
        assert scene.files == [file]
        assert scene.files[0] is file

        # Registered in the store identity map — cache-only lookup by temp id.
        assert respx_stash_processor.store.get_cached(Scene, scene.id) is scene

        # Stamped metadata landed in place (no save inside the method).
        assert scene.code == str(media.id)
        assert scene.title
        assert scene.details == mock_item.content


class TestProcessFileFirstNotOwned:
    """Tests for _process_file_first's NOT-OWNED branch.

    Our swept file is a SECONDARY on a shared scene another source owns. The
    branch is a 3-way dispatch on ``config.stash_enable_scene_split``:

    - ``"dry-run"``  -> log only, no create, no stash_id.
    - ``False``      -> warn + skip, no create, no stash_id.
    - ``True``       -> fail-safe force-refetch re-check, then SPLIT (create a new
                        owned scene) only if still not primary; else skip.

    The real ``store.populate`` runs and its ``findScenes``-by-path fallback is
    routed over respx with exact ``side_effect`` lists; assertions are on EFFECT
    (``result``, no ``sceneCreate`` on the wire), since every branch returns the
    adjudicated entities (``[]`` when nothing was split).
    """

    @pytest.mark.asyncio
    async def test_not_owned_dry_run_logs_no_create(
        self, respx_stash_processor, mock_item, mock_account
    ):
        """dry-run: no split, no sceneCreate, stash_id unchanged.

        Our file is a SECONDARY on a shared scene (``_owned_scene`` resolves to
        None without a populate since ``our.scenes`` is set). In dry-run the
        branch only LOGS and returns before the True-only fail-safe re-check — so
        ZERO GraphQL fires. ``result == []`` is the discriminator: any split
        would return a new scene, and any fall-through to the re-check would
        issue a populate query.
        """
        foreign = VideoFile(id="99", path="/other.mp4")
        our = VideoFile(id="100", path="/dl/u/x_id_42.mp4")
        shared = Scene(id="500", files=[foreign, our])  # foreign is primary
        our.scenes = [shared]  # _owned_scene → None without a populate
        media = MediaFactory.build(
            is_downloaded=True, mimetype="video/mp4", local_filename="x_id_42.mp4"
        )
        index = {PurePath(present(our.path)).name: (media, [mock_item])}
        respx_stash_processor.config.stash_enable_scene_split = "dry-run"

        # Zero calls expected (log-only returns before the fail-safe re-check):
        # an empty side_effect makes any GraphQL call raise.
        graphql_route = respx.post("http://localhost:9999/graphql").mock(side_effect=[])

        try:
            result = await respx_stash_processor._process_file_first(
                our, index, mock_account, None
            )
        finally:
            dump_graphql_calls(
                graphql_route.calls, "test_not_owned_dry_run_logs_no_create"
            )

        assert result == []  # dry-run adjudicates into nothing
        # Log-only: returned before the True-only fail-safe → no GraphQL at all
        # (no populate re-check, no sceneCreate).
        assert len(graphql_route.calls) == 0
        assert media.stash_id is None

    @pytest.mark.asyncio
    async def test_not_owned_false_warns_no_create(
        self, respx_stash_processor, mock_item, mock_account
    ):
        """False: no split, no sceneCreate, stash_id unchanged.

        Like the dry-run test: warn-and-skip returns before the True-only
        fail-safe, so ZERO GraphQL fires. ``result == []`` discriminates a split
        (which would return a scene) and ``len(calls) == 0`` discriminates a
        fall-through to the populate re-check.
        """
        foreign = VideoFile(id="99", path="/other.mp4")
        our = VideoFile(id="100", path="/dl/u/x_id_42.mp4")
        shared = Scene(id="500", files=[foreign, our])  # foreign is primary
        our.scenes = [shared]  # _owned_scene → None without a populate
        media = MediaFactory.build(
            is_downloaded=True, mimetype="video/mp4", local_filename="x_id_42.mp4"
        )
        index = {PurePath(present(our.path)).name: (media, [mock_item])}
        respx_stash_processor.config.stash_enable_scene_split = False

        # Zero calls expected (warn-and-skip returns before the fail-safe).
        graphql_route = respx.post("http://localhost:9999/graphql").mock(side_effect=[])

        try:
            result = await respx_stash_processor._process_file_first(
                our, index, mock_account, None
            )
        finally:
            dump_graphql_calls(
                graphql_route.calls, "test_not_owned_false_warns_no_create"
            )

        assert result == []  # warn-and-skip adjudicates into nothing
        assert len(graphql_route.calls) == 0  # returned before the fail-safe
        assert media.stash_id is None

    @pytest.mark.asyncio
    @pytest.mark.usefixtures("isolate_scene_create_input", "enable_scene_creation")
    async def test_not_owned_true_splits(
        self, respx_stash_processor, mock_item, mock_account
    ):
        """True + still-secondary after the fresh read -> SPLIT.

        Real fail-safe re-check: the force-refetch populate resolves the file's
        scenes via the uncapable-server path fallback
        (``find(Scene, path=...)`` → ``findScenes``). Route it to return a scene
        on which our file is a SECONDARY (foreign is primary), so the re-check
        confirms not-owned and a new owned scene is split off. (On the uncapable
        path populate mutates and returns the SAME file instance, so the
        capable-server identity discriminator does not apply; the split outcome
        is asserted by value.)
        """
        foreign = VideoFile(id="99", path="/other.mp4")
        our = VideoFile(id="100", path="/dl/u/x_id_42.mp4")
        shared = Scene(id="500", files=[foreign, our])  # foreign is primary
        our.scenes = [shared]  # _owned_scene → None without a populate
        media = MediaFactory.build(
            is_downloaded=True, mimetype="video/mp4", local_filename="x_id_42.mp4"
        )
        index = {PurePath(present(our.path)).name: (media, [mock_item])}

        # Seed account/performer caches so the stamp on the split scene fires no
        # incidental GraphQL; pass the studio explicitly.
        respx_stash_processor._account = mock_account
        respx_stash_processor._performer = PerformerFactory.build(
            id="123", name=mock_account.username
        )
        studio = StudioFactory.build(id="200", name=f"{mock_account.username} (Fansly)")
        respx_stash_processor.config.stash_enable_scene_split = True

        # The force-refetch re-check resolves scenes by path → findScenes returns
        # the shared scene with our file STILL a secondary (foreign primary).
        scene_data = create_scene_dict(
            id="500",
            title="Shared",
            files=[
                create_video_file_dict("99", "/other.mp4"),
                create_video_file_dict("100", present(our.path)),
            ],
        )

        # Two calls: the force-refetch populate's path fallback issues the
        # identical findScenes-by-path query twice (direct fetch + nested files).
        def _scenes_by_path():
            return httpx.Response(
                200,
                json=create_graphql_response(
                    "findScenes",
                    create_find_scenes_result(count=1, scenes=[scene_data]),
                ),
            )

        graphql_route = respx.post("http://localhost:9999/graphql").mock(
            side_effect=[_scenes_by_path(), _scenes_by_path()]
        )

        try:
            result = await respx_stash_processor._process_file_first(
                our, index, mock_account, studio
            )
        finally:
            dump_graphql_calls(graphql_route.calls, "test_not_owned_true_splits")

        # The fail-safe forced an authoritative re-check via findScenes.
        assert any(b"findScenes" in c.request.content for c in graphql_route.calls)
        # The split produced a NEW owned scene carrying the stamped code, with our
        # file as its primary.
        assert len(result) == 1
        new_scene = result[0]
        assert new_scene.is_new() is True
        assert new_scene.code == str(media.id)
        assert [f.id for f in new_scene.files] == ["100"]
        # No create flushed here (Phase 5 owns save_all); stash_id set post-flush.
        assert not graphql_op_fired(graphql_route.calls, "sceneCreate")
        assert media.stash_id is None

    @pytest.mark.asyncio
    @pytest.mark.usefixtures("isolate_scene_create_input", "enable_scene_creation")
    async def test_not_owned_true_indeterminate_skips(
        self, respx_stash_processor, mock_item, mock_account
    ):
        """True but the forced re-check leaves scenes UNSET -> indeterminate skip.

        The fail-safe must NOT split when ownership cannot be resolved. We drive
        the genuine UNSET-scenes state via a PATH-LESS file: the path-fallback
        ``_fetch_file_reverse_via_path`` cannot build its ``path``-keyed filter,
        returns False, and leaves ``file.scenes`` UNSET — so the indeterminate
        guard fires and the split is skipped. (An empty path is the only way to
        produce UNSET scenes through the real store; a path-bearing file always
        resolves ``scenes`` to at least ``[]``.)
        """
        # Path-less file → the reverse-path fallback can't resolve scenes.
        our = VideoFile(id="100", path="")
        assert not is_set(our.scenes)
        media = MediaFactory.build(
            is_downloaded=True, mimetype="video/mp4", local_filename="x_id_42.mp4"
        )
        index = {PurePath(present(our.path)).name: (media, [mock_item])}  # key is ""
        respx_stash_processor.config.stash_enable_scene_split = True

        # Two calls: the path-less file's reverse-path refetch fires once in
        # _owned_scene's populate and once in the fail-safe force-refetch; each
        # get(BaseFile, ["path"]) finds nothing → fallback returns False → scenes
        # stays UNSET (no findScenes filter is built).
        empty: dict[str, Any] = {"data": {}}
        graphql_route = respx.post("http://localhost:9999/graphql").mock(
            side_effect=[
                httpx.Response(200, json=empty),
                httpx.Response(200, json=empty),
            ]
        )

        try:
            result = await respx_stash_processor._process_file_first(
                our, index, mock_account, None
            )
        finally:
            dump_graphql_calls(
                graphql_route.calls, "test_not_owned_true_indeterminate_skips"
            )

        # The fail-safe found scenes indeterminate (UNSET) and skipped the split.
        assert result == []
        assert not graphql_op_fired(graphql_route.calls, "sceneCreate")
        assert media.stash_id is None

    @pytest.mark.asyncio
    @pytest.mark.usefixtures("isolate_scene_create_input", "enable_scene_creation")
    async def test_not_owned_true_indeterminate_unresolved_files_skips(
        self, respx_stash_processor, mock_item, mock_account
    ):
        """True; scenes RESOLVE but a scene's ``.files`` is UNSET -> indeterminate.

        The realistic indeterminate case: the force-refetch resolves a scene we
        CANNOT confirm primary on because its ``.files`` is UNSET. A fail-safe
        must treat "couldn't determine" as SKIP, never as "not owned -> split"
        (the dangerous direction). Driven via findScenes returning a scene whose
        ``files`` key is omitted so SGC parses ``files`` as UNSET.
        """
        our = VideoFile(id="100", path="/dl/u/x_id_42.mp4")
        shared = Scene(id="500", files=[VideoFile(id="99", path="/other.mp4"), our])
        our.scenes = [shared]  # _owned_scene → None without a populate
        media = MediaFactory.build(
            is_downloaded=True, mimetype="video/mp4", local_filename="x_id_42.mp4"
        )
        index = {PurePath(present(our.path)).name: (media, [mock_item])}
        respx_stash_processor.config.stash_enable_scene_split = True

        # findScenes returns a DIFFERENT scene (id 777) with NO files key → SGC
        # parses scene.files UNSET → the unresolved-files guard fires.
        scene_data = create_scene_dict(id="777", title="Unresolved")
        del scene_data["files"]

        # Two calls: the force-refetch populate's path fallback issues the
        # findScenes-by-path query twice (direct fetch + nested resolution).
        def _scenes_by_path():
            return httpx.Response(
                200,
                json=create_graphql_response(
                    "findScenes",
                    create_find_scenes_result(count=1, scenes=[scene_data]),
                ),
            )

        graphql_route = respx.post("http://localhost:9999/graphql").mock(
            side_effect=[_scenes_by_path(), _scenes_by_path()]
        )

        try:
            result = await respx_stash_processor._process_file_first(
                our, index, mock_account, None
            )
        finally:
            dump_graphql_calls(
                graphql_route.calls,
                "test_not_owned_true_indeterminate_unresolved_files_skips",
            )

        # Re-check fired, scenes resolved but files unresolved -> skip, no split.
        assert result == []
        assert any(b"findScenes" in c.request.content for c in graphql_route.calls)
        assert not graphql_op_fired(graphql_route.calls, "sceneCreate")
        assert media.stash_id is None

    @pytest.mark.asyncio
    @pytest.mark.usefixtures("isolate_scene_create_input", "enable_scene_creation")
    async def test_not_owned_true_primary_on_recheck_skips(
        self, respx_stash_processor, mock_item, mock_account
    ):
        """True, but the fail-safe re-check resolves us as PRIMARY -> NO split.

        The cached (non-forced) read saw our file as a secondary, but the
        authoritative force-refetch shows it is actually files[0]. The C1
        fail-safe must NOT split (that would duplicate a scene we already own).
        This is the sole defense in True mode against a stale not-owned read, so
        the discriminator is result == [] AND no sceneCreate on the wire.
        """
        foreign = VideoFile(id="99", path="/other.mp4")
        our = VideoFile(id="100", path="/dl/u/x_id_42.mp4")
        shared = Scene(id="500", files=[foreign, our])  # cached: our is secondary
        our.scenes = [shared]  # _owned_scene → None without a populate
        media = MediaFactory.build(
            is_downloaded=True, mimetype="video/mp4", local_filename="x_id_42.mp4"
        )
        index = {PurePath(present(our.path)).name: (media, [mock_item])}

        respx_stash_processor._account = mock_account
        respx_stash_processor._performer = PerformerFactory.build(
            id="123", name=mock_account.username, scenes=[], images=[]
        )
        studio = StudioFactory.build(id="200", name=f"{mock_account.username} (Fansly)")
        respx_stash_processor.config.stash_enable_scene_split = True

        # The force-refetch resolves the TRUE state: our file (id 100) is files[0].
        scene_data = create_scene_dict(
            id="500",
            title="Shared",
            files=[
                create_video_file_dict("100", present(our.path)),  # we are primary now
                create_video_file_dict("99", "/other.mp4"),
            ],
        )

        def _scenes_by_path():
            return httpx.Response(
                200,
                json=create_graphql_response(
                    "findScenes",
                    create_find_scenes_result(count=1, scenes=[scene_data]),
                ),
            )

        # Two calls: the force-refetch populate's path fallback issues the
        # findScenes-by-path query twice (direct fetch + nested files).
        graphql_route = respx.post("http://localhost:9999/graphql").mock(
            side_effect=[_scenes_by_path(), _scenes_by_path()]
        )

        try:
            result = await respx_stash_processor._process_file_first(
                our, index, mock_account, studio
            )
        finally:
            dump_graphql_calls(
                graphql_route.calls, "test_not_owned_true_primary_on_recheck_skips"
            )

        # Primary on the fresh read -> no split, nothing adjudicated, no create.
        assert result == []
        assert any(b"findScenes" in c.request.content for c in graphql_route.calls)
        assert not graphql_op_fired(graphql_route.calls, "sceneCreate")
        assert media.stash_id is None

    @pytest.mark.asyncio
    @pytest.mark.usefixtures("isolate_scene_create_input")
    async def test_two_layer_backstop_create_blocked_when_guard_shut(
        self, respx_stash_processor
    ):
        """Backstop: with the guard shut, SGC itself refuses a Scene create.

        ``isolate_scene_create_input`` forces ``Scene.__create_input_type__`` to
        None. Even if the mode-guard were bypassed, ``store.save`` on a new
        Scene must raise — proving the two-layer safety (mode guard + SGC's own
        create block).
        """
        assert getattr(Scene, "__create_input_type__", None) is None
        scene = Scene(files=[VideoFile(id="1", path="/x.mp4")])

        with pytest.raises(
            ValueError, match="Scene objects cannot be created, only updated"
        ):
            await respx_stash_processor.store.save(scene)


@pytest.mark.usefixtures("isolate_scene_create_input")
class TestSceneCreationGuard:
    """Tests for StashProcessingBase._configure_scene_creation_guard.

    These tests mutate the PROCESS-GLOBAL ``Scene.__create_input_type__``. The
    ``isolate_scene_create_input`` fixture (function-scoped, yield teardown —
    runs even on assertion failure) snapshots it before and restores it after
    EACH test, so a leak can never corrupt other tests, especially split
    integration tests. It also forces the attr to ``None`` before each test so
    the "starts None" preconditions are deterministic regardless of upstream
    worker state under ``-n8``.
    """

    def test_guard_enables_creation_when_true(self, respx_stash_processor):
        """Flag is True → guard sets __create_input_type__ to SceneCreateInput."""
        respx_stash_processor.config.stash_enable_scene_split = True
        assert getattr(Scene, "__create_input_type__", None) is None

        respx_stash_processor._configure_scene_creation_guard()

        assert Scene.__create_input_type__ is SceneCreateInput

    def test_guard_leaves_unset_when_false(self, respx_stash_processor):
        """Flag is False → guard leaves creation blocked (attr stays None)."""
        respx_stash_processor.config.stash_enable_scene_split = False

        respx_stash_processor._configure_scene_creation_guard()

        assert getattr(Scene, "__create_input_type__", None) is None

    def test_guard_leaves_unset_when_dry_run(self, respx_stash_processor):
        """Flag is "dry-run" → guard leaves creation blocked (attr stays None)."""
        respx_stash_processor.config.stash_enable_scene_split = "dry-run"

        respx_stash_processor._configure_scene_creation_guard()

        assert getattr(Scene, "__create_input_type__", None) is None

    def test_guard_default_config_is_noop(self, respx_stash_processor):
        """Default (unset) flag → guard is a no-op (default-False path)."""
        respx_stash_processor._configure_scene_creation_guard()

        assert getattr(Scene, "__create_input_type__", None) is None
