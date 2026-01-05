"""Unit tests for gallery-related methods.

These tests mock at the HTTP boundary using respx, allowing real code execution
through the entire processing pipeline.
"""

import json
from datetime import UTC, datetime

import httpx
import pytest
import respx
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from metadata.attachment import ContentType
from metadata.post import Post
from stash.processing import StashProcessing
from tests.fixtures import (
    AccountFactory,
    AttachmentFactory,
    HashtagFactory,
    PostFactory,
    StudioFactory,
)


class TestGalleryLookupMethods:
    """Test gallery lookup methods of StashProcessing using respx."""

    @pytest.fixture
    async def post_with_attachment(self, factory_async_session, session):
        """Create a post with attachment for testing."""
        AccountFactory(id=12345, username="test_user")
        PostFactory(
            id=12345,
            accountId=12345,
            content="Test post content",
            createdAt=datetime(2024, 4, 1, 12, 0, 0, tzinfo=UTC),
        )
        AttachmentFactory(
            postId=12345,
            contentId=12345,
            contentType=ContentType.ACCOUNT_MEDIA,
            pos=0,
        )

        factory_async_session.commit()
        await session.commit()

        result = await session.execute(
            select(Post).where(Post.id == 12345).options(selectinload(Post.attachments))
        )
        return result.unique().scalar_one()

    @pytest.mark.asyncio
    async def test_get_gallery_by_stash_id_no_id(
        self,
        factory_async_session,
        session,
        respx_stash_processor: StashProcessing,
    ):
        """Test _get_gallery_by_stash_id with no stash_id."""
        # Create post WITHOUT stash_id
        AccountFactory(id=12345, username="test_user")
        PostFactory(
            id=12345,
            accountId=12345,
            content="Test content",
            createdAt=datetime(2024, 4, 1, 12, 0, 0, tzinfo=UTC),
            stash_id=None,  # No stash_id
        )

        factory_async_session.commit()
        await session.commit()

        result = await session.execute(select(Post).where(Post.id == 12345))
        post = result.unique().scalar_one()

        # Set up respx - will error if called (shouldn't be)
        graphql_route = respx.post("http://localhost:9999/graphql").mock(
            side_effect=[]  # Empty list - any call will raise StopIteration
        )

        # Call method - should return None early without calling API
        result = await respx_stash_processor._get_gallery_by_stash_id(post)

        # Verify result and no API calls
        assert result is None
        assert len(graphql_route.calls) == 0

    @pytest.mark.asyncio
    async def test_get_gallery_by_stash_id_found(
        self,
        factory_async_session,
        session,
        respx_stash_processor: StashProcessing,
    ):
        """Test _get_gallery_by_stash_id when gallery is found."""
        # Create post WITH stash_id
        AccountFactory(id=12345, username="test_user")
        PostFactory(
            id=12345,
            accountId=12345,
            content="Test content",
            createdAt=datetime(2024, 4, 1, 12, 0, 0, tzinfo=UTC),
            stash_id=123,  # Has stash_id
        )

        factory_async_session.commit()
        await session.commit()

        result = await session.execute(select(Post).where(Post.id == 12345))
        post = result.unique().scalar_one()

        # Set up respx - findGallery returns gallery
        graphql_route = respx.post("http://localhost:9999/graphql").mock(
            side_effect=[
                httpx.Response(
                    200,
                    json={
                        "data": {
                            "findGallery": {
                                "id": "123",
                                "title": "Test Gallery",
                                "code": "12345",
                                "date": "2024-04-01",
                            }
                        }
                    },
                )
            ]
        )

        # Call method
        result = await respx_stash_processor._get_gallery_by_stash_id(post)

        # Verify result
        assert result is not None
        assert result.id == "123"
        assert result.title == "Test Gallery"

        # Verify API call
        assert len(graphql_route.calls) == 1
        req = json.loads(graphql_route.calls[0].request.content)
        assert "findGallery" in req["query"]
        assert req["variables"]["id"] == "123"

    @pytest.mark.asyncio
    async def test_get_gallery_by_stash_id_not_found(
        self,
        factory_async_session,
        session,
        respx_stash_processor: StashProcessing,
    ):
        """Test _get_gallery_by_stash_id when gallery not found."""
        # Create post WITH stash_id
        AccountFactory(id=12345, username="test_user")
        PostFactory(
            id=12345,
            accountId=12345,
            content="Test content",
            createdAt=datetime(2024, 4, 1, 12, 0, 0, tzinfo=UTC),
            stash_id=999,  # Has stash_id but gallery doesn't exist
        )

        factory_async_session.commit()
        await session.commit()

        result = await session.execute(select(Post).where(Post.id == 12345))
        post = result.unique().scalar_one()

        # Set up respx - findGallery returns null
        graphql_route = respx.post("http://localhost:9999/graphql").mock(
            side_effect=[httpx.Response(200, json={"data": {"findGallery": None}})]
        )

        # Call method
        result = await respx_stash_processor._get_gallery_by_stash_id(post)

        # Verify result
        assert result is None
        assert len(graphql_route.calls) == 1

        # Verify request
        req = json.loads(graphql_route.calls[0].request.content)
        assert "findGallery" in req["query"]
        assert req["variables"]["id"] == "999"

    @pytest.mark.asyncio
    async def test_get_gallery_by_title_not_found(
        self,
        factory_async_session,
        session,
        respx_stash_processor: StashProcessing,
    ):
        """Test _get_gallery_by_title when no galleries match."""
        # Create post
        AccountFactory(id=12345, username="test_user")
        PostFactory(
            id=12345,
            accountId=12345,
            content="Test content",
            createdAt=datetime(2024, 4, 1, 12, 0, 0, tzinfo=UTC),
        )

        factory_async_session.commit()
        await session.commit()

        result = await session.execute(select(Post).where(Post.id == 12345))
        post = result.unique().scalar_one()

        # Create real studio
        studio = StudioFactory.build(id="studio_123", name="Test Studio")

        # Set up respx - no galleries found
        graphql_route = respx.post("http://localhost:9999/graphql").mock(
            side_effect=[
                httpx.Response(
                    200,
                    json={"data": {"findGalleries": {"galleries": [], "count": 0}}},
                )
            ]
        )

        # Call method
        result = await respx_stash_processor._get_gallery_by_title(
            post, "Test Title", studio
        )

        # Verify result
        assert result is None
        assert len(graphql_route.calls) == 1

        # Verify request
        req = json.loads(graphql_route.calls[0].request.content)
        assert "findGalleries" in req["query"]
        assert req["variables"]["gallery_filter"]["title"]["value"] == "Test Title"
        assert req["variables"]["gallery_filter"]["title"]["modifier"] == "EQUALS"

    @pytest.mark.asyncio
    async def test_get_gallery_by_title_found(
        self,
        factory_async_session,
        session,
        respx_stash_processor: StashProcessing,
    ):
        """Test _get_gallery_by_title when gallery matches."""
        # Create post
        AccountFactory(id=12345, username="test_user")
        PostFactory(
            id=12345,
            accountId=12345,
            content="Test content",
            createdAt=datetime(2024, 4, 1, 12, 0, 0, tzinfo=UTC),
        )

        factory_async_session.commit()
        await session.commit()

        result = await session.execute(select(Post).where(Post.id == 12345))
        post = result.unique().scalar_one()

        # Create real studio
        studio = StudioFactory.build(id="studio_123", name="Test Studio")

        # Set up respx - gallery found
        graphql_route = respx.post("http://localhost:9999/graphql").mock(
            side_effect=[
                httpx.Response(
                    200,
                    json={
                        "data": {
                            "findGalleries": {
                                "galleries": [
                                    {
                                        "id": "123",
                                        "title": "Test Title",
                                        "code": "12345",
                                        "date": "2024-04-01",
                                        "studio": {
                                            "id": "studio_123",
                                            "name": "Test Studio",
                                        },
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
        result = await respx_stash_processor._get_gallery_by_title(
            post, "Test Title", studio
        )

        # Verify result
        assert result is not None
        assert result.id == "123"
        assert result.title == "Test Title"
        # Stash ID should be updated on item
        assert post.stash_id == 123

        # Verify request
        req = json.loads(graphql_route.calls[0].request.content)
        assert "findGalleries" in req["query"]
        assert req["variables"]["gallery_filter"]["title"]["value"] == "Test Title"

    @pytest.mark.asyncio
    async def test_get_gallery_by_code_not_found(
        self,
        factory_async_session,
        session,
        respx_stash_processor: StashProcessing,
    ):
        """Test _get_gallery_by_code when no galleries match."""
        # Create post
        AccountFactory(id=12345, username="test_user")
        PostFactory(
            id=12345,
            accountId=12345,
            content="Test content",
            createdAt=datetime(2024, 4, 1, 12, 0, 0, tzinfo=UTC),
        )

        factory_async_session.commit()
        await session.commit()

        result = await session.execute(select(Post).where(Post.id == 12345))
        post = result.unique().scalar_one()

        # Set up respx - no galleries found
        graphql_route = respx.post("http://localhost:9999/graphql").mock(
            side_effect=[
                httpx.Response(
                    200,
                    json={"data": {"findGalleries": {"galleries": [], "count": 0}}},
                )
            ]
        )

        # Call method
        result = await respx_stash_processor._get_gallery_by_code(post)

        # Verify result
        assert result is None
        assert len(graphql_route.calls) == 1

        # Verify request
        req = json.loads(graphql_route.calls[0].request.content)
        assert "findGalleries" in req["query"]
        assert req["variables"]["gallery_filter"]["code"]["value"] == "12345"
        assert req["variables"]["gallery_filter"]["code"]["modifier"] == "EQUALS"

    @pytest.mark.asyncio
    async def test_get_gallery_by_code_found(
        self,
        factory_async_session,
        session,
        respx_stash_processor: StashProcessing,
    ):
        """Test _get_gallery_by_code when gallery matches."""
        # Create post
        AccountFactory(id=12345, username="test_user")
        PostFactory(
            id=12345,
            accountId=12345,
            content="Test content",
            createdAt=datetime(2024, 4, 1, 12, 0, 0, tzinfo=UTC),
        )

        factory_async_session.commit()
        await session.commit()

        result = await session.execute(select(Post).where(Post.id == 12345))
        post = result.unique().scalar_one()

        # Set up respx - gallery found
        graphql_route = respx.post("http://localhost:9999/graphql").mock(
            side_effect=[
                httpx.Response(
                    200,
                    json={
                        "data": {
                            "findGalleries": {
                                "galleries": [
                                    {
                                        "id": "456",
                                        "title": "Code Gallery",
                                        "code": "12345",
                                        "date": "2024-04-01",
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
        result = await respx_stash_processor._get_gallery_by_code(post)

        # Verify result
        assert result is not None
        assert result.id == "456"
        assert result.code == "12345"
        # Stash ID should be updated on item
        assert post.stash_id == 456

        # Verify request
        req = json.loads(graphql_route.calls[0].request.content)
        assert "findGalleries" in req["query"]
        assert req["variables"]["gallery_filter"]["code"]["value"] == "12345"

    @pytest.mark.asyncio
    async def test_get_gallery_by_url_found(
        self,
        factory_async_session,
        session,
        respx_stash_processor: StashProcessing,
    ):
        """Test _get_gallery_by_url when gallery is found with correct code."""
        # Create post
        AccountFactory(id=12345, username="test_user")
        PostFactory(
            id=12345,
            accountId=12345,
            content="Test content",
            createdAt=datetime(2024, 4, 1, 12, 0, 0, tzinfo=UTC),
        )

        factory_async_session.commit()
        await session.commit()

        result = await session.execute(select(Post).where(Post.id == 12345))
        post = result.unique().scalar_one()

        # Set up respx - gallery found with code already matching, but still calls save()
        graphql_route = respx.post("http://localhost:9999/graphql").mock(
            side_effect=[
                # findGalleries - returns gallery with matching code
                httpx.Response(
                    200,
                    json={
                        "data": {
                            "findGalleries": {
                                "galleries": [
                                    {
                                        "id": "789",
                                        "title": "URL Gallery",
                                        "code": "12345",  # Already matches post.id
                                        "urls": ["https://example.com/gallery/123"],
                                    }
                                ],
                                "count": 1,
                            }
                        }
                    },
                ),
                # galleryUpdate - called by gallery.save() even though code matches
                httpx.Response(
                    200,
                    json={
                        "data": {
                            "galleryUpdate": {
                                "id": "789",
                                "code": "12345",
                                "title": "URL Gallery",
                                "urls": ["https://example.com/gallery/123"],
                            }
                        }
                    },
                ),
            ]
        )

        # Call method
        url = "https://example.com/gallery/123"
        result = await respx_stash_processor._get_gallery_by_url(post, url)

        # Verify result
        assert result is not None
        assert result.id == "789"
        assert result.code == "12345"

        # Verify request
        req = json.loads(graphql_route.calls[0].request.content)
        assert "findGalleries" in req["query"]
        assert req["variables"]["gallery_filter"]["url"]["value"] == url

    @pytest.mark.asyncio
    async def test_get_gallery_by_url_with_item_update(
        self,
        factory_async_session,
        session,
        respx_stash_processor: StashProcessing,
    ):
        """Test _get_gallery_by_url updates item stash_id and gallery code."""
        # Create post
        AccountFactory(id=12345, username="test_user")
        PostFactory(
            id=12345,
            accountId=12345,
            content="Test content",
            createdAt=datetime(2024, 4, 1, 12, 0, 0, tzinfo=UTC),
            stash_id=None,  # No stash_id initially
        )

        factory_async_session.commit()
        await session.commit()

        result = await session.execute(select(Post).where(Post.id == 12345))
        post = result.unique().scalar_one()

        # Set up respx - gallery found with different code (requires save)
        graphql_route = respx.post("http://localhost:9999/graphql").mock(
            side_effect=[
                # Call 0: findGalleries by url → found
                httpx.Response(
                    200,
                    json={
                        "data": {
                            "findGalleries": {
                                "galleries": [
                                    {
                                        "id": "999",
                                        "title": "URL Gallery",
                                        "code": "old_code",  # Different, needs update
                                        "urls": ["https://example.com/gallery/456"],
                                    }
                                ],
                                "count": 1,
                            }
                        }
                    },
                ),
                # Call 1: galleryUpdate from save()
                httpx.Response(
                    200,
                    json={
                        "data": {
                            "galleryUpdate": {
                                "id": "999",
                                "title": "URL Gallery",
                                "code": "12345",  # Updated to post.id
                                "urls": ["https://example.com/gallery/456"],
                            }
                        }
                    },
                ),
            ]
        )

        # Call method
        url = "https://example.com/gallery/456"
        result = await respx_stash_processor._get_gallery_by_url(post, url)

        # Verify result
        assert result is not None
        assert result.id == "999"
        # Item stash_id should be updated
        assert post.stash_id == 999
        # Gallery code should be updated
        assert result.code == "12345"

        # Verify both calls were made
        assert len(graphql_route.calls) == 2

        # Verify first call (findGalleries)
        req0 = json.loads(graphql_route.calls[0].request.content)
        assert "findGalleries" in req0["query"]
        assert req0["variables"]["gallery_filter"]["url"]["value"] == url

        # Verify second call (galleryUpdate)
        req1 = json.loads(graphql_route.calls[1].request.content)
        assert "galleryUpdate" in req1["query"]
        assert req1["variables"]["input"]["id"] == "999"
        assert req1["variables"]["input"]["code"] == "12345"


class TestGalleryCreation:
    """Test gallery creation methods using respx."""

    @pytest.mark.asyncio
    async def test_create_new_gallery(
        self,
        factory_async_session,
        session,
        respx_stash_processor: StashProcessing,
    ):
        """Test _create_new_gallery creates gallery with correct attributes."""
        # Create post
        AccountFactory(id=12345, username="test_user")
        PostFactory(
            id=12345,
            accountId=12345,
            content="Test post content",
            createdAt=datetime(2024, 4, 1, 12, 0, 0, tzinfo=UTC),
        )

        factory_async_session.commit()
        await session.commit()

        result = await session.execute(select(Post).where(Post.id == 12345))
        post = result.unique().scalar_one()

        # Note: _create_new_gallery doesn't make HTTP calls - it builds a Gallery object
        # The gallery is saved later via gallery.save()
        # So this test doesn't need respx mocking

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
    ):
        """Test _process_hashtags_to_tags with existing tags."""
        # Create real hashtag objects
        hashtag1 = HashtagFactory.build(value="test1")
        hashtag2 = HashtagFactory.build(value="test2")
        hashtags = [hashtag1, hashtag2]

        # Set up respx - both tags exist
        graphql_route = respx.post("http://localhost:9999/graphql").mock(
            side_effect=[
                # Call 0: findTags for "test1" → found
                httpx.Response(
                    200,
                    json={
                        "data": {
                            "findTags": {
                                "tags": [{"id": "tag_1", "name": "test1"}],
                                "count": 1,
                            }
                        }
                    },
                ),
                # Call 1: findTags for "test2" → found
                httpx.Response(
                    200,
                    json={
                        "data": {
                            "findTags": {
                                "tags": [{"id": "tag_2", "name": "test2"}],
                                "count": 1,
                            }
                        }
                    },
                ),
            ]
        )

        # Call method
        result = await respx_stash_processor._process_hashtags_to_tags(hashtags)

        # Verify result
        assert len(result) == 2
        assert result[0].name == "test1"
        assert result[1].name == "test2"

        # Verify both lookups were made
        assert len(graphql_route.calls) == 2

        # Verify requests
        req0 = json.loads(graphql_route.calls[0].request.content)
        assert "findTags" in req0["query"]
        assert req0["variables"]["tag_filter"]["name"]["value"] == "test1"

        req1 = json.loads(graphql_route.calls[1].request.content)
        assert "findTags" in req1["query"]
        assert req1["variables"]["tag_filter"]["name"]["value"] == "test2"

    @pytest.mark.asyncio
    async def test_process_hashtags_to_tags_create_new(
        self,
        respx_stash_processor: StashProcessing,
    ):
        """Test _process_hashtags_to_tags creates new tag when not found."""
        # Create real hashtag object
        hashtag = HashtagFactory.build(value="newtag")
        hashtags = [hashtag]

        # Set up respx - tag doesn't exist by name or alias, then create
        graphql_route = respx.post("http://localhost:9999/graphql").mock(
            side_effect=[
                # Call 0: findTags by name → not found
                httpx.Response(
                    200,
                    json={"data": {"findTags": {"tags": [], "count": 0}}},
                ),
                # Call 1: findTags by alias → not found
                httpx.Response(
                    200,
                    json={"data": {"findTags": {"tags": [], "count": 0}}},
                ),
                # Call 2: tagCreate
                httpx.Response(
                    200,
                    json={"data": {"tagCreate": {"id": "123", "name": "newtag"}}},
                ),
            ]
        )

        # Call method
        result = await respx_stash_processor._process_hashtags_to_tags(hashtags)

        # Verify result
        assert len(result) == 1
        assert result[0].name == "newtag"
        assert result[0].id == "123"

        # Verify lookups + create were made
        assert len(graphql_route.calls) == 3

        # Verify requests - two lookups then create
        req0 = json.loads(graphql_route.calls[0].request.content)
        assert "findTags" in req0["query"]
        assert req0["variables"]["tag_filter"]["name"]["value"] == "newtag"
        assert req0["variables"]["tag_filter"]["name"]["modifier"] == "EQUALS"

        req1 = json.loads(graphql_route.calls[1].request.content)
        assert "findTags" in req1["query"]
        assert req1["variables"]["tag_filter"]["aliases"]["value"] == "newtag"
        assert req1["variables"]["tag_filter"]["aliases"]["modifier"] == "INCLUDES"

        req2 = json.loads(graphql_route.calls[2].request.content)
        assert "tagCreate" in req2["query"]
        assert req2["variables"]["input"]["name"] == "newtag"


class TestTitleGeneration:
    """Test title generation methods - pure functions, no HTTP mocking needed."""

    @pytest.mark.asyncio
    async def test_generate_title_from_content_short(
        self,
        respx_stash_processor: StashProcessing,
    ):
        """Test _generate_title_from_content with short content."""
        content = "Short content"
        username = "test_user"
        created_at = datetime(2023, 1, 1, 12, 0, tzinfo=UTC)

        # Call method
        result = respx_stash_processor._generate_title_from_content(
            content, username, created_at
        )

        # Verify result uses content as title
        assert result == content

    @pytest.mark.asyncio
    async def test_generate_title_from_content_long(
        self,
        respx_stash_processor: StashProcessing,
    ):
        """Test _generate_title_from_content truncates long content."""
        content = "A" * 200  # Very long content
        username = "test_user"
        created_at = datetime(2023, 1, 1, 12, 0, tzinfo=UTC)

        # Call method
        result = respx_stash_processor._generate_title_from_content(
            content, username, created_at
        )

        # Verify result is truncated
        assert len(result) <= 128
        assert result.endswith("...")

    @pytest.mark.asyncio
    async def test_generate_title_from_content_with_newlines(
        self,
        respx_stash_processor: StashProcessing,
    ):
        """Test _generate_title_from_content uses first line."""
        content = "First line\nSecond line\nThird line"
        username = "test_user"
        created_at = datetime(2023, 1, 1, 12, 0, tzinfo=UTC)

        # Call method
        result = respx_stash_processor._generate_title_from_content(
            content, username, created_at
        )

        # Verify result uses first line only
        assert result == "First line"

    @pytest.mark.asyncio
    async def test_generate_title_from_content_no_content(
        self,
        respx_stash_processor: StashProcessing,
    ):
        """Test _generate_title_from_content with no content."""
        username = "test_user"
        created_at = datetime(2023, 1, 1, 12, 0, tzinfo=UTC)

        # Call method with None content
        result = respx_stash_processor._generate_title_from_content(
            None, username, created_at
        )

        # Verify result uses date format
        assert result == "test_user - 2023/01/01"

    @pytest.mark.asyncio
    async def test_generate_title_from_content_with_position(
        self,
        respx_stash_processor: StashProcessing,
    ):
        """Test _generate_title_from_content with position info."""
        content = "Short content"
        username = "test_user"
        created_at = datetime(2023, 1, 1, 12, 0, tzinfo=UTC)

        # Call method with position
        result = respx_stash_processor._generate_title_from_content(
            content, username, created_at, current_pos=2, total_media=5
        )

        # Verify result includes position
        assert result == "Short content - 2/5"
