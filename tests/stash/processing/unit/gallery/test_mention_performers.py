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
from stash_graphql_client import is_set

from metadata import PostMention
from stash.processing import StashProcessing
from tests.fixtures.metadata.metadata_factories import PostFactory
from tests.fixtures.stash.stash_api_fixtures import dump_graphql_calls
from tests.fixtures.stash.stash_graphql_fixtures import (
    create_find_performers_result,
    create_graphql_response,
)
from tests.fixtures.stash.stash_type_factories import GalleryFactory, PerformerFactory
from tests.fixtures.utils.test_isolation import snowflake_id


class TestGalleryMentionPerformers:
    """_setup_gallery_performers links mentioned accounts as gallery performers."""

    @pytest.mark.parametrize(
        ("handle", "seed_mentioned", "expected_names", "expected_calls"),
        [
            ("alice", True, {"creator", "alice"}, 0),
            ("ghost", False, {"creator"}, 1),
        ],
        ids=["mentioned_linked", "unresolvable_skipped"],
    )
    @pytest.mark.asyncio
    async def test_mention_performer_resolution(
        self,
        respx_stash_processor: StashProcessing,
        request: pytest.FixtureRequest,
        handle: str,
        seed_mentioned: bool,
        expected_names: set[str],
        expected_calls: int,
    ) -> None:
        """A post's PostMention resolves (or not) against the performer store.

        mentioned_linked: the mentioned performer is seeded in the store cache
        so ``_find_existing_performer`` resolves it by name with ZERO GraphQL
        (``galleries`` seeded empty so ``add_performer``'s inverse sync does
        not fetch); the gallery ends with BOTH the main creator and the
        mentioned performer.

        unresolvable_skipped: a mention with no matching performer is dropped —
        cache miss makes ``_find_existing_performer`` issue findPerformers
        (routed empty, exactly one call); the gallery keeps only the main
        performer rather than appending a None.
        """
        post = PostFactory.build(id=snowflake_id(), accountId=snowflake_id())
        # Set after build — PostFactory's model_validator drops non-dict mentions.
        post.mentions = [PostMention(id=1, postId=post.id, handle=handle)]
        main = PerformerFactory.build(id="123", name="creator", galleries=[])
        if seed_mentioned:
            respx_stash_processor.store.add(
                PerformerFactory.build(id="456", name=handle, galleries=[])
            )
        gallery = GalleryFactory.build()

        side_effect = (
            []
            if seed_mentioned
            else [
                httpx.Response(
                    200,
                    json=create_graphql_response(
                        "findPerformers", create_find_performers_result()
                    ),
                )
            ]
        )
        route = respx.post("http://localhost:9999/graphql").mock(
            side_effect=side_effect
        )
        try:
            await respx_stash_processor._setup_gallery_performers(gallery, post, main)
        finally:
            dump_graphql_calls(route.calls, request.node.name)

        assert len(route.calls) == expected_calls
        assert is_set(gallery.performers)
        assert {p.name for p in gallery.performers} == expected_names
