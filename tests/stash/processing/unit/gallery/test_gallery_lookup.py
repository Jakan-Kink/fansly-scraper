"""Tests for gallery lookup functionality using respx at HTTP boundary.

These tests mock at the HTTP boundary using respx, allowing real code execution
through the entire processing pipeline. We verify that data flows correctly from
database queries to GraphQL API calls.
"""

import json
from datetime import UTC, datetime

import httpx
import pytest
import respx

from stash.processing import StashProcessing
from tests.fixtures import (
    PostFactory,
    StudioFactory,
    create_find_galleries_result,
    create_gallery_dict,
    create_graphql_response,
)
from tests.fixtures.utils.test_isolation import snowflake_id


class TestGalleryLookup:
    """Test gallery lookup methods in GalleryProcessingMixin."""

    @pytest.mark.asyncio
    async def test_get_gallery_by_stash_id_found(
        self, respx_stash_processor: StashProcessing
    ):
        """Test _get_gallery_by_stash_id when gallery is found."""
        post_id = snowflake_id()
        acct_id = snowflake_id()

        # Build Post with stash_id set (no DB persistence needed)
        post_obj = PostFactory.build(id=post_id, accountId=acct_id, stash_id=123)

        # Set up respx - findGallery returns the gallery
        graphql_route = respx.post("http://localhost:9999/graphql").mock(
            side_effect=[
                httpx.Response(
                    200,
                    json={
                        "data": {
                            "findGallery": {
                                "id": "123",
                                "title": "Test Gallery",
                                "code": str(post_id),
                            }
                        }
                    },
                )
            ]
        )

        gallery = await respx_stash_processor._get_gallery_by_stash_id(post_obj)

        # Verify gallery was found
        assert gallery is not None
        assert gallery.id == "123"
        assert gallery.title == "Test Gallery"

        # Verify exactly 1 call
        assert len(graphql_route.calls) == 1

        # Verify request contains findGallery with correct id
        req = json.loads(graphql_route.calls[0].request.content)
        assert "findGallery" in req["query"]
        assert req["variables"]["id"] == "123"

    @pytest.mark.asyncio
    async def test_get_gallery_by_stash_id_no_stash_id(
        self, respx_stash_processor: StashProcessing
    ):
        """Test _get_gallery_by_stash_id when post has no stash_id."""
        post_id = snowflake_id()
        acct_id = snowflake_id()

        # Build Post without stash_id
        post_obj = PostFactory.build(id=post_id, accountId=acct_id, stash_id=None)

        # Set up respx - expect NO calls for post without stash_id
        graphql_route = respx.post("http://localhost:9999/graphql").mock(
            side_effect=[]  # Empty list catches any unexpected call
        )

        gallery = await respx_stash_processor._get_gallery_by_stash_id(post_obj)

        # Verify no gallery returned
        assert gallery is None

        # Verify no GraphQL calls made
        assert len(graphql_route.calls) == 0

    @pytest.mark.asyncio
    async def test_get_gallery_by_stash_id_not_found(
        self, respx_stash_processor: StashProcessing
    ):
        """Test _get_gallery_by_stash_id when gallery not found in Stash."""
        post_id = snowflake_id()
        acct_id = snowflake_id()

        # Build Post with stash_id
        post_obj = PostFactory.build(id=post_id, accountId=acct_id, stash_id=999)

        # Set up respx - findGallery returns null
        graphql_route = respx.post("http://localhost:9999/graphql").mock(
            side_effect=[
                httpx.Response(
                    200,
                    json={"data": {"findGallery": None}},
                )
            ]
        )

        gallery = await respx_stash_processor._get_gallery_by_stash_id(post_obj)

        # Verify no gallery returned
        assert gallery is None

        # Verify call was made
        assert len(graphql_route.calls) == 1

    @pytest.mark.asyncio
    async def test_get_gallery_by_title_found(
        self, respx_stash_processor: StashProcessing
    ):
        """Test _get_gallery_by_title when matching gallery found."""
        post_id = snowflake_id()
        acct_id = snowflake_id()

        # Build Post with specific date
        post_obj = PostFactory.build(
            id=post_id,
            accountId=acct_id,
            createdAt=datetime(2024, 4, 1, 12, 0, 0, tzinfo=UTC),
        )

        # Create real studio using factory
        studio = StudioFactory.build(id="123", name="Test Studio")

        # Set up respx - findGalleries returns matching gallery (needs 2 responses for find())
        graphql_route = respx.post("http://localhost:9999/graphql").mock(
            side_effect=[
                # Call 0: Count check (per_page=1)
                httpx.Response(
                    200,
                    json=create_graphql_response(
                        "findGalleries",
                        create_find_galleries_result(
                            count=1,
                            galleries=[
                                create_gallery_dict(
                                    id="200",
                                    title="Test Title",
                                    code=None,
                                    date="2024-04-01",
                                    studio={
                                        "__typename": "Studio",
                                        "id": "123",
                                        "name": "Test Studio",
                                    },
                                )
                            ],
                        ),
                    ),
                ),
                # Call 1: Fetch results (per_page=1)
                httpx.Response(
                    200,
                    json=create_graphql_response(
                        "findGalleries",
                        create_find_galleries_result(
                            count=1,
                            galleries=[
                                create_gallery_dict(
                                    id="200",
                                    title="Test Title",
                                    code=None,
                                    date="2024-04-01",
                                    studio={
                                        "__typename": "Studio",
                                        "id": "123",
                                        "name": "Test Studio",
                                    },
                                )
                            ],
                        ),
                    ),
                ),
            ]
        )

        gallery = await respx_stash_processor._get_gallery_by_title(
            post_obj, "Test Title", studio
        )

        # Verify gallery was found
        assert gallery is not None
        assert gallery.id == "200"
        assert gallery.title == "Test Title"
        assert post_obj.stash_id == 200  # Should update item's stash_id as int

        # Verify 2 calls (count check + fetch results)
        assert len(graphql_route.calls) == 2

        # Verify first request contains findGalleries with title filter
        req = json.loads(graphql_route.calls[0].request.content)
        assert "findGalleries" in req["query"]
        assert req["variables"]["gallery_filter"]["title"]["value"] == "Test Title"
        assert req["variables"]["gallery_filter"]["title"]["modifier"] == "EQUALS"

    @pytest.mark.asyncio
    async def test_get_gallery_by_title_not_found(
        self, respx_stash_processor: StashProcessing
    ):
        """Test _get_gallery_by_title when no matching gallery found."""
        post_id = snowflake_id()
        acct_id = snowflake_id()

        # Build Post
        post_obj = PostFactory.build(
            id=post_id,
            accountId=acct_id,
            createdAt=datetime(2024, 4, 1, 12, 0, 0, tzinfo=UTC),
        )

        studio = StudioFactory.build(id="124", name="Test Studio")

        # Set up respx - findGalleries returns empty
        graphql_route = respx.post("http://localhost:9999/graphql").mock(
            side_effect=[
                httpx.Response(
                    200,
                    json={"data": {"findGalleries": {"galleries": [], "count": 0}}},
                )
            ]
        )

        gallery = await respx_stash_processor._get_gallery_by_title(
            post_obj, "Test Title", studio
        )

        # Verify no gallery returned
        assert gallery is None

        # Verify call was made
        assert len(graphql_route.calls) == 1

    @pytest.mark.asyncio
    async def test_get_gallery_by_title_wrong_date(
        self, respx_stash_processor: StashProcessing
    ):
        """Test _get_gallery_by_title rejects gallery with wrong date."""
        post_id = snowflake_id()
        acct_id = snowflake_id()

        # Build Post
        post_obj = PostFactory.build(
            id=post_id,
            accountId=acct_id,
            createdAt=datetime(2024, 4, 1, 12, 0, 0, tzinfo=UTC),
        )

        studio = StudioFactory.build(id="125", name="Test Studio")

        # Set up respx - returns gallery with wrong date (needs 2 responses for find())
        graphql_route = respx.post("http://localhost:9999/graphql").mock(
            side_effect=[
                # Call 0: Count check
                httpx.Response(
                    200,
                    json=create_graphql_response(
                        "findGalleries",
                        create_find_galleries_result(
                            count=1,
                            galleries=[
                                create_gallery_dict(
                                    id="201",
                                    title="Test Title",
                                    code=None,
                                    date="2024-04-02",  # Wrong date
                                    studio={
                                        "__typename": "Studio",
                                        "id": "125",
                                        "name": "Test Studio",
                                    },
                                )
                            ],
                        ),
                    ),
                ),
                # Call 1: Fetch results
                httpx.Response(
                    200,
                    json=create_graphql_response(
                        "findGalleries",
                        create_find_galleries_result(
                            count=1,
                            galleries=[
                                create_gallery_dict(
                                    id="201",
                                    title="Test Title",
                                    code=None,
                                    date="2024-04-02",  # Wrong date
                                    studio={
                                        "__typename": "Studio",
                                        "id": "125",
                                        "name": "Test Studio",
                                    },
                                )
                            ],
                        ),
                    ),
                ),
            ]
        )

        gallery = await respx_stash_processor._get_gallery_by_title(
            post_obj, "Test Title", studio
        )

        # Verify no match (wrong date)
        assert gallery is None

    @pytest.mark.asyncio
    async def test_get_gallery_by_title_wrong_studio(
        self, respx_stash_processor: StashProcessing
    ):
        """Test _get_gallery_by_title rejects gallery with wrong studio."""
        post_id = snowflake_id()
        acct_id = snowflake_id()

        # Build Post
        post_obj = PostFactory.build(
            id=post_id,
            accountId=acct_id,
            createdAt=datetime(2024, 4, 1, 12, 0, 0, tzinfo=UTC),
        )

        studio = StudioFactory.build(id="126", name="Test Studio")

        # Set up respx - returns gallery with wrong studio (needs 2 responses for find())
        graphql_route = respx.post("http://localhost:9999/graphql").mock(
            side_effect=[
                # Call 0: Count check
                httpx.Response(
                    200,
                    json=create_graphql_response(
                        "findGalleries",
                        create_find_galleries_result(
                            count=1,
                            galleries=[
                                create_gallery_dict(
                                    id="202",
                                    title="Test Title",
                                    code=None,
                                    date="2024-04-01",
                                    studio={
                                        "__typename": "Studio",
                                        "id": "different_studio",
                                        "name": "Wrong Studio",
                                    },
                                )
                            ],
                        ),
                    ),
                ),
                # Call 1: Fetch results
                httpx.Response(
                    200,
                    json=create_graphql_response(
                        "findGalleries",
                        create_find_galleries_result(
                            count=1,
                            galleries=[
                                create_gallery_dict(
                                    id="202",
                                    title="Test Title",
                                    code=None,
                                    date="2024-04-01",
                                    studio={
                                        "__typename": "Studio",
                                        "id": "different_studio",
                                        "name": "Wrong Studio",
                                    },
                                )
                            ],
                        ),
                    ),
                ),
            ]
        )

        gallery = await respx_stash_processor._get_gallery_by_title(
            post_obj, "Test Title", studio
        )

        # Verify no match (wrong studio)
        assert gallery is None

    @pytest.mark.asyncio
    async def test_get_gallery_by_title_no_studio(
        self, respx_stash_processor: StashProcessing
    ):
        """Test _get_gallery_by_title with no studio parameter matches any studio."""
        post_id = snowflake_id()
        acct_id = snowflake_id()

        # Build Post
        post_obj = PostFactory.build(
            id=post_id,
            accountId=acct_id,
            createdAt=datetime(2024, 4, 1, 12, 0, 0, tzinfo=UTC),
        )

        # Set up respx - returns gallery with any studio (needs 2 responses for find())
        graphql_route = respx.post("http://localhost:9999/graphql").mock(
            side_effect=[
                # Call 0: Count check
                httpx.Response(
                    200,
                    json=create_graphql_response(
                        "findGalleries",
                        create_find_galleries_result(
                            count=1,
                            galleries=[
                                create_gallery_dict(
                                    id="203",
                                    title="Test Title",
                                    code=None,
                                    date="2024-04-01",
                                    studio={
                                        "__typename": "Studio",
                                        "id": "any_studio",
                                        "name": "Any Studio",
                                    },
                                )
                            ],
                        ),
                    ),
                ),
                # Call 1: Fetch results
                httpx.Response(
                    200,
                    json=create_graphql_response(
                        "findGalleries",
                        create_find_galleries_result(
                            count=1,
                            galleries=[
                                create_gallery_dict(
                                    id="203",
                                    title="Test Title",
                                    code=None,
                                    date="2024-04-01",
                                    studio={
                                        "__typename": "Studio",
                                        "id": "any_studio",
                                        "name": "Any Studio",
                                    },
                                )
                            ],
                        ),
                    ),
                ),
            ]
        )

        # Call without studio parameter
        gallery = await respx_stash_processor._get_gallery_by_title(
            post_obj, "Test Title", None
        )

        # Verify gallery matches (no studio check)
        assert gallery is not None
        assert gallery.id == "203"

    @pytest.mark.asyncio
    async def test_get_gallery_by_code_found(
        self, respx_stash_processor: StashProcessing
    ):
        """Test _get_gallery_by_code when matching gallery found."""
        post_id = snowflake_id()
        acct_id = snowflake_id()

        # Build Post
        post_obj = PostFactory.build(id=post_id, accountId=acct_id)

        # Set up respx - findGalleries returns matching gallery (find_one needs 1 response)
        graphql_route = respx.post("http://localhost:9999/graphql").mock(
            side_effect=[
                httpx.Response(
                    200,
                    json=create_graphql_response(
                        "findGalleries",
                        create_find_galleries_result(
                            count=1,
                            galleries=[
                                create_gallery_dict(
                                    id="300",
                                    title=None,
                                    code=str(post_id),
                                )
                            ],
                        ),
                    ),
                )
            ]
        )

        gallery = await respx_stash_processor._get_gallery_by_code(post_obj)

        # Verify gallery was found
        assert gallery is not None
        assert gallery.id == "300"
        assert gallery.code == str(post_id)
        assert post_obj.stash_id == 300  # Should update item's stash_id

        # Verify exactly 1 call
        assert len(graphql_route.calls) == 1

        # Verify request contains findGalleries with code filter
        req = json.loads(graphql_route.calls[0].request.content)
        assert "findGalleries" in req["query"]
        assert req["variables"]["gallery_filter"]["code"]["value"] == str(post_id)
        assert req["variables"]["gallery_filter"]["code"]["modifier"] == "EQUALS"

    @pytest.mark.asyncio
    async def test_get_gallery_by_code_not_found(
        self, respx_stash_processor: StashProcessing
    ):
        """Test _get_gallery_by_code when no matching gallery found."""
        post_id = snowflake_id()
        acct_id = snowflake_id()

        # Build Post
        post_obj = PostFactory.build(id=post_id, accountId=acct_id)

        # Set up respx - findGalleries returns empty
        graphql_route = respx.post("http://localhost:9999/graphql").mock(
            side_effect=[
                httpx.Response(
                    200,
                    json={"data": {"findGalleries": {"galleries": [], "count": 0}}},
                )
            ]
        )

        gallery = await respx_stash_processor._get_gallery_by_code(post_obj)

        # Verify no gallery returned
        assert gallery is None
        assert len(graphql_route.calls) == 1

    @pytest.mark.asyncio
    async def test_get_gallery_by_code_wrong_code(
        self, respx_stash_processor: StashProcessing
    ):
        """Test _get_gallery_by_code rejects gallery with wrong code."""
        post_id = snowflake_id()
        acct_id = snowflake_id()

        # Build Post
        post_obj = PostFactory.build(id=post_id, accountId=acct_id)

        # Set up respx - returns gallery with different code (find_one needs 1 response)
        graphql_route = respx.post("http://localhost:9999/graphql").mock(
            side_effect=[
                httpx.Response(
                    200,
                    json=create_graphql_response(
                        "findGalleries",
                        create_find_galleries_result(
                            count=1,
                            galleries=[
                                create_gallery_dict(
                                    id="301",
                                    title=None,
                                    code="54321",  # Wrong code (expected post_id)
                                )
                            ],
                        ),
                    ),
                )
            ]
        )

        gallery = await respx_stash_processor._get_gallery_by_code(post_obj)

        # Verify no match (wrong code)
        assert gallery is None

    @pytest.mark.asyncio
    async def test_get_gallery_by_url_found(
        self, respx_stash_processor: StashProcessing
    ):
        """Test _get_gallery_by_url when matching gallery found."""
        post_id = snowflake_id()
        acct_id = snowflake_id()

        # Build Post
        post_obj = PostFactory.build(id=post_id, accountId=acct_id)

        test_url = f"https://test.com/post/{post_id}"

        # Set up respx - findGalleries returns matching gallery (find() needs 2 responses)
        # Then galleryUpdate for save
        graphql_route = respx.post("http://localhost:9999/graphql").mock(
            side_effect=[
                # Call 0: findGalleries count check
                httpx.Response(
                    200,
                    json=create_graphql_response(
                        "findGalleries",
                        create_find_galleries_result(
                            count=1,
                            galleries=[
                                create_gallery_dict(
                                    id="400",
                                    title=None,
                                    code="",
                                    urls=[test_url],
                                )
                            ],
                        ),
                    ),
                ),
                # Call 1: findGalleries fetch results
                httpx.Response(
                    200,
                    json=create_graphql_response(
                        "findGalleries",
                        create_find_galleries_result(
                            count=1,
                            galleries=[
                                create_gallery_dict(
                                    id="400",
                                    title=None,
                                    code="",
                                    urls=[test_url],
                                )
                            ],
                        ),
                    ),
                ),
                # Call 2: galleryUpdate (from gallery.save())
                httpx.Response(
                    200,
                    json=create_graphql_response(
                        "galleryUpdate",
                        create_gallery_dict(
                            id="400",
                            title=None,
                            code=str(post_id),
                            urls=[test_url],
                        ),
                    ),
                ),
            ]
        )

        gallery = await respx_stash_processor._get_gallery_by_url(post_obj, test_url)

        # Verify gallery was found
        assert gallery is not None
        assert gallery.id == "400"
        assert test_url in gallery.urls
        assert post_obj.stash_id == 400  # Should update item's stash_id

        # Verify 3 calls (count check + fetch + save)
        assert len(graphql_route.calls) == 3

        # Verify first request is findGalleries count check with url filter
        req0 = json.loads(graphql_route.calls[0].request.content)
        assert "findGalleries" in req0["query"]
        assert req0["variables"]["gallery_filter"]["url"]["value"] == test_url
        assert req0["variables"]["gallery_filter"]["url"]["modifier"] == "INCLUDES"

        # Verify third request is galleryUpdate (calls 1 is fetch results)
        req2 = json.loads(graphql_route.calls[2].request.content)
        assert "galleryUpdate" in req2["query"]

    @pytest.mark.asyncio
    async def test_get_gallery_by_url_not_found(
        self, respx_stash_processor: StashProcessing
    ):
        """Test _get_gallery_by_url when no matching gallery found."""
        post_id = snowflake_id()
        acct_id = snowflake_id()

        # Build Post
        post_obj = PostFactory.build(id=post_id, accountId=acct_id)

        test_url = f"https://test.com/post/{post_id}"

        # Set up respx - findGalleries returns empty
        graphql_route = respx.post("http://localhost:9999/graphql").mock(
            side_effect=[
                httpx.Response(
                    200,
                    json={"data": {"findGalleries": {"galleries": [], "count": 0}}},
                )
            ]
        )

        gallery = await respx_stash_processor._get_gallery_by_url(post_obj, test_url)

        # Verify no gallery returned
        assert gallery is None
        assert len(graphql_route.calls) == 1

    @pytest.mark.asyncio
    async def test_get_gallery_by_url_wrong_url(
        self, respx_stash_processor: StashProcessing
    ):
        """Test _get_gallery_by_url rejects gallery with wrong URL."""
        post_id = snowflake_id()
        acct_id = snowflake_id()

        # Build Post
        post_obj = PostFactory.build(id=post_id, accountId=acct_id)

        test_url = f"https://test.com/post/{post_id}"

        # Set up respx - returns gallery with different URL (find() needs 2 responses)
        graphql_route = respx.post("http://localhost:9999/graphql").mock(
            side_effect=[
                # Call 0: findGalleries count check
                httpx.Response(
                    200,
                    json=create_graphql_response(
                        "findGalleries",
                        create_find_galleries_result(
                            count=1,
                            galleries=[
                                create_gallery_dict(
                                    id="401",
                                    title=None,
                                    code=None,
                                    urls=["https://test.com/post/54321"],  # Wrong URL
                                )
                            ],
                        ),
                    ),
                ),
                # Call 1: findGalleries fetch results
                httpx.Response(
                    200,
                    json=create_graphql_response(
                        "findGalleries",
                        create_find_galleries_result(
                            count=1,
                            galleries=[
                                create_gallery_dict(
                                    id="401",
                                    title=None,
                                    code=None,
                                    urls=["https://test.com/post/54321"],  # Wrong URL
                                )
                            ],
                        ),
                    ),
                ),
            ]
        )

        gallery = await respx_stash_processor._get_gallery_by_url(post_obj, test_url)

        # Verify no match (wrong URL)
        assert gallery is None
