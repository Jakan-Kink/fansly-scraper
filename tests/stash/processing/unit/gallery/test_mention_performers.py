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
    create_performer_dict,
)
from tests.fixtures.stash.stash_type_factories import GalleryFactory, PerformerFactory
from tests.fixtures.utils.test_isolation import snowflake_id


class TestGalleryMentionPerformers:
    """_setup_gallery_performers links mentioned accounts as gallery performers."""

    @pytest.mark.parametrize(
        ("handle", "in_stash", "expected_names", "expected_calls"),
        [
            ("alice", True, {"creator", "Alice Curated"}, 2),
            ("ghost", False, {"creator"}, 3),
        ],
        ids=["mentioned_linked", "unresolvable_skipped"],
    )
    @pytest.mark.asyncio
    async def test_mention_performer_resolution(
        self,
        respx_stash_processor: StashProcessing,
        request: pytest.FixtureRequest,
        *,
        handle: str,
        in_stash: bool,
        expected_names: set[str],
        expected_calls: int,
    ) -> None:
        """A post's PostMention resolves (or not) against Stash's performers.

        mentioned_linked: a performer carrying the handle's Fansly URL is
        returned by the URL search (count + fetch; ``galleries`` returned empty
        so ``add_performer``'s inverse sync does not fetch); the gallery ends
        with BOTH the main creator and the mentioned performer.

        unresolvable_skipped: the URL, alias, and name searches all come back
        empty (one call each); the gallery keeps only the main performer rather
        than appending a None.
        """
        post = PostFactory.build(id=snowflake_id(), accountId=snowflake_id())
        # Set after build — PostFactory's model_validator drops non-dict mentions.
        post.mentions = [PostMention(id=1, postId=post.id, handle=handle)]
        main = PerformerFactory.build(id="123", name="creator", galleries=[])
        gallery = GalleryFactory.build()

        none_found = create_graphql_response(
            "findPerformers", create_find_performers_result()
        )
        by_url = create_graphql_response(
            "findPerformers",
            create_find_performers_result(
                count=1,
                performers=[
                    create_performer_dict(
                        id="456",
                        name="Alice Curated",
                        urls=[f"https://fansly.com/{handle}/posts"],
                        galleries=[],
                    )
                ],
            ),
        )
        payloads = [by_url, by_url] if in_stash else [none_found] * 3
        route = respx.post("http://localhost:9999/graphql").mock(
            side_effect=[httpx.Response(200, json=payload) for payload in payloads]
        )
        try:
            await respx_stash_processor._setup_gallery_performers(gallery, post, main)
        finally:
            dump_graphql_calls(route.calls, request.node.name)

        assert len(route.calls) == expected_calls
        assert is_set(gallery.performers)
        assert {p.name for p in gallery.performers} == expected_names
