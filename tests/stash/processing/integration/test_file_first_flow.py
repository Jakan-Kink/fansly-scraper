"""Integration test for the file-first Stash processing flow.

TRUE INTEGRATION TEST: hits the real Docker Stash instance (localhost:9999).

Exercises ``StashProcessing._run_file_first`` end-to-end against real Stash
files. The flow sweeps every Stash file under the creator's root, adjudicates
each against the creator's downloaded media (gathered from posts), composes one
gallery per post, flushes via a single ``save_all``, and persists the affected
Media rows back to the FDNG metadata DB.

The test LEADS WITH POSTS (not messages): ``_gather_creator_posts`` keys only
on ``accountId == account.id and bool(attachments)`` — no Group.users wiring is
needed, so the gather provably finds the seeded posts (a guard asserts this so
the flow cannot false-green on an empty gather).

The stamped-entity assertion leans on IMAGES: ``_adjudicate_image``'s find-
fragment route is the reliable path on this server, whereas swept VideoFiles
depend on the unreliable ``file.scenes`` reverse field (see test_scene_split.py).
Scene-split is left disabled (default); owned-image stamping + gallery
composition satisfy the bar.
"""

from datetime import UTC, datetime
from pathlib import PurePath

import pytest
from stash_graphql_client.types import Gallery, Performer, Studio

from metadata import (
    Account,
    AccountMedia,
    AccountMediaBundle,
    Attachment,
    ContentType,
    Group,
    Hashtag,
    Media,
    MediaStory,
    MediaStoryState,
    Message,
    Post,
    PostMention,
    TimelineStats,
    Wall,
)
from metadata.models import get_store
from tests.fixtures.metadata.metadata_factories import (
    AccountFactory,
    AttachmentFactory,
    PostFactory,
)
from tests.fixtures.stash.stash_api_fixtures import dump_graphql_calls
from tests.fixtures.stash.stash_integration_fixtures import capture_graphql_calls
from tests.fixtures.utils import get_unique_test_id, poll_until


# Same order create_entity_store() preloads at startup: leaves -> media tree ->
# account -> content entities. A genuinely cold FDNG cache is rebuilt with this.
_PRELOAD_ORDER = [
    Hashtag,
    MediaStory,
    TimelineStats,
    MediaStoryState,
    Media,
    AccountMedia,
    AccountMediaBundle,
    Account,
    Wall,
    Attachment,
    PostMention,
    Post,
    Message,
    Group,
]


