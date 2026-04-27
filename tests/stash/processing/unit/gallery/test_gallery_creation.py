"""Tests for gallery creation methods in GalleryProcessingMixin.

These tests verify gallery creation logic. For data-building methods that don't
make HTTP calls, we test directly with factories. For methods that make GraphQL
calls, we use respx at the HTTP boundary.
"""

import json
from datetime import UTC, datetime

import httpx
import pytest
import respx

from metadata import Account, ContentType, PostMention
from stash.processing import StashProcessing
from tests.fixtures import (
    AttachmentFactory,
    GalleryFactory,
    PerformerFactory,
    PostFactory,
    StudioFactory,
    create_find_galleries_result,
    create_find_gallery_result,
    create_gallery_create_result,
    create_gallery_dict,
    create_gallery_update_result,
    create_graphql_response,
)
from tests.fixtures.stash.stash_api_fixtures import (
    assert_op,
    assert_op_with_vars,
    dump_graphql_calls,
)
from tests.fixtures.utils.test_isolation import snowflake_id


class TestGalleryCreation:
    """Test gallery creation methods in GalleryProcessingMixin."""

    @pytest.mark.asyncio
    async def test_create_new_gallery(
        self,
        respx_stash_processor: StashProcessing,
    ):
        """Test _create_new_gallery method creates gallery with correct metadata."""
        post = PostFactory.build(
            id=snowflake_id(),
            accountId=snowflake_id(),
            content="Test content #test #hashtag",
            createdAt=datetime(2024, 4, 1, 12, 0, 0, tzinfo=UTC),
        )

        gallery = await respx_stash_processor._create_new_gallery(post, "Test Title")

        assert gallery.is_new(), "Gallery should be marked as new (not yet saved)"
        assert gallery.title == "Test Title"
        assert gallery.details == post.content
        assert gallery.code == str(post.id)
        assert gallery.date == post.createdAt.strftime("%Y-%m-%d")
        assert gallery.organized is True

    @pytest.mark.asyncio
    async def test_get_gallery_metadata(
        self,
        respx_stash_processor: StashProcessing,
        faker,
    ):
        """Test _get_gallery_metadata method extracts correct metadata."""
        expected_username = faker.user_name()
        account_id = snowflake_id()
        post_id = snowflake_id()

        account = Account(id=account_id, username=expected_username)
        post = PostFactory.build(
            id=post_id,
            accountId=account_id,
            content="Test content #test",
            createdAt=datetime(2024, 4, 1, 12, 0, 0, tzinfo=UTC),
        )

        url_pattern = "https://test.com/{username}/post/{id}"
        username, title, url = await respx_stash_processor._get_gallery_metadata(
            post, account, url_pattern
        )

        assert username == expected_username
        assert title == "Test content #test"
        assert url == f"https://test.com/{expected_username}/post/{post_id}"

    @pytest.mark.asyncio
    async def test_setup_gallery_performers(
        self,
        respx_stash_processor: StashProcessing,
        faker,
    ):
        """Test _setup_gallery_performers adds main and mentioned performers."""
        post_id = snowflake_id()
        mention = PostMention(
            id=snowflake_id(),
            postId=post_id,
            accountId=snowflake_id(),
            handle="mention1",
        )
        post = PostFactory.build(
            id=post_id,
            accountId=snowflake_id(),
            content=faker.sentence(),
            mentions=[mention],
        )

        gallery = GalleryFactory.build(id="1023", title="Test Gallery")
        main_performer = PerformerFactory.build(id="5001", name="post_author")

        empty_find_galleries = httpx.Response(
            200,
            json={"data": {"findGalleries": {"count": 0, "galleries": []}}},
        )
        graphql_route = respx.post("http://localhost:9999/graphql").mock(
            side_effect=[
                httpx.Response(
                    200,
                    json={
                        "data": {
                            "findPerformers": {
                                "performers": [
                                    {
                                        "id": "5101",
                                        "name": "mention1",
                                        "urls": ["https://fansly.com/mention1"],
                                    }
                                ],
                                "count": 1,
                            }
                        }
                    },
                ),
                *([empty_find_galleries] * 5),
            ],
        )

        try:
            await respx_stash_processor._setup_gallery_performers(
                gallery, post, main_performer
            )
        finally:
            dump_graphql_calls(graphql_route.calls, "test_setup_gallery_performers")

        assert len(gallery.performers) == 2
        assert gallery.performers[0] == main_performer

        find_performer_queries = [
            json.loads(c.request.content).get("query", "") for c in graphql_route.calls
        ]
        assert any("findPerformers" in q for q in find_performer_queries)

    @pytest.mark.asyncio
    async def test_setup_gallery_performers_no_mentions(
        self,
        respx_stash_processor: StashProcessing,
    ):
        """Test _setup_gallery_performers with no mentions only adds main performer."""
        post = PostFactory.build(
            id=snowflake_id(),
            accountId=snowflake_id(),
            content="Test post no mentions",
        )

        gallery = GalleryFactory.build(id="1024", title="Test Gallery 2")
        main_performer = PerformerFactory.build(id="5002", name="post_author")

        graphql_route = respx.post("http://localhost:9999/graphql").mock(
            side_effect=[
                httpx.Response(
                    200,
                    json={"data": {"findGalleries": {"count": 0, "galleries": []}}},
                ),
            ],
        )

        try:
            await respx_stash_processor._setup_gallery_performers(
                gallery, post, main_performer
            )
        finally:
            dump_graphql_calls(
                graphql_route.calls, "test_setup_gallery_performers_no_mentions"
            )

        assert len(gallery.performers) == 1
        assert gallery.performers[0] == main_performer

        performer_lookup_calls = [
            c
            for c in graphql_route.calls
            if "findPerformers" in json.loads(c.request.content).get("query", "")
        ]
        assert len(performer_lookup_calls) == 0

    @pytest.mark.asyncio
    async def test_setup_gallery_performers_mention_not_found(
        self,
        respx_stash_processor: StashProcessing,
    ):
        """Test _setup_gallery_performers when mentioned performer not found in Stash."""
        post_id = snowflake_id()
        mention = PostMention(
            id=snowflake_id(),
            postId=post_id,
            accountId=snowflake_id(),
            handle="unknown_user",
        )
        post = PostFactory.build(
            id=post_id,
            accountId=snowflake_id(),
            content="Test post",
            mentions=[mention],
        )

        gallery = GalleryFactory.build(id="1025", title="Test Gallery 3")
        main_performer = PerformerFactory.build(id="5003", name="post_author")

        empty_performer_response = httpx.Response(
            200,
            json={
                "data": {
                    "findPerformers": {
                        "performers": [],
                        "count": 0,
                    }
                }
            },
        )
        graphql_route = respx.post("http://localhost:9999/graphql").mock(
            side_effect=[empty_performer_response] * 5,
        )

        await respx_stash_processor._setup_gallery_performers(
            gallery, post, main_performer
        )

        # Only main performer when mention not found
        assert len(gallery.performers) == 1
        assert gallery.performers[0] == main_performer

        # Verify findPerformers was called twice (name + alias search)
        assert len(graphql_route.calls) == 2
        assert_op(graphql_route.calls[0], "findPerformers")


