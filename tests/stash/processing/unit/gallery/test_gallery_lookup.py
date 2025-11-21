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
from sqlalchemy import select

from metadata import Post
from stash.processing import StashProcessing
from tests.fixtures import (
    AccountFactory,
    PostFactory,
    StudioFactory,
)


class TestGalleryLookup:
    """Test gallery lookup methods in GalleryProcessingMixin."""

    @pytest.mark.asyncio
    async def test_get_gallery_by_stash_id_found(
        self, factory_async_session, session, respx_stash_processor: StashProcessing
    ):
        """Test _get_gallery_by_stash_id when gallery is found."""
        # Create real Post object with stash_id set
        AccountFactory(id=12345, username="test_user")
        PostFactory(id=67890, accountId=12345, stash_id=123)
        factory_async_session.commit()
        await session.commit()

        # Query fresh from async session
        result = await session.execute(select(Post).where(Post.id == 67890))
        post_obj = result.unique().scalar_one()

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
                                "code": "67890",
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
        self, factory_async_session, session, respx_stash_processor: StashProcessing
    ):
        """Test _get_gallery_by_stash_id when post has no stash_id."""
        # Create real Post object without stash_id
        AccountFactory(id=12346, username="test_user_2")
        PostFactory(id=67891, accountId=12346)  # No stash_id
        factory_async_session.commit()
        await session.commit()

        # Query fresh from async session
        result = await session.execute(select(Post).where(Post.id == 67891))
        post_obj = result.unique().scalar_one()

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
        self, factory_async_session, session, respx_stash_processor: StashProcessing
    ):
        """Test _get_gallery_by_stash_id when gallery not found in Stash."""
        # Create real Post object with stash_id
        AccountFactory(id=12347, username="test_user_3")
        PostFactory(id=67892, accountId=12347, stash_id=999)
        factory_async_session.commit()
        await session.commit()

        # Query fresh from async session
        result = await session.execute(select(Post).where(Post.id == 67892))
        post_obj = result.unique().scalar_one()

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
        self, factory_async_session, session, respx_stash_processor: StashProcessing
    ):
        """Test _get_gallery_by_title when matching gallery found."""
        # Create real Post object with specific date
        AccountFactory(id=12345, username="test_user")
        PostFactory(
            id=67890,
            accountId=12345,
            createdAt=datetime(2024, 4, 1, 12, 0, 0, tzinfo=UTC),
        )
        factory_async_session.commit()
        await session.commit()

        # Query fresh from async session
        result = await session.execute(select(Post).where(Post.id == 67890))
        post_obj = result.unique().scalar_one()

        # Create real studio using factory
        studio = StudioFactory.build(id="123", name="Test Studio")

        # Set up respx - findGalleries returns matching gallery
        graphql_route = respx.post("http://localhost:9999/graphql").mock(
            side_effect=[
                httpx.Response(
                    200,
                    json={
                        "data": {
                            "findGalleries": {
                                "galleries": [
                                    {
                                        "id": "200",
                                        "title": "Test Title",
                                        "date": "2024-04-01",
                                        "studio": {"id": "123"},
                                    }
                                ],
                                "count": 1,
                            }
                        }
                    },
                )
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

        # Verify exactly 1 call
        assert len(graphql_route.calls) == 1

        # Verify request contains findGalleries with title filter
        req = json.loads(graphql_route.calls[0].request.content)
        assert "findGalleries" in req["query"]
        assert req["variables"]["gallery_filter"]["title"]["value"] == "Test Title"
        assert req["variables"]["gallery_filter"]["title"]["modifier"] == "EQUALS"

    @pytest.mark.asyncio
    async def test_get_gallery_by_title_not_found(
        self, factory_async_session, session, respx_stash_processor: StashProcessing
    ):
        """Test _get_gallery_by_title when no matching gallery found."""
        # Create real Post object
        AccountFactory(id=12346, username="test_user_2")
        PostFactory(
            id=67891,
            accountId=12346,
            createdAt=datetime(2024, 4, 1, 12, 0, 0, tzinfo=UTC),
        )
        factory_async_session.commit()
        await session.commit()

        # Query fresh from async session
        result = await session.execute(select(Post).where(Post.id == 67891))
        post_obj = result.unique().scalar_one()

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
        self, factory_async_session, session, respx_stash_processor: StashProcessing
    ):
        """Test _get_gallery_by_title rejects gallery with wrong date."""
        # Create real Post object
        AccountFactory(id=12347, username="test_user_3")
        PostFactory(
            id=67892,
            accountId=12347,
            createdAt=datetime(2024, 4, 1, 12, 0, 0, tzinfo=UTC),
        )
        factory_async_session.commit()
        await session.commit()

        result = await session.execute(select(Post).where(Post.id == 67892))
        post_obj = result.unique().scalar_one()

        studio = StudioFactory.build(id="125", name="Test Studio")

        # Set up respx - returns gallery with wrong date
        graphql_route = respx.post("http://localhost:9999/graphql").mock(
            side_effect=[
                httpx.Response(
                    200,
                    json={
                        "data": {
                            "findGalleries": {
                                "galleries": [
                                    {
                                        "id": "201",
                                        "title": "Test Title",
                                        "date": "2024-04-02",  # Wrong date
                                        "studio": {"id": "125"},
                                    }
                                ],
                                "count": 1,
                            }
                        }
                    },
                )
            ]
        )

        gallery = await respx_stash_processor._get_gallery_by_title(
            post_obj, "Test Title", studio
        )

        # Verify no match (wrong date)
        assert gallery is None

    @pytest.mark.asyncio
    async def test_get_gallery_by_title_wrong_studio(
        self, factory_async_session, session, respx_stash_processor: StashProcessing
    ):
        """Test _get_gallery_by_title rejects gallery with wrong studio."""
        # Create real Post object
        AccountFactory(id=12348, username="test_user_4")
        PostFactory(
            id=67893,
            accountId=12348,
            createdAt=datetime(2024, 4, 1, 12, 0, 0, tzinfo=UTC),
        )
        factory_async_session.commit()
        await session.commit()

        result = await session.execute(select(Post).where(Post.id == 67893))
        post_obj = result.unique().scalar_one()

        studio = StudioFactory.build(id="126", name="Test Studio")

        # Set up respx - returns gallery with wrong studio
        graphql_route = respx.post("http://localhost:9999/graphql").mock(
            side_effect=[
                httpx.Response(
                    200,
                    json={
                        "data": {
                            "findGalleries": {
                                "galleries": [
                                    {
                                        "id": "202",
                                        "title": "Test Title",
                                        "date": "2024-04-01",
                                        "studio": {"id": "different_studio"},  # Wrong
                                    }
                                ],
                                "count": 1,
                            }
                        }
                    },
                )
            ]
        )

        gallery = await respx_stash_processor._get_gallery_by_title(
            post_obj, "Test Title", studio
        )

        # Verify no match (wrong studio)
        assert gallery is None

    @pytest.mark.asyncio
    async def test_get_gallery_by_title_no_studio(
        self, factory_async_session, session, respx_stash_processor: StashProcessing
    ):
        """Test _get_gallery_by_title with no studio parameter matches any studio."""
        # Create real Post object
        AccountFactory(id=12349, username="test_user_5")
        PostFactory(
            id=67894,
            accountId=12349,
            createdAt=datetime(2024, 4, 1, 12, 0, 0, tzinfo=UTC),
        )
        factory_async_session.commit()
        await session.commit()

        result = await session.execute(select(Post).where(Post.id == 67894))
        post_obj = result.unique().scalar_one()

        # Set up respx - returns gallery with any studio
        graphql_route = respx.post("http://localhost:9999/graphql").mock(
            side_effect=[
                httpx.Response(
                    200,
                    json={
                        "data": {
                            "findGalleries": {
                                "galleries": [
                                    {
                                        "id": "203",
                                        "title": "Test Title",
                                        "date": "2024-04-01",
                                        "studio": {"id": "any_studio"},
                                    }
                                ],
                                "count": 1,
                            }
                        }
                    },
                )
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
        self, factory_async_session, session, respx_stash_processor: StashProcessing
    ):
        """Test _get_gallery_by_code when matching gallery found."""
        # Create real Post object
        AccountFactory(id=12345, username="test_user")
        PostFactory(id=67890, accountId=12345)
        factory_async_session.commit()
        await session.commit()

        result = await session.execute(select(Post).where(Post.id == 67890))
        post_obj = result.unique().scalar_one()

        # Set up respx - findGalleries returns matching gallery
        graphql_route = respx.post("http://localhost:9999/graphql").mock(
            side_effect=[
                httpx.Response(
                    200,
                    json={
                        "data": {
                            "findGalleries": {
                                "galleries": [{"id": "300", "code": "67890"}],
                                "count": 1,
                            }
                        }
                    },
                )
            ]
        )

        gallery = await respx_stash_processor._get_gallery_by_code(post_obj)

        # Verify gallery was found
        assert gallery is not None
        assert gallery.id == "300"
        assert gallery.code == "67890"
        assert post_obj.stash_id == 300  # Should update item's stash_id

        # Verify exactly 1 call
        assert len(graphql_route.calls) == 1

        # Verify request contains findGalleries with code filter
        req = json.loads(graphql_route.calls[0].request.content)
        assert "findGalleries" in req["query"]
        assert req["variables"]["gallery_filter"]["code"]["value"] == "67890"
        assert req["variables"]["gallery_filter"]["code"]["modifier"] == "EQUALS"

    @pytest.mark.asyncio
    async def test_get_gallery_by_code_not_found(
        self, factory_async_session, session, respx_stash_processor: StashProcessing
    ):
        """Test _get_gallery_by_code when no matching gallery found."""
        # Create real Post object
        AccountFactory(id=12346, username="test_user_2")
        PostFactory(id=67891, accountId=12346)
        factory_async_session.commit()
        await session.commit()

        result = await session.execute(select(Post).where(Post.id == 67891))
        post_obj = result.unique().scalar_one()

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
        self, factory_async_session, session, respx_stash_processor: StashProcessing
    ):
        """Test _get_gallery_by_code rejects gallery with wrong code."""
        # Create real Post object
        AccountFactory(id=12347, username="test_user_3")
        PostFactory(id=67892, accountId=12347)
        factory_async_session.commit()
        await session.commit()

        result = await session.execute(select(Post).where(Post.id == 67892))
        post_obj = result.unique().scalar_one()

        # Set up respx - returns gallery with different code
        graphql_route = respx.post("http://localhost:9999/graphql").mock(
            side_effect=[
                httpx.Response(
                    200,
                    json={
                        "data": {
                            "findGalleries": {
                                "galleries": [{"id": "301", "code": "54321"}],  # Wrong
                                "count": 1,
                            }
                        }
                    },
                )
            ]
        )

        gallery = await respx_stash_processor._get_gallery_by_code(post_obj)

        # Verify no match (wrong code)
        assert gallery is None

    @pytest.mark.asyncio
    async def test_get_gallery_by_url_found(
        self, factory_async_session, session, respx_stash_processor: StashProcessing
    ):
        """Test _get_gallery_by_url when matching gallery found."""
        # Create real Post object
        AccountFactory(id=12345, username="test_user")
        PostFactory(id=67890, accountId=12345)
        factory_async_session.commit()
        await session.commit()

        result = await session.execute(select(Post).where(Post.id == 67890))
        post_obj = result.unique().scalar_one()

        test_url = "https://test.com/post/67890"

        # Set up respx - findGalleries returns matching gallery, then galleryUpdate for save
        graphql_route = respx.post("http://localhost:9999/graphql").mock(
            side_effect=[
                # Call 0: findGalleries by URL
                httpx.Response(
                    200,
                    json={
                        "data": {
                            "findGalleries": {
                                "galleries": [
                                    {"id": "400", "urls": [test_url], "code": ""}
                                ],
                                "count": 1,
                            }
                        }
                    },
                ),
                # Call 1: galleryUpdate (from gallery.save())
                httpx.Response(
                    200,
                    json={
                        "data": {
                            "galleryUpdate": {
                                "id": "400",
                                "code": "67890",
                                "urls": [test_url],
                            }
                        }
                    },
                ),
            ]
        )

        gallery = await respx_stash_processor._get_gallery_by_url(post_obj, test_url)

        # Verify gallery was found
        assert gallery is not None
        assert gallery.id == "400"
        assert test_url in gallery.urls
        assert post_obj.stash_id == 400  # Should update item's stash_id

        # Verify 2 calls (find + save)
        assert len(graphql_route.calls) == 2

        # Verify first request is findGalleries with url filter
        req0 = json.loads(graphql_route.calls[0].request.content)
        assert "findGalleries" in req0["query"]
        assert req0["variables"]["gallery_filter"]["url"]["value"] == test_url
        assert req0["variables"]["gallery_filter"]["url"]["modifier"] == "EQUALS"

        # Verify second request is galleryUpdate
        req1 = json.loads(graphql_route.calls[1].request.content)
        assert "galleryUpdate" in req1["query"]

    @pytest.mark.asyncio
    async def test_get_gallery_by_url_not_found(
        self, factory_async_session, session, respx_stash_processor: StashProcessing
    ):
        """Test _get_gallery_by_url when no matching gallery found."""
        # Create real Post object
        AccountFactory(id=12346, username="test_user_2")
        PostFactory(id=67891, accountId=12346)
        factory_async_session.commit()
        await session.commit()

        result = await session.execute(select(Post).where(Post.id == 67891))
        post_obj = result.unique().scalar_one()

        test_url = "https://test.com/post/67891"

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
        self, factory_async_session, session, respx_stash_processor: StashProcessing
    ):
        """Test _get_gallery_by_url rejects gallery with wrong URL."""
        # Create real Post object
        AccountFactory(id=12347, username="test_user_3")
        PostFactory(id=67892, accountId=12347)
        factory_async_session.commit()
        await session.commit()

        result = await session.execute(select(Post).where(Post.id == 67892))
        post_obj = result.unique().scalar_one()

        test_url = "https://test.com/post/67892"

        # Set up respx - returns gallery with different URL
        graphql_route = respx.post("http://localhost:9999/graphql").mock(
            side_effect=[
                httpx.Response(
                    200,
                    json={
                        "data": {
                            "findGalleries": {
                                "galleries": [
                                    {
                                        "id": "401",
                                        "urls": ["https://test.com/post/54321"],
                                    }
                                ],
                                "count": 1,
                            }
                        }
                    },
                )
            ]
        )

        gallery = await respx_stash_processor._get_gallery_by_url(post_obj, test_url)

        # Verify no match (wrong URL)
        assert gallery is None