@pytest.mark.integration
@pytest.mark.asyncio
async def test_run_file_first_stamps_and_composes_galleries(
    entity_store,
    real_stash_processor,
    message_media_generator,
    stash_cleanup_tracker,
):
    """``_run_file_first`` stamps swept files and composes per-post galleries.

    Seeds posts whose media ``local_filename`` are REAL Stash file paths (from
    ``message_media_generator``) so the sweep's leaf-name index matches. Asserts:
      - the gather provably found the seeded posts (no false-green);
      - the flow completes end-to-end without error;
      - at least one stamped entity / gallery / link was produced;
      - composed gallery links rode ``save_all`` (re-fetched, entities attached);
      - affected Media received a ``stash_id``.
    """
    async with stash_cleanup_tracker(real_stash_processor.context.client) as cleanup:
        store = real_stash_processor.store
        client = real_stash_processor.context.client

        # spread_over_objs=3 → media distributed across 3 posts.
        media_meta = await message_media_generator(spread_over_objs=3)

        test_id = get_unique_test_id()
        account = AccountFactory.build(username=f"file_first_{test_id}")
        await entity_store.save(account)

        all_seeded_media = []
        posts = []
        for i in range(len(media_meta)):
            post_media = media_meta[i]

            for media in post_media.media_items:
                media.accountId = account.id
                # Clear any pre-seeded stash_id so the RUN is what assigns it
                # (the generator stamps ~33% of media); makes the stash_id
                # assertion below meaningful.
                media.stash_id = None
                await entity_store.save(media)
                all_seeded_media.append(media)

            for account_media in post_media.account_media_items:
                account_media.accountId = account.id
                await entity_store.save(account_media)

            post = PostFactory.build(
                accountId=account.id,
                content=f"File-first post {i}",
                # Date earlier than any Stash file date so _stamp_metadata does
                # not skip the update.
                createdAt=datetime(2000, 1, 1, tzinfo=UTC),
            )
            await entity_store.save(post)

            post_attachments = []
            for pos, account_media in enumerate(post_media.account_media_items):
                attachment = AttachmentFactory.build(
                    postId=post.id,
                    contentType=ContentType.ACCOUNT_MEDIA,
                    contentId=account_media.id,
                    pos=pos,
                )
                await entity_store.save(attachment)
                post_attachments.append(attachment)

            post.attachments = post_attachments
            posts.append(post)

        # --- GUARD: the gather must provably find the seeded posts, else the ---
        # flow processes nothing and false-greens on "ran without error".
        gathered = await real_stash_processor._gather_creator_posts(account)
        gathered_ids = {p.id for p in gathered}
        assert {p.id for p in posts} <= gathered_ids, (
            f"gather did not find seeded posts: seeded={[p.id for p in posts]} "
            f"gathered={sorted(gathered_ids)}"
        )

        # Performer + studio for gallery composition.
        performer = Performer(
            name=f"[TEST] File First {test_id}",
            urls=[f"https://fansly.com/{account.username}"],
        )
        performer = await client.create_performer(performer)
        cleanup["performers"].append(performer.id)

        studio_result = await client.find_studios(q="Fansly (network)")
        studio: Studio | None = None
        if studio_result and studio_result.count > 0:
            studio_item = studio_result.studios[0]
            studio = (
                Studio(**studio_item) if isinstance(studio_item, dict) else studio_item
            )

        store.invalidate_all()

        calls: list = []
        try:
            with capture_graphql_calls(client) as calls:
                # --- Run the file-first flow end-to-end. ---
                await real_stash_processor._run_file_first(account, performer, studio)
        finally:
            dump_graphql_calls(calls, "test_run_file_first_stamps_and_composes")

        # Galleries created this run, harvested off the captured galleryCreate wire
        # (no store.save spy needed — the mutation result carries the new id).
        created_galleries: list[str] = []
        for c in calls:
            if "galleryCreate" in c.get("query", ""):
                gid = (c.get("result") or {}).get("galleryCreate", {}).get("id")
                if gid is not None and gid not in created_galleries:
                    created_galleries.append(gid)
        for gid in created_galleries:
            if gid not in cleanup["galleries"]:
                cleanup["galleries"].append(gid)

        # --- Outcome 1: media stamped this run (stash_id assigned + persisted). ---
        media_with_stash_id = [m for m in all_seeded_media if m.stash_id]

        # --- Outcome 2: galleries created AND their links rode save_all. ---
        gallery_create_ops = [c for c in calls if "galleryCreate" in c.get("query", "")]
        # Product contract, timing-independent: the link mutations fired in the
        # batched flush. Asserted off the captured wire (not a server re-fetch)
        # so it cannot race Stash's SQLite read-after-write visibility.
        link_ops = [
            c
            for c in calls
            if "addGalleryImages" in c.get("query", "")
            or "addGalleryScenes" in c.get("query", "")
        ]

        # Server-state confirmation, SQLite-lag-tolerant: a burst of
        # CreateGallery + AddGalleryImages followed by an immediate force_refetch
        # can read 0 because the new read connection beats the write's WAL
        # visibility. Poll the re-fetch briefly so it reflects committed state.
        async def _count_gallery_links(gid: str) -> int:
            fresh = await store.get_many(Gallery, [gid])
            gal = fresh[0] if fresh else None
            if gal is None:
                return 0
            gal = await store.populate(gal, ["images", "scenes"], force_refetch=True)
            imgs = gal.images if isinstance(gal.images, list) else []
            scns = gal.scenes if isinstance(gal.scenes, list) else []
            return len(imgs) + len(scns)

        linked_entity_total = 0
        for gid in set(created_galleries):
            linked_entity_total += await poll_until(
                lambda gid=gid: _count_gallery_links(gid),
                lambda n: n > 0,
            )

        # --- The bar: end-to-end success + at least one positive outcome. ---
        produced_something = (
            len(media_with_stash_id) > 0
            or len(gallery_create_ops) > 0
            or linked_entity_total > 0
        )
        assert produced_something, (
            "file-first flow produced no stamped media, no galleries, and no "
            f"links. galleryCreate ops={len(gallery_create_ops)}, "
            f"media_with_stash_id={len(media_with_stash_id)}, "
            f"linked_entities={linked_entity_total}. "
            "(If Docker Stash files do not match seeded media basenames, the "
            "sweep adjudicates nothing — check base_path vs seeded paths.)"
        )

        # If galleries were created, their links MUST have ridden save_all (there
        # is NO explicit per-gallery save in _compose_gallery_for_item). Assert on
        # the link mutations off the wire — the SQLite-lagged re-fetch above is a
        # supplementary confirmation, not the gate.
        if gallery_create_ops:
            assert link_ops, (
                f"{len(gallery_create_ops)} galleries created but no "
                "addGalleryImages/addGalleryScenes mutation fired — links did "
                "not ride the batched flush."
            )

        # --- Stamping proof: an owned image got metadata stamped in place. ---
        # Each stamped image fires imageUpdate AND sets media.stash_id; the skip
        # path returns without either. With per-run unique titles the update is
        # not a no-op. (Scenes are NOT exercised here — file.scenes reverse
        # resolution is unreliable on this Docker Stash, so swept VideoFiles
        # fall through to the disabled split path; see module docstring.)
        assert any("imageUpdate" in c.get("query", "") for c in calls), (
            "no imageUpdate fired — no image was stamped, so the file-first "
            "adjudication did not stamp metadata this run"
        )

        # --- stash_id proof: stamped media carry a stash_id. ---
        assert len(media_with_stash_id) > 0, (
            "no seeded media received a stash_id; expected the stamped images' "
            "media to be assigned and persisted by the flow"
        )

        # --- Persistence proof (g): get_store().save() WROTE the row. ---
        # Re-read one stamped media fresh from the FDNG metadata DB and confirm
        # its stash_id persisted (the wrap-validator merges into the identity
        # map, but find() still issues a real DB query for non-fully-loaded
        # Media, exercising the write path's durability).
        sample = media_with_stash_id[0]
        fdng_store = get_store()
        rows = await fdng_store.find(Media, id=sample.id)
        assert rows, f"stamped media {sample.id} not found in FDNG DB after save"
        assert rows[0].stash_id == sample.stash_id, (
            f"persisted stash_id {rows[0].stash_id!r} != in-memory "
            f"{sample.stash_id!r} for media {sample.id}"
        )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_run_file_first_incremental_scans_and_adjudicates_by_basename(
    entity_store,
    real_stash_processor,
    message_media_generator,
    stash_cleanup_tracker,
):
    """The daemon's sweep-free incremental pass: refined scan + find_one(basename).

    Mirrors the full-sweep test but exercises ``_run_file_first_incremental``:
    each media carries its full ``local_path`` (captured at download time) and a
    basename ``local_filename`` (the production shape). The pass scans EXACTLY
    those files (``metadataScan`` on the file paths — the full sweep never scans),
    settles, locates each by basename (override-safe), adjudicates, and flushes.

    Asserts the refined scan fired on the seeded files and that adjudication ran
    end-to-end (an image stamped + its media got a persisted stash_id).
    """
    async with stash_cleanup_tracker(real_stash_processor.context.client) as cleanup:
        store = real_stash_processor.store
        client = real_stash_processor.context.client
        # Settle is a real sleep; zero it so the test does not idle.
        real_stash_processor.config.stash_scan_settle_s = 0.0

        media_meta = await message_media_generator(spread_over_objs=3)

        test_id = get_unique_test_id()
        account = AccountFactory.build(username=f"incr_first_{test_id}")
        await entity_store.save(account)

        all_seeded_media = []
        seeded_basenames: set[str] = set()
        posts = []
        for i in range(len(media_meta)):
            post_media = media_meta[i]
            for media in post_media.media_items:
                media.accountId = account.id
                media.stash_id = None
                # Production shape: full path on local_path (the daemon captured
                # it at download time), basename on local_filename.
                real_path = media.local_filename
                media.local_path = real_path
                media.local_filename = PurePath(real_path).name
                seeded_basenames.add(media.local_filename)
                await entity_store.save(media)
                all_seeded_media.append(media)

            for account_media in post_media.account_media_items:
                account_media.accountId = account.id
                await entity_store.save(account_media)

            post = PostFactory.build(
                accountId=account.id,
                content=f"Incremental post {i}",
                createdAt=datetime(2000, 1, 1, tzinfo=UTC),
            )
            await entity_store.save(post)

            post_attachments = []
            for pos, account_media in enumerate(post_media.account_media_items):
                attachment = AttachmentFactory.build(
                    postId=post.id,
                    contentType=ContentType.ACCOUNT_MEDIA,
                    contentId=account_media.id,
                    pos=pos,
                )
                await entity_store.save(attachment)
                post_attachments.append(attachment)
            post.attachments = post_attachments
            posts.append(post)

        # GUARD: the gather must provably find the seeded posts (no false-green).
        gathered = await real_stash_processor._gather_creator_posts(account)
        assert {p.id for p in posts} <= {p.id for p in gathered}, (
            "gather did not find seeded posts"
        )

        performer = Performer(
            name=f"[TEST] Incremental {test_id}",
            urls=[f"https://fansly.com/{account.username}"],
        )
        performer = await client.create_performer(performer)
        cleanup["performers"].append(performer.id)

        studio_result = await client.find_studios(q="Fansly (network)")
        studio: Studio | None = None
        if studio_result and studio_result.count > 0:
            studio_item = studio_result.studios[0]
            studio = (
                Studio(**studio_item) if isinstance(studio_item, dict) else studio_item
            )

        store.invalidate_all()

        calls: list = []
        try:
            with capture_graphql_calls(client) as calls:
                await real_stash_processor._run_file_first_incremental(
                    account, performer, studio
                )
        finally:
            dump_graphql_calls(calls, "test_run_file_first_incremental")

        for c in calls:
            if "galleryCreate" in c.get("query", ""):
                gid = (c.get("result") or {}).get("galleryCreate", {}).get("id")
                if gid and gid not in cleanup["galleries"]:
                    cleanup["galleries"].append(gid)

        # --- Discriminator 1: a REFINED scan fired on the seeded files. The full
        # sweep path (_run_file_first) never scans, so metadataScan here proves
        # the incremental path ran; the basenames prove it was file-scoped. ---
        scan_calls = [c for c in calls if "metadataScan" in c.get("query", "")]
        assert scan_calls, "incremental pass issued no metadataScan (no refined scan)"
        scanned_basenames: set[str] = set()
        for c in scan_calls:
            paths = ((c.get("variables") or {}).get("input") or {}).get("paths") or []
            scanned_basenames.update(PurePath(p).name for p in paths)
        assert seeded_basenames & scanned_basenames, (
            f"metadataScan did not target the seeded files; "
            f"scanned={scanned_basenames}, seeded={seeded_basenames}"
        )

        # --- Discriminator 2: the per-media basename lookup ran (findFiles). ---
        assert any("findFiles" in c.get("query", "") for c in calls), (
            "no findFiles issued — the per-media basename lookup did not run"
        )

        # --- Outcome: adjudication ran end-to-end (image stamped + stash_id). ---
        assert any("imageUpdate" in c.get("query", "") for c in calls), (
            "no imageUpdate — find_one located the file but nothing was stamped"
        )
        media_with_stash_id = [m for m in all_seeded_media if m.stash_id]
        assert media_with_stash_id, "no seeded media received a stash_id"

        # Persistence: the assigned stash_id reached the FDNG metadata DB.
        sample = media_with_stash_id[0]
        rows = await get_store().find(Media, id=sample.id)
        assert rows, f"media {sample.id} not found in metadata DB"
        assert rows[0].stash_id == sample.stash_id, (
            f"stash_id for media {sample.id} did not persist"
        )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_process_creator_incremental_full_lifecycle(
    entity_store,
    real_stash_processor,
    message_media_generator,
    stash_cleanup_tracker,
):
    """The daemon entry ``process_creator_incremental`` runs the whole lifecycle.

    Unlike the prior test (which calls ``_run_file_first_incremental`` directly),
    this drives the real daemon entry end-to-end: connect -> resolve studio +
    performer -> refined scan -> find_one(basename) -> adjudicate -> flush ->
    finalize. The processor resolves/creates the performer itself (captured off
    the wire for cleanup). Proves the awaited daemon path archives content.
    """
    async with stash_cleanup_tracker(real_stash_processor.context.client) as cleanup:
        client = real_stash_processor.context.client
        real_stash_processor.config.stash_scan_settle_s = 0.0
        # The fixture connects the client but leaves stash_context_conn unset
        # (sibling tests call _run_file_first directly, bypassing _connect_stash).
        # The real daemon path runs only when Stash is configured, so set it.
        real_stash_processor.config.stash_context_conn = {
            "scheme": "http",
            "host": "localhost",
            "port": 9999,
            "apikey": "",
        }

        media_meta = await message_media_generator(spread_over_objs=3)

        test_id = get_unique_test_id()
        # process_creator resolves the account via state.creator_id, so the
        # seeded account must use it (the daemon entry resolves, not injects).
        account = AccountFactory.build(
            id=real_stash_processor.state.creator_id,
            username=f"incr_life_{test_id}",
        )
        await entity_store.save(account)

        all_seeded_media = []
        for i in range(len(media_meta)):
            post_media = media_meta[i]
            for media in post_media.media_items:
                media.accountId = account.id
                media.stash_id = None
                real_path = media.local_filename
                media.local_path = real_path
                media.local_filename = PurePath(real_path).name
                await entity_store.save(media)
                all_seeded_media.append(media)
            for account_media in post_media.account_media_items:
                account_media.accountId = account.id
                await entity_store.save(account_media)
            post = PostFactory.build(
                accountId=account.id,
                content=f"Lifecycle post {i}",
                createdAt=datetime(2000, 1, 1, tzinfo=UTC),
            )
            await entity_store.save(post)
            post_attachments = []
            for pos, account_media in enumerate(post_media.account_media_items):
                attachment = AttachmentFactory.build(
                    postId=post.id,
                    contentType=ContentType.ACCOUNT_MEDIA,
                    contentId=account_media.id,
                    pos=pos,
                )
                await entity_store.save(attachment)
                post_attachments.append(attachment)
            post.attachments = post_attachments

        calls: list = []
        try:
            with capture_graphql_calls(client) as calls:
                # The real daemon entry — resolves performer/studio itself.
                await real_stash_processor.process_creator_incremental()
        finally:
            dump_graphql_calls(calls, "test_process_creator_incremental_lifecycle")

        # Capture the performer + galleries this run created, for cleanup.
        for c in calls:
            query = c.get("query", "")
            result = c.get("result") or {}
            if "performerCreate" in query:
                pid = (result.get("performerCreate") or {}).get("id")
                if pid and pid not in cleanup["performers"]:
                    cleanup["performers"].append(pid)
            if "galleryCreate" in query:
                gid = (result.get("galleryCreate") or {}).get("id")
                if gid and gid not in cleanup["galleries"]:
                    cleanup["galleries"].append(gid)

        # The lifecycle ran the refined scan and stamped content end-to-end.
        assert any("metadataScan" in c.get("query", "") for c in calls), (
            "process_creator_incremental issued no metadataScan"
        )
        assert any("imageUpdate" in c.get("query", "") for c in calls), (
            "process_creator_incremental stamped nothing"
        )
        assert [m for m in all_seeded_media if m.stash_id], (
            "no seeded media received a stash_id from the lifecycle entry"
        )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_run_file_first_archives_content_on_cold_cache(
    entity_store,
    real_stash_processor,
    message_media_generator,
    stash_cleanup_tracker,
):
    """STASH_ONLY's cold cache still archives content (regression for #15).

    The STASH_ONLY path runs no ``download_*`` populate, so the only thing
    warming the FDNG identity map is the startup ``preload`` — which resolves
    ``belongs_to``/``habtm`` but NOT ``has_many`` reverse-FK lists. That left
    ``Post.attachments`` empty, the gather blind, and the whole creator a silent
    no-op. ``_run_file_first`` now rebuilds those lists before gathering.

    This test reproduces the real cold path end-to-end against live Docker Stash
    with ACTUAL objects — no stubbed sweep, gather, or reconstruction:

      - seed posts whose media ``local_filename`` are REAL Stash file paths, but
        DO NOT set ``post.attachments`` by hand (that is the warm-cache shortcut
        the sibling test takes — and exactly what hid this bug);
      - drop the FDNG identity map and rebuild it with the startup ``preload``
        order, so the cache is genuinely cold;
      - PROVE the cache is cold: the bare gather returns nothing (pre-flow, the
        creator is invisible — the defect surface);
      - run the REAL ``_run_file_first`` and assert it archives content anyway.

    Discriminator: revert the in-flow reconstruction and the cold gather stays
    empty -> the flow produces nothing -> ``produced_something`` fails.
    """
    async with stash_cleanup_tracker(real_stash_processor.context.client) as cleanup:
        stash_store = real_stash_processor.store
        client = real_stash_processor.context.client

        media_meta = await message_media_generator(spread_over_objs=3)

        test_id = get_unique_test_id()
        account = AccountFactory.build(username=f"cold_first_{test_id}")
        await entity_store.save(account)

        all_seeded_media = []
        seeded_post_ids = []
        for i in range(len(media_meta)):
            post_media = media_meta[i]

            for media in post_media.media_items:
                media.accountId = account.id
                media.stash_id = None  # the RUN must assign it
                await entity_store.save(media)
                all_seeded_media.append(media)

            for account_media in post_media.account_media_items:
                account_media.accountId = account.id
                await entity_store.save(account_media)

            post = PostFactory.build(
                accountId=account.id,
                content=f"Cold-cache post {i}",
                createdAt=datetime(2000, 1, 1, tzinfo=UTC),
            )
            await entity_store.save(post)
            seeded_post_ids.append(post.id)

            for pos, account_media in enumerate(post_media.account_media_items):
                attachment = AttachmentFactory.build(
                    postId=post.id,
                    contentType=ContentType.ACCOUNT_MEDIA,
                    contentId=account_media.id,
                    pos=pos,
                )
                await entity_store.save(attachment)
                # NOTE: deliberately NOT setting post.attachments — the cold
                # preload below must be the only thing that can link them.

        # --- Make the FDNG cache genuinely cold: drop the identity map and ---
        # rebuild it exactly as a fresh STASH_ONLY process would at startup.
        entity_store.invalidate_all()
        await entity_store.preload(_PRELOAD_ORDER)

        # --- PROVE the defect surface: cold gather is blind pre-flow. ---
        assert await real_stash_processor._gather_creator_posts(account) == [], (
            "expected the cold preload to leave posts attachment-less (gather "
            "blind); if this is non-empty the cache was not actually cold and "
            "the regression cannot be proven"
        )

        performer = Performer(
            name=f"[TEST] Cold First {test_id}",
            urls=[f"https://fansly.com/{account.username}"],
        )
        performer = await client.create_performer(performer)
        cleanup["performers"].append(performer.id)

        studio_result = await client.find_studios(q="Fansly (network)")
        studio: Studio | None = None
        if studio_result and studio_result.count > 0:
            studio_item = studio_result.studios[0]
            studio = (
                Studio(**studio_item) if isinstance(studio_item, dict) else studio_item
            )

        # Clear only the Stash identity map (find_* cache pollution); the FDNG
        # cache must stay cold to exercise the in-flow reconstruction.
        stash_store.invalidate_all()

        calls: list = []
        try:
            with capture_graphql_calls(client) as calls:
                await real_stash_processor._run_file_first(account, performer, studio)
        finally:
            dump_graphql_calls(calls, "test_run_file_first_cold_cache")

        # Cleanup tracks created galleries off the wire (no save spy needed).
        for c in calls:
            if "galleryCreate" in c.get("query", ""):
                gid = (c.get("result") or {}).get("galleryCreate", {}).get("id")
                if gid is not None and gid not in cleanup["galleries"]:
                    cleanup["galleries"].append(gid)

        # --- The bar: the cold path archived content (the bug made this zero). ---
        media_with_stash_id = [m for m in all_seeded_media if m.stash_id]
        gallery_create_ops = [c for c in calls if "galleryCreate" in c.get("query", "")]
        produced_something = len(media_with_stash_id) > 0 or len(gallery_create_ops) > 0
        assert produced_something, (
            "cold-cache file-first flow archived nothing: no stamped media and "
            f"no galleries (galleryCreate={len(gallery_create_ops)}, "
            f"media_with_stash_id={len(media_with_stash_id)}). The in-flow "
            "attachment reconstruction did not run or did not link the posts."
        )

        # --- Stamping proof through the real adjudication path. ---
        assert any("imageUpdate" in c.get("query", "") for c in calls), (
            "no imageUpdate fired — the cold-cache flow swept files but stamped "
            "no owned image, so reconstruction did not surface the media index"
        )
