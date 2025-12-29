"""Tests for metadata update methods in MediaProcessingMixin."""

import json
from datetime import UTC, datetime

import httpx
import pytest
import respx

from stash.client.utils import _get_attr
from tests.fixtures.stash.stash_graphql_fixtures import (
    create_find_performers_result,
    create_find_studios_result,
    create_find_tags_result,
    create_graphql_response,
    create_performer_dict,
    create_studio_dict,
    create_tag_dict,
)


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
        # Mock GraphQL HTTP responses for the complete flow:
        # By the time _update_stash_metadata is called, creator processing is DONE:
        # - Performer already exists
        # - Fansly (network) studio already exists
        # - Creator studio already exists
        # So we only need to mock that they're FOUND, not created
        #
        # Expected GraphQL call sequence:
        # 1. findPerformers - _find_existing_performer finds the existing performer (cached, may not be called)
        # 2. findStudios - _find_existing_studio finds Fansly studio
        # 3. findStudios - _find_existing_studio finds creator studio (already exists)
        # 4. imageUpdate - stash_obj.save() persists updated metadata

        # Response 1: findPerformers - performer already exists
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

        # Response 3: findStudios - creator studio already exists
        creator_studio = create_studio_dict(
            id="creator_123",
            name=f"{mock_account.username} (Fansly)",
            urls=[f"https://fansly.com/{mock_account.username}"],
            parent_studio=fansly_studio,
        )
        creator_studio_result = create_find_studios_result(
            count=1, studios=[creator_studio]
        )

        # Response 4: imageUpdate - save updated image
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
                    json=create_graphql_response("findStudios", creator_studio_result),
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
        assert len(graphql_route.calls) == 4, "Expected exactly 4 GraphQL calls"
        calls = graphql_route.calls

        # Verify query types in order
        assert "findPerformers" in json.loads(calls[0].request.content)["query"]
        assert "findStudios" in json.loads(calls[1].request.content)["query"]
        assert "findStudios" in json.loads(calls[2].request.content)["query"]
        assert "imageUpdate" in json.loads(calls[3].request.content)["query"]

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
        # No need to check save() - method exits early, no exception = success

    @pytest.mark.asyncio
    async def test_update_stash_metadata_later_date(
        self, respx_stash_processor, mock_item, mock_account, mock_image
    ):
        """Test _update_stash_metadata preserves earliest metadata.

        The method should SKIP updates when the new item is LATER than existing,
        to preserve the earliest occurrence's metadata.
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
        # No exception = method exited early correctly

        # Test 2: Item is EARLIER than existing date - should UPDATE
        mock_image.date = "2024-05-01"  # Later date in storage

        # Create item with earlier date using PostFactory
        from tests.fixtures.metadata.metadata_factories import PostFactory

        earlier_item = PostFactory.build(
            id=99999,
            accountId=mock_account.id,
            content="Earlier content",
            createdAt=datetime(2024, 3, 1, 0, 0, 0, tzinfo=UTC),  # Earlier!
        )
        earlier_item.hashtags = []
        earlier_item.accountMentions = []

        # Mock GraphQL responses for the update path (performer, studios already exist)
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

        creator_studio = create_studio_dict(
            id="creator_123",
            name=f"{mock_account.username} (Fansly)",
            urls=[f"https://fansly.com/{mock_account.username}"],
            parent_studio=fansly_studio,
        )
        creator_studio_result = create_find_studios_result(
            count=1, studios=[creator_studio]
        )

        image_update_result = {
            "id": mock_image.id,
            "title": "Earlier content",
            "code": "media_456",
            "date": "2024-03-01",
            "details": "Earlier content",
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
                    json=create_graphql_response("findStudios", creator_studio_result),
                ),
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

        # Verify GraphQL call sequence (permanent assertion)
        assert len(graphql_route.calls) == 4, "Expected exactly 4 GraphQL calls"
        calls = graphql_route.calls

        # Verify query types in order
        assert "findPerformers" in json.loads(calls[0].request.content)["query"]
        assert "findStudios" in json.loads(calls[1].request.content)["query"]
        assert "findStudios" in json.loads(calls[2].request.content)["query"]
        assert "imageUpdate" in json.loads(calls[3].request.content)["query"]

    @pytest.mark.asyncio
    async def test_update_stash_metadata_performers(
        self, respx_stash_processor, mock_item, mock_account, mock_image, session
    ):
        """Test _update_stash_metadata method with performers.

        Unit test using respx - tests performer lookup and creation for account mentions.
        """
        # Create account mentions using AccountFactory
        from contextlib import asynccontextmanager

        from tests.fixtures.metadata.metadata_factories import AccountFactory

        mention1 = AccountFactory.build(
            id=22222,
            username="mention_user1",
        )
        mention2 = AccountFactory.build(
            id=33333,
            username="mention_user2",
        )
        mock_item.accountMentions = [mention1, mention2]

        # Create REAL Account objects in database (_update_account_stash_id needs to query them)
        real_main_account = AccountFactory.build(
            id=mock_account.id,
            username=mock_account.username,
        )
        real_mention1 = AccountFactory.build(
            id=22222,
            username="mention_user1",
        )
        real_mention2 = AccountFactory.build(
            id=33333,
            username="mention_user2",
        )
        session.add(real_main_account)
        session.add(real_mention1)
        session.add(real_mention2)
        await session.commit()

        # Mock database.async_session_scope() to return our session for @with_session() decorator
        @asynccontextmanager
        async def mock_session_scope():
            yield session

        respx_stash_processor.database.async_session_scope = mock_session_scope

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
            id="performer_123",
            name=mock_account.username,
        )
        main_performers_result = create_find_performers_result(
            count=1, performers=[main_performer_dict]
        )

        # Response 2: findPerformers for mention1 (found)
        mention1_performer_dict = create_performer_dict(
            id="performer_456",
            name=mention1.username,
        )
        mention1_performers_result = create_find_performers_result(
            count=1, performers=[mention1_performer_dict]
        )

        # Response 3: findPerformers for mention2 by name (not found)
        empty_performers_name = create_find_performers_result(count=0, performers=[])

        # Response 4: findPerformers for mention2 by alias (not found, will create)
        empty_performers_alias = create_find_performers_result(count=0, performers=[])

        # Response 5: performerCreate for mention2
        new_performer = create_performer_dict(
            id="789",
            name=mention2.username,
        )

        # Response 5: findStudios for Fansly (network)
        fansly_studio = create_studio_dict(
            id="fansly_246", name="Fansly (network)", urls=["https://fansly.com"]
        )
        fansly_result = create_find_studios_result(count=1, studios=[fansly_studio])

        # Response 6: findStudios for creator studio (already exists)
        creator_studio = create_studio_dict(
            id="creator_123",
            name=f"{mock_account.username} (Fansly)",
            urls=[f"https://fansly.com/{mock_account.username}"],
            parent_studio=fansly_studio,
        )
        creator_studio_result = create_find_studios_result(
            count=1, studios=[creator_studio]
        )

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
                    200, json=create_graphql_response("performerCreate", new_performer)
                ),
                httpx.Response(
                    200, json=create_graphql_response("findStudios", fansly_result)
                ),
                httpx.Response(
                    200,
                    json=create_graphql_response("findStudios", creator_studio_result),
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
        # Performers are dicts from GraphQL (not Performer objects)
        performer_names = [_get_attr(p, "name") for p in mock_image.performers]
        assert mock_account.username in performer_names
        assert mention1.username in performer_names
        # mention2 is newly created, so it might have "Display " prefix from Performer.from_account()
        assert any(mention2.username in name for name in performer_names)

        # Verify GraphQL call sequence (permanent assertion)
        assert len(graphql_route.calls) == 8, "Expected exactly 8 GraphQL calls"
        calls = graphql_route.calls

        # Verify query types in order
        assert "findPerformers" in json.loads(calls[0].request.content)["query"]
        assert "findPerformers" in json.loads(calls[1].request.content)["query"]
        assert "findPerformers" in json.loads(calls[2].request.content)["query"]
        assert "findPerformers" in json.loads(calls[3].request.content)["query"]
        assert "performerCreate" in json.loads(calls[4].request.content)["query"]
        assert "findStudios" in json.loads(calls[5].request.content)["query"]
        assert "findStudios" in json.loads(calls[6].request.content)["query"]
        assert "imageUpdate" in json.loads(calls[7].request.content)["query"]

    @pytest.mark.asyncio
    async def test_update_stash_metadata_studio(
        self, respx_stash_processor, mock_item, mock_account, mock_image
    ):
        """Test _update_stash_metadata method with studio.

        Unit test using respx - tests studio lookup for Fansly network and creator studio.
        """
        # Mock GraphQL HTTP responses - 6 sequential calls:
        # 1: findPerformers for main account (by name - not found)
        # 2: findPerformers for main account (by alias - not found, no create for studio test)
        # 3: findStudios for Fansly (network) - found
        # 4: findStudios for creator studio - NOT found, triggers create
        # 5: studioCreate for creator studio
        # 6: imageUpdate

        # Response 1: findPerformers by name - not found (focus on studio test)
        empty_performers_name = create_find_performers_result(count=0, performers=[])

        # Response 2: findPerformers by alias - not found (find_performer searches by name then alias)
        empty_performers_alias = create_find_performers_result(count=0, performers=[])

        # Response 3: findStudios for Fansly (network) - found
        fansly_studio = create_studio_dict(
            id="fansly_246", name="Fansly (network)", urls=["https://fansly.com"]
        )
        fansly_result = create_find_studios_result(count=1, studios=[fansly_studio])

        # Response 4: findStudios for creator studio - NOT found (will trigger create)
        empty_studios = create_find_studios_result(count=0, studios=[])

        # Response 5: studioCreate for creator studio
        creator_studio = create_studio_dict(
            id="studio_123",
            name=f"{mock_account.username} (Fansly)",
            urls=[f"https://fansly.com/{mock_account.username}"],
            parent_studio=fansly_studio,
        )

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
                    200, json=create_graphql_response("findStudios", fansly_result)
                ),
                httpx.Response(
                    200, json=create_graphql_response("findStudios", empty_studios)
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
        assert len(graphql_route.calls) == 6, "Expected exactly 6 GraphQL calls"
        calls = graphql_route.calls

        # Verify query types in order
        assert "findPerformers" in json.loads(calls[0].request.content)["query"]
        assert "findPerformers" in json.loads(calls[1].request.content)["query"]
        assert "findStudios" in json.loads(calls[2].request.content)["query"]
        assert "findStudios" in json.loads(calls[3].request.content)["query"]
        assert "studioCreate" in json.loads(calls[4].request.content)["query"]
        assert "imageUpdate" in json.loads(calls[5].request.content)["query"]

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

        # Mock GraphQL HTTP responses - Actual sequence discovered via debug output:
        # 1-2: findPerformers (by name, by alias - account lookup)
        # 3: findStudios for Fansly (network)
        # 4: findStudios for creator studio
        # 5: studioCreate (creator studio doesn't exist)
        # 6-7: findTags (one per hashtag - tags processed AFTER studio)
        # 8: imageUpdate

        # Response 1-2: findPerformers (by name, by alias - not found)
        empty_performers_name = create_find_performers_result(count=0, performers=[])
        empty_performers_alias = create_find_performers_result(count=0, performers=[])

        # Response 3: findStudios for Fansly (network)
        fansly_studio = create_studio_dict(
            id="fansly_246", name="Fansly (network)", urls=["https://fansly.com"]
        )
        fansly_result = create_find_studios_result(count=1, studios=[fansly_studio])

        # Response 4: findStudios for creator studio (not found)
        empty_studios = create_find_studios_result(count=0, studios=[])

        # Response 5: studioCreate for creator studio
        creator_studio = create_studio_dict(
            id="studio_123",
            name=f"{mock_account.username} (Fansly)",
            urls=[f"https://fansly.com/{mock_account.username}"],
            parent_studio=fansly_studio,
        )

        # Response 6-7: findTags for each hashtag
        tag1 = create_tag_dict(id="tag_123", name="test_tag")
        tag1_result = create_find_tags_result(count=1, tags=[tag1])

        tag2 = create_tag_dict(id="tag_456", name="another_tag")
        tag2_result = create_find_tags_result(count=1, tags=[tag2])

        # Response 8: imageUpdate
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
                    200, json=create_graphql_response("findStudios", fansly_result)
                ),
                httpx.Response(
                    200, json=create_graphql_response("findStudios", empty_studios)
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
        tag_names = [_get_attr(t, "name") for t in mock_image.tags]
        assert "test_tag" in tag_names
        assert "another_tag" in tag_names

        # Verify GraphQL call sequence (permanent assertion)
        assert len(graphql_route.calls) == 8, "Expected exactly 8 GraphQL calls"
        calls = graphql_route.calls

        # Verify query types in order
        assert "findPerformers" in json.loads(calls[0].request.content)["query"]
        assert "findPerformers" in json.loads(calls[1].request.content)["query"]
        assert "findStudios" in json.loads(calls[2].request.content)["query"]
        assert "findStudios" in json.loads(calls[3].request.content)["query"]
        assert "studioCreate" in json.loads(calls[4].request.content)["query"]
        assert "findTags" in json.loads(calls[5].request.content)["query"]
        assert "findTags" in json.loads(calls[6].request.content)["query"]
        assert "imageUpdate" in json.loads(calls[7].request.content)["query"]

    @pytest.mark.asyncio
    async def test_update_stash_metadata_preview(
        self, respx_stash_processor, mock_item, mock_account, mock_image
    ):
        """Test _update_stash_metadata method with preview flag.

        Unit test using respx - tests that is_preview=True adds "Trailer" tag.
        """
        # Mock GraphQL HTTP responses - Expected sequence (will verify with debug):
        # 1-2: findPerformers (by name, by alias - account lookup)
        # 3: findStudios for Fansly (network)
        # 4: findStudios for creator studio (not found)
        # 5: studioCreate
        # 6: findTags for "Trailer" tag
        # 7: imageUpdate

        # Response 1-2: findPerformers (by name, by alias - not found)
        empty_performers_name = create_find_performers_result(count=0, performers=[])
        empty_performers_alias = create_find_performers_result(count=0, performers=[])

        # Response 3: findStudios for Fansly (network)
        fansly_studio = create_studio_dict(
            id="fansly_246", name="Fansly (network)", urls=["https://fansly.com"]
        )
        fansly_result = create_find_studios_result(count=1, studios=[fansly_studio])

        # Response 4: findStudios for creator studio (not found)
        empty_studios = create_find_studios_result(count=0, studios=[])

        # Response 5: studioCreate for creator studio
        creator_studio = create_studio_dict(
            id="studio_123",
            name=f"{mock_account.username} (Fansly)",
            urls=[f"https://fansly.com/{mock_account.username}"],
            parent_studio=fansly_studio,
        )

        # Response 6: findTags for "Trailer" tag
        trailer_tag = create_tag_dict(id="preview_tag_id", name="Trailer")
        trailer_result = create_find_tags_result(count=1, tags=[trailer_tag])

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
                    200, json=create_graphql_response("findStudios", fansly_result)
                ),
                httpx.Response(
                    200, json=create_graphql_response("findStudios", empty_studios)
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
        tag_names = [_get_attr(t, "name") for t in mock_image.tags]
        assert "Trailer" in tag_names

        # Verify GraphQL call sequence (permanent assertion)
        assert len(graphql_route.calls) == 7, "Expected exactly 7 GraphQL calls"
        calls = graphql_route.calls

        # Verify query types in order
        assert "findPerformers" in json.loads(calls[0].request.content)["query"]
        assert "findPerformers" in json.loads(calls[1].request.content)["query"]
        assert "findStudios" in json.loads(calls[2].request.content)["query"]
        assert "findStudios" in json.loads(calls[3].request.content)["query"]
        assert "studioCreate" in json.loads(calls[4].request.content)["query"]
        assert "findTags" in json.loads(calls[5].request.content)["query"]
        assert "imageUpdate" in json.loads(calls[6].request.content)["query"]

    @pytest.mark.asyncio
    async def test_update_stash_metadata_no_changes(
        self, respx_stash_processor, mock_item, mock_account, mock_image
    ):
        """Test _update_stash_metadata method when no changes are needed.

        Unit test using respx - tests that when stash_obj.is_dirty() returns False,
        the final imageUpdate GraphQL call is skipped (optimization).
        """
        # Mark object as not dirty - this should prevent the final save()
        from unittest.mock import Mock

        mock_image.is_dirty = Mock(return_value=False)

        # Mock GraphQL HTTP responses - Expected sequence (will verify with debug):
        # All the normal metadata processing happens (performers, studio lookup),
        # but the final imageUpdate should NOT be called because is_dirty() = False
        # 1-2: findPerformers (by name, by alias - not found)
        # 3: findStudios for Fansly (network)
        # 4: findStudios for creator studio (not found)
        # 5: studioCreate
        # NO imageUpdate call - that's what we're verifying!

        # Response 1-2: findPerformers (by name, by alias - not found)
        empty_performers_name = create_find_performers_result(count=0, performers=[])
        empty_performers_alias = create_find_performers_result(count=0, performers=[])

        # Response 3: findStudios for Fansly (network)
        fansly_studio = create_studio_dict(
            id="fansly_246", name="Fansly (network)", urls=["https://fansly.com"]
        )
        fansly_result = create_find_studios_result(count=1, studios=[fansly_studio])

        # Response 4: findStudios for creator studio (not found)
        empty_studios = create_find_studios_result(count=0, studios=[])

        # Response 5: studioCreate for creator studio
        creator_studio = create_studio_dict(
            id="studio_123",
            name=f"{mock_account.username} (Fansly)",
            urls=[f"https://fansly.com/{mock_account.username}"],
            parent_studio=fansly_studio,
        )

        graphql_route = respx.post("http://localhost:9999/graphql").mock(
            side_effect=[
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
                    200, json=create_graphql_response("findStudios", fansly_result)
                ),
                httpx.Response(
                    200, json=create_graphql_response("findStudios", empty_studios)
                ),
                httpx.Response(
                    200, json=create_graphql_response("studioCreate", creator_studio)
                ),
                # NO imageUpdate response - it should not be called!
            ]
        )

        # Call method - object is marked not dirty, so save() should be skipped
        await respx_stash_processor._update_stash_metadata(
            stash_obj=mock_image,
            item=mock_item,
            account=mock_account,
            media_id="media_123",
        )

        # Verify imageUpdate was NOT called (because is_dirty = False)
        # Should have exactly 5 calls (2 findPerformers + 2 findStudios + 1 studioCreate)
        assert len(graphql_route.calls) == 5, (
            f"Expected 5 calls, got {len(graphql_route.calls)}"
        )

        # Verify none of the calls were imageUpdate
        for call in graphql_route.calls:
            response_data = call.response.json()
            operations = list(response_data.get("data", {}).keys())
            assert "imageUpdate" not in operations, (
                "imageUpdate should not be called when is_dirty=False"
            )
