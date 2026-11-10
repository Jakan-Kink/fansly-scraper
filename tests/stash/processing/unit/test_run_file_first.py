"""Unit tests for ``StashProcessing._run_file_first`` failure handling.

Two orchestration guarantees, both exercised end-to-end with the REAL sweep,
adjudication, and ``save_all`` (no SUT-method stubs) — only the Stash HTTP edge
is routed via respx:

1. **Per-file isolation** — one swept file that raises during adjudication must
   NOT abort the creator's sweep; the loop continues to the next file.
2. **Batch-flush propagation** — a ``save_all`` (the SGC batched GraphQL commit)
   failure must NOT be swallowed: a silently-swallowed batch failure reports the
   creator as a clean success with stash_ids lost.
"""

import json
from pathlib import PurePath

import httpx
import pytest
import respx
from stash_graphql_client import present
from stash_graphql_client.types import VideoFile

from metadata import ContentType
from pathio import get_stash_path
from tests.fixtures.metadata.metadata_factories import (
    AccountFactory,
    AccountMediaFactory,
    AttachmentFactory,
    MediaFactory,
    PostFactory,
)
from tests.fixtures.stash import find_files_response
from tests.fixtures.stash.stash_api_fixtures import dump_graphql_calls
from tests.fixtures.stash.stash_graphql_fixtures import (
    create_find_scenes_result,
    create_gallery_dict,
    create_graphql_response,
    create_scene_dict,
    create_video_file_dict,
)
from tests.fixtures.stash.stash_type_factories import (
    PerformerFactory,
    SceneFactory,
    StudioFactory,
)
from tests.fixtures.utils.test_isolation import snowflake_id


_GRAPHQL_URL = "http://localhost:9999/graphql"


@pytest.mark.asyncio
async def test_run_file_first_isolates_failing_file(
    entity_store, respx_stash_processor, mock_item
):
    """A swept file that raises during adjudication does not abort the sweep.

    Real multi-file sweep: ``findFiles`` yields two video files, both in the
    creator's media index (bad first). The BAD file's ``_owned_scene`` re-fetch
    (``findScenes`` by path) is routed to a GraphQL error, so its real
    adjudication raises — the per-file isolation must catch it and the loop must
    continue to the GOOD file, which resolves as owned and stamps its media.
    Discriminator: the flow does not raise AND the good media receives a
    stash_id (impossible if the bad file aborted the sweep).
    """
    processor = respx_stash_processor
    root = get_stash_path(processor.state.base_path, processor.config).rstrip("/")
    acct_id = snowflake_id()
    account = AccountFactory.build(id=acct_id, username="test_user")
    await entity_store.save(account)

    bad_path = f"{root}/test_user/bad_id_1.mp4"
    good_path = f"{root}/test_user/good_id_2.mp4"
    media_bad = MediaFactory.build(
        id=snowflake_id(),
        accountId=acct_id,
        mimetype="video/mp4",
        is_downloaded=True,
        local_filename="bad_id_1.mp4",
    )
    media_good = MediaFactory.build(
        id=snowflake_id(),
        accountId=acct_id,
        mimetype="video/mp4",
        is_downloaded=True,
        local_filename="good_id_2.mp4",
    )
    await entity_store.save(media_bad)
    await entity_store.save(media_good)
    for m in (media_bad, media_good):
        await entity_store.save(
            AccountMediaFactory.build(id=m.id, accountId=acct_id, mediaId=m.id)
        )
    post = PostFactory.build(id=snowflake_id(), accountId=acct_id)
    await entity_store.save(post)
    for pos, m in enumerate((media_bad, media_good)):
        await entity_store.save(
            AttachmentFactory.build(
                id=snowflake_id(),
                postId=post.id,
                contentId=m.id,
                contentType=ContentType.ACCOUNT_MEDIA,
                pos=pos,
            )
        )

    # Point the processor at this creator, as continue_stash_processing does,
    # so the good file's stamp fires no incidental performer query.
    processor._account = account
    processor._performer = PerformerFactory(
        id="123", name="test_user", scenes=[], images=[], galleries=[]
    )
    studio = StudioFactory(id="200", name="test_user (Fansly)")

    good_scene = create_scene_dict(
        id="700",
        title="Good",
        files=[create_video_file_dict("20", good_path)],
    )

    def _scenes_good():
        return httpx.Response(
            200,
            json=create_graphql_response(
                "findScenes", create_find_scenes_result(count=1, scenes=[good_scene])
            ),
        )

    error = httpx.Response(
        200, json={"errors": [{"message": "simulated adjudication failure"}]}
    )

    def _galleries_empty():
        return httpx.Response(
            200,
            json=create_graphql_response(
                "findGalleries", {"count": 0, "galleries": []}
            ),
        )

    gallery = create_gallery_dict(id="5000", title="Good")
    # Exact 10-call sequence (the loop survives the bad file's raise):
    #  [0] sweep findFiles (bad, good)
    #  [1] BAD file _owned_scene populate → findScenes(bad) → error → isolated
    #  [2][3] GOOD file _owned_scene populate → findScenes(good) (direct + nested)
    #         → good is files[0] → OWNED → stamp scene + media.stash_id
    #  [4][5][6] gallery preload, all empty: the studio's galleries, then those
    #         whose code or URL names the post
    #  [7] galleryCreate (new gallery id 5000) → [8] findGallery(5000) re-fetch
    #  [9] save_all Batch(op0: SceneUpdateInput) — the stamped scene flush
    route = respx.post(_GRAPHQL_URL).mock(
        side_effect=[
            find_files_response(
                create_video_file_dict("10", bad_path),
                create_video_file_dict("20", good_path),
            ),
            error,
            _scenes_good(),
            _scenes_good(),
            _galleries_empty(),
            _galleries_empty(),
            _galleries_empty(),
            httpx.Response(200, json=create_graphql_response("galleryCreate", gallery)),
            httpx.Response(200, json=create_graphql_response("findGallery", gallery)),
            httpx.Response(200, json={"data": {"op0": good_scene, "op1": gallery}}),
        ]
    )

    try:
        # Must not raise despite the bad file.
        await processor._run_file_first(account, processor._performer, studio)
    finally:
        dump_graphql_calls(route.calls, "isolates_failing_file")

    # Discriminator: the loop survived the bad file and stamped the good media.
    assert media_good.stash_id == 700
    # The post's gallery id is written to the metadata DB once the flush lands.
    assert post.stash_id == 5000
    stored_gallery_id = await entity_store.pool.fetchval(
        "SELECT stash_id FROM posts WHERE id = $1", post.id
    )
    assert stored_gallery_id == 5000


