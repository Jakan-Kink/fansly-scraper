"""Test to demonstrate cleanup enforcement mechanism.

This test intentionally violates the cleanup pattern to demonstrate that
the pytest hook properly enforces the requirement.

The test SHOULD FAIL because:
1. It uses stash_client fixture
2. It does NOT use stash_cleanup_tracker fixture
3. The pytest_collection_modifyitems hook marks it as xfail(strict=True)
4. If the test passes, strict mode converts it to a failure

This demonstrates the enforcement mechanism is working correctly.

See tests/stash/CLEANUP_ENFORCEMENT_SUMMARY.md for full documentation.
"""

import pytest

from stash import StashClient


@pytest.mark.asyncio
async def test_missing_cleanup_tracker(stash_client: StashClient) -> None:
    """Test that intentionally violates cleanup pattern.

    This test deliberately uses stash_client without stash_cleanup_tracker
    to demonstrate enforcement.

    Expected behavior:
    - Test is marked with xfail(strict=True) by pytest hook
    - If Stash is unavailable: Test skips (expected)
    - If Stash is available and test passes: Fails due to strict mode (enforcement working)
    - If Stash is available and test fails: Fails normally (expected)

    The enforcement message should be:
    "Tests using stash_client MUST also use stash_cleanup_tracker
    for test isolation and cleanup. See tests/stash/CLEANUP_ENFORCEMENT_SUMMARY.md"
    """
    # This test does minimal work - just verifies client is available
    # The enforcement happens at collection time, not execution time
    assert stash_client is not None

    # We don't actually want to create any objects since we can't clean them up!
    # This test is just to demonstrate the enforcement mechanism.
    # The pytest hook should have already marked this test as xfail.
