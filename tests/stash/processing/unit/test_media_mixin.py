"""Tests for the MediaProcessingMixin.

This module imports all the media mixin tests to ensure they are discovered by pytest.
"""

import json

import httpx
import pytest
import respx
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from stash_graphql_client.types import Image

# Import the modules instead of the classes to avoid fixture issues
from metadata import Account, AccountMedia, Media, Post
from metadata.attachment import ContentType
from tests.fixtures import (
    AccountFactory,
    AccountMediaFactory,
    AttachmentFactory,
    MediaFactory,
    PostFactory,
    create_find_studios_result,
    create_graphql_response,
    create_studio_dict,
)
from tests.fixtures.stash.stash_graphql_fixtures import create_image_dict


class TestMediaProcessingWithRealData:
    """Test media processing mixin with real JSON data."""

    @pytest.mark.asyncio
    async def test_process_media_with_real_data(
        self, respx_stash_processor, factory_async_session, session
    ):
        """Test processing media with real data using factories."""
        await respx_stash_processor.context.get_client()

        # Create test data with factories
        AccountFactory(id=12345, username="test_user")
        PostFactory(id=200, accountId=12345, content="Test post #test")
        MediaFactory(id=123, accountId=12345, mimetype="image/jpeg", is_downloaded=True)
        AccountMediaFactory(id=123, accountId=12345, mediaId=123)
        AttachmentFactory(
            id=60001,
            postId=200,
            contentId=123,
            contentType=ContentType.ACCOUNT_MEDIA,
            pos=0,
        )

        # Commit factory changes
        factory_async_session.commit()

        # Query fresh objects from async session with eager loading
        result_account = await session.execute(
            select(Account).where(Account.id == 12345)
        )
        account = result_account.scalar_one()

        # Eager load relationships to prevent lazy loading in async context
        result_post = await session.execute(
            select(Post)
            .where(Post.id == 200)
            .options(
                selectinload(Post.accountMentions),
                selectinload(Post.hashtags),
            )
        )
        post = result_post.unique().scalar_one()

        result_media = await session.execute(
            select(Media).where(Media.id == 123).options(selectinload(Media.variants))
        )
        media = result_media.unique().scalar_one()

        # Create image dict for GraphQL response using fixture
        image_dict = create_image_dict(
            id="600",
            title="Test Image",
            visual_files=[
                {
                    "id": "800",
                    "path": f"/path/to/{media.id}.jpg",
                    "basename": f"{media.id}.jpg",
                    "parent_folder_id": "folder_1",
                    "size": 1024000,
                    "width": 1920,
                    "height": 1080,
                    "mod_time": "2024-01-01T00:00:00Z",
                    "fingerprints": [],
                }
            ],
        )

        # Studio fix pattern: Add Fansly parent studio
        fansly_studio_dict = create_studio_dict(
            id="246", name="Fansly (network)", urls=["https://fansly.com"]
        )
        fansly_studio_result = create_find_studios_result(
            count=1, studios=[fansly_studio_dict]
        )

        # Creator studio "not found" (triggers studioCreate)
        creator_not_found_result = create_find_studios_result(count=0, studios=[])

        # Creator studio after creation
        creator_studio_dict = create_studio_dict(
            id="999",
            name="test_user (Fansly)",
            urls=["https://fansly.com/test_user"],
            parent_studio=fansly_studio_dict,
        )

        # Create empty responses for cache checks
        empty_tags_result = {"count": 0, "tags": []}

        # Mock GraphQL responses - use respx route matcher to return appropriate responses
        # This is more flexible than a strict side_effect list
        def graphql_handler(request):  # noqa: PLR0911
            body = json.loads(request.content)
            query = body.get("query", "")
            variables = body.get("variables", {})

            # Return appropriate response based on query type
            if "findImages" in query:
                return httpx.Response(
                    200,
                    json=create_graphql_response(
                        "findImages",
                        {
                            "count": 1,
                            "megapixels": 2.0,
                            "filesize": 1024000.0,
                            "images": [image_dict],
                        },
                    ),
                )
            if "findPerformers" in query:
                return httpx.Response(
                    200,
                    json=create_graphql_response(
                        "findPerformers", {"count": 0, "performers": []}
                    ),
                )
            if "findTags" in query:
                return httpx.Response(
                    200,
                    json=create_graphql_response("findTags", empty_tags_result),
                )
            if "studioCreate" in query:
                return httpx.Response(
                    200,
                    json=create_graphql_response("studioCreate", creator_studio_dict),
                )
            if "imageUpdate" in query:
                return httpx.Response(
                    200,
                    json=create_graphql_response("imageUpdate", image_dict),
                )
            if "findStudios" in query:
                # Check if searching for "Fansly (network)" - return found
                # Otherwise return not found (to trigger studioCreate)
                studio_filter = variables.get("studio_filter", {})
                name_filter = studio_filter.get("name", {})
                search_name = name_filter.get("value", "")

                if search_name == "Fansly (network)" or not studio_filter:
                    # Searching for parent studio or no filter - return Fansly
                    return httpx.Response(
                        200,
                        json=create_graphql_response(
                            "findStudios", fansly_studio_result
                        ),
                    )
                # Searching for creator studio - return not found
                return httpx.Response(
                    200,
                    json=create_graphql_response(
                        "findStudios", creator_not_found_result
                    ),
                )
            # Default response for any other query
            return httpx.Response(200, json={"data": {}})

        graphql_route = respx.post("http://localhost:9999/graphql").mock(
            side_effect=graphql_handler
        )

        # Create an empty result dictionary
        result = {"images": [], "scenes": []}

        # Call _process_media with queried data
        await respx_stash_processor._process_media(media, post, account, result)

        # Verify results
        assert len(result["images"]) == 1
        assert isinstance(result["images"][0], Image)
        assert result["images"][0].id == "600"
        assert len(result["scenes"]) == 0

        # Verify key GraphQL calls were made (less strict on count)
        calls = graphql_route.calls
        request_bodies = [json.loads(call.request.content) for call in calls]

        # Verify findImages was called
        find_images_calls = [
            body for body in request_bodies if "findImages" in body["query"]
        ]
        assert len(find_images_calls) >= 1
        path_pattern = find_images_calls[0]["variables"]["image_filter"]["path"][
            "value"
        ]
        assert "(123)" in path_pattern

        # Verify findPerformers was called
        find_performers_calls = [
            body for body in request_bodies if "findPerformers" in body["query"]
        ]
        assert len(find_performers_calls) >= 1

        # Verify studioCreate was called
        studio_create_calls = [
            body for body in request_bodies if "studioCreate" in body["query"]
        ]
        assert len(studio_create_calls) >= 1
        assert (
            studio_create_calls[0]["variables"]["input"]["name"] == "test_user (Fansly)"
        )

        # Verify imageUpdate was called
        image_update_calls = [
            body for body in request_bodies if "imageUpdate" in body["query"]
        ]
        assert len(image_update_calls) >= 1

    @pytest.mark.asyncio
    async def test_process_creator_attachment_with_real_data(
        self, respx_stash_processor, factory_async_session, session
    ):
        """Test process_creator_attachment with real data using factories."""
        await respx_stash_processor.context.get_client()

        # Create test data with factories
        AccountFactory(id=12346, username="test_user_2")
        PostFactory(id=201, accountId=12346, content="Test post #test")
        MediaFactory(id=124, accountId=12346, mimetype="image/jpeg")
        AccountMediaFactory(id=124, accountId=12346, mediaId=124)
        AttachmentFactory(
            id=60002,
            postId=201,
            contentId=124,
            contentType=ContentType.ACCOUNT_MEDIA,
            pos=0,
        )

        # Commit factory changes
        factory_async_session.commit()

        # Query fresh objects from async session
        result_account = await session.execute(
            select(Account).where(Account.id == 12346)
        )
        account = result_account.scalar_one()

        result_post = await session.execute(select(Post).where(Post.id == 201))
        post = result_post.unique().scalar_one()

        # Query attachment with eager loading of media relationship
        from metadata.attachment import Attachment

        result_attachment = await session.execute(
            select(Attachment)
            .where(Attachment.id == 60002)
            .options(
                selectinload(Attachment.media).selectinload(AccountMedia.media),
                selectinload(Attachment.bundle),
                selectinload(Attachment.aggregated_post),
            )
        )
        attachment = result_attachment.scalar_one()

        # Create image dict for GraphQL response using fixture
        image_dict = create_image_dict(
            id="601",
            title="Test Image",
            visual_files=[
                {
                    "id": "801",
                    "path": "/path/to/124.jpg",
                    "basename": "124.jpg",
                    "parent_folder_id": "folder_1",
                    "size": 1024000,
                    "width": 1920,
                    "height": 1080,
                    "mod_time": "2024-01-01T00:00:00Z",
                    "fingerprints": [],
                }
            ],
        )

        # Studio fix pattern: Add Fansly parent studio
        fansly_studio_dict = create_studio_dict(
            id="246", name="Fansly (network)", urls=["https://fansly.com"]
        )
        fansly_studio_result = create_find_studios_result(
            count=1, studios=[fansly_studio_dict]
        )

        # Creator studio "not found" (triggers studioCreate)
        creator_not_found_result = create_find_studios_result(count=0, studios=[])

        # Creator studio after creation
        creator_studio_dict = create_studio_dict(
            id="1000",
            name="test_user_2 (Fansly)",
            urls=["https://fansly.com/test_user_2"],
            parent_studio=fansly_studio_dict,
        )

        # Create empty responses for cache checks
        empty_tags_result = {"count": 0, "tags": []}

        # Mock GraphQL responses - use respx route matcher to return appropriate responses
        # This is more flexible than a strict side_effect list
        def graphql_handler(request):  # noqa: PLR0911
            body = json.loads(request.content)
            query = body.get("query", "")
            variables = body.get("variables", {})

            # Return appropriate response based on query type
            if "findImages" in query:
                return httpx.Response(
                    200,
                    json=create_graphql_response(
                        "findImages",
                        {
                            "count": 1,
                            "megapixels": 2.0,
                            "filesize": 1024000.0,
                            "images": [image_dict],
                        },
                    ),
                )
            if "findPerformers" in query:
                return httpx.Response(
                    200,
                    json=create_graphql_response(
                        "findPerformers", {"count": 0, "performers": []}
                    ),
                )
            if "findTags" in query:
                return httpx.Response(
                    200,
                    json=create_graphql_response("findTags", empty_tags_result),
                )
            if "studioCreate" in query:
                return httpx.Response(
                    200,
                    json=create_graphql_response("studioCreate", creator_studio_dict),
                )
            if "imageUpdate" in query:
                return httpx.Response(
                    200,
                    json=create_graphql_response("imageUpdate", image_dict),
                )
            if "findStudios" in query:
                # Check if searching for "Fansly (network)" - return found
                # Otherwise return not found (to trigger studioCreate)
                studio_filter = variables.get("studio_filter", {})
                name_filter = studio_filter.get("name", {})
                search_name = name_filter.get("value", "")

                if search_name == "Fansly (network)" or not studio_filter:
                    # Searching for parent studio or no filter - return Fansly
                    return httpx.Response(
                        200,
                        json=create_graphql_response(
                            "findStudios", fansly_studio_result
                        ),
                    )
                # Searching for creator studio - return not found
                return httpx.Response(
                    200,
                    json=create_graphql_response(
                        "findStudios", creator_not_found_result
                    ),
                )
            # Default response for any other query
            return httpx.Response(200, json={"data": {}})

        graphql_route = respx.post("http://localhost:9999/graphql").mock(
            side_effect=graphql_handler
        )

        # Call process_creator_attachment with queried data - let real code flow execute
        result = await respx_stash_processor.process_creator_attachment(
            attachment=attachment,
            item=post,
            account=account,
        )

        # Verify results
        assert len(result["images"]) == 1
        assert isinstance(result["images"][0], Image)
        assert result["images"][0].id == "601"
        assert len(result["scenes"]) == 0

        # Verify key GraphQL calls were made (less strict on count)
        calls = graphql_route.calls
        request_bodies = [json.loads(call.request.content) for call in calls]

        # Verify findImages was called
        find_images_calls = [
            body for body in request_bodies if "findImages" in body["query"]
        ]
        assert len(find_images_calls) >= 1
        path_pattern = find_images_calls[0]["variables"]["image_filter"]["path"][
            "value"
        ]
        assert "(124)" in path_pattern

        # Verify findPerformers was called
        find_performers_calls = [
            body for body in request_bodies if "findPerformers" in body["query"]
        ]
        assert len(find_performers_calls) >= 1

        # Verify studioCreate was called
        studio_create_calls = [
            body for body in request_bodies if "studioCreate" in body["query"]
        ]
        assert len(studio_create_calls) >= 1
        assert (
            studio_create_calls[0]["variables"]["input"]["name"]
            == "test_user_2 (Fansly)"
        )

        # Verify imageUpdate was called
        image_update_calls = [
            body for body in request_bodies if "imageUpdate" in body["query"]
        ]
        assert len(image_update_calls) >= 1


# No need to import classes directly as they're discovered by pytest
