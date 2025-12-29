"""Unit tests for background processing methods.

Uses real database and factory objects, mocks only Stash API calls via respx.
"""

import asyncio
import json
from contextlib import asynccontextmanager
from unittest.mock import patch

import httpx
import pytest
import respx
from sqlalchemy import select

from metadata import Account
from metadata.attachment import ContentType
from tests.fixtures import (
    AccountMediaFactory,
    MediaFactory,
    MediaLocationFactory,
    PostFactory,
)
from tests.fixtures.metadata.metadata_factories import AccountFactory, AttachmentFactory
from tests.fixtures.stash import (
    create_find_studios_result,
    create_graphql_response,
    create_studio_dict,
)


class TestBackgroundProcessing:
    """Test the background processing methods of StashProcessing."""

    @pytest.mark.asyncio
    async def test_safe_background_processing_success(
        self, respx_stash_processor, factory_async_session, mock_performer, session
    ):
        """Test _safe_background_processing succeeds with real DB queries and sets cleanup event.

        Mocks only GraphQL HTTP calls, lets real database queries execute.
        """
        # Create real account in database
        account = AccountFactory(id=12345, username="test_user", stash_id=123)
        factory_async_session.commit()

        # Create a post with attachments so process_creator_posts has data to process
        post = PostFactory(accountId=12345)
        factory_async_session.commit()

        # Create media for the post
        media = MediaFactory(
            id=99999, accountId=12345, mimetype="image/jpeg", is_downloaded=True
        )
        factory_async_session.commit()

        # Create media location
        media_location = MediaLocationFactory(
            mediaId=99999, locationId=1, location="https://example.com/image.jpg"
        )
        factory_async_session.commit()

        # Create AccountMedia as attachment content
        account_media = AccountMediaFactory(accountId=12345, mediaId=99999)
        factory_async_session.commit()

        # Create Attachment linking the post to the media
        attachment = AttachmentFactory(
            postId=post.id,
            contentId=account_media.id,
            contentType=ContentType.ACCOUNT_MEDIA,
            pos=0,
        )
        factory_async_session.commit()

        # Query fresh account from async session
        result = await session.execute(select(Account).where(Account.id == 12345))
        account = result.scalar_one()

        # Mock GraphQL HTTP responses for complete process_creator_studio flow
        # 1. Find Fansly parent studio
        fansly_studio = create_studio_dict(
            id="fansly_246", name="Fansly (network)", urls=["https://fansly.com"]
        )
        fansly_result = create_find_studios_result(count=1, studios=[fansly_studio])

        # 2. Creator studio not found initially
        empty_studios = create_find_studios_result(count=0, studios=[])

        # 3. Create creator studio with Fansly as parent
        creator_studio = create_studio_dict(
            id="123",
            name="test_user (Fansly)",
            urls=["https://fansly.com/test_user"],
            parent_studio=fansly_studio,
        )

        # 4. Empty galleries result (process_creator_posts checks for existing galleries)
        empty_galleries = {"count": 0, "galleries": []}

        graphql_route = respx.post("http://localhost:9999/graphql").mock(
            side_effect=[
                # process_creator_studio: find Fansly parent
                httpx.Response(
                    200, json=create_graphql_response("findStudios", fansly_result)
                ),
                # process_creator_studio: creator studio not found
                httpx.Response(
                    200, json=create_graphql_response("findStudios", empty_studios)
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
        await respx_stash_processor._safe_background_processing(account, mock_performer)

        # Assert - verify cleanup event set and real database was queried
        assert respx_stash_processor._cleanup_event.is_set()

        # Verify account still exists in database (real query executed)
        result = await session.execute(select(Account).where(Account.id == 12345))
        assert result.scalar_one() is not None

        # Verify GraphQL call sequence (permanent assertion)
        # 4 calls: 3 for studio setup + 1 for post processing (no messages in this test)
        assert len(graphql_route.calls) == 4, "Expected exactly 4 GraphQL calls"
        calls = graphql_route.calls

        # Verify query types in order
        assert "findStudios" in json.loads(calls[0].request.content)["query"]
        assert "findStudios" in json.loads(calls[1].request.content)["query"]
        assert "studioCreate" in json.loads(calls[2].request.content)["query"]
        assert "findGalleries" in json.loads(calls[3].request.content)["query"]

    @pytest.mark.asyncio
    async def test_safe_background_processing_cancelled(
        self, respx_stash_processor, factory_async_session, mock_performer, session
    ):
        """Test _safe_background_processing handles CancelledError with real DB queries.

        Simulates task cancellation during GraphQL call by patching at continue_stash_processing level.
        """
        # Create real account
        account = AccountFactory(id=12346, username="test_cancel", stash_id=124)
        factory_async_session.commit()

        result = await session.execute(select(Account).where(Account.id == 12346))
        account = result.scalar_one()

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
        self, respx_stash_processor, factory_async_session, mock_performer, session
    ):
        """Test _safe_background_processing handles exceptions with real DB queries.

        Simulates processing error by patching at continue_stash_processing level.
        """
        # Create real account
        account = AccountFactory(id=12347, username="test_error", stash_id=125)
        factory_async_session.commit()

        result = await session.execute(select(Account).where(Account.id == 12347))
        account = result.scalar_one()

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
        self, factory_async_session, respx_stash_processor, mock_performer, session
    ):
        """Test continue_stash_processing orchestration with real DB and respx GraphQL mocking.

        Verifies:
        1. Real orchestration flow executes (process_creator_studio → posts → messages)
        2. Correct GraphQL requests sent to Stash with right variables
        3. Real database queries execute
        """
        # Create real account
        account = AccountFactory(
            id=12345,
            username="test_user",
            displayName="Test User",
            stash_id=123,
        )
        factory_async_session.commit()

        # Query fresh account from async session
        result = await session.execute(select(Account).where(Account.id == 12345))
        account = result.scalar_one()

        # Set mock_performer.id to match account.stash_id (avoids _update_account_stash_id)
        mock_performer.id = str(account.stash_id)

        # Mock complete GraphQL flow
        fansly_studio = create_studio_dict(
            id="fansly_246", name="Fansly (network)", urls=["https://fansly.com"]
        )
        fansly_result = create_find_studios_result(count=1, studios=[fansly_studio])
        empty_studios = create_find_studios_result(count=0, studios=[])
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
                # process_creator_studio: creator studio not found
                httpx.Response(
                    200, json=create_graphql_response("findStudios", empty_studios)
                ),
                # process_creator_studio: create creator studio
                httpx.Response(
                    200, json=create_graphql_response("studioCreate", creator_studio)
                ),
                # process_creator_posts: check for existing galleries
                httpx.Response(
                    200, json=create_graphql_response("findGalleries", empty_galleries)
                ),
                # process_creator_messages: check for existing galleries
                httpx.Response(
                    200, json=create_graphql_response("findGalleries", empty_galleries)
                ),
            ]
        )

        # Act - let real orchestration flow execute
        await respx_stash_processor.continue_stash_processing(
            account, mock_performer, session=session
        )

        # Assert - verify correct GraphQL requests were sent
        # Note: Only 3 calls because account has no posts/messages in database
        # (process_creator_posts/messages only call findGalleries if there's content)
        assert len(graphql_route.calls) == 3

        # Verify GraphQL call sequence (permanent assertion)
        calls = graphql_route.calls
        assert "findStudios" in json.loads(calls[0].request.content)["query"]
        assert "findStudios" in json.loads(calls[1].request.content)["query"]
        assert "studioCreate" in json.loads(calls[2].request.content)["query"]

        # Verify studioCreate request has correct variables
        studio_create_request = json.loads(graphql_route.calls[2].request.content)
        assert "studioCreate" in studio_create_request.get("query", "")
        studio_vars = studio_create_request.get("variables", {}).get("input", {})
        assert studio_vars["name"] == "test_user (Fansly)"
        assert studio_vars["urls"] == ["https://fansly.com/test_user"]

    @pytest.mark.asyncio
    async def test_continue_stash_processing_stash_id_update(
        self, factory_async_session, respx_stash_processor, mock_performer, session
    ):
        """Test continue_stash_processing updates stash_id when mismatched.

        Verifies real database UPDATE executes and stash_id is persisted.

        Note: Patches async_session_scope to return the existing session instead of
        creating a new one. This is necessary because:
        1. Tests use SERIALIZABLE isolation (snapshot at transaction start)
        2. Production code doesn't pass session to _update_account_stash_id
        3. Without patch, decorator creates NEW session, commits there
        4. Test session (SERIALIZABLE) can't see changes from other transactions

        This patch simulates what would happen under READ COMMITTED isolation
        where session reuse is less critical.
        """
        # Create account with no stash_id
        account = AccountFactory(id=12346, username="test_user2", stash_id=None)
        factory_async_session.commit()

        result = await session.execute(select(Account).where(Account.id == 12346))
        account = result.scalar_one()

        # Performer has stash_id
        mock_performer.id = "456"

        # Mock GraphQL responses
        fansly_studio = create_studio_dict(
            id="fansly_246", name="Fansly (network)", urls=["https://fansly.com"]
        )
        fansly_result = create_find_studios_result(count=1, studios=[fansly_studio])
        empty_studios = create_find_studios_result(count=0, studios=[])
        creator_studio = create_studio_dict(
            id="456",
            name="test_user2 (Fansly)",
            url="https://fansly.com/test_user2",
            parent_studio=fansly_studio,
        )
        empty_galleries = {"count": 0, "galleries": []}

        graphql_route = respx.post("http://localhost:9999/graphql").mock(
            side_effect=[
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
                    200, json=create_graphql_response("findGalleries", empty_galleries)
                ),
                httpx.Response(
                    200, json=create_graphql_response("findGalleries", empty_galleries)
                ),
            ]
        )

        # Patch async_session_scope to return existing session
        # This simulates session reuse that would happen naturally under READ COMMITTED
        @asynccontextmanager
        async def mock_session_scope():
            yield session

        with patch.object(
            respx_stash_processor.database, "async_session_scope", mock_session_scope
        ):
            # Act
            await respx_stash_processor.continue_stash_processing(
                account, mock_performer, session=session
            )

        # Refresh the account object to see changes made during processing
        await session.refresh(account)

        # Assert - verify real database UPDATE executed
        assert account.stash_id == 456  # int, not str

        # Verify GraphQL call sequence (permanent assertion)
        assert len(graphql_route.calls) == 3, "Expected exactly 3 GraphQL calls"
        calls = graphql_route.calls

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
        self, respx_stash_processor, session
    ):
        """Test continue_stash_processing raises errors for missing account/performer.

        Note: finally block tries to access performer.name, so AttributeError raised
        instead of the initial ValueError.
        """
        # Case 1: Missing both
        with pytest.raises(AttributeError):  # from finally block: performer.name
            await respx_stash_processor.continue_stash_processing(
                None, None, session=session
            )

        # Case 2: Missing performer
        account = Account(id=1, username="test")
        with pytest.raises(AttributeError):  # from finally block: performer.name
            await respx_stash_processor.continue_stash_processing(
                account, None, session=session
            )

    @pytest.mark.asyncio
    async def test_continue_stash_processing_performer_dict(
        self, factory_async_session, respx_stash_processor, session
    ):
        """Test continue_stash_processing converts dict performer to Performer object."""
        # Create account
        account = AccountFactory(id=12347, username="test_user3", stash_id=789)
        factory_async_session.commit()

        result = await session.execute(select(Account).where(Account.id == 12347))
        account = result.scalar_one()

        # Provide performer as dict
        performer_dict = {"id": "789", "name": "test_user3"}

        # Mock GraphQL responses
        fansly_studio = create_studio_dict(
            id="fansly_246", name="Fansly (network)", urls=["https://fansly.com"]
        )
        fansly_result = create_find_studios_result(count=1, studios=[fansly_studio])
        empty_studios = create_find_studios_result(count=0, studios=[])
        creator_studio = create_studio_dict(
            id="789",
            name="test_user3 (Fansly)",
            url="https://fansly.com/test_user3",
            parent_studio=fansly_studio,
        )
        empty_galleries = {"count": 0, "galleries": []}

        graphql_route = respx.post("http://localhost:9999/graphql").mock(
            side_effect=[
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
                    200, json=create_graphql_response("findGalleries", empty_galleries)
                ),
                httpx.Response(
                    200, json=create_graphql_response("findGalleries", empty_galleries)
                ),
            ]
        )

        # Act - dict should be converted internally via Performer.from_dict
        await respx_stash_processor.continue_stash_processing(
            account, performer_dict, session=session
        )

        # Assert - if we got here, dict was successfully converted and processing completed
        assert True

        # Verify GraphQL call sequence (permanent assertion)
        assert len(graphql_route.calls) == 3, "Expected exactly 3 GraphQL calls"
        calls = graphql_route.calls

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
        self, factory_async_session, respx_stash_processor, session
    ):
        """Test continue_stash_processing raises error for invalid performer type.

        Note: finally block tries to access performer.name, so AttributeError raised
        instead of the initial TypeError.
        """
        account = AccountFactory(id=12348, username="test_user4", stash_id=123)
        factory_async_session.commit()

        result = await session.execute(select(Account).where(Account.id == 12348))
        account = result.scalar_one()

        # Invalid performer type (string instead of Performer or dict)
        with pytest.raises(AttributeError):  # from finally block: "invalid".name
            await respx_stash_processor.continue_stash_processing(
                account, "invalid", session=session
            )

    @pytest.mark.asyncio
    async def test_continue_stash_processing_invalid_account_type(
        self, respx_stash_processor, mock_performer, session
    ):
        """Test continue_stash_processing raises AttributeError for invalid account type."""
        # Invalid account type (string instead of Account object)
        with pytest.raises(AttributeError):
            await respx_stash_processor.continue_stash_processing(
                "invalid", mock_performer, session=session
            )
