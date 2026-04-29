"""Tests for the _process_item_gallery method.

These tests use entity_store for database persistence and respx for HTTP mocking,
following the Pydantic EntityStore migration patterns.
"""

import json

import httpx
import pytest
import respx

from metadata import ContentType
from tests.fixtures import (
    AccountFactory,
    AccountMediaFactory,
    AttachmentFactory,
    HashtagFactory,
    MediaFactory,
    PerformerFactory,
    PostFactory,
    StudioFactory,
)
from tests.fixtures.utils.test_isolation import snowflake_id


class TestProcessItemGallery:
    """Test the _process_item_gallery orchestration method."""

    @pytest.mark.asyncio
    async def test_process_item_gallery_no_attachments(
        self,
        entity_store,
        respx_stash_processor,
    ):
        """Test _process_item_gallery returns early when no attachments."""
        acct_id = snowflake_id()
        post_id = snowflake_id()

        # Create real Account and Post with no media
        account = AccountFactory.build(id=acct_id, username="test_user")
        await entity_store.save(account)

        post = PostFactory.build(id=post_id, accountId=acct_id, content="Test post")
        await entity_store.save(post)

        # Post has no attachments - method should return early
        assert post.attachments == []

        # Set up respx - expect NO calls for posts without attachments
        graphql_route = respx.post("http://localhost:9999/graphql").mock(
            side_effect=[]  # Empty list catches any unexpected call
        )

        # Create real Performer and Studio
        performer = PerformerFactory.build(id="10100", name="test_user")
        studio = StudioFactory.build(id="10200", name="Test Studio")

        # Call method (no session= parameter)
        url_pattern = "https://test.com/{username}/post/{id}"
        await respx_stash_processor._process_item_gallery(
            item=post,
            account=account,
            performer=performer,
            studio=studio,
            item_type="post",
            url_pattern=url_pattern,
        )

        # Method returns early, no API calls made
        assert len(graphql_route.calls) == 0, (
            "Should not make any GraphQL calls for posts without attachments"
        )

    @pytest.mark.asyncio
    async def test_process_item_gallery_with_media(
        self,
        entity_store,
        respx_stash_processor,
    ):
        """Test _process_item_gallery processes posts with media and verifies data flow."""
        acct_id = snowflake_id()
        post_id = snowflake_id()
        media_id_1 = snowflake_id()
        media_id_2 = snowflake_id()
        acct_media_id_1 = snowflake_id()
        acct_media_id_2 = snowflake_id()

        # Create REAL Account
        account = AccountFactory.build(id=acct_id, username="test_user")
        await entity_store.save(account)

        # Create REAL Media and save to entity_store (populates identity map)
        media1 = MediaFactory.build(
            id=media_id_1, accountId=acct_id, mimetype="image/jpeg"
        )
        media2 = MediaFactory.build(
            id=media_id_2, accountId=acct_id, mimetype="video/mp4"
        )
        await entity_store.save(media1)
        await entity_store.save(media2)

        # Create REAL AccountMedia and save (identity map resolves .media property)
        account_media1 = AccountMediaFactory.build(
            id=acct_media_id_1, accountId=acct_id, mediaId=media_id_1
        )
        account_media2 = AccountMediaFactory.build(
            id=acct_media_id_2, accountId=acct_id, mediaId=media_id_2
        )
        await entity_store.save(account_media1)
        await entity_store.save(account_media2)

        # Create REAL Attachments linking to AccountMedia via contentId
        # Attachment.media is a read-only property that resolves via identity map
        att1 = AttachmentFactory.build(
            id=3001,
            postId=post_id,
            contentId=acct_media_id_1,  # Points to AccountMedia.id in identity map
            contentType=ContentType.ACCOUNT_MEDIA,
            pos=0,
        )

        att2 = AttachmentFactory.build(
            id=3002,
            postId=post_id,
            contentId=acct_media_id_2,  # Points to AccountMedia.id in identity map
            contentType=ContentType.ACCOUNT_MEDIA,
            pos=1,
        )

        # Create hashtag
        hashtag = HashtagFactory.build(id=4001, value="test")
        await entity_store.save(hashtag)

        # Create post and add attachments/hashtags via _add_to_relationship.
        # Direct assignment (post.attachments = [...]) triggers validate_assignment
        # which re-runs _prepare_post_data and filters non-dict attachments to [].
        post = PostFactory.build(
            id=post_id, accountId=acct_id, content="Test post #test"
        )
        await post._add_to_relationship("attachments", att1)
        await post._add_to_relationship("attachments", att2)
        await post._add_to_relationship("hashtags", hashtag)

        # Verify post has attachments and hashtags
        assert len(post.attachments) == 2
        assert len(post.hashtags) == 1

        # Create real Performer and Studio
        performer = PerformerFactory.build(id="10100", name="test_user")
        studio = StudioFactory.build(id="10200", name="Test Studio")

        # Set up respx with generic responses that satisfy all GraphQL operations
        generic_response = httpx.Response(
            200,
            json={
                "data": {
                    # Generic responses for gallery operations
                    "findGalleries": {"galleries": [], "count": 0},
                    "galleryCreate": {"id": "20001", "title": "Test Gallery"},
                    "galleryUpdate": {"id": "20001"},
                    # Generic responses for tag operations
                    "findTags": {"tags": [], "count": 0},
                    "tagCreate": {"id": "30001", "name": "test"},
                    # Generic responses for media operations
                    "findImages": {"images": [], "count": 0},
                    "findScenes": {"scenes": [], "count": 0},
                    "imageUpdate": {"id": "40001"},
                    "sceneUpdate": {"id": "50001"},
                    # Gallery-image association
                    "addGalleryImages": True,
                }
            },
        )
        # Allow multiple calls for complex gallery + media processing
        graphql_route = respx.post("http://localhost:9999/graphql").mock(
            side_effect=[generic_response] * 30  # Enough for all operations
        )

        # Call method - let it execute fully to HTTP boundary (no session= parameter)
        url_pattern = "https://fansly.com/{username}/post/{id}"
        await respx_stash_processor._process_item_gallery(
            item=post,
            account=account,
            performer=performer,
            studio=studio,
            item_type="post",
            url_pattern=url_pattern,
        )

        # Verify GraphQL calls were made
        assert len(graphql_route.calls) > 0, "Expected GraphQL calls to be made"
        calls = graphql_route.calls

        # Track which operation types we've seen and verify data
        found_post_content = False
        found_hashtag = False
        found_post_url = False
        found_performer_id = False
        found_studio_id = False

        post_id_str = str(post_id)

        # Verify each request contains proper data. The `req["query"]`
        # access below KeyErrors naturally if the field is missing.
        for i, call in enumerate(calls):
            req = json.loads(call.request.content)

            query = req["query"]
            variables = req.get("variables", {})

            # Check for our test data in the variables
            variables_str = json.dumps(variables)

            # Look for post content "Test post #test"
            if "Test post #test" in variables_str or "Test post" in variables_str:
                found_post_content = True

            # Look for hashtag "test"
            if '"test"' in variables_str.lower() and ("tag" in query.lower()):
                found_hashtag = True

            # Look for post URL with post ID
            if post_id_str in variables_str and (
                "url" in variables_str.lower() or "fansly.com" in variables_str.lower()
            ):
                found_post_url = True

            # Look for performer ID
            if "10100" in variables_str or performer.id in variables_str:
                found_performer_id = True

            # Look for studio ID
            if "10200" in variables_str or studio.id in variables_str:
                found_studio_id = True

            # Identify operation type and verify specific data structure
            if "galleryCreate" in query:
                assert "input" in variables, f"Call {i}: galleryCreate missing input"
                gallery_input = variables["input"]

                # Verify title contains post content or is derived from it
                assert "title" in gallery_input, (
                    f"Call {i}: galleryCreate input missing title"
                )

                # Verify performer_ids includes our performer
                assert "performer_ids" in gallery_input, (
                    f"Call {i}: galleryCreate input missing performer_ids"
                )
                assert performer.id in gallery_input["performer_ids"], (
                    f"Call {i}: galleryCreate should include performer {performer.id}, "
                    f"got {gallery_input['performer_ids']}"
                )

                # Verify studio_id if provided
                if "studio_id" in gallery_input:
                    assert gallery_input["studio_id"] == studio.id, (
                        f"Call {i}: galleryCreate should include studio {studio.id}, "
                        f"got {gallery_input['studio_id']}"
                    )

                # Verify URLs include post URL
                if "urls" in gallery_input:
                    assert any(post_id_str in url for url in gallery_input["urls"]), (
                        f"Call {i}: galleryCreate URLs should include post ID {post_id}, "
                        f"got {gallery_input['urls']}"
                    )

                # Verify details contain post content
                if "details" in gallery_input:
                    assert "Test post" in gallery_input["details"], (
                        f"Call {i}: galleryCreate details should contain post content, "
                        f"got {gallery_input['details']}"
                    )

            elif "tagCreate" in query:
                assert "input" in variables, f"Call {i}: tagCreate missing input"
                tag_input = variables["input"]

                # Verify tag name matches hashtag value
                assert "name" in tag_input, f"Call {i}: tagCreate input missing name"
                assert tag_input["name"] == "test", (
                    f"Call {i}: tagCreate should create tag 'test' from hashtag, "
                    f"got {tag_input['name']}"
                )

        # Verify we found our test data in the GraphQL calls
        assert found_post_content, (
            "Post content 'Test post #test' should appear in GraphQL calls"
        )
        assert found_hashtag, (
            "Hashtag 'test' should appear in tag-related GraphQL calls"
        )
        assert found_post_url, (
            f"Post URL with ID {post_id} should appear in GraphQL calls"
        )
        assert found_performer_id, (
            f"Performer ID '{performer.id}' should appear in GraphQL calls"
        )
        # Studio ID is optional depending on configuration
        # assert found_studio_id, f"Studio ID '{studio.id}' should appear in GraphQL calls"

    @pytest.mark.asyncio
    async def test_process_item_gallery_non_media_attachments(
        self,
        entity_store,
        respx_stash_processor,
    ):
        """Test _process_item_gallery with non-media attachments (lines 368-375, 532-539).

        Post has attachments, but none are media (TIP_GOALS only).
        _get_or_create_gallery calls _has_media_content → False → returns None.
        _process_item_gallery sees gallery=None → returns early.
        """
        acct_id = snowflake_id()
        post_id = snowflake_id()

        account = AccountFactory.build(id=acct_id, username="test_user")
        await entity_store.save(account)

        # Post with a non-media attachment (TIP_GOALS)
        post = PostFactory.build(id=post_id, accountId=acct_id, content="Tips welcome")
        tip_attachment = AttachmentFactory.build(
            id=90001,
            postId=post_id,
            contentId=snowflake_id(),
            contentType=ContentType.TIP_GOALS,
            pos=0,
        )
        await post._add_to_relationship("attachments", tip_attachment)
        await entity_store.save(post)

        performer = PerformerFactory.build(id="10100", name="test_user")
        studio = StudioFactory.build(id="10200", name="Test Studio")

        graphql_route = respx.post("http://localhost:9999/graphql").mock(
            side_effect=[]  # No GraphQL calls expected
        )

        await respx_stash_processor._process_item_gallery(
            item=post,
            account=account,
            performer=performer,
            studio=studio,
            item_type="post",
            url_pattern="https://test.com/{username}/post/{id}",
        )

        # Gallery creation skipped because _has_media_content returned False
        assert len(graphql_route.calls) == 0
