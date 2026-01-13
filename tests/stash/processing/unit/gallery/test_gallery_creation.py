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
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from metadata import Account, Post, post_mentions
from metadata.attachment import ContentType
from stash.processing import StashProcessing
from tests.fixtures import (
    AccountFactory,
    AttachmentFactory,
    GalleryFactory,
    PerformerFactory,
    PostFactory,
    StudioFactory,
)


class TestGalleryCreation:
    """Test gallery creation methods in GalleryProcessingMixin."""

    @pytest.mark.asyncio
    async def test_create_new_gallery(
        self,
        factory_async_session,
        session,
        respx_stash_processor: StashProcessing,
    ):
        """Test _create_new_gallery method creates gallery with correct metadata."""
        # Create account first (FK requirement)
        AccountFactory(id=10000, username="test_user")

        # Create real Post object with factory
        PostFactory(
            id=12345,
            accountId=10000,
            content="Test content #test #hashtag",
            createdAt=datetime(2024, 4, 1, 12, 0, 0, tzinfo=UTC),
        )

        factory_async_session.commit()
        await session.commit()

        # Query fresh from async session
        result = await session.execute(select(Post).where(Post.id == 12345))
        post_item = result.unique().scalar_one()

        # Call method - no external API calls in _create_new_gallery
        gallery = await respx_stash_processor._create_new_gallery(
            post_item, "Test Title"
        )

        # Verify gallery properties
        assert gallery.id == "new"
        assert gallery.title == "Test Title"
        assert gallery.details == post_item.content
        assert gallery.code == str(post_item.id)
        assert gallery.date == post_item.createdAt.strftime("%Y-%m-%d")
        assert gallery.organized is True

    @pytest.mark.asyncio
    async def test_get_gallery_metadata(
        self,
        factory_async_session,
        session,
        respx_stash_processor: StashProcessing,
        faker,
    ):
        """Test _get_gallery_metadata method extracts correct metadata."""
        # Create real Account and Post with factories
        expected_username = faker.user_name()

        AccountFactory(id=12345, username=expected_username)
        PostFactory(
            id=67890,
            accountId=12345,
            content="Test content #test",
            createdAt=datetime(2024, 4, 1, 12, 0, 0, tzinfo=UTC),
        )

        factory_async_session.commit()
        await session.commit()

        # Query fresh from async session
        result = await session.execute(select(Account).where(Account.id == 12345))
        account_obj = result.scalar_one()

        result = await session.execute(select(Post).where(Post.id == 67890))
        post_obj = result.unique().scalar_one()

        # Call method - let real _generate_title_from_content run
        url_pattern = "https://test.com/{username}/post/{id}"
        username, title, url = await respx_stash_processor._get_gallery_metadata(
            post_obj, account_obj, url_pattern
        )

        # Verify results
        assert username == expected_username
        assert title == "Test content #test"
        assert url == f"https://test.com/{expected_username}/post/67890"

    @pytest.mark.asyncio
    async def test_setup_gallery_performers(
        self,
        factory_async_session,
        session,
        respx_stash_processor: StashProcessing,
        faker,
    ):
        """Test _setup_gallery_performers adds main and mentioned performers."""
        # Create post author account (FK requirement)
        AccountFactory(id=10000, username=faker.user_name())

        # Create account for mention
        AccountFactory(id=20001, username=faker.user_name())

        # Create main post for testing
        PostFactory(id=77777, accountId=10000, content=faker.sentence())

        factory_async_session.commit()

        # Create mention relationship
        await session.execute(
            post_mentions.insert().values(
                {"postId": 77777, "accountId": 20001, "handle": "mention1"}
            )
        )
        await session.commit()

        # Query post with mentions loaded
        result = await session.execute(
            select(Post)
            .where(Post.id == 77777)
            .options(selectinload(Post.accountMentions))
        )
        post_obj = result.unique().scalar_one()

        # Verify mention was loaded
        assert len(post_obj.accountMentions) == 1

        # Create real gallery and performer using factories
        gallery = GalleryFactory.build(id="gallery_123", title="Test Gallery")
        main_performer = PerformerFactory.build(id="performer_main", name="post_author")

        # Set up respx to capture GraphQL calls for find_performer
        graphql_route = respx.post("http://localhost:9999/graphql").mock(
            side_effect=[
                httpx.Response(
                    200,
                    json={
                        "data": {
                            "findPerformers": {
                                "performers": [
                                    {
                                        "id": "performer_mention1",
                                        "name": "mention1",
                                        "urls": ["https://fansly.com/mention1"],
                                    }
                                ],
                                "count": 1,
                            }
                        }
                    },
                )
            ]
        )

        # Call method
        await respx_stash_processor._setup_gallery_performers(
            gallery, post_obj, main_performer
        )

        # Verify results - gallery should have main performer + mentioned performer
        assert len(gallery.performers) == 2
        assert gallery.performers[0] == main_performer

        # Verify GraphQL call was made for mention lookup
        assert len(graphql_route.calls) > 0
        req = json.loads(graphql_route.calls[0].request.content)
        assert "findPerformers" in req["query"]

    @pytest.mark.asyncio
    async def test_setup_gallery_performers_no_mentions(
        self,
        factory_async_session,
        session,
        respx_stash_processor: StashProcessing,
    ):
        """Test _setup_gallery_performers with no mentions only adds main performer."""
        # Create post author account
        AccountFactory(id=10001, username="post_author")

        # Create post without mentions
        PostFactory(id=88888, accountId=10001, content="Test post no mentions")

        factory_async_session.commit()
        await session.commit()

        # Query post with mentions loaded
        result = await session.execute(
            select(Post)
            .where(Post.id == 88888)
            .options(selectinload(Post.accountMentions))
        )
        post_obj = result.unique().scalar_one()

        # Create real gallery and performer
        gallery = GalleryFactory.build(id="gallery_124", title="Test Gallery 2")
        main_performer = PerformerFactory.build(
            id="performer_main2", name="post_author"
        )

        # Set up respx - expect NO calls for post without mentions
        graphql_route = respx.post("http://localhost:9999/graphql").mock(
            side_effect=[]  # Empty list catches any unexpected call
        )

        # Call method
        await respx_stash_processor._setup_gallery_performers(
            gallery, post_obj, main_performer
        )

        # Verify results - only main performer
        assert len(gallery.performers) == 1
        assert gallery.performers[0] == main_performer

        # No GraphQL calls for performer lookup (no mentions)
        performer_lookup_calls = [
            c
            for c in graphql_route.calls
            if "findPerformers" in json.loads(c.request.content).get("query", "")
        ]
        assert len(performer_lookup_calls) == 0

    @pytest.mark.asyncio
    async def test_setup_gallery_performers_mention_not_found(
        self,
        factory_async_session,
        session,
        respx_stash_processor: StashProcessing,
    ):
        """Test _setup_gallery_performers when mentioned performer not found in Stash."""
        # Create post author account
        AccountFactory(id=10002, username="post_author")

        # Create account for mention
        AccountFactory(id=20002, username="unknown_user")

        # Create post with mention
        PostFactory(id=99999, accountId=10002, content="Test post")

        factory_async_session.commit()

        # Create mention relationship
        await session.execute(
            post_mentions.insert().values(
                {"postId": 99999, "accountId": 20002, "handle": "unknown_user"}
            )
        )
        await session.commit()

        # Query post with mentions loaded
        result = await session.execute(
            select(Post)
            .where(Post.id == 99999)
            .options(selectinload(Post.accountMentions))
        )
        post_obj = result.unique().scalar_one()

        # Create real gallery and performer
        gallery = GalleryFactory.build(id="gallery_125", title="Test Gallery 3")
        main_performer = PerformerFactory.build(
            id="performer_main3", name="post_author"
        )

        # Set up respx to return empty results (mention not found)
        # May have multiple lookup attempts (by URL, by name)
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
            side_effect=[empty_performer_response] * 5  # Allow multiple lookup attempts
        )

        # Call method
        await respx_stash_processor._setup_gallery_performers(
            gallery, post_obj, main_performer
        )

        # Verify results - only main performer when mention not found
        assert len(gallery.performers) == 1
        assert gallery.performers[0] == main_performer

        # Verify findPerformers was called (may be multiple calls for performer lookup logic)
        assert len(graphql_route.calls) >= 1
        req = json.loads(graphql_route.calls[0].request.content)
        assert "findPerformers" in req["query"]


