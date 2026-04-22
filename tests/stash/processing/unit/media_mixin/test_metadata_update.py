"""Tests for metadata update methods in MediaProcessingMixin."""

import json
from datetime import UTC, datetime

import httpx
import pytest
import respx

from tests.fixtures.metadata.metadata_factories import PostFactory
from tests.fixtures.stash.stash_graphql_fixtures import (
    create_find_performers_result,
    create_find_studios_result,
    create_find_tags_result,
    create_graphql_response,
    create_performer_dict,
    create_studio_dict,
    create_tag_dict,
)
from tests.fixtures.stash.stash_type_factories import PerformerFactory
from tests.fixtures.utils.test_isolation import snowflake_id


class TestMetadataUpdate:
    """Test metadata update methods in MediaProcessingMixin."""

    @pytest.mark.asyncio
    async def test_update_stash_metadata_basic(
        self, respx_stash_processor, mock_item, mock_account, mock_image
    ):
        """Test _update_stash_metadata method with basic metadata.

        Unit test using respx to mock Stash GraphQL HTTP responses.
        Tests the complete metadata update flow.
        """
        # Mock GraphQL HTTP responses - v0.10.3 pattern:
        # Expected GraphQL call sequence:
        # 1. findPerformers - _find_existing_performer finds performer
        # 2. findStudios - _find_existing_studio finds Fansly studio
        # 3. findStudios - look for creator studio (not found)
        # 4. studioCreate - create creator studio
        # 5. imageUpdate - stash_obj.save() persists updated metadata

        # Response 1: findPerformers - performer found
        performer_dict = create_performer_dict(
            id=str(mock_account.stash_id or "123"),
            name=mock_account.username,
        )
        performers_result = create_find_performers_result(
            count=1, performers=[performer_dict]
        )

        # Response 2: findStudios - Fansly network studio exists
        fansly_studio = create_studio_dict(
            id="fansly_246", name="Fansly (network)", urls=["https://fansly.com"]
        )
        fansly_result = create_find_studios_result(count=1, studios=[fansly_studio])

        # Response 3: findStudios - creator studio not found
        creator_not_found_result = create_find_studios_result(count=0, studios=[])

        # Response 4: studioCreate - create creator studio
        creator_studio = create_studio_dict(
            id="creator_123",
            name=f"{mock_account.username} (Fansly)",
            urls=[f"https://fansly.com/{mock_account.username}"],
            parent_studio=fansly_studio,
        )

        # Response 5: imageUpdate - save updated image
        image_update_result = {
            "id": mock_image.id,
            "title": "Test title",
            "code": "media_123",
            "date": mock_item.createdAt.strftime("%Y-%m-%d"),
            "details": mock_item.content,
        }

        graphql_route = respx.post("http://localhost:9999/graphql").mock(
            side_effect=[
                httpx.Response(
                    200,
                    json=create_graphql_response("findPerformers", performers_result),
                ),
                httpx.Response(
                    200, json=create_graphql_response("findStudios", fansly_result)
                ),
                httpx.Response(
                    200,
                    json=create_graphql_response(
                        "findStudios", creator_not_found_result
                    ),
                ),
                httpx.Response(
                    200, json=create_graphql_response("studioCreate", creator_studio)
                ),
                httpx.Response(
                    200,
                    json=create_graphql_response("imageUpdate", image_update_result),
                ),
            ]
        )

        # Call method - real internal methods execute with respx mocking HTTP boundary
        await respx_stash_processor._update_stash_metadata(
            stash_obj=mock_image,
            item=mock_item,
            account=mock_account,
            media_id="media_123",
        )

        # Verify basic metadata was set (check RESULTS, not mock calls)
        assert mock_image.details == mock_item.content
        assert mock_image.date == mock_item.createdAt.strftime("%Y-%m-%d")
        assert mock_image.code == "media_123"
        # Title should be set by real _generate_title_from_content method
        assert mock_image.title is not None
        assert len(mock_image.title) > 0

        # Verify URL was added (since item is a Post)
        assert f"https://fansly.com/post/{mock_item.id}" in mock_image.urls

        # Verify GraphQL call sequence (permanent assertion)
        assert len(graphql_route.calls) == 5, "Expected exactly 5 GraphQL calls"
        calls = graphql_route.calls

        # Verify query types in order
        assert "findPerformers" in json.loads(calls[0].request.content)["query"]
        assert "findStudios" in json.loads(calls[1].request.content)["query"]
        assert "findStudios" in json.loads(calls[2].request.content)["query"]
        assert "studioCreate" in json.loads(calls[3].request.content)["query"]
        assert "imageUpdate" in json.loads(calls[4].request.content)["query"]

    @pytest.mark.asyncio
    async def test_update_stash_metadata_already_organized(
        self, respx_stash_processor, mock_item, mock_account, mock_image
    ):
        """Test _update_stash_metadata method with already organized object.

        Unit test - when image is already organized, method exits early with no GraphQL calls.
        """
        # Mark as already organized and save original values
        mock_image.organized = True
        original_title = mock_image.title
        original_code = mock_image.code
        original_details = mock_image.details

        # No respx mocks needed - method should exit early without any GraphQL calls

        # Call method
        await respx_stash_processor._update_stash_metadata(
            stash_obj=mock_image,
            item=mock_item,
            account=mock_account,
            media_id="media_123",
        )

        # Verify metadata was NOT updated (values unchanged)
        assert mock_image.title == original_title
        assert mock_image.code == original_code
        assert mock_image.details == original_details

        # Test 2: Bad title "Media from" overrides organized check (line 461)
        mock_image.title = "Media from old batch"
        mock_image.organized = True

        performer_dict = create_performer_dict(
            id=str(mock_account.stash_id or "123"),
            name=mock_account.username,
        )
        fansly_studio = create_studio_dict(
            id="fansly_246", name="Fansly (network)", urls=["https://fansly.com"]
        )
        creator_studio = create_studio_dict(
            id="creator_123",
            name=f"{mock_account.username} (Fansly)",
            parent_studio=fansly_studio,
        )
        graphql_route = respx.post("http://localhost:9999/graphql").mock(
            side_effect=[
                httpx.Response(
                    200,
                    json=create_graphql_response(
                        "findPerformers",
                        create_find_performers_result(
                            count=1, performers=[performer_dict]
                        ),
                    ),
                ),
                httpx.Response(
                    200,
                    json=create_graphql_response(
                        "findStudios",
                        create_find_studios_result(count=1, studios=[fansly_studio]),
                    ),
                ),
                httpx.Response(
                    200,
                    json=create_graphql_response(
                        "findStudios", create_find_studios_result(count=0, studios=[])
                    ),
                ),
                httpx.Response(
                    200, json=create_graphql_response("studioCreate", creator_studio)
                ),
                httpx.Response(
                    200,
                    json=create_graphql_response("imageUpdate", {"id": mock_image.id}),
                ),
            ]
        )

        await respx_stash_processor._update_stash_metadata(
            stash_obj=mock_image,
            item=mock_item,
            account=mock_account,
            media_id="media_123",
        )

        # Bad title forced full update despite organized=True
        assert graphql_route.call_count == 5
        assert mock_image.title != "Media from old batch"  # Title was replaced

    @pytest.mark.asyncio
    async def test_update_stash_metadata_later_date(
        self, respx_stash_processor, mock_item, mock_account, mock_image
    ):
        """Test _update_stash_metadata preserves earliest metadata.

        The method should SKIP updates when the new item is LATER than existing,
        to preserve the earliest occurrence's metadata.
        Also covers invalid date format (line 493-494) and bad title (line 461).
        """
        # Test 1: Item is LATER than existing date - should NOT update
        mock_image.date = "2024-03-01"  # Earlier date already stored
        original_title = mock_image.title  # Save original
        original_code = mock_image.code

        # mock_item has createdAt = 2024-04-01 (later than 2024-03-01)
        # No respx mocks needed - method exits early when date is later
        await respx_stash_processor._update_stash_metadata(
            stash_obj=mock_image,
            item=mock_item,
            account=mock_account,
            media_id="media_123",
        )

        # Verify metadata was NOT updated (item is later, keep earliest)
        assert mock_image.title == original_title  # Title unchanged
        assert mock_image.date == "2024-03-01"  # Date unchanged
        assert mock_image.code == original_code  # Code unchanged

        # Shared GraphQL responses for Test 1b and Test 2 (both go through full update)
        performer_dict = create_performer_dict(
            id=str(mock_account.stash_id or "123"),
            name=mock_account.username,
        )
        performers_result = create_find_performers_result(
            count=1, performers=[performer_dict]
        )

        fansly_studio = create_studio_dict(
            id="fansly_246", name="Fansly (network)", urls=["https://fansly.com"]
        )
        fansly_result = create_find_studios_result(count=1, studios=[fansly_studio])

        creator_not_found_result = create_find_studios_result(count=0, studios=[])

        creator_studio = create_studio_dict(
            id="creator_123",
            name=f"{mock_account.username} (Fansly)",
            urls=[f"https://fansly.com/{mock_account.username}"],
            parent_studio=fansly_studio,
        )

        image_update_result = {
            "id": mock_image.id,
            "title": "Test",
            "code": "media_123",
            "date": mock_item.createdAt.strftime("%Y-%m-%d"),
            "details": mock_item.content,
        }

        def _make_update_responses():
            """5 responses for a full metadata update path."""
            return [
                httpx.Response(
                    200,
                    json=create_graphql_response("findPerformers", performers_result),
                ),
                httpx.Response(
                    200, json=create_graphql_response("findStudios", fansly_result)
                ),
                httpx.Response(
                    200,
                    json=create_graphql_response(
                        "findStudios", creator_not_found_result
                    ),
                ),
                httpx.Response(
                    200, json=create_graphql_response("studioCreate", creator_studio)
                ),
                httpx.Response(
                    200,
                    json=create_graphql_response("imageUpdate", image_update_result),
                ),
            ]

        # Test 1b: Invalid date string → ValueError caught, proceeds to update (line 493-494)
        mock_image.date = "not-a-valid-date"
        mock_image.organized = False

        graphql_route = respx.post("http://localhost:9999/graphql").mock(
            side_effect=_make_update_responses()
        )

        await respx_stash_processor._update_stash_metadata(
            stash_obj=mock_image,
            item=mock_item,
            account=mock_account,
            media_id="media_123",
        )

        # Invalid date was parsed, ValueError caught, update proceeded through full path
        assert len(graphql_route.calls) == 5, "Expected exactly 5 GraphQL calls"
        calls = graphql_route.calls
        assert "findPerformers" in json.loads(calls[0].request.content)["query"]
        assert "findStudios" in json.loads(calls[1].request.content)["query"]
        assert "findStudios" in json.loads(calls[2].request.content)["query"]
        assert "studioCreate" in json.loads(calls[3].request.content)["query"]
        assert "imageUpdate" in json.loads(calls[4].request.content)["query"]

        # Test 2: Item is EARLIER than existing date - should UPDATE
        # Performer and studios are now cached from Test 1b, so only imageUpdate needed
        respx.reset()
        mock_image.date = "2024-05-01"  # Later date in storage

        earlier_item = PostFactory.build(
            id=snowflake_id(),
            accountId=mock_account.id,
            content="Earlier content",
            createdAt=datetime(2024, 3, 1, 0, 0, 0, tzinfo=UTC),  # Earlier!
        )
        earlier_item.hashtags = []
        earlier_item.mentions = []

        graphql_route = respx.post("http://localhost:9999/graphql").mock(
            side_effect=[
                httpx.Response(
                    200,
                    json=create_graphql_response("imageUpdate", image_update_result),
                ),
            ]
        )

        # Call method with earlier item
        await respx_stash_processor._update_stash_metadata(
            stash_obj=mock_image,
            item=earlier_item,
            account=mock_account,
            media_id="media_456",
        )

        # Verify metadata WAS updated (item is earlier, replace with earlier)
        assert mock_image.date == "2024-03-01"  # Updated to earlier date
        assert mock_image.code == "media_456"  # Updated
        assert mock_image.details == "Earlier content"  # Updated

        # Performer and studios cached from Test 1b — only imageUpdate call needed
        assert graphql_route.call_count == 1
        assert (
            "imageUpdate" in json.loads(graphql_route.calls[0].request.content)["query"]
        )

    @pytest.mark.asyncio
    async def test_update_stash_metadata_performers(
        self, respx_stash_processor, mock_item, mock_account, mock_image
    ):
        """Test _update_stash_metadata method with performers.

        Unit test using respx - tests performer lookup and creation for account mentions.
        PostMention objects (not Account) are used since Pydantic migration changed
        the mentions relationship from Account objects to PostMention objects.
        """
        from metadata import PostMention

        mention1 = PostMention(
            id=22222,
            postId=mock_item.id,
            handle="mention_user1",
        )
        mention2 = PostMention(
            id=33333,
            postId=mock_item.id,
            handle="mention_user2",
        )
        mock_item.mentions = [mention1, mention2]

        # Mock GraphQL HTTP responses - 8 sequential calls:
        # 1: findPerformers for main account (by name)
        # 2: findPerformers for mention1 (by name)
        # 3: findPerformers for mention2 (by name - not found)
        # 4: findPerformers for mention2 (by alias - not found, triggers create)
        # 5: performerCreate for mention2
        # 6: findStudios for Fansly (network)
        # 7: findStudios for creator studio
        # 8: imageUpdate

        # Response 1: findPerformers for main account (found)
        main_performer_dict = create_performer_dict(
            id="123",
            name=mock_account.username,
        )
        main_performers_result = create_find_performers_result(
            count=1, performers=[main_performer_dict]
        )

        # Response 2: findPerformers for mention1 (found)
        mention1_performer_dict = create_performer_dict(
            id="456",
            name=mention1.handle,
        )
        mention1_performers_result = create_find_performers_result(
            count=1, performers=[mention1_performer_dict]
        )

        # Response 3: findPerformers for mention2 by name (not found)
        empty_performers_name = create_find_performers_result(count=0, performers=[])

        # Response 4: findPerformers for mention2 by alias (not found)
        empty_performers_alias = create_find_performers_result(count=0, performers=[])

        # Response 5: findPerformers for mention2 by URL (not found)
        empty_performers_url = create_find_performers_result(count=0, performers=[])

        # Response 6: performerCreate for mention2
        new_performer = create_performer_dict(
            id="789",
            name=mention2.handle,
        )

        # Response 7: findStudios for Fansly (network)
        fansly_studio = create_studio_dict(
            id="fansly_246", name="Fansly (network)", urls=["https://fansly.com"]
        )
        fansly_result = create_find_studios_result(count=1, studios=[fansly_studio])

        # Response 8: findStudios for creator studio (not found)
        creator_not_found_result = create_find_studios_result(count=0, studios=[])

        # Response 9: studioCreate for creator studio
        creator_studio = create_studio_dict(
            id="creator_123",
            name=f"{mock_account.username} (Fansly)",
            urls=[f"https://fansly.com/{mock_account.username}"],
            parent_studio=fansly_studio,
        )

        # Response 10: imageUpdate
        image_update_result = {
            "id": mock_image.id,
            "title": mock_image.title,
            "code": "media_123",
        }

        graphql_route = respx.post("http://localhost:9999/graphql").mock(
            side_effect=[
                httpx.Response(
                    200,
                    json=create_graphql_response(
                        "findPerformers", main_performers_result
                    ),
                ),
                httpx.Response(
                    200,
                    json=create_graphql_response(
                        "findPerformers", mention1_performers_result
                    ),
                ),
                httpx.Response(
                    200,
                    json=create_graphql_response(
                        "findPerformers", empty_performers_name
                    ),
                ),
                httpx.Response(
                    200,
                    json=create_graphql_response(
                        "findPerformers", empty_performers_alias
                    ),
                ),
                httpx.Response(
                    200,
                    json=create_graphql_response(
                        "findPerformers", empty_performers_url
                    ),
                ),
                httpx.Response(
                    200, json=create_graphql_response("performerCreate", new_performer)
                ),
                httpx.Response(
                    200, json=create_graphql_response("findStudios", fansly_result)
                ),
                httpx.Response(
                    200,
                    json=create_graphql_response(
                        "findStudios", creator_not_found_result
                    ),
                ),
                httpx.Response(
                    200, json=create_graphql_response("studioCreate", creator_studio)
                ),
                httpx.Response(
                    200,
                    json=create_graphql_response("imageUpdate", image_update_result),
                ),
            ]
        )

        # Call method - real _find_existing_performer runs with real GraphQL mocking
        await respx_stash_processor._update_stash_metadata(
            stash_obj=mock_image,
            item=mock_item,
            account=mock_account,
            media_id="media_123",
        )

        # Verify performers were added (check RESULTS)
        assert len(mock_image.performers) == 3
        # Verify performers have correct names
        # Performers are Pydantic models from stash-graphql-client
        performer_names = [p.name for p in mock_image.performers]
        assert mock_account.username in performer_names
        # PostMention uses handle as the performer name
        assert any(mention1.handle in name for name in performer_names)
        assert any(mention2.handle in name for name in performer_names)

        # Verify GraphQL call sequence (permanent assertion)
        assert len(graphql_route.calls) == 10, "Expected exactly 10 GraphQL calls"
        calls = graphql_route.calls

        # Verify query types in order
        assert "findPerformers" in json.loads(calls[0].request.content)["query"]
        assert "findPerformers" in json.loads(calls[1].request.content)["query"]
        assert "findPerformers" in json.loads(calls[2].request.content)["query"]
        assert "findPerformers" in json.loads(calls[3].request.content)["query"]
        assert "findPerformers" in json.loads(calls[4].request.content)["query"]
        assert "performerCreate" in json.loads(calls[5].request.content)["query"]
        assert "findStudios" in json.loads(calls[6].request.content)["query"]
        assert "findStudios" in json.loads(calls[7].request.content)["query"]
        assert "studioCreate" in json.loads(calls[8].request.content)["query"]
        assert "imageUpdate" in json.loads(calls[9].request.content)["query"]

    @pytest.mark.asyncio
    async def test_update_stash_metadata_studio(
        self, respx_stash_processor, mock_item, mock_account, mock_image
    ):
        """Test _update_stash_metadata method with studio.

        Unit test using respx - tests studio lookup for Fansly network and creator studio.
        """
        # Mock GraphQL HTTP responses - v0.10.3 pattern (4 sequential calls):
        # 1: findPerformers for main account (by name only - not found)
        # 2: findStudios for Fansly (network) - found
        # 3: studioCreate for creator studio (Pattern 1: get_or_create creates immediately)
        # 4: imageUpdate

        # Response 1: findPerformers by name - not found (focus on studio test)
        empty_performers = create_find_performers_result(count=0, performers=[])

        # Response 2: findStudios for Fansly (network) - found
        fansly_studio = create_studio_dict(
            id="fansly_246", name="Fansly (network)", urls=["https://fansly.com"]
        )
        fansly_result = create_find_studios_result(count=1, studios=[fansly_studio])

        # Response 3: findStudios for creator studio - not found
        creator_not_found_result = create_find_studios_result(count=0, studios=[])

        # Response 4: studioCreate for creator studio
        creator_studio = create_studio_dict(
            id="studio_123",
            name=f"{mock_account.username} (Fansly)",
            urls=[f"https://fansly.com/{mock_account.username}"],
            parent_studio=fansly_studio,
        )

        # Response 5: imageUpdate
        image_update_result = {
            "id": mock_image.id,
            "title": mock_image.title,
            "code": "media_123",
        }

        graphql_route = respx.post("http://localhost:9999/graphql").mock(
            side_effect=[
                httpx.Response(
                    200,
                    json=create_graphql_response("findPerformers", empty_performers),
                ),
                httpx.Response(
                    200, json=create_graphql_response("findStudios", fansly_result)
                ),
                httpx.Response(
                    200,
                    json=create_graphql_response(
                        "findStudios", creator_not_found_result
                    ),
                ),
                httpx.Response(
                    200, json=create_graphql_response("studioCreate", creator_studio)
                ),
                httpx.Response(
                    200,
                    json=create_graphql_response("imageUpdate", image_update_result),
                ),
            ]
        )

        # Call method - real _find_existing_studio runs with respx mocking HTTP boundary
        await respx_stash_processor._update_stash_metadata(
            stash_obj=mock_image,
            item=mock_item,
            account=mock_account,
            media_id="media_123",
        )

        # Verify studio was set (check RESULTS)
        assert mock_image.studio is not None
        assert mock_image.studio.name == f"{mock_account.username} (Fansly)"

        # Verify GraphQL call sequence (permanent assertion)
        assert len(graphql_route.calls) == 5, "Expected exactly 5 GraphQL calls"
        calls = graphql_route.calls

        # Verify query types in order
        assert "findPerformers" in json.loads(calls[0].request.content)["query"]
        assert "findStudios" in json.loads(calls[1].request.content)["query"]
        assert "findStudios" in json.loads(calls[2].request.content)["query"]
        assert "studioCreate" in json.loads(calls[3].request.content)["query"]
        assert "imageUpdate" in json.loads(calls[4].request.content)["query"]

    @pytest.mark.asyncio
    async def test_update_stash_metadata_tags(
        self, respx_stash_processor, mock_item, mock_account, mock_image
    ):
        """Test _update_stash_metadata method with tags.

        Unit test using respx - tests hashtag to tag conversion.
        """
        # Create real hashtag objects using HashtagFactory
        from tests.fixtures.metadata.metadata_factories import HashtagFactory

        hashtag1 = HashtagFactory.build(value="test_tag")
        hashtag2 = HashtagFactory.build(value="another_tag")
        mock_item.hashtags = [hashtag1, hashtag2]

        # Mock GraphQL HTTP responses - v0.10.3 pattern (6 sequential calls):
        # 1: findPerformers (by name only - account lookup)
        # 2: findStudios for Fansly (network)
        # 3: studioCreate (Pattern 1: get_or_create creates immediately)
        # 4-5: findTags (one per hashtag - tags processed AFTER studio)
        # 6: imageUpdate

        # Response 1: findPerformers (by name - not found)
        empty_performers = create_find_performers_result(count=0, performers=[])

        # Response 2: findStudios for Fansly (network)
        fansly_studio = create_studio_dict(
            id="fansly_246", name="Fansly (network)", urls=["https://fansly.com"]
        )
        fansly_result = create_find_studios_result(count=1, studios=[fansly_studio])

        # Response 3: findStudios for creator studio - not found
        creator_not_found_result = create_find_studios_result(count=0, studios=[])

        # Response 4: studioCreate for creator studio
        creator_studio = create_studio_dict(
            id="studio_123",
            name=f"{mock_account.username} (Fansly)",
            urls=[f"https://fansly.com/{mock_account.username}"],
            parent_studio=fansly_studio,
        )

        # Response 5-6: findTags for each hashtag
        tag1 = create_tag_dict(id="tag_123", name="test_tag")
        tag1_result = create_find_tags_result(count=1, tags=[tag1])

        tag2 = create_tag_dict(id="tag_456", name="another_tag")
        tag2_result = create_find_tags_result(count=1, tags=[tag2])

        # Response 7: imageUpdate
        image_update_result = {
            "id": mock_image.id,
            "title": mock_image.title,
            "code": "media_123",
        }

        graphql_route = respx.post("http://localhost:9999/graphql").mock(
            side_effect=[
                httpx.Response(
                    200,
                    json=create_graphql_response("findPerformers", empty_performers),
                ),
                httpx.Response(
                    200, json=create_graphql_response("findStudios", fansly_result)
                ),
                httpx.Response(
                    200,
                    json=create_graphql_response(
                        "findStudios", creator_not_found_result
                    ),
                ),
                httpx.Response(
                    200, json=create_graphql_response("studioCreate", creator_studio)
                ),
                httpx.Response(
                    200, json=create_graphql_response("findTags", tag1_result)
                ),
                httpx.Response(
                    200, json=create_graphql_response("findTags", tag2_result)
                ),
                httpx.Response(
                    200,
                    json=create_graphql_response("imageUpdate", image_update_result),
                ),
            ]
        )

        # Call method - real _process_hashtags_to_tags runs with respx mocking HTTP boundary
        await respx_stash_processor._update_stash_metadata(
            stash_obj=mock_image,
            item=mock_item,
            account=mock_account,
            media_id="media_123",
        )

        # Verify tags were set (check RESULTS)
        assert len(mock_image.tags) == 2
        tag_names = [t.name for t in mock_image.tags]
        assert "test_tag" in tag_names
        assert "another_tag" in tag_names

        # Verify GraphQL call sequence (permanent assertion)
        assert len(graphql_route.calls) == 7, "Expected exactly 7 GraphQL calls"
        calls = graphql_route.calls

        # Verify query types in order
        assert "findPerformers" in json.loads(calls[0].request.content)["query"]
        assert "findStudios" in json.loads(calls[1].request.content)["query"]
        assert "findStudios" in json.loads(calls[2].request.content)["query"]
        assert "studioCreate" in json.loads(calls[3].request.content)["query"]
        assert "findTags" in json.loads(calls[4].request.content)["query"]
        assert "findTags" in json.loads(calls[5].request.content)["query"]
        assert "imageUpdate" in json.loads(calls[6].request.content)["query"]

    @pytest.mark.asyncio
    async def test_update_stash_metadata_preview(
        self, respx_stash_processor, mock_item, mock_account, mock_image
    ):
        """Test _update_stash_metadata method with preview flag.

        Unit test using respx - tests that is_preview=True adds "Trailer" tag.
        """
        # Mock GraphQL HTTP responses - v0.10.3 pattern (5 sequential calls):
        # 1: findPerformers (by name only - account lookup)
        # 2: findStudios for Fansly (network)
        # 3: studioCreate (Pattern 1: get_or_create creates immediately)
        # 4: findTags for "Trailer" tag
        # 5: imageUpdate

        # Response 1: findPerformers (by name - not found)
        empty_performers = create_find_performers_result(count=0, performers=[])

        # Response 2: findStudios for Fansly (network)
        fansly_studio = create_studio_dict(
            id="fansly_246", name="Fansly (network)", urls=["https://fansly.com"]
        )
        fansly_result = create_find_studios_result(count=1, studios=[fansly_studio])

        # Response 3: findStudios for creator studio - not found
        creator_not_found_result = create_find_studios_result(count=0, studios=[])

        # Response 4: studioCreate for creator studio
        creator_studio = create_studio_dict(
            id="studio_123",
            name=f"{mock_account.username} (Fansly)",
            urls=[f"https://fansly.com/{mock_account.username}"],
            parent_studio=fansly_studio,
        )

        # Response 5: findTags for "Trailer" tag
        trailer_tag = create_tag_dict(id="preview_tag_id", name="Trailer")
        trailer_result = create_find_tags_result(count=1, tags=[trailer_tag])

        # Response 6: imageUpdate
        image_update_result = {
            "id": mock_image.id,
            "title": mock_image.title,
            "code": "media_123",
        }

        graphql_route = respx.post("http://localhost:9999/graphql").mock(
            side_effect=[
                httpx.Response(
                    200,
                    json=create_graphql_response("findPerformers", empty_performers),
                ),
                httpx.Response(
                    200, json=create_graphql_response("findStudios", fansly_result)
                ),
                httpx.Response(
                    200,
                    json=create_graphql_response(
                        "findStudios", creator_not_found_result
                    ),
                ),
                httpx.Response(
                    200, json=create_graphql_response("studioCreate", creator_studio)
                ),
                httpx.Response(
                    200, json=create_graphql_response("findTags", trailer_result)
                ),
                httpx.Response(
                    200,
                    json=create_graphql_response("imageUpdate", image_update_result),
                ),
            ]
        )

        # Call method with preview flag - real _add_preview_tag runs with respx mocking HTTP boundary
        await respx_stash_processor._update_stash_metadata(
            stash_obj=mock_image,
            item=mock_item,
            account=mock_account,
            media_id="media_123",
            is_preview=True,
        )

        # Verify "Trailer" tag was added (check RESULTS)
        tag_names = [t.name for t in mock_image.tags]
        assert "Trailer" in tag_names

        # Verify GraphQL call sequence (permanent assertion)
        assert len(graphql_route.calls) == 6, "Expected exactly 6 GraphQL calls"
        calls = graphql_route.calls

        # Verify query types in order
        assert "findPerformers" in json.loads(calls[0].request.content)["query"]
        assert "findStudios" in json.loads(calls[1].request.content)["query"]
        assert "findStudios" in json.loads(calls[2].request.content)["query"]
        assert "studioCreate" in json.loads(calls[3].request.content)["query"]
        assert "findTags" in json.loads(calls[4].request.content)["query"]
        assert "imageUpdate" in json.loads(calls[5].request.content)["query"]

    @pytest.mark.asyncio
    async def test_update_stash_metadata_with_studio_create(
        self, respx_stash_processor, mock_item, mock_account, mock_image
    ):
        """Test _update_stash_metadata method when studio needs to be created.

        Unit test using respx - tests the full metadata update flow including studio creation.
        Tests sequence: performer lookup, studio lookup (not found), studio creation, save.
        """
        # Mock GraphQL HTTP responses - Expected sequence (v0.10.3):
        # 1: findPerformers (name EQUALS) - find_one() searches by name only (no alias fallback)
        # 2: findStudios for Fansly (network)
        # 3: studioCreate (creator - get_or_create creates immediately)
        # 4: imageUpdate - save the updated image

        # Response 1: findPerformers (name) - performer not found
        empty_performers = create_find_performers_result(count=0, performers=[])

        # Response 2: findStudios for Fansly (network)
        fansly_studio = create_studio_dict(
            id="fansly_246", name="Fansly (network)", urls=["https://fansly.com"]
        )
        fansly_result = create_find_studios_result(count=1, studios=[fansly_studio])

        # Response 3: findStudios for creator studio - not found
        creator_not_found_result = create_find_studios_result(count=0, studios=[])

        # Response 4: studioCreate for creator studio
        creator_studio = create_studio_dict(
            id="studio_123",
            name=f"{mock_account.username} (Fansly)",
            urls=[f"https://fansly.com/{mock_account.username}"],
            parent_studio=fansly_studio,
        )

        graphql_route = respx.post("http://localhost:9999/graphql").mock(
            side_effect=[
                # 1: findPerformers (name EQUALS)
                httpx.Response(
                    200,
                    json=create_graphql_response("findPerformers", empty_performers),
                ),
                # 2: findStudios (Fansly network)
                httpx.Response(
                    200, json=create_graphql_response("findStudios", fansly_result)
                ),
                # 3: findStudios (creator studio - not found)
                httpx.Response(
                    200,
                    json=create_graphql_response(
                        "findStudios", creator_not_found_result
                    ),
                ),
                # 4: studioCreate (creator studio)
                httpx.Response(
                    200, json=create_graphql_response("studioCreate", creator_studio)
                ),
                # 5: imageUpdate - save the updated image
                httpx.Response(
                    200,
                    json=create_graphql_response(
                        "imageUpdate",
                        {
                            "id": mock_image.id,
                            "title": f"Test title for {mock_account.username}",
                            "code": "media_123",
                            "date": mock_item.createdAt.strftime("%Y-%m-%d"),
                        },
                    ),
                ),
            ]
        )

        # Call method
        await respx_stash_processor._update_stash_metadata(
            stash_obj=mock_image,
            item=mock_item,
            account=mock_account,
            media_id="media_123",
        )

        # Verify all 5 GraphQL calls were made (v0.10.3 pattern)
        # Should have exactly 5 calls: 1 findPerformers + 2 findStudios + 1 studioCreate + 1 imageUpdate
        assert len(graphql_route.calls) == 5, (
            f"Expected 5 calls, got {len(graphql_route.calls)}"
        )

        # Verify the last call was imageUpdate
        last_call = graphql_route.calls[-1]
        response_data = last_call.response.json()
        assert "imageUpdate" in response_data.get("data", {}), (
            "Last call should be imageUpdate"
        )

        # Test 2: Performer cache hit — _performer and _account set (line 550)
        # Skips _find_existing_performer GraphQL lookup, uses cached performer
        respx.reset()
        cached_perf = PerformerFactory.build(
            id="cached_999", name=mock_account.username
        )
        respx_stash_processor._performer = cached_perf
        respx_stash_processor._account = mock_account

        graphql_route = respx.post("http://localhost:9999/graphql").mock(
            side_effect=[
                # Only imageUpdate — performer cached, studio cached from Test 1
                httpx.Response(
                    200,
                    json=create_graphql_response("imageUpdate", {"id": mock_image.id}),
                ),
            ]
        )

        mock_image.title = None  # Reset so it gets updated
        mock_image.organized = False
        mock_image.date = None

        await respx_stash_processor._update_stash_metadata(
            stash_obj=mock_image,
            item=mock_item,
            account=mock_account,
            media_id="media_cached",
        )

        # Only 1 call — performer and studio were cached
        assert graphql_route.call_count == 1
        assert (
            "imageUpdate" in json.loads(graphql_route.calls[0].request.content)["query"]
        )

        # Test 3: Save error — imageUpdate returns GraphQL error (lines 617-628)
        respx.reset()
        respx.post("http://localhost:9999/graphql").mock(
            side_effect=[
                httpx.Response(
                    200,
                    json={
                        "errors": [{"message": "save failed"}],
                        "data": None,
                    },
                ),
            ]
        )

        mock_image.title = None
        mock_image.date = None

        with pytest.raises(ValueError, match="Failed to save"):
            await respx_stash_processor._update_stash_metadata(
                stash_obj=mock_image,
                item=mock_item,
                account=mock_account,
                media_id="media_save_fail",
            )
