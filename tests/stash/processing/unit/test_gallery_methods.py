"""Unit tests for gallery-related methods.

These tests mock at the HTTP boundary using respx, allowing real code execution
through the entire processing pipeline.

Lookup found/not-found pairs are parametrized with divergent call-counts encoded
as an explicit ``expected_calls`` column (the url item-update branch carries an
EXTRA galleryUpdate call from the save).

Title generation (``_generate_title_from_content``) is deep-tested in
tests/stash/processing/unit/test_base.py::TestStashProcessingBase::
test_generate_title_from_content (short/long/no-content/position/no-date); the
shallow single-assert duplicates that used to live here were deleted.
"""

from datetime import UTC, datetime

import httpx
import pytest
import respx

from stash.processing import StashProcessing
from tests.fixtures.metadata import HashtagFactory, PostFactory
from tests.fixtures.stash import (
    create_find_galleries_result,
    create_gallery_dict,
    create_graphql_response,
)
from tests.fixtures.stash.stash_api_fixtures import (
    assert_op_with_vars,
    dump_graphql_calls,
)
from tests.fixtures.utils.test_isolation import snowflake_id


class TestGalleryLookupMethods:
    """Test gallery lookup methods of StashProcessing using respx.

    The _get_gallery_by_stash_id / _title / _code lookup families that used to
    live here were exact-duplicate (or strict-subset) rows of the parametrized
    tables in tests/stash/processing/unit/gallery/test_gallery_lookup.py
    (which adds wrong-date / wrong-studio / no-studio / wrong-code rows) and
    were deleted. Only _get_gallery_by_url remains: its
    ``code-matches-no-save`` row (no galleryUpdate fires) and its galleryUpdate
    variable assertions (``input__id``/``input__code``) are NOT covered by the
    lookup file's url table, which in turn owns the not-found / wrong-url rows.
    """

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("gallery_id", "resp_code_kind", "expected_calls", "expects_update"),
        [
            # Code already matches post.id → library skips save() (2 find calls).
            pytest.param("789", "match", 2, False, id="code-matches-no-save"),
            # Code differs → item stash_id + gallery code updated; the save
            # fires an EXTRA galleryUpdate call (2 find + 1 save = 3).
            pytest.param("999", "old", 3, True, id="code-differs-updates-and-saves"),
        ],
    )
    async def test_get_gallery_by_url(
        self,
        respx_stash_processor: StashProcessing,
        request: pytest.FixtureRequest,
        gallery_id: str,
        resp_code_kind: str,
        expected_calls: int,
        expects_update: bool,
    ) -> None:
        """Test _get_gallery_by_url found variants (no-save vs item-update+save)."""
        post_id = snowflake_id()
        acct_id = snowflake_id()

        post = PostFactory.build(
            id=post_id,
            accountId=acct_id,
            content="Test content",
            createdAt=datetime(2024, 4, 1, 12, 0, 0, tzinfo=UTC),
            stash_id=None,  # No stash_id initially
        )

        url = f"https://example.com/gallery/{gallery_id}"
        resp_code = str(post_id) if resp_code_kind == "match" else "old_code"

        # store.find() makes 2 queries (count check + fetch).
        def _find_response() -> httpx.Response:
            return httpx.Response(
                200,
                json=create_graphql_response(
                    "findGalleries",
                    create_find_galleries_result(
                        count=1,
                        galleries=[
                            create_gallery_dict(
                                id=gallery_id,
                                title="URL Gallery",
                                code=resp_code,
                                urls=[url],
                            )
                        ],
                    ),
                ),
            )

        side_effect = [_find_response(), _find_response()]
        if expects_update:
            # Call 2: galleryUpdate from save() (code updated to post.id).
            side_effect.append(
                httpx.Response(
                    200,
                    json=create_graphql_response(
                        "galleryUpdate",
                        create_gallery_dict(
                            id=gallery_id,
                            title="URL Gallery",
                            code=str(post_id),
                            urls=[url],
                        ),
                    ),
                )
            )

        graphql_route = respx.post("http://localhost:9999/graphql").mock(
            side_effect=side_effect
        )

        try:
            result = await respx_stash_processor._get_gallery_by_url(post, url)
        finally:
            dump_graphql_calls(graphql_route.calls, request.node.name)

        # Verify result
        assert result is not None
        assert result.id == gallery_id
        # Item stash_id should be updated
        assert post.stash_id == int(gallery_id)
        # Gallery code matches post.id (updated when it differed)
        assert result.code == str(post_id)

        assert len(graphql_route.calls) == expected_calls

        # Verify first call (findGalleries count check)
        assert_op_with_vars(
            graphql_route.calls[0],
            "findGalleries",
            gallery_filter__url__value=url,
        )

        if expects_update:
            # Verify third call (galleryUpdate)
            assert_op_with_vars(
                graphql_route.calls[2],
                "galleryUpdate",
                input__id=gallery_id,
                input__code=str(post_id),
            )


