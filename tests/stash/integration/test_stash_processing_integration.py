"""Integration tests for stash processing module.

Uses real database fixtures and Stash type factories instead of mocks.
"""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from tests.fixtures import AccountFactory
from tests.fixtures.stash.stash_type_factories import PerformerFactory
from tests.fixtures.utils.test_isolation import snowflake_id


@pytest.mark.asyncio
async def test_safe_background_processing_integration(real_stash_processor):
    """Test the safe background processing with error handling."""
    # Create test data using factories
    acct_id = snowflake_id()
    account = AccountFactory.build(
        id=acct_id,
        username="test_user",
    )
    performer = PerformerFactory(
        id="5132",
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
