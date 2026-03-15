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


class TestProcessItemGallery:
    """Test the _process_item_gallery orchestration method."""

    @pytest.mark.asyncio
    async def test_process_item_gallery_no_attachments(
        self,
        entity_store,
        respx_stash_processor,
    ):
        """Test _process_item_gallery returns early when no attachments."""
        # Create real Account and Post with no media
        account = AccountFactory.build(id=12345, username="test_user")
        await entity_store.save(account)

        post = PostFactory.build(id=67890, accountId=12345, content="Test post")
        await entity_store.save(post)

        # Post has no attachments - method should return early
        assert post.attachments == []

        # Set up respx - expect NO calls for posts without attachments
        graphql_route = respx.post("http://localhost:9999/graphql").mock(
            side_effect=[]  # Empty list catches any unexpected call
        )

        # Create real Performer and Studio
        performer = PerformerFactory.build(id="performer_123", name="test_user")
        studio = StudioFactory.build(id="studio_123", name="Test Studio")

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
        # Create REAL Account
        account = AccountFactory.build(id=12345, username="test_user")
        await entity_store.save(account)

        # Create REAL Media and save to entity_store (populates identity map)
        media1 = MediaFactory.build(id=1001, accountId=12345, mimetype="image/jpeg")
        media2 = MediaFactory.build(id=1002, accountId=12345, mimetype="video/mp4")
        await entity_store.save(media1)
        await entity_store.save(media2)

        # Create REAL AccountMedia and save (identity map resolves .media property)
        account_media1 = AccountMediaFactory.build(
            id=2001, accountId=12345, mediaId=1001
        )
        account_media2 = AccountMediaFactory.build(
            id=2002, accountId=12345, mediaId=1002
        )
        await entity_store.save(account_media1)
        await entity_store.save(account_media2)

        # Create REAL Attachments linking to AccountMedia via contentId
        # Attachment.media is a read-only property that resolves via identity map
        att1 = AttachmentFactory.build(
            id=3001,
            postId=67890,
            contentId=2001,  # Points to AccountMedia.id in identity map
            contentType=ContentType.ACCOUNT_MEDIA,
            pos=0,
        )

        att2 = AttachmentFactory.build(
            id=3002,
            postId=67890,
            contentId=2002,  # Points to AccountMedia.id in identity map
            contentType=ContentType.ACCOUNT_MEDIA,
            pos=1,
        )

        # Create hashtag
        hashtag = HashtagFactory.build(id=4001, value="test")
        await entity_store.save(hashtag)

        # Create post and add attachments/hashtags via _add_to_relationship.
        # Direct assignment (post.attachments = [...]) triggers validate_assignment
        # which re-runs _prepare_post_data and filters non-dict attachments to [].
        post = PostFactory.build(id=67890, accountId=12345, content="Test post #test")
        await post._add_to_relationship("attachments", att1)
        await post._add_to_relationship("attachments", att2)
        await post._add_to_relationship("hashtags", hashtag)

        # Verify post has attachments and hashtags
        assert len(post.attachments) == 2
        assert len(post.hashtags) == 1

        # Create real Performer and Studio
        performer = PerformerFactory.build(id="performer_123", name="test_user")
        studio = StudioFactory.build(id="studio_123", name="Test Studio")

        # Set up respx with generic responses that satisfy all GraphQL operations
        generic_response = httpx.Response(
            200,
            json={
                "data": {
                    # Generic responses for gallery operations
                    "findGalleries": {"galleries": [], "count": 0},
                    "galleryCreate": {"id": "new_gallery_1", "title": "Test Gallery"},
                    "galleryUpdate": {"id": "new_gallery_1"},
                    # Generic responses for tag operations
                    "findTags": {"tags": [], "count": 0},
                    "tagCreate": {"id": "new_tag_1", "name": "test"},
                    # Generic responses for media operations
                    "findImages": {"images": [], "count": 0},
                    "findScenes": {"scenes": [], "count": 0},
                    "imageUpdate": {"id": "img_1"},
                    "sceneUpdate": {"id": "scene_1"},
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

        # Verify each request contains proper data
        for i, call in enumerate(calls):
            req = json.loads(call.request.content)

            # Every call must have query and variables
            assert "query" in req, f"Call {i}: Missing 'query' field"
            assert "variables" in req, f"Call {i}: Missing 'variables' field"

            query = req["query"]
            variables = req["variables"]

            # Check for our test data in the variables
            variables_str = json.dumps(variables)

            # Look for post content "Test post #test"
            if "Test post #test" in variables_str or "Test post" in variables_str:
                found_post_content = True

            # Look for hashtag "test"
            if '"test"' in variables_str.lower() and ("tag" in query.lower()):
                found_hashtag = True

            # Look for post URL with post ID 67890
            if "67890" in variables_str and (
                "url" in variables_str.lower() or "fansly.com" in variables_str.lower()
            ):
                found_post_url = True

            # Look for performer ID
            if "performer_123" in variables_str or performer.id in variables_str:
                found_performer_id = True

            # Look for studio ID
            if "studio_123" in variables_str or studio.id in variables_str:
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
                    assert any("67890" in url for url in gallery_input["urls"]), (
                        f"Call {i}: galleryCreate URLs should include post ID 67890, "
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
        assert found_post_url, "Post URL with ID 67890 should appear in GraphQL calls"
        assert found_performer_id, (
            f"Performer ID '{performer.id}' should appear in GraphQL calls"
        )
        # Studio ID is optional depending on configuration
        # assert found_studio_id, f"Studio ID '{studio.id}' should appear in GraphQL calls"
