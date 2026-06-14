"""Mention -> performer linking in gallery composition (audit gap coverage).

``_setup_gallery_performers`` links the main creator plus every mentioned
account (``PostMention``) as a gallery performer. The removed
``test_process_timeline_account_mentions`` was never re-covered; this fills that
gap. The monitoring daemon reaches this path via ``_compose_and_flush`` ->
``_compose_gallery_for_item`` -> ``_get_or_create_gallery``, so the linking it
verifies applies to the incremental flow too (mentions hydrated by
``_reconstruct_mention_lists`` on a cold cache).
"""

import httpx
import pytest
import respx

from metadata import PostMention
from tests.fixtures.metadata.metadata_factories import AccountFactory, PostFactory
from tests.fixtures.stash.stash_api_fixtures import dump_graphql_calls
from tests.fixtures.stash.stash_graphql_fixtures import create_graphql_response
from tests.fixtures.stash.stash_type_factories import GalleryFactory, PerformerFactory
from tests.fixtures.utils.test_isolation import snowflake_id


class TestGalleryMentionPerformers:
    """_setup_gallery_performers links mentioned accounts as gallery performers."""

    @pytest.mark.asyncio
    async def test_mentioned_account_linked_as_gallery_performer(
        self, respx_stash_processor
    ):
        """A post's PostMention resolves to a cached performer and joins the gallery.

        Discriminating: the gallery ends with BOTH the main creator and the
        mentioned performer. Zero GraphQL — the mentioned performer is seeded in
        the store cache so ``_find_existing_performer`` resolves it by name, and
        ``add_performer`` stays in memory (``galleries`` seeded empty so the
        inverse sync does not fetch).
        """
        acct = AccountFactory.build(id=snowflake_id(), username="creator")
        post = PostFactory.build(id=snowflake_id(), accountId=acct.id)
        # Set after build — PostFactory's model_validator drops non-dict mentions.
        post.mentions = [PostMention(id=1, postId=post.id, handle="alice")]

        main = PerformerFactory(id="123", name="creator", galleries=[])
        respx_stash_processor.store.add(
            PerformerFactory(id="456", name="alice", galleries=[])
        )
        gallery = GalleryFactory()

        await respx_stash_processor._setup_gallery_performers(gallery, post, main)

        assert {p.name for p in gallery.performers} == {"creator", "alice"}

    @pytest.mark.asyncio
    async def test_unresolvable_mention_is_skipped_main_still_linked(
        self, respx_stash_processor
    ):
        """A mention with no matching performer is dropped; the creator remains.

        ``_find_existing_performer`` returns None for an unknown handle (cache
        miss + a routed-empty findPerformers would too); the gallery keeps only
        the main performer rather than appending a None.
        """
        post = PostFactory.build(id=snowflake_id(), accountId=snowflake_id())
        post.mentions = [PostMention(id=1, postId=post.id, handle="ghost")]
        main = PerformerFactory(id="123", name="creator", galleries=[])
        gallery = GalleryFactory()

        # Cache miss -> _find_existing_performer issues findPerformers; route empty.
        route = respx.post("http://localhost:9999/graphql").mock(
            side_effect=[
                httpx.Response(200, json=create_graphql_response("findPerformers", []))
            ]
        )
        try:
            await respx_stash_processor._setup_gallery_performers(gallery, post, main)
        finally:
            dump_graphql_calls(route.calls, "unresolvable_mention_skipped")

        assert {p.name for p in gallery.performers} == {"creator"}
