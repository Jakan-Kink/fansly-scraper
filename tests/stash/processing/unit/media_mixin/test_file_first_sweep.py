"""Tests for _build_media_index — file-first scene-split helper.

Verifies that the index maps each downloaded file's leaf basename to
(Media, owning Post) and that variants are included alongside their parent.
"""

import io
from pathlib import PurePath

import pytest
import respx
from loguru import logger as loguru_logger

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
from tests.fixtures.utils.test_isolation import snowflake_id


_GRAPHQL_URL = "http://localhost:9999/graphql"


class TestSweepCreatorFiles:
    """Tests for StashProcessingBase._sweep_creator_files scoping."""

    @pytest.mark.asyncio
    async def test_sweep_anchors_root_with_separator(self, respx_stash_processor):
        """The real sweep queries Stash scoped by ``root + '/'``.

        A bare ``path__contains=root`` substring would also pull a sibling
        creator ``/dl/annabelle/...`` into ``/dl/anna``'s sweep. The real
        ``find_iter`` runs and emits a real ``findFiles`` query; we route an empty
        result and assert the separator-anchored root on the captured REQUEST
        (only the HTTP edge is observed — the store is not patched).
        """
        processor = respx_stash_processor
        root = get_stash_path(processor.state.base_path, processor.config).rstrip("/")

        # One call: the sweep's single findFiles page (empty → stops).
        route = respx.post(_GRAPHQL_URL).mock(side_effect=[find_files_response()])
        try:
            files = [f async for f in processor._sweep_creator_files()]
        finally:
            dump_graphql_calls(route.calls, "sweep_anchors_root_with_separator")

        assert files == []
        assert route.calls, "sweep issued no findFiles GraphQL request"
        request_text = route.calls[0].request.content.decode()
        # The real query carries the separator-anchored root.
        assert f"{root}/" in request_text