class TestGalleryOrchestration:
    """Test _get_or_create_gallery waterfall lookup pattern using respx.

    Each test verifies the exact request variables to document the call sequence.
    Post objects without stash_id skip the stash_id lookup (no API call).
    """

    @pytest.fixture
    async def orchestration_setup(self, factory_async_session, session):
        """Set up common data for orchestration tests."""
        # Create real account and post with factory
        AccountFactory(id=12345, username="test_user")
        PostFactory(
            id=67890,
            accountId=12345,
            content="Test post content",
            createdAt=datetime(2024, 4, 1, 12, 0, 0, tzinfo=UTC),
        )
        # Add attachment so _has_media_content returns True
        AttachmentFactory(
            postId=67890,
            contentId=67890,
            contentType=ContentType.ACCOUNT_MEDIA,
            pos=0,
        )

        factory_async_session.commit()
        await session.commit()

        # Query fresh from async session
        result = await session.execute(select(Account).where(Account.id == 12345))
        account_obj = result.scalar_one()

        result = await session.execute(
            select(Post).where(Post.id == 67890).options(selectinload(Post.attachments))
        )
        post_obj = result.unique().scalar_one()

        # Create real performer and studio using factories
        performer = PerformerFactory.build(id="performer_123", name="test_user")
        studio = StudioFactory.build(id="studio_123", name="Test Studio")

        return {
            "account": account_obj,
            "post": post_obj,
            "performer": performer,
            "studio": studio,
            "url_pattern": "https://test.com/{username}/post/{id}",
        }

    @pytest.mark.asyncio
    async def test_gallery_found_by_stash_id(
        self,
        factory_async_session,
        session,
        respx_stash_processor: StashProcessing,
    ):
        """Test when gallery is found by stash_id (post has stash_id set)."""
        # Create post WITH stash_id set
        AccountFactory(id=11111, username="test_user_stash")
        PostFactory(
            id=11111,
            accountId=11111,
            content="Test post with stash_id",
            createdAt=datetime(2024, 4, 1, 12, 0, 0, tzinfo=UTC),
            stash_id=999,  # Has stash_id set
        )
        # Add attachment so _has_media_content returns True
        AttachmentFactory(
            postId=11111,
            contentId=11111,
            contentType=ContentType.ACCOUNT_MEDIA,
            pos=0,
        )

        factory_async_session.commit()
        await session.commit()

        result = await session.execute(select(Account).where(Account.id == 11111))
        account = result.scalar_one()

        result = await session.execute(
            select(Post).where(Post.id == 11111).options(selectinload(Post.attachments))
        )
        post = result.unique().scalar_one()

        performer = PerformerFactory.build(id="performer_stash", name="test_user_stash")
        studio = StudioFactory.build(id="studio_stash", name="Test Studio")

        # First lookup (by stash_id) succeeds - use side_effect to verify exactly 1 call
        graphql_route = respx.post("http://localhost:9999/graphql").mock(
            side_effect=[
                httpx.Response(
                    200,
                    json={
                        "data": {
                            "findGallery": {
                                "id": "999",
                                "title": "Found by Stash ID",
                                "code": "11111",
                            }
                        }
                    },
                )
            ]
        )

        gallery = await respx_stash_processor._get_or_create_gallery(
            post,
            account,
            performer,
            studio,
            "post",
            "https://test.com/{username}/post/{id}",
        )

        # Verify gallery was found
        assert gallery is not None
        assert gallery.id == "999"

        # Verify exactly 1 call (stash_id lookup succeeded)
        assert len(graphql_route.calls) == 1, "Expected exactly 1 GraphQL call"

        # Verify request: findGallery with id
        req0 = json.loads(graphql_route.calls[0].request.content)
        assert "findGallery" in req0["query"]
        assert req0["variables"]["id"] == "999"

    @pytest.mark.asyncio
    async def test_gallery_found_by_code(
        self,
        respx_stash_processor: StashProcessing,
        orchestration_setup,
    ):
        """Test when gallery is found by code (first lookup for post without stash_id)."""
        data = orchestration_setup

        # Post has no stash_id, so stash_id lookup is skipped
        # First API call is by code - use side_effect to verify exactly 1 call
        graphql_route = respx.post(
            "http://localhost:9999/graphql"
        ).mock(
            side_effect=[
                httpx.Response(
                    200,
                    json={
                        "data": {
                            "findGalleries": {
                                "galleries": [
                                    {
                                        "id": "1001",  # Must be numeric for stash_id conversion
                                        "title": "Found by Code",
                                        "code": "67890",
                                    }
                                ],
                                "count": 1,
                            }
                        }
                    },
                )
            ]
        )

        gallery = await respx_stash_processor._get_or_create_gallery(
            data["post"],
            data["account"],
            data["performer"],
            data["studio"],
            "post",
            data["url_pattern"],
        )

        # Verify gallery was found
        assert gallery is not None
        assert gallery.id == "1001"

        # Verify exactly 1 call (code lookup succeeded)
        assert len(graphql_route.calls) == 1, "Expected exactly 1 GraphQL call"

        # Verify request: findGalleries with code filter
        req0 = json.loads(graphql_route.calls[0].request.content)
        assert "findGalleries" in req0["query"]
        assert req0["variables"]["gallery_filter"]["code"]["value"] == "67890"
        assert req0["variables"]["gallery_filter"]["code"]["modifier"] == "EQUALS"

    @pytest.mark.asyncio
    async def test_gallery_found_by_title(
        self,
        respx_stash_processor: StashProcessing,
        orchestration_setup,
    ):
        """Test when gallery is found by title (code fails, title succeeds)."""
        data = orchestration_setup

        # side_effect chain: code fails, title succeeds
        graphql_route = respx.post(
            "http://localhost:9999/graphql"
        ).mock(
            side_effect=[
                # Call 0: findGalleries by code → not found
                httpx.Response(
                    200,
                    json={"data": {"findGalleries": {"galleries": [], "count": 0}}},
                ),
                # Call 1: findGalleries by title → found
                # Must include date matching item.createdAt and studio.id for the match to succeed
                httpx.Response(
                    200,
                    json={
                        "data": {
                            "findGalleries": {
                                "galleries": [
                                    {
                                        "id": "1002",  # Must be numeric for stash_id conversion
                                        "title": "Test post content",
                                        "code": "",
                                        "date": "2024-04-01",  # Must match item.createdAt
                                        "studio": {
                                            "id": "studio_123"
                                        },  # Must match studio.id
                                    }
                                ],
                                "count": 1,
                            }
                        }
                    },
                ),
            ]
        )

        gallery = await respx_stash_processor._get_or_create_gallery(
            data["post"],
            data["account"],
            data["performer"],
            data["studio"],
            "post",
            data["url_pattern"],
        )

        # Verify gallery was found
        assert gallery is not None
        assert gallery.id == "1002"

        # Verify exactly 2 calls
        assert len(graphql_route.calls) == 2, "Expected exactly 2 GraphQL calls"

        # Call 0: code lookup (failed)
        req0 = json.loads(graphql_route.calls[0].request.content)
        assert "findGalleries" in req0["query"]
        assert req0["variables"]["gallery_filter"]["code"]["value"] == "67890"

        # Call 1: title lookup (succeeded)
        req1 = json.loads(graphql_route.calls[1].request.content)
        assert "findGalleries" in req1["query"]
        assert (
            req1["variables"]["gallery_filter"]["title"]["value"] == "Test post content"
        )
        assert req1["variables"]["gallery_filter"]["title"]["modifier"] == "EQUALS"

    @pytest.mark.asyncio
    async def test_gallery_found_by_url(
        self,
        respx_stash_processor: StashProcessing,
        orchestration_setup,
    ):
        """Test when gallery is found by URL (code + title fail, url succeeds)."""
        data = orchestration_setup

        # side_effect chain: code fails, title fails, url succeeds, then gallery.save()
        # Note: _get_gallery_by_url calls gallery.save() after finding by URL
        graphql_route = respx.post(
            "http://localhost:9999/graphql"
        ).mock(
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
                # Call 2: findGalleries by url → found
                httpx.Response(
                    200,
                    json={
                        "data": {
                            "findGalleries": {
                                "galleries": [
                                    {
                                        "id": "1003",  # Must be numeric for stash_id conversion
                                        "title": "Found by URL",
                                        "urls": [
                                            "https://test.com/test_user/post/67890"
                                        ],
                                    }
                                ],
                                "count": 1,
                            }
                        }
                    },
                ),
                # Call 3: galleryUpdate (from gallery.save() in _get_gallery_by_url)
                httpx.Response(
                    200,
                    json={
                        "data": {
                            "galleryUpdate": {
                                "id": "1003",
                                "title": "Found by URL",
                                "code": "67890",
                            }
                        }
                    },
                ),
            ]
        )

        gallery = await respx_stash_processor._get_or_create_gallery(
            data["post"],
            data["account"],
            data["performer"],
            data["studio"],
            "post",
            data["url_pattern"],
        )

        # Verify gallery was found
        assert gallery is not None
        assert gallery.id == "1003"

        # Verify exactly 4 calls (3 lookups + 1 save)
        assert len(graphql_route.calls) == 4, (
            f"Expected exactly 4 GraphQL calls, got {len(graphql_route.calls)}"
        )

        # Call 0: code lookup (failed)
        req0 = json.loads(graphql_route.calls[0].request.content)
        assert "findGalleries" in req0["query"]
        assert "code" in req0["variables"]["gallery_filter"]

        # Call 1: title lookup (failed)
        req1 = json.loads(graphql_route.calls[1].request.content)
        assert "findGalleries" in req1["query"]
        assert "title" in req1["variables"]["gallery_filter"]

        # Call 2: url lookup (succeeded)
        req2 = json.loads(graphql_route.calls[2].request.content)
        assert "findGalleries" in req2["query"]
        assert (
            req2["variables"]["gallery_filter"]["url"]["value"]
            == "https://test.com/test_user/post/67890"
        )
        assert req2["variables"]["gallery_filter"]["url"]["modifier"] == "EQUALS"

    @pytest.mark.asyncio
    async def test_gallery_created_when_not_found(
        self,
        respx_stash_processor: StashProcessing,
        orchestration_setup,
    ):
        """Test when no gallery found (all lookups fail, create new)."""
        data = orchestration_setup

        # side_effect chain: all lookups fail, then create
        graphql_route = respx.post("http://localhost:9999/graphql").mock(
            side_effect=[
                # Call 0: code → not found
                httpx.Response(
                    200,
                    json={"data": {"findGalleries": {"galleries": [], "count": 0}}},
                ),
                # Call 1: title → not found
                httpx.Response(
                    200,
                    json={"data": {"findGalleries": {"galleries": [], "count": 0}}},
                ),
                # Call 2: url → not found
                httpx.Response(
                    200,
                    json={"data": {"findGalleries": {"galleries": [], "count": 0}}},
                ),
                # Call 3: galleryCreate
                httpx.Response(
                    200,
                    json={
                        "data": {
                            "galleryCreate": {
                                "id": "new_gallery_123",
                                "title": "Test post content",
                                "code": "67890",
                            }
                        }
                    },
                ),
            ]
        )

        gallery = await respx_stash_processor._get_or_create_gallery(
            data["post"],
            data["account"],
            data["performer"],
            data["studio"],
            "post",
            data["url_pattern"],
        )

        # Verify gallery was created
        assert gallery is not None
        assert gallery.title == "Test post content"

        # Verify at least 4 calls (3 lookups + 1 create)
        assert len(graphql_route.calls) >= 4, (
            f"Expected at least 4 GraphQL calls, got {len(graphql_route.calls)}"
        )

        # Call 0: code lookup
        req0 = json.loads(graphql_route.calls[0].request.content)
        assert "findGalleries" in req0["query"]
        assert "code" in req0["variables"]["gallery_filter"]

        # Call 1: title lookup
        req1 = json.loads(graphql_route.calls[1].request.content)
        assert "findGalleries" in req1["query"]
        assert "title" in req1["variables"]["gallery_filter"]

        # Call 2: url lookup
        req2 = json.loads(graphql_route.calls[2].request.content)
        assert "findGalleries" in req2["query"]
        assert "url" in req2["variables"]["gallery_filter"]

        # Call 3: galleryCreate
        req3 = json.loads(graphql_route.calls[3].request.content)
        assert "galleryCreate" in req3["query"]