@pytest.mark.asyncio
async def test_run_file_first_propagates_batch_flush_failure(
    entity_store, respx_stash_processor
):
    """A batch-flush (``save_all``) failure propagates — not reported clean.

    The real sweep's ``findFiles`` is routed EMPTY (sweep succeeds, yields
    nothing), then the batched commit of the freshly-built (dirty) performer is
    routed to a server error — so the REAL SGC ``save_all`` raises and
    ``_run_file_first`` must NOT swallow it.

    Sequence: [0] sweep findFiles (empty) → [1] save_all performerCreate (error).
    """
    processor = respx_stash_processor
    account = AccountFactory.build()
    performer = PerformerFactory.build()  # dirty → flushed by the SUT's save_all

    error_route = respx.post(_GRAPHQL_URL).mock(
        side_effect=[
            httpx.Response(
                200, json={"data": {"findFiles": {"count": 0, "files": []}}}
            ),
            httpx.Response(
                200, json={"errors": [{"message": "simulated stash batch failure"}]}
            ),
        ]
    )

    raised: Exception | None = None
    try:
        await processor._run_file_first(account, performer, studio=None)
    except Exception as exc:
        raised = exc
    finally:
        dump_graphql_calls(error_route.calls, "propagates_batch_flush_failure")

    assert raised is not None, (
        "save_all failure was swallowed — _run_file_first reported the creator "
        "clean despite a failed Stash batch write"
    )


