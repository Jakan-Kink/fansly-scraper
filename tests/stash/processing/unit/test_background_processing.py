"""Unit tests for background processing methods.

Uses real database and factory objects, mocks only Stash API calls via respx.
"""

import asyncio
import logging

import httpx
import pytest
import respx
from stash_graphql_client.types import Performer

from metadata import Account, ContentType
from tests.fixtures.metadata import (
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
                # _run_file_first: the creator's file sweep finds nothing.
                httpx.Response(
                    200, json={"data": {"findFiles": {"count": 0, "files": []}}}
                ),
            ]
        )

        # Act - run the full real wrapper (studio + stash_id + file-first content
        # + cleanup). The seeded post has no Stash file, so the sweep's findFiles
        # is empty and nothing is adjudicated.
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

        # Verify GraphQL call sequence — the real studio flow then the real sweep.
        calls = graphql_route.calls
        assert len(calls) == 4, f"Expected 4 GraphQL calls, got {len(calls)}"

        # Verify query types in order
        assert_op(calls[0], "findStudios")
        assert_op(calls[1], "findStudios")
        assert_op(calls[2], "studioCreate")
        assert_op(calls[3], "findFiles")

    @pytest.mark.asyncio
    async def test_safe_background_processing_exception(
        self, respx_stash_processor, entity_store, mock_performer, caplog
    ):
        """_safe_background_processing handles a failure raised by the REAL pipeline.

        Drives failure through the real continue_stash_processing: the first
        Stash GraphQL POST (findStudios in process_creator_studio) returns HTTP
        500, so the real method raises a StashConnectionError, and
        _safe_background_processing's generic `except Exception` branch runs
        end-to-end (no behavior-replacing mock of continue_stash_processing).

        Regression: this real path surfaced a production bug. The except block
        logged via ``logger.exception(f"Background task failed: {e}", ...)``; the
        f-string interpolated an exception whose repr contains literal braces
        (``[{'message': 'boom'}]``), which loguru fed to ``str.format()`` →
        ``KeyError('message')``, masking the real failure. Fixed in base.py to a
        static message + ``exc_info=e`` (sibling convention, file_first.py:109).
        """
        caplog.set_level(logging.DEBUG)
        acct_id = snowflake_id()

        # Real account; stash_id matches performer.id ("123") so the real method
        # skips _update_account_stash_id and the first POST is findStudios.
        account = AccountFactory.build(id=acct_id, username="test_err", stash_id=123)
        await entity_store.save(account)
        mock_performer.id = "123"

        # 500 on the first findStudios POST drives the real failure. The error
        # body deliberately carries braces to guard the loguru-format regression.
        graphql_route = respx.post("http://localhost:9999/graphql").mock(
            side_effect=[httpx.Response(500, json={"errors": [{"message": "boom"}]})]
        )

        try:
            with pytest.raises(Exception):
                await respx_stash_processor._safe_background_processing(
                    account, mock_performer
                )
        finally:
            dump_graphql_calls(
                graphql_route.calls, "test_safe_background_processing_exception"
            )

        # The real first GraphQL call was attempted.
        assert graphql_route.called

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
    async def test_safe_background_processing_cancelled(
        self, respx_stash_processor, entity_store, mock_performer, caplog
    ):
        """_safe_background_processing handles CancelledError from the REAL pipeline.

        gql's transport wraps any transport-layer exception (including a
        respx-injected CancelledError) into TransportConnectionFailed, so a true
        asyncio.CancelledError cannot survive injection at the respx layer.
        Instead we spy on continue_stash_processing with ``wraps=`` so the REAL
        method runs end-to-end (real studio + file-first flow against the four
        seeded GraphQL responses), then raises CancelledError — mimicking task
        cancellation at await-completion. This exercises the real method while
        delivering a genuine CancelledError to the `except asyncio.CancelledError`
        branch (NOT a behavior-replacing AsyncMock).
        """
        caplog.set_level(logging.DEBUG)
        acct_id = snowflake_id()

        account = AccountFactory.build(id=acct_id, username="test_cancel", stash_id=123)
        await entity_store.save(account)
        mock_performer.id = "123"

        # The real continue_stash_processing needs the full successful studio +
        # file-first response sequence to run to completion before we cancel.
        fansly_studio = create_studio_dict(
            id="10400", name="Fansly (network)", urls=["https://fansly.com"]
        )
        fansly_result = create_find_studios_result(count=1, studios=[fansly_studio])
        creator_not_found_result = create_find_studios_result(count=0, studios=[])
        creator_studio = create_studio_dict(
            id="123",
            name="test_cancel (Fansly)",
            urls=["https://fansly.com/test_cancel"],
            parent_studio=fansly_studio,
        )
        graphql_route = respx.post("http://localhost:9999/graphql").mock(
            side_effect=[
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
                    200, json={"data": {"findFiles": {"count": 0, "files": []}}}
                ),
            ]
        )

        # wraps-spy: delegate to the real bound method, then raise CancelledError.
        real_continue = respx_stash_processor.continue_stash_processing

        async def cancel_after_real(account, performer):
            await real_continue(account, performer)
            raise asyncio.CancelledError

        respx_stash_processor.continue_stash_processing = cancel_after_real
        try:
            with pytest.raises(asyncio.CancelledError):
                await respx_stash_processor._safe_background_processing(
                    account, mock_performer
                )
        finally:
            respx_stash_processor.continue_stash_processing = real_continue
            dump_graphql_calls(
                graphql_route.calls, "test_safe_background_processing_cancelled"
            )

        # The real pipeline ran end-to-end before the cancel.
        assert graphql_route.called
        assert len(graphql_route.calls) == 4

        debug_records = [r for r in caplog.records if r.levelname == "DEBUG"]
        cancel_logs = [
            r.getMessage()
            for r in debug_records
            if "cancelled" in r.getMessage().lower()
        ]
        assert len(cancel_logs) >= 1
        cancel_status_logs = [
            r.getMessage()
            for r in debug_records
            if "background_task_cancelled" in r.getMessage()
        ]
        assert len(cancel_status_logs) == 1
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
                # _run_file_first: the creator's file sweep finds nothing.
                httpx.Response(
                    200, json={"data": {"findFiles": {"count": 0, "files": []}}}
                ),
            ]
        )

        # Act - run the real wrapper end-to-end INCLUDING the file-first content
        # flow. The creator has no Stash files, so the sweep's findFiles is empty
        # and no entities are adjudicated.
        try:
            await respx_stash_processor.continue_stash_processing(
                account, mock_performer
            )
        finally:
            dump_graphql_calls(graphql_route.calls, "test_continue_stash_processing")

        # Assert - the real studio flow then the real file-first sweep.
        calls = graphql_route.calls
        assert len(calls) == 4, f"Expected 4 GraphQL calls, got {len(calls)}"

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
        assert_op(calls[3], "findFiles")

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
                # _run_file_first: the creator's file sweep finds nothing.
                httpx.Response(
                    200, json={"data": {"findFiles": {"count": 0, "files": []}}}
                ),
            ]
        )

        # Act - run the real wrapper end-to-end (stash_id DB update + studio flow
        # + file-first content). No Stash files → empty findFiles sweep.
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

        # Verify GraphQL call sequence — the real studio flow then the real sweep.
        calls = graphql_route.calls
        assert len(calls) == 4, f"Expected 4 GraphQL calls, got {len(calls)}"

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
        assert_op(calls[3], "findFiles")

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
                # _run_file_first: the creator's file sweep finds nothing.
                httpx.Response(
                    200, json={"data": {"findFiles": {"count": 0, "files": []}}}
                ),
            ]
        )

        # Act - Pass Performer object directly; run the full real wrapper.
        try:
            await respx_stash_processor.continue_stash_processing(account, performer)
        finally:
            dump_graphql_calls(
                graphql_route.calls,
                "test_continue_stash_processing_performer_dict",
            )

        # Verify GraphQL call sequence — the real studio flow then the real sweep.
        calls = graphql_route.calls
        assert len(calls) == 4, f"Expected 4 GraphQL calls, got {len(calls)}"

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
        assert_op(calls[3], "findFiles")

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
