"""Integration test for MediaProcessingMixin._split_scene_for_file.

TRUE INTEGRATION TEST: hits the real Docker Stash instance.

Scenario: our downloaded file is a SECONDARY on a scene that another source
OWNS (its primary is a foreign file). The split CREATEs a brand-new Scene that
owns our file; the Stash server reassigns our file off the shared scene
(per SceneCreateInput: "Files will be reassigned from existing scenes if
applicable. Files must not already be primary for another scene.").

The split only fires when our file is primary of NO scene. The setup uses
``scene_assign_file`` whose primary/secondary semantics are determined
EMPIRICALLY here via the precondition assertions (see the docstring on the
test).
"""

import time

import pytest
from stash_graphql_client.types import Scene

from tests.fixtures.metadata.metadata_factories import (
    AccountFactory,
    MediaFactory,
    MessageFactory,
)
from tests.fixtures.stash.stash_api_fixtures import dump_graphql_calls
from tests.fixtures.stash.stash_integration_fixtures import capture_graphql_calls


@pytest.mark.integration
@pytest.mark.asyncio
async def test_split_scene_for_file_reassigns_off_shared_scene(
    entity_store,
    real_stash_processor,
    stash_cleanup_tracker,
    enable_scene_creation,
):
    """Our file (secondary on a foreign-owned scene) splits onto a NEW scene.

    Setup makes our file a SECONDARY of a shared scene and primary of nothing,
    then asserts that precondition (so the test cannot pass for the wrong
    reason). After the split + save_all, force-refetches and asserts our file
    is now primary of a NEW scene and detached from the shared scene.
    """
    async with stash_cleanup_tracker(real_stash_processor.context.client) as cleanup:
        client = real_stash_processor.context.client
        store = real_stash_processor.store

        # --- Fetch ALL scenes (per_page=-1) and build a COMPLETE primary map. ---
        # We need: a SHARED scene whose primary is a foreign file, carrying our
        # file as a SECONDARY (our file primary of NOTHING).
        #
        # scene_assign_file REFUSES a file that is primary of another scene
        # ("cannot reassign primary file" — empirically confirmed), so we cannot
        # demote a primary with assign. Two ways to obtain a secondary:
        #   A. an existing scene with >=2 files holding a secondary that is
        #      primary of nothing; zero setup mutation, repeatable.
        #   B. sceneMerge a source INTO dest — the source's primary lands as a
        #      SECONDARY on dest (dest already has a primary) and the source
        #      scene is destroyed → our file primary of nothing. This is the
        #      real-world pHash-dedup collapse this feature exists to handle.
        #
        # "primary of nothing" is derived from the FORWARD direction
        # (scene.files[0]) across ALL scenes — the reverse field (file.scenes)
        # is unreliable on this server (no findVideoFile → populate fails), so
        # we never use _owned_scene here.
        scenes_result = await client.find_scenes(filter_={"per_page": -1})
        if scenes_result.count < 2:
            pytest.skip("Docker Stash needs >=2 scenes with files for the split test.")

        scenes: list[Scene] = [
            await store.populate(s, ["files"], force_refetch=True)
            for s in scenes_result.scenes
        ]
        # The primary map must cover EVERY scene, else a file primary of an
        # un-fetched scene reads as primary-of-nothing (a false-clean pick).
        assert len(scenes) == scenes_result.count, (
            f"incomplete scene fetch: {len(scenes)} of {scenes_result.count}"
        )
        # Every file that is some scene's PRIMARY (files[0]).
        primary_ids = {s.files[0].id for s in scenes if s.files}

        shared: Scene | None = None
        our_file = None
        # Approach A: a scene with a secondary that is primary of nothing.
        for s in scenes:
            if not s.files or len(s.files) < 2:
                continue
            for candidate in s.files[1:]:
                if candidate.id not in primary_ids:
                    shared = s
                    our_file = candidate
                    break
            if shared is not None:
                break

        # Approach B: merge two single-file scenes (fallback).
        if shared is None:
            single = [s for s in scenes if s.files]
            if len(single) < 2:
                pytest.skip("Docker Stash needs >=2 scenes with files for the split.")
            dest, src = single[0], single[1]
            assert dest.files[0].id != src.files[0].id, "need two distinct files"
            our_file = src.files[0]
            await client.scene_merge({"source": [src.id], "destination": dest.id})
            shared = await store.populate(dest, ["files"], force_refetch=True)
            primary_ids.discard(our_file.id)  # src destroyed → no longer primary

        # --- ASSERT THE PRECONDITION (self-verifying; forward, Scene-driven). ---
        shared = await store.populate(shared, ["files"], force_refetch=True)
        shared_file_ids = [f.id for f in (shared.files or [])]

        assert our_file.id in shared_file_ids, (
            f"PRECONDITION FAILED: our_file {our_file.id} not attached to shared "
            f"scene {shared.id}; shared.files={shared_file_ids}"
        )
        assert shared.files[0].id != our_file.id, (
            f"PRECONDITION FAILED: our_file {our_file.id} is PRIMARY of shared "
            f"scene {shared.id}; shared.files={shared_file_ids}"
        )
        # Primary of NOTHING: not the first file of any scene (forward map).
        assert our_file.id not in primary_ids, (
            f"PRECONDITION FAILED: our_file {our_file.id} is primary of some "
            f"scene (in forward primary map); primary_ids sample shows it present"
        )

        # --- Build the FDNG-side metadata (real DB) for stamping. ---
        unique_id = int(time.time() * 1000) % 1000000
        account = AccountFactory.build(
            id=100000000000000000 + unique_id,
            username=f"test_split_{unique_id}",
        )
        await entity_store.save(account)
        media = MediaFactory.build(
            id=200000000000000000 + unique_id,
            accountId=account.id,
            mimetype="video/mp4",
            is_downloaded=True,
        )
        await entity_store.save(media)
        message = MessageFactory.build(
            id=500000000000000000 + unique_id,
            senderId=account.id,
        )

        # --- Run the split + flush. ---
        try:
            with capture_graphql_calls(client) as calls:
                new_scene = await real_stash_processor._split_scene_for_file(
                    our_file, media, message, account
                )
        finally:
            dump_graphql_calls(calls, "split_scene_reassigns_off_shared")
        assert new_scene.is_new()
        await store.save_all()
        # Track the created scene for cleanup (belt-and-suspenders alongside
        # the tracker's auto-capture of sceneCreate).
        cleanup["scenes"].append(new_scene.id)

        # --- Force-refetch before the post-split assertions (cache is stale). ---
        # Drive all facts off SCENE refetches (VideoFile has no findVideoFile on
        # this server, so file-by-id force_refetch is unavailable).
        new_scene = await store.populate(new_scene, ["files"], force_refetch=True)
        shared = await store.populate(shared, ["files"], force_refetch=True)

        our_file_id = our_file.id
        new_scene_file_ids = [f.id for f in (new_scene.files or [])]
        new_shared_file_ids = [f.id for f in (shared.files or [])]

        # (a) our_file is now PRIMARY of the NEW scene.
        assert new_scene.files, f"new scene {new_scene.id} has no files after split"
        assert new_scene.files[0].id == our_file_id, (
            f"our_file {our_file_id} is not primary of new scene {new_scene.id}; "
            f"new_scene.files={new_scene_file_ids}"
        )
        # The new scene is distinct from the shared scene.
        assert new_scene.id != shared.id

        # (b) our_file is DETACHED from the shared scene.
        assert our_file_id not in new_shared_file_ids, (
            f"our_file {our_file_id} still attached to shared scene {shared.id}; "
            f"shared.files={new_shared_file_ids}"
        )