@pytest.mark.asyncio
async def test_adjudicate_swept_file_fans_entity_to_all_owners(
    respx_stash_processor, mock_item, mock_account
):
    """A shared media's owned scene joins EVERY owning item's gallery, once.

    The index carries two owners for one media (the shared-media fix). Adjudicating
    the owned VideoFile must accumulate the scene under BOTH item ids — the old
    last-writer-wins index would have dropped one gallery — while recording the
    media for stash_id persistence exactly ONCE (per-entity, not per-owner). Zero
    GraphQL: ownership resolves in-memory and the stamp uses cached performer/studio.
    """
    processor = respx_stash_processor
    file = VideoFile(id="500", path="/dl/test_user/x_id_42.mp4")
    scene = SceneFactory.build(id="700", title="Test Scene")
    scene.files = [file]
    file.scenes = [scene]  # _owned_scene resolves without a populate

    media = MediaFactory.build(
        is_downloaded=True, mimetype="video/mp4", local_filename="x_id_42.mp4"
    )
    # mock_item is the canonical (earliest) owner; post_b only needs an id.
    post_b = PostFactory.build(id=snowflake_id(), accountId=mock_item.accountId)
    file_path = present(file.path)
    index = {PurePath(file_path).name: (media, [mock_item, post_b])}

    processor._account = mock_account
    processor._performer = PerformerFactory.build(
        id="123", name=mock_account.username, scenes=[], images=[], galleries=[]
    )
    studio = StudioFactory.build(id="200", name=f"{mock_account.username} (Fansly)")

    item_entities: dict = {}
    media_with_id: list = []
    split_pairs: list = []

    # Zero GraphQL: any call raises, proving the owned+stamp path stays in-memory.
    route = respx.post(_GRAPHQL_URL).mock(side_effect=[])
    try:
        await processor._adjudicate_swept_file(
            file, index, mock_account, studio, item_entities, media_with_id, split_pairs
        )
    finally:
        dump_graphql_calls(route.calls, "adjudicate_fans_to_all_owners")

    # The one owned scene joined BOTH owners' gallery accumulators.
    assert set(item_entities) == {mock_item.id, post_b.id}
    assert item_entities[mock_item.id][1] == [scene]
    assert item_entities[post_b.id][1] == [scene]
    # Recorded once for stash_id persistence (per-entity, not per-owner).
    assert media_with_id == [media]
    assert split_pairs == []


@pytest.mark.asyncio
async def test_fast_path_known_media_reverifies_and_drops_from_index(
    respx_stash_processor, mock_item, mock_account
):
    """Stamped media are re-verified by-id (fast-path) and dropped from the sweep.

    The incremental entry point: an indexed media that already carries a stash_id
    goes straight to its Scene via store.get (a findScene by id) + primary
    re-verify, accumulates into every owner's gallery, and is removed from the
    index so the sweep does not re-adjudicate it. A media WITHOUT a stash_id is
    left in the index for the sweep. Discriminator: exactly the stamped leaf is
    removed, the by-id query fires, and the unstamped leaf survives untouched.
    """
    processor = respx_stash_processor

    stamped = MediaFactory.build(
        is_downloaded=True, mimetype="video/mp4", local_filename="x_id_42.mp4"
    )
    stamped.stash_id = 700
    unstamped = MediaFactory.build(
        is_downloaded=True, mimetype="video/mp4", local_filename="y_id_99.mp4"
    )  # stash_id is None -> left for the sweep

    post_b = PostFactory.build(id=snowflake_id(), accountId=mock_item.accountId)
    index = {
        "x_id_42.mp4": (stamped, [mock_item, post_b]),
        "y_id_99.mp4": (unstamped, [post_b]),
    }

    processor._account = mock_account
    processor._performer = PerformerFactory(
        id="123", name=mock_account.username, scenes=[], images=[], galleries=[]
    )
    studio = StudioFactory(id="200", name=f"{mock_account.username} (Fansly)")

    # The fast-path's store.get issues findScene by id; route a scene whose
    # primary file basename-matches the stamped media (still owned -> stamp).
    scene_data = create_scene_dict(
        id="700",
        title="Test Scene",
        performers=[],
        tags=[],
        files=[create_video_file_dict("500", "/dl/test_user/x_id_42.mp4")],
    )
    route = respx.post(_GRAPHQL_URL).mock(
        side_effect=[
            httpx.Response(200, json=create_graphql_response("findScene", scene_data))
        ]
    )

    item_entities: dict = {}
    media_with_id: list = []
    split_pairs: list = []

    try:
        await processor._fast_path_known_media(
            index, mock_account, studio, item_entities, media_with_id, split_pairs
        )
    finally:
        dump_graphql_calls(route.calls, "fast_path_known_media")

    # Only the stamped leaf was consumed by the fast-path; the unstamped leaf
    # survives for the sweep.
    assert "x_id_42.mp4" not in index
    assert "y_id_99.mp4" in index
    # The by-id re-verify fired and re-stamped the media.
    assert any(b"findScene" in c.request.content for c in route.calls)
    assert stamped.stash_id == 700
    # The owned scene joined BOTH of the stamped media's owners' galleries, once.
    assert set(item_entities) == {mock_item.id, post_b.id}
    assert media_with_id == [stamped]
    assert split_pairs == []


