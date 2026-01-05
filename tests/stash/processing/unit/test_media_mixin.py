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

        # Create Fansly network studio response
        fansly_studio_dict = create_studio_dict(
            id="246", name="Fansly (network)", urls=[]
        )
        fansly_studio_result = create_find_studios_result(
            count=1, studios=[fansly_studio_dict]
        )

        # Create creator studio response
        creator_studio_dict = create_studio_dict(
            id="999",
            name="test_user (Fansly)",
            urls=["https://fansly.com/test_user"],
        )
        creator_studio_result = create_find_studios_result(
            count=1, studios=[creator_studio_dict]
        )

        # Mock GraphQL responses - chain all responses
        graphql_route = respx.post("http://localhost:9999/graphql").mock(
            side_effect=[
                # findImages - find by path
                httpx.Response(
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
                ),
                # findPerformers - find performer by name (called by _find_existing_performer)
                httpx.Response(
                    200,
                    json=create_graphql_response(
                        "findPerformers", {"count": 0, "performers": []}
                    ),
                ),
                # findPerformers - find performer by aliases (called by _find_existing_performer)
                httpx.Response(
                    200,
                    json=create_graphql_response(
                        "findPerformers", {"count": 0, "performers": []}
                    ),
                ),
                # findStudios - find Fansly network studio (called by process_creator_studio)
                httpx.Response(
                    200,
                    json=create_graphql_response("findStudios", fansly_studio_result),
                ),
                # findStudios - find creator studio (called by process_creator_studio)
                httpx.Response(
                    200,
                    json=create_graphql_response(
                        "findStudios", {"count": 0, "studios": []}
                    ),
                ),
                # studioCreate - create creator studio (called by process_creator_studio)
                httpx.Response(
                    200,
                    json=create_graphql_response("studioCreate", creator_studio_dict),
                ),
                # imageUpdate - update image with metadata
                httpx.Response(
                    200,
                    json=create_graphql_response("imageUpdate", image_dict),
                ),
            ]
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

        # REQUIRED: Verify exact GraphQL call sequence
        calls = graphql_route.calls
        assert len(calls) == 7, f"Expected 7 GraphQL calls, got {len(calls)}"

        # Call 0: findImages (by path filter)
        req0 = json.loads(calls[0].request.content)
        assert "findImages" in req0["query"]
        assert req0["variables"]["image_filter"]["path"]["value"] == "123"
        assert calls[0].response.json()["data"]["findImages"]["count"] == 1

        # Call 1: findPerformers (by name)
        req1 = json.loads(calls[1].request.content)
        assert "findPerformers" in req1["query"]
        assert req1["variables"]["performer_filter"]["name"]["value"] == "test_user"
        assert calls[1].response.json()["data"]["findPerformers"]["count"] == 0

        # Call 2: findPerformers (by aliases)
        req2 = json.loads(calls[2].request.content)
        assert "findPerformers" in req2["query"]
        assert req2["variables"]["performer_filter"]["aliases"]["value"] == "test_user"
        assert calls[2].response.json()["data"]["findPerformers"]["count"] == 0

        # Call 3: findStudios (Fansly network)
        req3 = json.loads(calls[3].request.content)
        assert "findStudios" in req3["query"]
        assert req3["variables"]["filter"]["q"] == "Fansly (network)"
        assert calls[3].response.json()["data"]["findStudios"]["count"] == 1

        # Call 4: findStudios (creator studio)
        req4 = json.loads(calls[4].request.content)
        assert "findStudios" in req4["query"]
        assert req4["variables"]["filter"]["q"] == "test_user (Fansly)"
        assert calls[4].response.json()["data"]["findStudios"]["count"] == 0

        # Call 5: studioCreate (create creator studio)
        req5 = json.loads(calls[5].request.content)
        assert "studioCreate" in req5["query"]
        assert req5["variables"]["input"]["name"] == "test_user (Fansly)"
        assert calls[5].response.json()["data"]["studioCreate"]["id"] == "999"

        # Call 6: imageUpdate (save metadata)
        req6 = json.loads(calls[6].request.content)
        assert "imageUpdate" in req6["query"]
        assert calls[6].response.json()["data"]["imageUpdate"]["id"] == "600"

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

        # Create Fansly network studio response
        fansly_studio_dict = create_studio_dict(
            id="246", name="Fansly (network)", urls=[]
        )
        fansly_studio_result = create_find_studios_result(
            count=1, studios=[fansly_studio_dict]
        )

        # Create creator studio response
        creator_studio_dict = create_studio_dict(
            id="1000",
            name="test_user_2 (Fansly)",
            urls=["https://fansly.com/test_user_2"],
        )

        # Mock GraphQL responses - chain all responses
        graphql_route = respx.post("http://localhost:9999/graphql").mock(
            side_effect=[
                # findImages - find by path (called by _process_media_batch_by_mimetype)
                httpx.Response(
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
                ),
                # findPerformers - find performer by name (called by _find_existing_performer)
                httpx.Response(
                    200,
                    json=create_graphql_response(
                        "findPerformers", {"count": 0, "performers": []}
                    ),
                ),
                # findPerformers - find performer by aliases (called by _find_existing_performer)
                httpx.Response(
                    200,
                    json=create_graphql_response(
                        "findPerformers", {"count": 0, "performers": []}
                    ),
                ),
                # findStudios - find Fansly network studio (called by process_creator_studio)
                httpx.Response(
                    200,
                    json=create_graphql_response("findStudios", fansly_studio_result),
                ),
                # findStudios - find creator studio (called by process_creator_studio)
                httpx.Response(
                    200,
                    json=create_graphql_response(
                        "findStudios", {"count": 0, "studios": []}
                    ),
                ),
                # studioCreate - create creator studio (called by process_creator_studio)
                httpx.Response(
                    200,
                    json=create_graphql_response("studioCreate", creator_studio_dict),
                ),
                # imageUpdate - update image with metadata
                httpx.Response(
                    200,
                    json=create_graphql_response("imageUpdate", image_dict),
                ),
            ]
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

        # REQUIRED: Verify exact GraphQL call sequence
        calls = graphql_route.calls
        assert len(calls) == 7, f"Expected 7 GraphQL calls, got {len(calls)}"

        # Call 0: findImages (by path filter)
        req0 = json.loads(calls[0].request.content)
        assert "findImages" in req0["query"]
        assert req0["variables"]["image_filter"]["path"]["value"] == "124"
        assert calls[0].response.json()["data"]["findImages"]["count"] == 1

        # Call 1: findPerformers (by name)
        req1 = json.loads(calls[1].request.content)
        assert "findPerformers" in req1["query"]
        assert req1["variables"]["performer_filter"]["name"]["value"] == "test_user_2"
        assert calls[1].response.json()["data"]["findPerformers"]["count"] == 0

        # Call 2: findPerformers (by aliases)
        req2 = json.loads(calls[2].request.content)
        assert "findPerformers" in req2["query"]
        assert (
            req2["variables"]["performer_filter"]["aliases"]["value"] == "test_user_2"
        )
        assert calls[2].response.json()["data"]["findPerformers"]["count"] == 0

        # Call 3: findStudios (Fansly network)
        req3 = json.loads(calls[3].request.content)
        assert "findStudios" in req3["query"]
        assert req3["variables"]["filter"]["q"] == "Fansly (network)"
        assert calls[3].response.json()["data"]["findStudios"]["count"] == 1

        # Call 4: findStudios (creator studio)
        req4 = json.loads(calls[4].request.content)
        assert "findStudios" in req4["query"]
        assert req4["variables"]["filter"]["q"] == "test_user_2 (Fansly)"
        assert calls[4].response.json()["data"]["findStudios"]["count"] == 0

        # Call 5: studioCreate (create creator studio)
        req5 = json.loads(calls[5].request.content)
        assert "studioCreate" in req5["query"]
        assert req5["variables"]["input"]["name"] == "test_user_2 (Fansly)"
        assert calls[5].response.json()["data"]["studioCreate"]["id"] == "1000"

        # Call 6: imageUpdate (save metadata)
        req6 = json.loads(calls[6].request.content)
        assert "imageUpdate" in req6["query"]
        assert calls[6].response.json()["data"]["imageUpdate"]["id"] == "601"


# No need to import classes directly as they're discovered by pytest
