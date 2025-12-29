"""Tests for batch processing functionality in ContentProcessingMixin.

This test module uses real database fixtures and factories with respx edge mocking
to provide reliable unit testing while letting real code flow execute.
"""

import json
from unittest.mock import AsyncMock, patch

import httpx
import pytest
import respx
from sqlalchemy import insert, select
from sqlalchemy.orm import selectinload

from metadata import Account, AccountMedia, AccountMediaBundle, Attachment, Post
from metadata.account import account_media_bundle_media
from metadata.attachment import ContentType
from tests.fixtures import (
    AccountFactory,
    AccountMediaBundleFactory,
    AccountMediaFactory,
    AttachmentFactory,
    MediaFactory,
    PostFactory,
    create_graphql_response,
)
from tests.fixtures.stash.stash_type_factories import PerformerFactory, StudioFactory


@pytest.mark.asyncio
async def test_process_creator_posts_with_batch_processing(
    factory_async_session, session, respx_stash_processor
):
    """Test process_creator_posts processes galleries correctly."""
    await respx_stash_processor.context.get_client()

    # Create account with factory
    AccountFactory(id=12345, username="test_user", displayName="Test User")

    # Create post with attachment (required by INNER JOIN in process_creator_posts)
    PostFactory(id=200, accountId=12345, content="Test post")
    MediaFactory(id=123, accountId=12345, mimetype="image/jpeg", is_downloaded=True)
    AccountMediaFactory(id=123, accountId=12345, mediaId=123)
    AttachmentFactory(
        id=60001,
        postId=200,  # Link attachment to post
        contentId=123,  # Points to AccountMedia
        contentType=ContentType.ACCOUNT_MEDIA,
        pos=0,
    )

    # Commit factory changes
    factory_async_session.commit()

    # Query fresh from async session
    result = await session.execute(select(Account).where(Account.id == 12345))
    account = result.scalar_one()

    # Create performer and studio using factories
    performer = PerformerFactory.build(id="500", name="test_user")
    studio = StudioFactory.build(id="999", name="test_user (Fansly)")

    # Create minimal GraphQL responses to let batch processing execute
    # This is a UNIT test - we only care that batch processing orchestration works
    from tests.fixtures.stash import create_find_galleries_result, create_gallery_dict

    graphql_route = respx.post("http://localhost:9999/graphql").mock(
        side_effect=[
            # findGalleries - search by code (returns no galleries)
            httpx.Response(
                200,
                json=create_graphql_response(
                    "findGalleries", create_find_galleries_result(count=0, galleries=[])
                ),
            ),
            # findGalleries - search by title (returns no galleries)
            httpx.Response(
                200,
                json=create_graphql_response(
                    "findGalleries", create_find_galleries_result(count=0, galleries=[])
                ),
            ),
            # findGalleries - search by URL (returns no galleries, will create new)
            httpx.Response(
                200,
                json=create_graphql_response(
                    "findGalleries", create_find_galleries_result(count=0, galleries=[])
                ),
            ),
            # galleryCreate - create new gallery
            httpx.Response(
                200,
                json=create_graphql_response(
                    "galleryCreate",
                    create_gallery_dict(
                        id="700",
                        title="test_user - 2025/11/21",
                        code="200",
                        urls=["https://fansly.com/post/200"],
                        studio={"id": "999"},
                        performers=[{"id": "500", "name": "test_user"}],
                    ),
                ),
            ),
        ]
    )

    await respx_stash_processor.process_creator_posts(
        account=account,
        performer=performer,
        studio=studio,
        session=session,
    )

    # Verify GraphQL calls were made in expected sequence
    calls = graphql_route.calls
    assert len(calls) == 4, f"Expected 4 GraphQL calls, got {len(calls)}"

    # Call 0: findGalleries (by code)
    req0 = json.loads(calls[0].request.content)
    assert "findGalleries" in req0["query"]
    assert req0["variables"]["gallery_filter"]["code"]["value"] == "200"
    assert calls[0].response.json()["data"]["findGalleries"]["count"] == 0

    # Call 1: findGalleries (by title)
    req1 = json.loads(calls[1].request.content)
    assert "findGalleries" in req1["query"]
    assert "test_user" in req1["variables"]["gallery_filter"]["title"]["value"]
    assert calls[1].response.json()["data"]["findGalleries"]["count"] == 0

    # Call 2: findGalleries (by URL)
    req2 = json.loads(calls[2].request.content)
    assert "findGalleries" in req2["query"]
    assert (
        req2["variables"]["gallery_filter"]["url"]["value"]
        == "https://fansly.com/post/200"
    )
    assert calls[2].response.json()["data"]["findGalleries"]["count"] == 0

    # Call 3: galleryCreate (create new gallery)
    req3 = json.loads(calls[3].request.content)
    assert "GalleryCreate" in req3["query"]
    assert req3["variables"]["input"]["code"] == "200"
    assert req3["variables"]["input"]["studio_id"] == "999"
    assert req3["variables"]["input"]["performer_ids"] == ["500"]
    assert calls[3].response.json()["data"]["galleryCreate"]["id"] == "700"