@pytest.mark.asyncio
async def test_fast_path_skips_gallery_link_only_when_both_ids_were_flushed(
    entity_store, respx_stash_processor
):
    """A re-verified entity is re-linked unless media AND post ids are persisted.

    ``media_done`` carries a saved stash_id, so its scene is skipped for the
    post whose gallery id is also saved, and linked for the post whose gallery
    id is only in memory and the post with none. ``media_retry`` carries an
    unsaved stash_id (a flush that never landed), so its scene is linked even
    for the post with a saved gallery id.
    """
    processor = respx_stash_processor
    acct_id = snowflake_id()
    account = AccountFactory.build(id=acct_id, username="test_user")
    await entity_store.save(account)

    media_done = MediaFactory.build(
        id=snowflake_id(),
        accountId=acct_id,
        mimetype="video/mp4",
        is_downloaded=True,
        local_filename="done_id_1.mp4",
        stash_id=700,
    )
    media_retry = MediaFactory.build(
        id=snowflake_id(),
        accountId=acct_id,
        mimetype="video/mp4",
        is_downloaded=True,
        local_filename="retry_id_2.mp4",
    )
    post_done = PostFactory.build(id=snowflake_id(), accountId=acct_id, stash_id=5000)
    post_retry = PostFactory.build(id=snowflake_id(), accountId=acct_id)
    post_new = PostFactory.build(id=snowflake_id(), accountId=acct_id)
    for saved in (media_done, media_retry, post_done, post_retry, post_new):
        await entity_store.save(saved)
    media_retry.stash_id = 701
    post_retry.stash_id = 5001

    index = {
        "done_id_1.mp4": (media_done, [post_done, post_retry, post_new]),
        "retry_id_2.mp4": (media_retry, [post_done]),
    }

    processor._account = account
    processor._performer = PerformerFactory(
        id="123", name="test_user", scenes=[], images=[], galleries=[]
    )
    studio = StudioFactory(id="200", name="test_user (Fansly)")

    def _scene(scene_id: str, file_id: str, leaf: str) -> httpx.Response:
        scene = create_scene_dict(
            id=scene_id,
            title="Test Scene",
            performers=[],
            tags=[],
            files=[create_video_file_dict(file_id, f"/dl/test_user/{leaf}")],
        )
        return httpx.Response(200, json=create_graphql_response("findScene", scene))

    route = respx.post(_GRAPHQL_URL).mock(
        side_effect=[
            _scene("700", "500", "done_id_1.mp4"),
            _scene("701", "501", "retry_id_2.mp4"),
        ]
    )

    item_entities: dict = {}
    media_with_id: list = []
    split_pairs: list = []
    try:
        await processor._fast_path_known_media(
            index, account, studio, item_entities, media_with_id, split_pairs
        )
    finally:
        dump_graphql_calls(route.calls, "fast_path_skips_flushed_links")

    assert route.call_count == 2
    assert index == {}
    linked = {
        item_id: [entity.id for entity in entities]
        for item_id, (_item, entities) in item_entities.items()
    }
    assert linked == {
        post_retry.id: ["700"],
        post_new.id: ["700"],
        post_done.id: ["701"],
    }