class TestBuildMediaIndex:
    """Tests for StashProcessingBase._build_media_index."""

    @pytest.mark.asyncio
    async def test_index_keys_on_local_filename_leaf(
        self, entity_store, respx_stash_processor
    ):
        """Index maps the basename of local_filename to (Media, owning Post)."""
        acct_id = snowflake_id()
        account = AccountFactory.build(id=acct_id, username="test_user")
        await entity_store.save(account)

        media = MediaFactory.build(
            id=snowflake_id(),
            accountId=acct_id,
            mimetype="video/mp4",
            type=2,
            is_downloaded=True,
            local_filename="/dl/test_user/2026-01-01_at_00-00_UTC_id_42.mp4",
        )
        await entity_store.save(media)

        account_media = AccountMediaFactory.build(
            id=snowflake_id(),
            accountId=acct_id,
            mediaId=media.id,
        )
        await entity_store.save(account_media)

        post = PostFactory.build(id=snowflake_id(), accountId=acct_id)
        attachment = AttachmentFactory.build(
            postId=post.id,
            contentId=account_media.id,
            contentType=ContentType.ACCOUNT_MEDIA,
            pos=0,
        )
        # Set after build — PostFactory's model_validator drops non-dict attachments
        post.attachments = [attachment]

        index = await respx_stash_processor._build_media_index([post])

        leaf = PurePath(media.local_filename).name
        assert leaf in index
        assert index[leaf] == (media, [post])

    @pytest.mark.asyncio
    async def test_index_includes_variants(self, entity_store, respx_stash_processor):
        """Variants get their own entry keyed on the variant's local_filename leaf."""
        acct_id = snowflake_id()
        account = AccountFactory.build(id=acct_id, username="test_user")
        await entity_store.save(account)

        variant = MediaFactory.build(
            id=snowflake_id(),
            accountId=acct_id,
            mimetype="video/mp4",
            type=2,
            is_downloaded=True,
            local_filename="/dl/test_user/variant_id_43.mp4",
        )
        await entity_store.save(variant)

        media = MediaFactory.build(
            id=snowflake_id(),
            accountId=acct_id,
            mimetype="video/mp4",
            type=2,
            is_downloaded=True,
            local_filename="/dl/test_user/orig_id_42.mp4",
        )
        await entity_store.save(media)
        # Set variants after save to avoid habtm FK issues during save
        media.variants = [variant]

        account_media = AccountMediaFactory.build(
            id=snowflake_id(),
            accountId=acct_id,
            mediaId=media.id,
        )
        await entity_store.save(account_media)

        post = PostFactory.build(id=snowflake_id(), accountId=acct_id)
        attachment = AttachmentFactory.build(
            postId=post.id,
            contentId=account_media.id,
            contentType=ContentType.ACCOUNT_MEDIA,
            pos=0,
        )
        post.attachments = [attachment]

        index = await respx_stash_processor._build_media_index([post])

        variant_leaf = PurePath(variant.local_filename).name
        assert variant_leaf in index
        assert index[variant_leaf][0] is variant

    @pytest.mark.asyncio
    async def test_shared_media_carries_all_owners_earliest_first(
        self, entity_store, respx_stash_processor
    ):
        """A Media shared across items lists every owning item, earliest id first.

        Two posts attach the SAME account_media (one downloaded Media). The old
        last-writer-wins index dropped the earlier post — only one gallery would
        receive the media's adjudicated entity. The index must carry BOTH post
        ids, ordered by id (Snowflake ~ creation time) so the earliest is the
        canonical owner for the entity's own metadata.
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
            local_filename="/dl/test_user/shared_id_99.mp4",
        )
        await entity_store.save(media)

        account_media = AccountMediaFactory.build(
            id=snowflake_id(), accountId=acct_id, mediaId=media.id
        )
        await entity_store.save(account_media)

        # Two posts (distinct ids) BOTH attaching the same account_media.
        older_id, newer_id = sorted([snowflake_id(), snowflake_id()])
        older = PostFactory.build(id=older_id, accountId=acct_id)
        newer = PostFactory.build(id=newer_id, accountId=acct_id)
        for post in (older, newer):
            post.attachments = [
                AttachmentFactory.build(
                    postId=post.id,
                    contentId=account_media.id,
                    contentType=ContentType.ACCOUNT_MEDIA,
                    pos=0,
                )
            ]

        # Pass newer-first so the assertion proves ordering is by id, not
        # insertion order.
        index = await respx_stash_processor._build_media_index([newer, older])

        leaf = PurePath(media.local_filename).name
        assert leaf in index
        got_media, owners = index[leaf]
        assert got_media is media
        # Both owners present, earliest (smallest id) first.
        assert [o.id for o in owners] == [older_id, newer_id]

    @pytest.mark.asyncio
    async def test_leaf_collision_keeps_first_media_and_warns(
        self, entity_store, respx_stash_processor
    ):
        """Two DIFFERENT media sharing a basename → keep first, drop second, warn.

        A genuine leaf collision (distinct media, same basename) is ambiguous;
        the index keeps the first writer and logs a warning rather than silently
        adjudicating the swept file against the wrong Media.
        """
        acct_id = snowflake_id()
        account = AccountFactory.build(id=acct_id, username="test_user")
        await entity_store.save(account)

        first = MediaFactory.build(
            id=snowflake_id(),
            accountId=acct_id,
            mimetype="video/mp4",
            is_downloaded=True,
            local_filename="/dl/a/clash.mp4",
        )
        second = MediaFactory.build(
            id=snowflake_id(),
            accountId=acct_id,
            mimetype="video/mp4",
            is_downloaded=True,
            local_filename="/dl/b/clash.mp4",  # same leaf, different file
        )
        posts = []
        for m in (first, second):
            await entity_store.save(m)
            am = AccountMediaFactory.build(
                id=snowflake_id(), accountId=acct_id, mediaId=m.id
            )
            await entity_store.save(am)
            post = PostFactory.build(id=snowflake_id(), accountId=acct_id)
            post.attachments = [
                AttachmentFactory.build(
                    postId=post.id,
                    contentId=am.id,
                    contentType=ContentType.ACCOUNT_MEDIA,
                    pos=0,
                )
            ]
            posts.append(post)

        sink = io.StringIO()
        sink_id = loguru_logger.add(sink, level="WARNING")
        try:
            index = await respx_stash_processor._build_media_index(posts)
        finally:
            loguru_logger.remove(sink_id)

        got_media, owners = index["clash.mp4"]
        # First writer wins; the second (different) media is dropped.
        assert got_media is first
        assert [o.id for o in owners] == [posts[0].id]
        # The collision was surfaced, naming both media ids.
        output = sink.getvalue()
        assert "leaf collision" in output
        assert str(second.id) in output

    @pytest.mark.asyncio
    async def test_leaf_collision_same_download_target_unions_owners(
        self, entity_store, respx_stash_processor
    ):
        """Distinct media resolving to the SAME downloaded file union owners.

        When two media share a download target (same ``download_id``) they
        render to the same leaf — they are one physical file. Dropping all but
        the first would strip the dropped owners' gallery joins. The index must
        keep the first media canonical but carry BOTH owning posts, and must NOT
        warn (this is same-file dedup, not an ambiguous basename clash).
        """
        acct_id = snowflake_id()
        account = AccountFactory.build(id=acct_id, username="test_user")
        await entity_store.save(account)

        target_id = snowflake_id()  # shared download-target (variant) id
        leaf_name = f"2026-01-01_at_00-00_UTC_id_{target_id}.mp4"

        media_objs = []
        posts = []
        for _ in range(2):
            m = MediaFactory.build(
                id=snowflake_id(),  # distinct media ids
                accountId=acct_id,
                mimetype="video/mp4",
                is_downloaded=True,
                download_id=target_id,  # same target -> same file -> same leaf
                local_filename=f"/dl/test_user/{leaf_name}",
            )
            await entity_store.save(m)
            media_objs.append(m)
            am = AccountMediaFactory.build(
                id=snowflake_id(), accountId=acct_id, mediaId=m.id
            )
            await entity_store.save(am)
            post = PostFactory.build(id=snowflake_id(), accountId=acct_id)
            post.attachments = [
                AttachmentFactory.build(
                    postId=post.id,
                    contentId=am.id,
                    contentType=ContentType.ACCOUNT_MEDIA,
                    pos=0,
                )
            ]
            posts.append(post)

        sink = io.StringIO()
        sink_id = loguru_logger.add(sink, level="WARNING")
        try:
            index = await respx_stash_processor._build_media_index(posts)
        finally:
            loguru_logger.remove(sink_id)

        got_media, owners = index[leaf_name]
        assert got_media is media_objs[0]  # first writer canonical
        # Both owning posts present (no dropped gallery joins).
        assert sorted(o.id for o in owners) == sorted(p.id for p in posts)
        # Same-file dedup is not an ambiguous collision — no warning.
        assert "leaf collision" not in sink.getvalue()