class TestGalleryOrchestration:
    """Test _get_or_create_gallery waterfall lookup pattern using respx.

    Each test verifies the exact request variables to document the call sequence.
    Post objects without stash_id skip the stash_id lookup (no API call).
    """

    @pytest.fixture
    def orchestration_setup(self):
        """Set up common data for orchestration tests."""
        account_id = snowflake_id()
        post_id = snowflake_id()

        account = Account(id=account_id, username="test_user")
        post = PostFactory.build(
            id=post_id,
            accountId=account_id,
            content="Test post content",
            createdAt=datetime(2024, 4, 1, 12, 0, 0, tzinfo=UTC),
        )
        # Set attachments after construction to bypass _prepare_post_data validator
        # (which filters non-dict items). In production, attachments are de-nested
        # before reaching this point.
        post.attachments = [
            AttachmentFactory.build(
                postId=post_id,
                contentId=post_id,
                contentType=ContentType.ACCOUNT_MEDIA,
                pos=0,
            )
        ]

        performer = PerformerFactory.build(id="10100", name="test_user")
        studio = StudioFactory.build(id="10200", name="Test Studio")

        return {
            "account": account,
            "post": post,
            "performer": performer,
            "studio": studio,
            "url_pattern": "https://test.com/{username}/post/{id}",
        }

    @pytest.mark.asyncio
    async def test_gallery_found_by_stash_id(
        self,
        respx_stash_processor: StashProcessing,
    ):
        """Test when gallery is found by stash_id (post has stash_id set)."""
        account_id = snowflake_id()
        post_id = snowflake_id()

        account = Account(id=account_id, username="test_user_stash")
        post = PostFactory.build(
            id=post_id,
            accountId=account_id,
            content="Test post with stash_id",
            createdAt=datetime(2024, 4, 1, 12, 0, 0, tzinfo=UTC),
            stash_id=999,
        )
        post.attachments = [
            AttachmentFactory.build(
                postId=post_id,
                contentId=post_id,
                contentType=ContentType.ACCOUNT_MEDIA,
                pos=0,
            )
        ]

        performer = PerformerFactory.build(id="5201", name="test_user_stash")
        studio = StudioFactory.build(id="10201", name="Test Studio")

        # First lookup (by stash_id) succeeds
        graphql_route = respx.post("http://localhost:9999/graphql").mock(
            side_effect=[
                httpx.Response(
                    200,
                    json={
                        "data": {
                            "findGallery": {
                                "id": "999",
                                "title": "Found by Stash ID",
                                "code": str(post_id),
                            }
                        }
                    },
                )
            ],
        )

        gallery = await respx_stash_processor._get_or_create_gallery(
            post,
            account,
            performer,
            studio,
            "post",
            "https://test.com/{username}/post/{id}",
        )

        assert gallery is not None
        assert gallery.id == "999"

        # Verify exactly 1 call (stash_id lookup succeeded)
        assert len(graphql_route.calls) == 1
        assert_op_with_vars(graphql_route.calls[0], "findGallery", id="999")

    @pytest.mark.asyncio
    async def test_gallery_found_by_code(
        self,
        respx_stash_processor: StashProcessing,
        orchestration_setup,
    ):
        """Test when gallery is found by code (first lookup for post without stash_id)."""
        data = orchestration_setup
        post_id = str(data["post"].id)

        graphql_route = respx.post("http://localhost:9999/graphql").mock(
            side_effect=[
                # Call 0: findGalleries by code → found
                httpx.Response(
                    200,
                    json=create_graphql_response(
                        "findGalleries",
                        create_find_galleries_result(
                            count=1,
                            galleries=[
                                create_gallery_dict(
                                    id="1001",
                                    title="Found by Code",
                                    code=post_id,
                                )
                            ],
                        ),
                    ),
                ),
                # Call 1: findGalleries by title → not found
                httpx.Response(
                    200,
                    json=create_graphql_response(
                        "findGalleries", {"galleries": [], "count": 0}
                    ),
                ),
                # Call 2: findGalleries by url → not found
                httpx.Response(
                    200,
                    json=create_graphql_response(
                        "findGalleries", {"galleries": [], "count": 0}
                    ),
                ),
                # Call 3: findGallery (fetch complete object with all fields)
                httpx.Response(
                    200,
                    json=create_graphql_response(
                        "findGallery",
                        create_find_gallery_result(
                            create_gallery_dict(
                                id="1001",
                                title="Found by Code",
                                code=post_id,
                            )
                        ),
                    ),
                ),
                # Extra responses if library makes more calls
                httpx.Response(
                    200,
                    json=create_graphql_response(
                        "findGallery",
                        create_gallery_dict(id="1001", title="x", code="x"),
                    ),
                ),
                httpx.Response(
                    200,
                    json=create_graphql_response(
                        "findGallery",
                        create_gallery_dict(id="1001", title="x", code="x"),
                    ),
                ),
            ],
        )

        gallery = await respx_stash_processor._get_or_create_gallery(
            data["post"],
            data["account"],
            data["performer"],
            data["studio"],
            "post",
            data["url_pattern"],
        )

        assert gallery is not None
        assert gallery.id == "1001"

        # Single call: findGalleries by code → found, short-circuits the rest
        assert len(graphql_route.calls) == 1
        assert_op_with_vars(
            graphql_route.calls[0],
            "findGalleries",
            gallery_filter__code__value=post_id,
            gallery_filter__code__modifier="EQUALS",
        )

    @pytest.mark.asyncio
    async def test_gallery_found_by_title(
        self,
        respx_stash_processor: StashProcessing,
        orchestration_setup,
    ):
        """Test when gallery is found by title (code fails, title succeeds)."""
        data = orchestration_setup
        post_id = str(data["post"].id)

        graphql_route = respx.post("http://localhost:9999/graphql").mock(
            side_effect=[
                # Call 0: findGalleries by code → not found
                httpx.Response(
                    200,
                    json={"data": {"findGalleries": {"galleries": [], "count": 0}}},
                ),
                # Call 1: findGalleries by title (count check) → found
                httpx.Response(
                    200,
                    json=create_graphql_response(
                        "findGalleries",
                        create_find_galleries_result(
                            count=1,
                            galleries=[
                                create_gallery_dict(
                                    id="1002",
                                    title="Test post content",
                                    code="",
                                    date="2024-04-01",
                                    studio={"id": "10200"},
                                )
                            ],
                        ),
                    ),
                ),
                # Call 2: findGalleries by title (fetch results)
                httpx.Response(
                    200,
                    json=create_graphql_response(
                        "findGalleries",
                        create_find_galleries_result(
                            count=1,
                            galleries=[
                                create_gallery_dict(
                                    id="1002",
                                    title="Test post content",
                                    code="",
                                    date="2024-04-01",
                                    studio={"id": "10200"},
                                )
                            ],
                        ),
                    ),
                ),
            ],
        )

        gallery = await respx_stash_processor._get_or_create_gallery(
            data["post"],
            data["account"],
            data["performer"],
            data["studio"],
            "post",
            data["url_pattern"],
        )

        assert gallery is not None
        assert gallery.id == "1002"

        assert len(graphql_route.calls) == 3

        # Call 0: code lookup (failed)
        assert_op_with_vars(
            graphql_route.calls[0],
            "findGalleries",
            gallery_filter__code__value=post_id,
        )

        # Call 1: title lookup count check (succeeded)
        assert_op_with_vars(
            graphql_route.calls[1],
            "findGalleries",
            gallery_filter__title__value="Test post content",
            gallery_filter__title__modifier="EQUALS",
        )

        # Call 2: title lookup fetch results
        assert_op(graphql_route.calls[2], "findGalleries")

    @pytest.mark.asyncio
    async def test_gallery_found_by_url(
        self,
        respx_stash_processor: StashProcessing,
        orchestration_setup,
    ):
        """Test when gallery is found by URL (code + title fail, url succeeds)."""
        data = orchestration_setup
        post_id = str(data["post"].id)
        expected_url = f"https://test.com/test_user/post/{post_id}"

        graphql_route = respx.post("http://localhost:9999/graphql").mock(
            side_effect=[
                # Call 0: findGalleries by code → not found
                httpx.Response(
                    200,
                    json={"data": {"findGalleries": {"galleries": [], "count": 0}}},
                ),
                # Call 1: findGalleries by title → not found
                httpx.Response(
                    200,
                    json={"data": {"findGalleries": {"galleries": [], "count": 0}}},
                ),
                # Call 2: findGalleries by url (count check) → found
                httpx.Response(
                    200,
                    json=create_graphql_response(
                        "findGalleries",
                        create_find_galleries_result(
                            count=1,
                            galleries=[
                                create_gallery_dict(
                                    id="1003",
                                    title="Found by URL",
                                    urls=[expected_url],
                                )
                            ],
                        ),
                    ),
                ),
                # Call 3: findGalleries by url (fetch results)
                httpx.Response(
                    200,
                    json=create_graphql_response(
                        "findGalleries",
                        create_find_galleries_result(
                            count=1,
                            galleries=[
                                create_gallery_dict(
                                    id="1003",
                                    title="Found by URL",
                                    urls=[expected_url],
                                )
                            ],
                        ),
                    ),
                ),
                # Call 4: galleryUpdate (from gallery.save() in _get_gallery_by_url)
                httpx.Response(
                    200,
                    json=create_graphql_response(
                        "galleryUpdate",
                        create_gallery_update_result(
                            create_gallery_dict(
                                id="1003",
                                title="Found by URL",
                                code=post_id,
                            )
                        ),
                    ),
                ),
            ],
        )

        gallery = await respx_stash_processor._get_or_create_gallery(
            data["post"],
            data["account"],
            data["performer"],
            data["studio"],
            "post",
            data["url_pattern"],
        )

        assert gallery is not None
        assert gallery.id == "1003"

        # Verify exactly 5 calls (code, title, url x2, galleryUpdate)
        assert len(graphql_route.calls) == 5, (
            f"Expected exactly 5 GraphQL calls, got {len(graphql_route.calls)}"
        )

        # Call 0: code lookup (failed)
        assert_op(graphql_route.calls[0], "findGalleries")
        req0 = json.loads(graphql_route.calls[0].request.content)
        assert "code" in req0["variables"]["gallery_filter"]

        # Call 1: title lookup (failed)
        assert_op(graphql_route.calls[1], "findGalleries")
        req1 = json.loads(graphql_route.calls[1].request.content)
        assert "title" in req1["variables"]["gallery_filter"]

        # Call 2: url lookup (succeeded)
        assert_op_with_vars(
            graphql_route.calls[2],
            "findGalleries",
            gallery_filter__url__value=expected_url,
            gallery_filter__url__modifier="INCLUDES",
        )

    @pytest.mark.asyncio
    async def test_gallery_created_when_not_found(
        self,
        respx_stash_processor: StashProcessing,
        orchestration_setup,
    ):
        """Test when no gallery found (all lookups fail, create new)."""
        data = orchestration_setup
        post_id = str(data["post"].id)

        empty_find_galleries = httpx.Response(
            200,
            json={"data": {"findGalleries": {"galleries": [], "count": 0}}},
        )
        graphql_route = respx.post("http://localhost:9999/graphql").mock(
            side_effect=[
                # Call 0: code → not found
                empty_find_galleries,
                # Call 1: title → not found
                empty_find_galleries,
                # Call 2: url → not found
                empty_find_galleries,
                # Call 3: populate filter-query for gallery inverse
                empty_find_galleries,
                # Call 4: galleryCreate
                httpx.Response(
                    200,
                    json=create_graphql_response(
                        "galleryCreate",
                        create_gallery_create_result(
                            create_gallery_dict(
                                id="20123",
                                title="Test post content",
                                code=post_id,
                            )
                        ),
                    ),
                ),
                *([empty_find_galleries] * 10),
            ],
        )

        try:
            gallery = await respx_stash_processor._get_or_create_gallery(
                data["post"],
                data["account"],
                data["performer"],
                data["studio"],
                "post",
                data["url_pattern"],
            )
        finally:
            dump_graphql_calls(
                graphql_route.calls, "test_gallery_created_when_not_found"
            )

        assert gallery is not None
        assert gallery.title == "Test post content"

        # 5 calls: code/title/url finds + populate filter-query + galleryCreate
        assert len(graphql_route.calls) == 5, (
            f"Expected exactly 5 GraphQL calls, got {len(graphql_route.calls)}"
        )

        # Call 0: code lookup
        assert_op(graphql_route.calls[0], "findGalleries")
        req0 = json.loads(graphql_route.calls[0].request.content)
        assert "code" in req0["variables"]["gallery_filter"]

        # Call 1: title lookup
        assert_op(graphql_route.calls[1], "findGalleries")
        req1 = json.loads(graphql_route.calls[1].request.content)
        assert "title" in req1["variables"]["gallery_filter"]

        # Call 2: url lookup
        assert_op(graphql_route.calls[2], "findGalleries")
        req2 = json.loads(graphql_route.calls[2].request.content)
        assert "url" in req2["variables"]["gallery_filter"]

        gallery_create_queries = [
            json.loads(c.request.content).get("query", "") for c in graphql_route.calls
        ]
        assert any("galleryCreate" in q for q in gallery_create_queries)
