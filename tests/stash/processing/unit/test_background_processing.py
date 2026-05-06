"""Unit tests for background processing methods.

Uses real database and factory objects, mocks only Stash API calls via respx.
"""

import asyncio
import logging
from unittest.mock import patch

import httpx
import pytest
import respx
from stash_graphql_client.types import Performer

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
from tests.fixtures.stash.stash_api_fixtures import assert_op, assert_op_with_vars
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
        assert_op(calls[0], "findStudios")
        assert_op(calls[1], "findStudios")
        assert_op(calls[2], "studioCreate")
        assert_op(calls[3], "findGalleries")

    @pytest.mark.asyncio
    async def test_safe_background_processing_cancelled(
        self, respx_stash_processor, entity_store, mock_performer, caplog
    ):
        """Test _safe_background_processing handles CancelledError with real DB queries.

        Simulates task cancellation during GraphQL call by patching at continue_stash_processing level.
        """
        caplog.set_level(logging.DEBUG)
        acct_id = snowflake_id()

        # Create real account
        account = AccountFactory.build(id=acct_id, username="test_cancel", stash_id=124)
        await entity_store.save(account)

        # Patch continue_stash_processing to raise CancelledError (simulates task cancellation).
        # Acceptable: we're testing _safe_background_processing's error handling, not the
        # continue_stash_processing flow itself.
        with (
            patch.object(
                respx_stash_processor,
                "continue_stash_processing",
                side_effect=asyncio.CancelledError(),
            ),
            pytest.raises(asyncio.CancelledError),
        ):
            await respx_stash_processor._safe_background_processing(
                account, mock_performer
            )

        # Production emits a DEBUG record from logger.debug() acknowledging cancellation.
        debug_records = [r for r in caplog.records if r.levelname == "DEBUG"]
        cancel_logs = [
            r.getMessage()
            for r in debug_records
            if "cancelled" in r.getMessage().lower()
        ]
        assert len(cancel_logs) >= 1
        # debug_print emits a separate DEBUG record (pformatted dict) tagged with the status.
        cancel_status_logs = [
            r.getMessage()
            for r in debug_records
            if "background_task_cancelled" in r.getMessage()
        ]
        assert len(cancel_status_logs) == 1
        assert respx_stash_processor._cleanup_event.is_set()

    @pytest.mark.asyncio
    async def test_safe_background_processing_exception(
        self, respx_stash_processor, entity_store, mock_performer, caplog
    ):
        """Test _safe_background_processing handles exceptions with real DB queries.

        Simulates processing error by patching at continue_stash_processing level.
        """
        caplog.set_level(logging.DEBUG)
        acct_id = snowflake_id()

        # Create real account
        account = AccountFactory.build(id=acct_id, username="test_error", stash_id=125)
        await entity_store.save(account)

        # Patch continue_stash_processing to raise error (simulates processing failure).
        with (
            patch.object(
                respx_stash_processor,
                "continue_stash_processing",
                side_effect=Exception("Test error"),
            ),
            pytest.raises(Exception, match="Test error"),
        ):
            await respx_stash_processor._safe_background_processing(
                account, mock_performer
            )

        # logger.exception → ERROR record with exc_info attached.
        error_records = [r for r in caplog.records if r.levelname == "ERROR"]
        bg_failed_errors = [
            r for r in error_records if "Background task failed" in r.getMessage()
        ]
        assert len(bg_failed_errors) == 1
        assert bg_failed_errors[0].exc_info is not None, (
            "logger.exception should attach exc_info"
        )
        # debug_print → DEBUG record tagged with the status.
        debug_status_logs = [
            r.getMessage()
            for r in caplog.records
            if r.levelname == "DEBUG" and "background_task_failed" in r.getMessage()
        ]
        assert len(debug_status_logs) == 1
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
        # Only 3 calls: studio lookup + creation. No findGalleries because
        # account has no posts/messages in database — _run_worker_pool exits
        # with empty items.
        calls = graphql_route.calls
        assert len(calls) == 3, f"Expected 3 GraphQL calls, got {len(calls)}"

        # Verify GraphQL call sequence — studioCreate variables are part of
        # the same assertion since this test cares about the studio's identity.
        assert_op(calls[0], "findStudios")
        assert_op(calls[1], "findStudios")
        assert_op_with_vars(
            calls[2],
            "studioCreate",
            input__name="test_user (Fansly)",
            input__urls=["https://fansly.com/test_user"],
        )

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

        # Verify GraphQL call sequence — no findGalleries calls because the
        # account has no posts/messages, so _run_worker_pool exits early.
        calls = graphql_route.calls
        assert len(calls) == 3, f"Expected 3 GraphQL calls, got {len(calls)}"

        # Verify query types in order — studioCreate variables are part of
        # the same assertion since this test cares about the studio's identity.
        assert_op(calls[0], "findStudios")
        assert_op(calls[1], "findStudios")
        assert_op_with_vars(
            calls[2],
            "studioCreate",
            input__name="test_user2 (Fansly)",
            input__urls=["https://fansly.com/test_user2"],
        )

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

        # Verify GraphQL call sequence — no findGalleries calls because the
        # account has no posts/messages, so _run_worker_pool exits early.
        calls = graphql_route.calls
        assert len(calls) == 3, f"Expected 3 GraphQL calls, got {len(calls)}"

        # Verify query types in order — studioCreate variables are part of
        # the same assertion since this test cares about the studio's identity.
        assert_op(calls[0], "findStudios")
        assert_op(calls[1], "findStudios")
        assert_op_with_vars(
            calls[2],
            "studioCreate",
            input__name="test_user3 (Fansly)",
            input__urls=["https://fansly.com/test_user3"],
        )

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