@pytest.mark.asyncio
async def test_run_file_first_reads_synced_media_from_the_sweep(
    entity_store, respx_stash_processor
):
    """A synced creator costs one sweep request when files carry their scenes.

    The swept file comes back with its scene, so re-verifying the already
    stamped media reads the store's copy instead of asking for it by id. The
    scene's code already matches and the post's gallery id is saved, so nothing
    else is read and nothing is written.
    """
    processor = respx_stash_processor
    processor.config.stash_files_batch = 120
    root = get_stash_path(processor.state.base_path, processor.config).rstrip("/")
    acct_id = snowflake_id()
    account = AccountFactory.build(id=acct_id, username="test_user")
    await entity_store.save(account)

    path = f"{root}/test_user/done_id_1.mp4"
    media = MediaFactory.build(
        id=snowflake_id(),
        accountId=acct_id,
        mimetype="video/mp4",
        is_downloaded=True,
        local_filename="done_id_1.mp4",
        stash_id=700,
    )
    await entity_store.save(media)
    await entity_store.save(
        AccountMediaFactory.build(id=media.id, accountId=acct_id, mediaId=media.id)
    )
    post = PostFactory.build(id=snowflake_id(), accountId=acct_id, stash_id=5000)
    await entity_store.save(post)
    await entity_store.save(
        AttachmentFactory.build(
            id=snowflake_id(),
            postId=post.id,
            contentId=media.id,
            contentType=ContentType.ACCOUNT_MEDIA,
            pos=0,
        )
    )

    processor._account = account
    processor._performer = PerformerFactory(
        id="123", name="test_user", scenes=[], images=[], galleries=[]
    )
    studio = StudioFactory(id="200", name="test_user (Fansly)")

    file_dict = create_video_file_dict("20", path)
    scene = create_scene_dict(
        id="700",
        title="Done",
        code=str(media.id),
        date="2024-01-01",
        details="synced",
        files=[file_dict],
    )
    route = respx.post(_GRAPHQL_URL).mock(
        side_effect=[find_files_response({**file_dict, "scenes": [scene]})]
    )

    try:
        await processor._run_file_first(account, processor._performer, studio)
    finally:
        dump_graphql_calls(route.calls, "reads_synced_media_from_sweep")

    assert route.call_count == 1
    assert media.stash_id == 700
    assert post.stash_id == 5000
    # The sweep's page size comes from stash_context.batches.files.
    sweep_request = json.loads(route.calls[0].request.content)
    assert sweep_request["variables"]["filter"]["per_page"] == 120


@pytest.mark.asyncio
async def test_fast_path_current_only_leaves_earlier_media_unread(
    respx_stash_processor, mock_item, mock_account
):
    """The daemon pass re-verifies only media downloaded this cycle.

    Both media carry a stash_id; only the one with ``local_path`` (this cycle's
    download) is read from Stash. The other stays in the index, unread.
    """
    processor = respx_stash_processor
    current = MediaFactory.build(
        is_downloaded=True, mimetype="video/mp4", local_filename="new_id_1.mp4"
    )
    current.stash_id = 700
    current.local_path = "/dl/test_user/new_id_1.mp4"
    earlier = MediaFactory.build(
        is_downloaded=True, mimetype="video/mp4", local_filename="old_id_2.mp4"
    )
    earlier.stash_id = 701
    index = {
        "new_id_1.mp4": (current, [mock_item]),
        "old_id_2.mp4": (earlier, [mock_item]),
    }

    processor._account = mock_account
    processor._performer = PerformerFactory(
        id="123", name=mock_account.username, scenes=[], images=[], galleries=[]
    )
    studio = StudioFactory(id="200", name=f"{mock_account.username} (Fansly)")

    scene_data = create_scene_dict(
        id="700",
        title="Test Scene",
        performers=[],
        tags=[],
        files=[create_video_file_dict("500", "/dl/test_user/new_id_1.mp4")],
    )
    route = respx.post(_GRAPHQL_URL).mock(
        side_effect=[
            httpx.Response(200, json=create_graphql_response("findScene", scene_data))
        ]
    )

    item_entities: dict = {}
    media_with_id: list = []
    split_pairs: list = []
    try:
        await processor._fast_path_known_media(
            index,
            mock_account,
            studio,
            item_entities,
            media_with_id,
            split_pairs,
            current_only=True,
        )
    finally:
        dump_graphql_calls(route.calls, "fast_path_current_only")

    assert route.call_count == 1
    assert list(index) == ["old_id_2.mp4"]
    assert media_with_id == [current]
