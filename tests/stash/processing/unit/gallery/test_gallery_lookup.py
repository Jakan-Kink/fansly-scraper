"""Tests for gallery lookup functionality using respx at HTTP boundary.

These tests mock at the HTTP boundary using respx, allowing real code execution
through the entire processing pipeline. We verify that data flows correctly from
database queries to GraphQL API calls.

Each lookup family (_get_gallery_by_stash_id / _title / _code / _url) is
parametrized over its found / not-found / wrong-* variants. Divergent
call-counts are encoded as an explicit ``expected_calls`` column — notably the
url-success branch, which carries an EXTRA galleryUpdate call from the save.
"""

from datetime import UTC, datetime

import httpx
import pytest
import respx
from stash_graphql_client import is_set

from stash.processing import StashProcessing
from tests.fixtures.metadata import PostFactory
from tests.fixtures.stash import (
    StudioFactory,
    create_find_galleries_result,
    create_gallery_dict,
    create_graphql_response,
)
from tests.fixtures.stash.stash_api_fixtures import (
    assert_op,
    assert_op_with_vars,
    dump_graphql_calls,
)
from tests.fixtures.utils.test_isolation import snowflake_id


class TestGalleryLookup:
    """Test gallery lookup methods in GalleryProcessingMixin."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("stash_id", "find_gallery_result", "expect_found", "expected_calls"),
        [
            # findGallery returns the gallery → found, 1 call.
            pytest.param(123, "gallery", True, 1, id="found"),
            # Post has no stash_id → early return, NO GraphQL call.
            pytest.param(None, None, False, 0, id="no-stash-id"),
            # findGallery returns null → not found, 1 call.
            pytest.param(999, "null", False, 1, id="not-found"),
        ],
    )
    async def test_get_gallery_by_stash_id(
        self,
        respx_stash_processor: StashProcessing,
        request: pytest.FixtureRequest,
        stash_id: int | None,
        find_gallery_result: str | None,
        expect_found: bool,
        expected_calls: int,
    ) -> None:
        """Test _get_gallery_by_stash_id across found/no-id/not-found variants."""
        post_id = snowflake_id()
        acct_id = snowflake_id()

        post_obj = PostFactory.build(id=post_id, accountId=acct_id, stash_id=stash_id)

        # An empty side_effect list catches any unexpected call (StopIteration).
        side_effect: list[httpx.Response] = []
        if find_gallery_result == "gallery":
            side_effect = [
                httpx.Response(
                    200,
                    json={
                        "data": {
                            "findGallery": {
                                "id": str(stash_id),
                                "title": "Test Gallery",
                                "code": str(post_id),
                            }
                        }
                    },
                )
            ]
        elif find_gallery_result == "null":
            side_effect = [httpx.Response(200, json={"data": {"findGallery": None}})]

        graphql_route = respx.post("http://localhost:9999/graphql").mock(
            side_effect=side_effect
        )

        try:
            gallery = await respx_stash_processor._get_gallery_by_stash_id(post_obj)
        finally:
            dump_graphql_calls(graphql_route.calls, request.node.name)

        assert len(graphql_route.calls) == expected_calls

        if expect_found:
            assert gallery is not None
            assert gallery.id == str(stash_id)
            assert gallery.title == "Test Gallery"
        else:
            assert gallery is None

        if expected_calls:
            # Request contains findGallery with the post's stash_id.
            assert_op_with_vars(graphql_route.calls[0], "findGallery", id=str(stash_id))

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        (
            "gallery_id",
            "resp_date",
            "resp_studio",
            "studio_arg_id",
            "expect_found",
            "expected_calls",
        ),
        [
            # Matching title, date, and studio → found (count check + fetch).
            pytest.param(
                "200",
                "2024-04-01",
                ("123", "Test Studio"),
                "123",
                True,
                2,
                id="found",
            ),
            # findGalleries returns empty → not found after the count check.
            pytest.param(None, None, None, "124", False, 1, id="not-found"),
            # Right title/studio but wrong date → rejected.
            pytest.param(
                "201",
                "2024-04-02",
                ("125", "Test Studio"),
                "125",
                False,
                2,
                id="wrong-date",
            ),
            # Right title/date but wrong studio → rejected.
            pytest.param(
                "202",
                "2024-04-01",
                ("9001", "Wrong Studio"),
                "126",
                False,
                2,
                id="wrong-studio",
            ),
            # No studio parameter → any studio matches.
            pytest.param(
                "203",
                "2024-04-01",
                ("9002", "Any Studio"),
                None,
                True,
                2,
                id="no-studio-matches-any",
            ),
        ],
    )
    async def test_get_gallery_by_title(
        self,
        respx_stash_processor: StashProcessing,
        request: pytest.FixtureRequest,
        gallery_id: str | None,
        resp_date: str | None,
        resp_studio: tuple[str, str] | None,
        studio_arg_id: str | None,
        expect_found: bool,
        expected_calls: int,
    ) -> None:
        """Test _get_gallery_by_title match/reject variants (date/studio checks)."""
        post_id = snowflake_id()
        acct_id = snowflake_id()

        # Build Post with specific date (2024-04-01 is the matching date).
        post_obj = PostFactory.build(
            id=post_id,
            accountId=acct_id,
            createdAt=datetime(2024, 4, 1, 12, 0, 0, tzinfo=UTC),
        )

        studio = (
            StudioFactory.build(id=studio_arg_id, name="Test Studio")
            if studio_arg_id is not None
            else None
        )

        if gallery_id is None:
            # findGalleries returns empty on the count check.
            side_effect = [
                httpx.Response(
                    200,
                    json={"data": {"findGalleries": {"galleries": [], "count": 0}}},
                )
            ]
        else:
            assert resp_studio is not None

            # find() needs 2 responses: count check + fetch results.
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
                                    title="Test Title",
                                    code=None,
                                    date=resp_date,
                                    studio={
                                        "__typename": "Studio",
                                        "id": resp_studio[0],
                                        "name": resp_studio[1],
                                    },
                                )
                            ],
                        ),
                    ),
                )

            side_effect = [_find_response(), _find_response()]

        graphql_route = respx.post("http://localhost:9999/graphql").mock(
            side_effect=side_effect
        )

        try:
            gallery = await respx_stash_processor._get_gallery_by_title(
                post_obj, "Test Title", studio
            )
        finally:
            dump_graphql_calls(graphql_route.calls, request.node.name)

        assert len(graphql_route.calls) == expected_calls

        if expect_found:
            assert gallery is not None
            assert gallery.id == gallery_id
            assert gallery.title == "Test Title"
            assert post_obj.stash_id == int(gallery_id)  # updated as int
        else:
            assert gallery is None

        # First request is findGalleries with the title filter.
        assert_op_with_vars(
            graphql_route.calls[0],
            "findGalleries",
            gallery_filter__title__value="Test Title",
            gallery_filter__title__modifier="EQUALS",
        )

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("gallery_id", "resp_code", "expect_found"),
        [
            # Gallery code matches the post id → found.
            pytest.param("300", "match", True, id="found"),
            # findGalleries returns empty → not found.
            pytest.param(None, None, False, id="not-found"),
            # Gallery returned but with a different code → rejected.
            pytest.param("301", "54321", False, id="wrong-code"),
        ],
    )
    async def test_get_gallery_by_code(
        self,
        respx_stash_processor: StashProcessing,
        request: pytest.FixtureRequest,
        gallery_id: str | None,
        resp_code: str | None,
        expect_found: bool,
    ) -> None:
        """Test _get_gallery_by_code found/not-found/wrong-code variants."""
        post_id = snowflake_id()
        acct_id = snowflake_id()

        post_obj = PostFactory.build(id=post_id, accountId=acct_id)

        if gallery_id is None:
            galleries_result = {"galleries": [], "count": 0}
        else:
            code = str(post_id) if resp_code == "match" else resp_code
            galleries_result = create_find_galleries_result(
                count=1,
                galleries=[create_gallery_dict(id=gallery_id, title=None, code=code)],
            )

        # find_one needs 1 response for every variant.
        graphql_route = respx.post("http://localhost:9999/graphql").mock(
            side_effect=[
                httpx.Response(
                    200,
                    json=create_graphql_response("findGalleries", galleries_result),
                )
            ]
        )

        try:
            gallery = await respx_stash_processor._get_gallery_by_code(post_obj)
        finally:
            dump_graphql_calls(graphql_route.calls, request.node.name)

        assert len(graphql_route.calls) == 1

        if expect_found:
            assert gallery is not None
            assert gallery.id == gallery_id
            assert gallery.code == str(post_id)
            assert post_obj.stash_id == int(gallery_id)  # updated as int
        else:
            assert gallery is None

        # Request contains findGalleries with the code filter.
        assert_op_with_vars(
            graphql_route.calls[0],
            "findGalleries",
            gallery_filter__code__value=str(post_id),
            gallery_filter__code__modifier="EQUALS",
        )

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("gallery_id", "url_kind", "expect_found", "expected_calls"),
        [
            # URL matches → found; save fires an EXTRA galleryUpdate call
            # (count check + fetch + save = 3).
            pytest.param("400", "match", True, 3, id="found-extra-save-call"),
            # findGalleries returns empty on the count check → 1 call.
            pytest.param(None, None, False, 1, id="not-found"),
            # Gallery returned but URL doesn't match → rejected after fetch (2).
            pytest.param("401", "wrong", False, 2, id="wrong-url"),
        ],
    )
    async def test_get_gallery_by_url(
        self,
        respx_stash_processor: StashProcessing,
        request: pytest.FixtureRequest,
        gallery_id: str | None,
        url_kind: str | None,
        expect_found: bool,
        expected_calls: int,
    ) -> None:
        """Test _get_gallery_by_url found/not-found/wrong-url variants."""
        post_id = snowflake_id()
        acct_id = snowflake_id()

        post_obj = PostFactory.build(id=post_id, accountId=acct_id)

        test_url = f"https://test.com/post/{post_id}"

        if gallery_id is None:
            side_effect = [
                httpx.Response(
                    200,
                    json={"data": {"findGalleries": {"galleries": [], "count": 0}}},
                )
            ]
        else:
            resp_urls = (
                [test_url] if url_kind == "match" else ["https://test.com/post/54321"]
            )

            # find() needs 2 responses: count check + fetch results.
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
                                    title=None,
                                    code="" if url_kind == "match" else None,
                                    urls=resp_urls,
                                )
                            ],
                        ),
                    ),
                )

            side_effect = [_find_response(), _find_response()]
            if url_kind == "match":
                # Call 2: galleryUpdate (from gallery.save() setting the code).
                side_effect.append(
                    httpx.Response(
                        200,
                        json=create_graphql_response(
                            "galleryUpdate",
                            create_gallery_dict(
                                id=gallery_id,
                                title=None,
                                code=str(post_id),
                                urls=resp_urls,
                            ),
                        ),
                    )
                )

        graphql_route = respx.post("http://localhost:9999/graphql").mock(
            side_effect=side_effect
        )

        try:
            gallery = await respx_stash_processor._get_gallery_by_url(
                post_obj, test_url
            )
        finally:
            dump_graphql_calls(graphql_route.calls, request.node.name)

        assert len(graphql_route.calls) == expected_calls

        if expect_found:
            assert gallery is not None
            assert gallery.id == gallery_id
            assert is_set(gallery.urls)
            assert test_url in gallery.urls
            assert post_obj.stash_id == int(gallery_id)  # updated as int
            # The EXTRA third call is the galleryUpdate from the save.
            assert_op(graphql_route.calls[2], "galleryUpdate")
        else:
            assert gallery is None

        # First request is the findGalleries count check with the url filter.
        assert_op_with_vars(
            graphql_route.calls[0],
            "findGalleries",
            gallery_filter__url__value=test_url,
            gallery_filter__url__modifier="INCLUDES",
        )