@pytest.mark.asyncio
async def test_collect_media_from_attachments_with_aggregated_post(
    factory_async_session,
    session,
    respx_stash_processor,
):
    """Test _collect_media_from_attachments with an aggregated post."""
    # Create account and media
    account = AccountFactory(id=12345, username="test_user")
    media = MediaFactory(id=125, accountId=12345, mimetype="image/jpeg")
    account_media = AccountMediaFactory(id=125, accountId=12345, mediaId=125)

    # Create aggregated post with nested attachment
    agg_post = PostFactory(id=201, accountId=12345, content="Aggregated post")
    nested_attachment = AttachmentFactory(
        id=60003,
        postId=201,  # Link nested attachment to aggregated post
        contentId=125,  # Points to AccountMedia
        contentType=ContentType.ACCOUNT_MEDIA,
        pos=0,
    )

    # Create main post with attachment pointing to aggregated post
    main_post = PostFactory(id=202, accountId=12345, content="Main post")
    main_attachment = AttachmentFactory(
        id=60004,
        postId=202,  # Link attachment to main post
        contentId=201,  # Points to aggregated post
        contentType=ContentType.AGGREGATED_POSTS,
        pos=0,
    )

    # Query fresh from async session with eager loading
    result = await session.execute(
        select(Attachment)
        .where(Attachment.id == 60004)
        .options(
            selectinload(Attachment.aggregated_post)
            .selectinload(Post.attachments)
            .selectinload(Attachment.media)
            .selectinload(AccountMedia.media)
        )
    )
    main_attachment = result.scalar_one()

    # Call the method
    result = await respx_stash_processor._collect_media_from_attachments(
        [main_attachment]
    )

    # Verify nested media was found
    assert len(result) == 1
    assert result[0].id == 125


@pytest.mark.asyncio
async def test_collect_media_from_attachments_with_bundle(
    factory_async_session,
    session,
    respx_stash_processor,
):
    """Test _collect_media_from_attachments with a media bundle."""
    # Create account
    account = AccountFactory(id=12345, username="test_user")

    # Create bundle
    bundle = AccountMediaBundleFactory(id=80001, accountId=12345)

    # Create media for bundle
    media1 = MediaFactory(id=126, accountId=12345, mimetype="image/jpeg")
    preview1 = MediaFactory(id=127, accountId=12345, mimetype="image/jpeg")
    account_media1 = AccountMediaFactory(
        id=126, accountId=12345, mediaId=126, previewId=127
    )

    media2 = MediaFactory(id=128, accountId=12345, mimetype="video/mp4")
    account_media2 = AccountMediaFactory(id=128, accountId=12345, mediaId=128)

    # Create post attachment pointing to bundle BEFORE bundle association
    # This ensures attachment exists before we try to load relationships
    post = PostFactory(id=203, accountId=12345, content="Post with bundle")
    attachment = AttachmentFactory(
        id=60005,
        postId=203,  # Link attachment to post
        contentId=80001,  # Points to bundle
        contentType=ContentType.ACCOUNT_MEDIA_BUNDLE,
        pos=0,
    )

    # Now add media to bundle via association table
    await session.execute(
        insert(account_media_bundle_media).values(
            [
                {"bundle_id": 80001, "media_id": 126, "pos": 0},
                {"bundle_id": 80001, "media_id": 128, "pos": 1},
            ]
        )
    )

    # Flush to ensure all objects are persisted before querying
    await session.flush()

    # Query fresh from async session with eager loading
    result = await session.execute(
        select(Attachment)
        .where(Attachment.id == 60005)
        .options(
            selectinload(Attachment.bundle)
            .selectinload(AccountMediaBundle.accountMedia)
            .selectinload(AccountMedia.media)
        )
    )
    attachment = result.scalar_one()

    # Call the method
    result = await respx_stash_processor._collect_media_from_attachments([attachment])

    # Verify all media was collected
    assert len(result) >= 2  # At least two bundle media (preview is optional)
    media_ids = {m.id for m in result}
    assert 126 in media_ids
    assert 128 in media_ids


@pytest.mark.asyncio
async def test_process_items_with_gallery_error_handling(
    factory_async_session, session, respx_stash_processor
):
    """Test error handling in _process_items_with_gallery."""
    # Create account
    AccountFactory(id=12345, username="test_user", displayName="Test User")

    # Create a working post with attachment
    PostFactory(id=204, accountId=12345, content="Working post #test")
    MediaFactory(id=129, accountId=12345, mimetype="image/jpeg")
    AccountMediaFactory(id=129, accountId=12345, mediaId=129)
    AttachmentFactory(
        id=60006,
        postId=204,  # Link attachment to post
        contentId=129,  # Points to AccountMedia
        contentType=ContentType.ACCOUNT_MEDIA,
        pos=0,
    )
    # Commit factory changes
    factory_async_session.commit()

    # Query fresh from async session
    result = await session.execute(select(Account).where(Account.id == 12345))
    account = result.scalar_one()

    result = await session.execute(
        select(Post).where(Post.id == 204).options(selectinload(Post.attachments))
    )
    working_post = result.unique().scalar_one()

    # Create performer and studio
    performer = PerformerFactory.build(id="500", name="test_user")
    studio = StudioFactory.build(id="999", name="test_user (Fansly)")

    # Patch _process_item_gallery to track calls and verify error handling works
    with patch.object(
        respx_stash_processor, "_process_item_gallery", new=AsyncMock()
    ) as mock_gallery:
        await respx_stash_processor._process_items_with_gallery(
            account=account,
            performer=performer,
            studio=studio,
            item_type="post",
            items=[working_post],
            url_pattern_func=lambda x: f"https://example.com/post/{x.id}",
            session=session,
        )

        # Verify the working post was processed
        assert mock_gallery.call_count == 1
        assert mock_gallery.call_args[1]["item"].id == 204