class TestGalleryCreation:
    """Test gallery creation methods using respx."""

    @pytest.mark.asyncio
    async def test_create_new_gallery(
        self,
        respx_stash_processor: StashProcessing,
    ) -> None:
        """Test _create_new_gallery creates gallery with correct attributes."""
        post_id = snowflake_id()
        acct_id = snowflake_id()

        # Build post (in-memory only)
        post = PostFactory.build(
            id=post_id,
            accountId=acct_id,
            content="Test post content",
            createdAt=datetime(2024, 4, 1, 12, 0, 0, tzinfo=UTC),
        )

        # Note: _create_new_gallery doesn't make HTTP calls - it builds a Gallery object
        title = "New Test Gallery"
        result = await respx_stash_processor._create_new_gallery(post, title)

        # Verify result
        assert result is not None
        assert result.title == title
        assert result.code == str(post.id)
        assert result.date == "2024-04-01"
        assert result.details == post.content
        assert result.organized is True


class TestHashtagProcessing:
    """Test hashtag to tag processing using respx."""

    @pytest.mark.asyncio
    async def test_process_hashtags_to_tags_existing_tags(
        self,
        respx_stash_processor: StashProcessing,
    ) -> None:
        """Test _process_hashtags_to_tags with existing tags."""
        # Create real hashtag objects
        hashtag1 = HashtagFactory.build(value="test1")
        hashtag2 = HashtagFactory.build(value="test2")
        hashtags = [hashtag1, hashtag2]

        # Set up respx - both tags exist
        graphql_route = respx.post("http://localhost:9999/graphql").mock(
            side_effect=[
                # Call 0: findTags for "test1" -> found
                httpx.Response(
                    200,
                    json={
                        "data": {
                            "findTags": {
                                "tags": [{"id": "100", "name": "test1"}],
                                "count": 1,
                            }
                        }
                    },
                ),
                # Call 1: findTags for "test2" -> found
                httpx.Response(
                    200,
                    json={
                        "data": {
                            "findTags": {
                                "tags": [{"id": "101", "name": "test2"}],
                                "count": 1,
                            }
                        }
                    },
                ),
            ]
        )

        # Call method
        try:
            result = await respx_stash_processor._process_hashtags_to_tags(hashtags)
        finally:
            dump_graphql_calls(
                graphql_route.calls, "process_hashtags_to_tags_existing_tags"
            )

        # Verify result
        assert len(result) == 2
        assert result[0].name == "test1"
        assert result[1].name == "test2"

        # Verify both lookups were made
        assert len(graphql_route.calls) == 2

        # Verify requests
        assert_op_with_vars(
            graphql_route.calls[0],
            "findTags",
            tag_filter__name__value="test1",
        )
        assert_op_with_vars(
            graphql_route.calls[1],
            "findTags",
            tag_filter__name__value="test2",
        )

    @pytest.mark.asyncio
    async def test_process_hashtags_to_tags_create_new(
        self,
        respx_stash_processor: StashProcessing,
    ) -> None:
        """Test _process_hashtags_to_tags creates new tag when not found."""
        # Create real hashtag object
        hashtag = HashtagFactory.build(value="newtag")
        hashtags = [hashtag]

        # Set up respx - _get_or_create_tag does: find by name, find by alias, create
        graphql_route = respx.post("http://localhost:9999/graphql").mock(
            side_effect=[
                # findTags by name (not found)
                httpx.Response(
                    200,
                    json={"data": {"findTags": {"tags": [], "count": 0}}},
                ),
                # findTags by alias (not found)
                httpx.Response(
                    200,
                    json={"data": {"findTags": {"tags": [], "count": 0}}},
                ),
                # tagCreate
                httpx.Response(
                    200,
                    json={"data": {"tagCreate": {"id": "123", "name": "newtag"}}},
                ),
            ]
        )

        # Call method
        try:
            result = await respx_stash_processor._process_hashtags_to_tags(hashtags)
        finally:
            dump_graphql_calls(
                graphql_route.calls, "process_hashtags_to_tags_create_new"
            )

        # Verify result
        assert len(result) == 1
        assert result[0].name == "newtag"
        # Note: Don't assert on ID - library generates UUIDs for new tags
        assert hasattr(result[0], "id")

        # _get_or_create_tag executes a fixed 3-step sequence for a new (uncached) tag:
        # findTags-by-name → findTags-by-alias → tagCreate.
        assert len(graphql_route.calls) == 3
