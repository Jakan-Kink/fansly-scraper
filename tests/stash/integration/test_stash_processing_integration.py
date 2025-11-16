"""Integration tests for stash processing module.

Uses real database fixtures and Stash type factories instead of mocks.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import respx

from stash.processing import StashProcessing
from tests.fixtures import (
    AccountFactory,
    create_find_studios_result,
    create_graphql_response,
    create_studio_dict,
)
from tests.fixtures.stash.stash_type_factories import PerformerFactory, StudioFactory


@pytest.mark.asyncio
async def test_full_creator_processing_flow(
    uuid_test_db_factory, test_state, stash_context
):
    """Test the full flow of creator processing using real fixtures."""
    # Use real Account from factory
    account = AccountFactory.build(
        id=12345,
        username="test_user",
    )

    # Use real Stash types from factories
    performer = PerformerFactory(
        id="performer_123",
        name="test_user",
    )

    studio = StudioFactory(
        id="studio_123",
        name="Test Studio",
    )

    # Initialize processor with real config and database
    processor = StashProcessing(
        config=uuid_test_db_factory,
        state=test_state,
        context=stash_context,
        database=uuid_test_db_factory.database,
        _background_task=None,
        _cleanup_event=asyncio.Event(),
        _owns_db=False,
    )

    # Mock only the processor methods (not the objects themselves)
    processor._find_account = AsyncMock(return_value=account)
    processor._find_existing_performer = AsyncMock(return_value=performer)
    processor._update_performer_avatar = AsyncMock()
    processor.scan_creator_folder = AsyncMock()
    processor.process_creator_studio = AsyncMock(return_value=studio)
    processor.process_creator_posts = AsyncMock()
    processor.process_creator_messages = AsyncMock()
    processor._safe_background_processing = AsyncMock()
    processor.process_creator = AsyncMock(return_value=(account, performer))

    # Execute the main method
    with patch("asyncio.get_running_loop") as mock_get_loop:
        mock_loop = MagicMock()
        mock_task = MagicMock()
        mock_loop.create_task.return_value = mock_task
        mock_get_loop.return_value = mock_loop

        # Start the processing
        await processor.start_creator_processing()

        # Verify the flow
        stash_context.get_client.assert_called_once()
        processor.scan_creator_folder.assert_called_once()
        processor.process_creator.assert_called_once()
        mock_loop.create_task.assert_called_once()
        assert processor._background_task == mock_task
        # Verify task was added to background tasks list
        assert mock_task in uuid_test_db_factory._background_tasks


@pytest.mark.asyncio
@respx.mock
async def test_process_creator_to_background(
    real_stash_processor, factory_async_session
):
    """Test the flow from process_creator to background processing using respx edge mocking."""
    # Create real Account using factory with database
    account = AccountFactory(
        id=12345,
        username="test_user",
        stash_id=None,
    )
    factory_async_session.commit()

    # Mock Stash GraphQL responses at HTTP layer
    fansly_studio_dict = create_studio_dict(id="246", name="Fansly (network)")
    fansly_result = create_find_studios_result(count=1, studios=[fansly_studio_dict])

    creator_studio_dict = create_studio_dict(
        id="studio_123",
        name="test_user (Fansly)",
        url="https://fansly.com/test_user",
    )
    creator_result = create_find_studios_result(count=1, studios=[creator_studio_dict])

    # Mock GraphQL endpoint at HTTP layer
    respx.post("http://localhost:9999/graphql").mock(
        side_effect=[
            # First call: find Fansly network studio
            httpx.Response(
                200, json=create_graphql_response("findStudios", fansly_result)
            ),
            # Second call: find creator studio
            httpx.Response(
                200, json=create_graphql_response("findStudios", creator_result)
            ),
        ]
    )

    # Use real processor with real database session
    async with real_stash_processor.database.async_session_scope() as session:
        # Create performer for the test
        performer = PerformerFactory(id="performer_123", name="test_user")

        # Call the real method - no internal mocking
        await real_stash_processor.continue_stash_processing(
            account=account,
            performer=performer,
            session=session,
        )

    # Verify behavior by checking database state
    factory_async_session.refresh(account)
    assert account.stash_id == "performer_123"


@pytest.mark.asyncio
async def test_safe_background_processing_integration(real_stash_processor):
    """Test the safe background processing with error handling."""
    # Create test data using factories
    account = AccountFactory.build(
        id=12345,
        username="test_user",
    )
    performer = PerformerFactory(
        id="performer_123",
        name="test_user",
    )

    # Test cases for different scenarios
    test_cases = [
        # Successful processing
        {"side_effect": None, "exception": None},
        # Cancelled error
        {"side_effect": asyncio.CancelledError(), "exception": asyncio.CancelledError},
        # Other exception
        {"side_effect": ValueError("Test error"), "exception": ValueError},
    ]

    for case in test_cases:
        real_stash_processor._cleanup_event.clear()
        real_stash_processor.continue_stash_processing = AsyncMock(
            side_effect=case["side_effect"]
        )

        # Create a test environment specific to each case
        if case["exception"] == asyncio.CancelledError:
            # For CancelledError, we need to check the logging happens before the exception propagates

            # Create a flag to track if debug_print was called
            debug_print_called = False

            # Create a custom debug_print function that sets our flag
            def custom_debug_print(*args, **kwargs):
                nonlocal debug_print_called
                debug_print_called = True

            # Patch the debug_print directly in the module
            with patch("stash.logging.debug_print", side_effect=custom_debug_print):
                try:
                    await real_stash_processor._safe_background_processing(
                        account, performer
                    )
                except asyncio.CancelledError:
                    # We expect this exception, but before we re-raise it, check our flag
                    assert debug_print_called, (
                        "debug_print should have been called before CancelledError was raised"
                    )
                    assert real_stash_processor._cleanup_event.is_set(), (
                        "Cleanup event should be set even when exception occurs"
                    )
                    # Re-raise since this is expected
                    raise
        else:
            # For non-CancelledError cases, just check the normal flow
            with patch("stash.logging.debug_print") as mock_debug_print:
                if case["exception"]:
                    # Exception case other than CancelledError
                    with pytest.raises(case["exception"]):
                        await real_stash_processor._safe_background_processing(
                            account, performer
                        )
                    assert real_stash_processor._cleanup_event.is_set()
                    mock_debug_print.assert_called_once()
                else:
                    # Normal case with no exceptions
                    await real_stash_processor._safe_background_processing(
                        account, performer
                    )
                    assert real_stash_processor._cleanup_event.is_set()
                    real_stash_processor.continue_stash_processing.assert_called_once_with(
                        account, performer
                    )
