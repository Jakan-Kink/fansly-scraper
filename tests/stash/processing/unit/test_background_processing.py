"""Unit tests for background processing methods.

Uses real database and factory objects, mocks only Stash API calls via respx.
"""

import asyncio
import json
from unittest.mock import patch

import httpx
import pytest
import respx

from metadata import Account, ContentType
from tests.fixtures import (
    AccountMediaFactory,
    MediaFactory,
    PostFactory,
)
from tests.fixtures.metadata.metadata_factories import AccountFactory, AttachmentFactory
from tests.fixtures.stash import (
    create_find_studios_result,
    create_graphql_response,
    create_studio_dict,
    dump_graphql_calls,
)
from tests.fixtures.utils.test_isolation import snowflake_id


class TestBackgroundProcessing:
    """Test the background processing methods of StashProcessing."""

    @pytest.mark.asyncio
    async def test_safe_background_processing_success(
        self, respx_stash_processor, entity_store, mock_performer
    ):
        """Test _safe_background_processing succeeds with real DB queries and sets cleanup event.

        Mocks only GraphQL HTTP calls, lets real database queries execute.
        """
        acct_id = snowflake_id()
        media_id = snowflake_id()

        # Create real account in database
        account = AccountFactory.build(id=acct_id, username="test_user", stash_id=123)
        await entity_store.save(account)

        # Create a post with attachments so process_creator_posts has data to process
        post = PostFactory.build(accountId=acct_id)
        await entity_store.save(post)

        # Create media for the post
        media = MediaFactory.build(
            id=media_id, accountId=acct_id, mimetype="image/jpeg", is_downloaded=True
        )
        await entity_store.save(media)

        # Create AccountMedia as attachment content
        account_media = AccountMediaFactory.build(accountId=acct_id, mediaId=media_id)
        await entity_store.save(account_media)

        # Create Attachment linking the post to the media
        attachment = AttachmentFactory.build(
            postId=post.id,
            contentId=account_media.id,
            contentType=ContentType.ACCOUNT_MEDIA,
            pos=0,
        )

        # Add attachment to post relationship AFTER construction
        # (model_validator filters non-dict attachments during construction)
        # Don't save attachment separately - it's a FanslyObject with enum field
        # that the entity_store handles via the post's relationship.
        post.attachments = [attachment]
        await entity_store.save(post)

        # Mock GraphQL HTTP responses for complete process_creator_studio flow
        fansly_studio = create_studio_dict(
            id="10400", name="Fansly (network)", urls=["https://fansly.com"]
        )
        fansly_result = create_find_studios_result(count=1, studios=[fansly_studio])
        creator_not_found_result = create_find_studios_result(count=0, studios=[])
        creator_studio = create_studio_dict(
            id="123",
            name="test_user (Fansly)",
            urls=["https://fansly.com/test_user"],
            parent_studio=fansly_studio,
        )
        empty_galleries = {"count": 0, "galleries": []}

        graphql_route = respx.post("http://localhost:9999/graphql").mock(
            side_effect=[
                # process_creator_studio: find Fansly parent
                httpx.Response(
                    200, json=create_graphql_response("findStudios", fansly_result)
                ),
                # process_creator_studio: find creator studio (not found)
                httpx.Response(
                    200,
                    json=create_graphql_response(
                        "findStudios", creator_not_found_result
                    ),
                ),
                # process_creator_studio: create creator studio
                httpx.Response(
                    200, json=create_graphql_response("studioCreate", creator_studio)
                ),
                # process_creator_posts: check for existing galleries (has 1 post with attachment)
                httpx.Response(
                    200, json=create_graphql_response("findGalleries", empty_galleries)
                ),
            ]
        )

        # Act - let real flow execute with real database queries
        try:
            await respx_stash_processor._safe_background_processing(
                account, mock_performer
            )
        finally:
            dump_graphql_calls(
                graphql_route.calls, "test_safe_background_processing_success"
            )

        # Assert - verify cleanup event set and real database was queried
        assert respx_stash_processor._cleanup_event.is_set()

        # Verify account still exists in database (real query executed)
        found_account = await entity_store.get(Account, acct_id)
        assert found_account is not None

        # Verify GraphQL call sequence (no preload - continue_stash_processing
        # is called directly, preload only happens in start_creator_processing)
        calls = graphql_route.calls
        assert len(calls) == 4, f"Expected 4 GraphQL calls, got {len(calls)}"

        # Verify query types in order
        assert "findStudios" in json.loads(calls[0].request.content)["query"]
        assert "findStudios" in json.loads(calls[1].request.content)["query"]
        assert "studioCreate" in json.loads(calls[2].request.content)["query"]
        assert "findGalleries" in json.loads(calls[3].request.content)["query"]

    @pytest.mark.asyncio
    async def test_safe_background_processing_cancelled(
        self, respx_stash_processor, entity_store, mock_performer
    ):
        """Test _safe_background_processing handles CancelledError with real DB queries.

        Simulates task cancellation during GraphQL call by patching at continue_stash_processing level.
        """
        acct_id = snowflake_id()

        # Create real account
        account = AccountFactory.build(id=acct_id, username="test_cancel", stash_id=124)
        await entity_store.save(account)

        # Patch continue_stash_processing to raise CancelledError (simulates task cancellation)
        # This is acceptable because we're testing _safe_background_processing's error handling,
        # not the continue_stash_processing flow itself
        with (
            patch.object(
                respx_stash_processor,
                "continue_stash_processing",
                side_effect=asyncio.CancelledError(),
            ),
            pytest.raises(asyncio.CancelledError),
            patch("stash.processing.base.logger.debug") as mock_logger_debug,
            patch("stash.processing.base.debug_print") as mock_debug_print,
        ):
            await respx_stash_processor._safe_background_processing(
                account, mock_performer
            )

        # Verify logging and cleanup
        mock_logger_debug.assert_called_once()
        assert "cancelled" in str(mock_logger_debug.call_args).lower()
        mock_debug_print.assert_called_once()
        assert "background_task_cancelled" in str(mock_debug_print.call_args)
        assert respx_stash_processor._cleanup_event.is_set()

    @pytest.mark.asyncio
    async def test_safe_background_processing_exception(
        self, respx_stash_processor, entity_store, mock_performer
    ):
        """Test _safe_background_processing handles exceptions with real DB queries.

        Simulates processing error by patching at continue_stash_processing level.
        """
        acct_id = snowflake_id()

        # Create real account
        account = AccountFactory.build(id=acct_id, username="test_error", stash_id=125)
        await entity_store.save(account)

        # Patch continue_stash_processing to raise error (simulates processing failure)
        # This is acceptable because we're testing _safe_background_processing's error handling,
        # not the continue_stash_processing flow itself
        with (
            patch.object(
                respx_stash_processor,
                "continue_stash_processing",
                side_effect=Exception("Test error"),
            ),
            pytest.raises(Exception, match="Test error"),
            patch("stash.processing.base.logger.exception") as mock_logger_exception,
            patch("stash.processing.base.debug_print") as mock_debug_print,
        ):
            await respx_stash_processor._safe_background_processing(
                account, mock_performer
            )

        # Verify logging and cleanup
        mock_logger_exception.assert_called_once()
        assert "Background task failed" in str(mock_logger_exception.call_args)
        mock_debug_print.assert_called_once()
        assert "background_task_failed" in str(mock_debug_print.call_args)
        assert respx_stash_processor._cleanup_event.is_set()

    @pytest.mark.asyncio
    async def test_continue_stash_processing(
        self, entity_store, respx_stash_processor, mock_performer
    ):
        """Test continue_stash_processing orchestration with real DB and respx GraphQL mocking.

        Verifies:
        1. Real orchestration flow executes (process_creator_studio -> posts -> messages)
        2. Correct GraphQL requests sent to Stash with right variables
        3. Real database queries execute
        """
        acct_id = snowflake_id()

        # Create real account
        account = AccountFactory.build(
            id=acct_id,
            username="test_user",
            displayName="Test User",
            stash_id=123,
        )
        await entity_store.save(account)

        # Set mock_performer.id to match account.stash_id (avoids _update_account_stash_id)
        mock_performer.id = str(account.stash_id)

        # Mock complete GraphQL flow
        fansly_studio = create_studio_dict(
            id="10400", name="Fansly (network)", urls=["https://fansly.com"]
        )
        fansly_result = create_find_studios_result(count=1, studios=[fansly_studio])
        creator_not_found_result = create_find_studios_result(count=0, studios=[])
        creator_studio = create_studio_dict(
            id="123",
            name="test_user (Fansly)",
            urls=["https://fansly.com/test_user"],
            parent_studio=fansly_studio,
        )

        graphql_route = respx.post("http://localhost:9999/graphql").mock(
            side_effect=[
                # process_creator_studio: find Fansly parent
                httpx.Response(
                    200, json=create_graphql_response("findStudios", fansly_result)
                ),
                # process_creator_studio: find creator studio (not found)
                httpx.Response(
                    200,
                    json=create_graphql_response(
                        "findStudios", creator_not_found_result
                    ),
                ),
                # process_creator_studio: create creator studio
                httpx.Response(
                    200, json=create_graphql_response("studioCreate", creator_studio)
                ),
                # process_creator_posts: check for existing galleries
                httpx.Response(
                    200,
                    json=create_graphql_response(
                        "findGalleries", {"count": 0, "galleries": []}
                    ),
                ),
                # process_creator_messages: check for existing galleries
                httpx.Response(
                    200,
                    json=create_graphql_response(
                        "findGalleries", {"count": 0, "galleries": []}
                    ),
                ),
            ]
        )

        # Act - let real orchestration flow execute
        try:
            await respx_stash_processor.continue_stash_processing(
                account, mock_performer
            )
        finally:
            dump_graphql_calls(graphql_route.calls, "test_continue_stash_processing")

        # Assert - verify correct GraphQL requests were sent
        # (no preload calls -- preload only happens in start_creator_processing)
        # Only 3 calls: studio lookup + creation. No findGalleries because
        # account has no posts/messages in database.
        calls = graphql_route.calls
        assert len(calls) == 3, f"Expected 3 GraphQL calls, got {len(calls)}"

        # Verify GraphQL call sequence
        assert "findStudios" in json.loads(calls[0].request.content)["query"]
        assert "findStudios" in json.loads(calls[1].request.content)["query"]
        assert "studioCreate" in json.loads(calls[2].request.content)["query"]

        # Verify studioCreate request has correct variables
        studio_create_request = json.loads(calls[2].request.content)
        assert "studioCreate" in studio_create_request.get("query", "")
        studio_vars = studio_create_request.get("variables", {}).get("input", {})
        assert studio_vars["name"] == "test_user (Fansly)"
        assert studio_vars["urls"] == ["https://fansly.com/test_user"]

    @pytest.mark.asyncio
    async def test_continue_stash_processing_stash_id_update(
        self, entity_store, respx_stash_processor, mock_performer
    ):
        """Test continue_stash_processing updates stash_id when mismatched.

        Verifies real database UPDATE executes and stash_id is persisted.

        The entity_store is the global singleton used by production code
        (via get_store()), so _update_account_stash_id will use it directly.
        """
        acct_id = snowflake_id()

        # Create account with no stash_id
        account = AccountFactory.build(id=acct_id, username="test_user2", stash_id=None)
        await entity_store.save(account)

        # Performer has stash_id
        mock_performer.id = "456"

        # Mock GraphQL responses
        fansly_studio = create_studio_dict(
            id="10400", name="Fansly (network)", urls=["https://fansly.com"]
        )
        fansly_result = create_find_studios_result(count=1, studios=[fansly_studio])
        creator_not_found_result = create_find_studios_result(count=0, studios=[])
        creator_studio = create_studio_dict(
            id="456",
            name="test_user2 (Fansly)",
            urls=["https://fansly.com/test_user2"],
            parent_studio=fansly_studio,
        )

        graphql_route = respx.post("http://localhost:9999/graphql").mock(
            side_effect=[
                # process_creator_studio: find Fansly parent
                httpx.Response(
                    200, json=create_graphql_response("findStudios", fansly_result)
                ),
                # process_creator_studio: find creator studio (not found)
                httpx.Response(
                    200,
                    json=create_graphql_response(
                        "findStudios", creator_not_found_result
                    ),
                ),
                # process_creator_studio: create creator studio
                httpx.Response(
                    200, json=create_graphql_response("studioCreate", creator_studio)
                ),
                # process_creator_posts: check for existing galleries
                httpx.Response(
                    200,
                    json=create_graphql_response(
                        "findGalleries", {"count": 0, "galleries": []}
                    ),
                ),
                # process_creator_messages: check for existing galleries
                httpx.Response(
                    200,
                    json=create_graphql_response(
                        "findGalleries", {"count": 0, "galleries": []}
                    ),
                ),
            ]
        )

        # Act
        try:
            await respx_stash_processor.continue_stash_processing(
                account, mock_performer
            )
        finally:
            dump_graphql_calls(
                graphql_route.calls,
                "test_continue_stash_processing_stash_id_update",
            )

        # Assert - verify real database UPDATE executed
        # Re-fetch from entity_store to see persisted changes
        updated_account = await entity_store.get(Account, acct_id)
        assert updated_account is not None
        assert updated_account.stash_id == 456  # int, not str

        # Verify GraphQL call sequence (no preload calls, no gallery calls
        # since account has no posts/messages in database)
        calls = graphql_route.calls
        assert len(calls) == 3, f"Expected 3 GraphQL calls, got {len(calls)}"

        # Verify query types in order
        assert "findStudios" in json.loads(calls[0].request.content)["query"]
        assert "findStudios" in json.loads(calls[1].request.content)["query"]
        assert "studioCreate" in json.loads(calls[2].request.content)["query"]

        # Verify studioCreate request has correct variables
        studio_create_request = json.loads(calls[2].request.content)
        assert "studioCreate" in studio_create_request.get("query", "")
        studio_vars = studio_create_request.get("variables", {}).get("input", {})
        assert studio_vars["name"] == "test_user2 (Fansly)"
        assert studio_vars["urls"] == ["https://fansly.com/test_user2"]

    @pytest.mark.asyncio
    async def test_continue_stash_processing_missing_inputs(
        self, respx_stash_processor
    ):
        """Test continue_stash_processing raises ValueError for missing account/performer.

        Note: Fixed finally block now uses safe attribute access, so the proper
        ValueError is raised instead of AttributeError.
        """
        # Case 1: Missing both
        with pytest.raises(ValueError, match="Missing account or performer data"):
            await respx_stash_processor.continue_stash_processing(None, None)

        # Case 2: Missing performer
        account = Account(id=snowflake_id(), username="test")
        with pytest.raises(ValueError, match="Missing account or performer data"):
            await respx_stash_processor.continue_stash_processing(account, None)

    @pytest.mark.asyncio
    async def test_continue_stash_processing_performer_dict(
        self, entity_store, respx_stash_processor
    ):
        """Test continue_stash_processing with Performer object."""
        acct_id = snowflake_id()

        # Create account
        account = AccountFactory.build(id=acct_id, username="test_user3", stash_id=789)
        await entity_store.save(account)

        # Create Performer object (Pydantic-based library, not dicts)
        from stash_graphql_client.types import Performer

        performer = Performer(id="789", name="test_user3")

        # Mock GraphQL responses
        fansly_studio = create_studio_dict(
            id="10400", name="Fansly (network)", urls=["https://fansly.com"]
        )
        fansly_result = create_find_studios_result(count=1, studios=[fansly_studio])
        creator_not_found_result = create_find_studios_result(count=0, studios=[])
        creator_studio = create_studio_dict(
            id="789",
            name="test_user3 (Fansly)",
            urls=["https://fansly.com/test_user3"],
            parent_studio=fansly_studio,
        )

        graphql_route = respx.post("http://localhost:9999/graphql").mock(
            side_effect=[
                # process_creator_studio: find Fansly parent
                httpx.Response(
                    200, json=create_graphql_response("findStudios", fansly_result)
                ),
                # process_creator_studio: find creator studio (not found)
                httpx.Response(
                    200,
                    json=create_graphql_response(
                        "findStudios", creator_not_found_result
                    ),
                ),
                # process_creator_studio: create creator studio
                httpx.Response(
                    200, json=create_graphql_response("studioCreate", creator_studio)
                ),
                # process_creator_posts: check for existing galleries
                httpx.Response(
                    200,
                    json=create_graphql_response(
                        "findGalleries", {"count": 0, "galleries": []}
                    ),
                ),
                # process_creator_messages: check for existing galleries
                httpx.Response(
                    200,
                    json=create_graphql_response(
                        "findGalleries", {"count": 0, "galleries": []}
                    ),
                ),
            ]
        )

        # Act - Pass Performer object directly
        try:
            await respx_stash_processor.continue_stash_processing(account, performer)
        finally:
            dump_graphql_calls(
                graphql_route.calls,
                "test_continue_stash_processing_performer_dict",
            )

        # Verify GraphQL call sequence (no preload calls, no gallery calls
        # since account has no posts/messages in database)
        calls = graphql_route.calls
        assert len(calls) == 3, f"Expected 3 GraphQL calls, got {len(calls)}"

        # Verify query types in order
        assert "findStudios" in json.loads(calls[0].request.content)["query"]
        assert "findStudios" in json.loads(calls[1].request.content)["query"]
        assert "studioCreate" in json.loads(calls[2].request.content)["query"]

        # Verify studioCreate request has correct variables
        studio_create_request = json.loads(calls[2].request.content)
        assert "studioCreate" in studio_create_request.get("query", "")
        studio_vars = studio_create_request.get("variables", {}).get("input", {})
        assert studio_vars["name"] == "test_user3 (Fansly)"
        assert studio_vars["urls"] == ["https://fansly.com/test_user3"]

    @pytest.mark.asyncio
    async def test_continue_stash_processing_invalid_performer_type(
        self, entity_store, respx_stash_processor
    ):
        """Test continue_stash_processing raises error for invalid performer type.

        Note: finally block tries to access performer.name, so AttributeError raised
        instead of the initial TypeError.
        """
        acct_id = snowflake_id()
        account = AccountFactory.build(id=acct_id, username="test_user4", stash_id=123)
        await entity_store.save(account)

        # Invalid performer type (string instead of Performer or dict)
        with pytest.raises(
            TypeError, match="performer must be a Stash Performer object"
        ):
            await respx_stash_processor.continue_stash_processing(account, "invalid")

    @pytest.mark.asyncio
    async def test_continue_stash_processing_invalid_account_type(
        self, respx_stash_processor, mock_performer
    ):
        """Test continue_stash_processing raises AttributeError for invalid account type."""
        # Invalid account type (string instead of Account object)
        with pytest.raises(AttributeError):
            await respx_stash_processor.continue_stash_processing(
                "invalid", mock_performer
            )
