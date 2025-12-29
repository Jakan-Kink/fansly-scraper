"""Test isolation utilities for pytest-xdist parallel execution.

Provides helper functions to generate unique identifiers per worker to prevent
race conditions and data collisions when running tests in parallel.
"""

import os
import time


def get_unique_test_id() -> str:
    """Get unique identifier for test data (titles, names, etc.).

    Combines monotonic_ns() with pytest-xdist worker ID to create unique strings.
    Use this in tests for any data that might collide in parallel runs:
    - Account usernames
    - Gallery titles/codes
    - Performer names
    - Post/Message content identifiers

    Returns:
        str: Unique identifier like "gw0_123456" or "master_123456"

    Example:
        from tests.fixtures.utils.test_isolation import get_unique_test_id

        test_id = get_unique_test_id()
        account = AccountFactory(username=f"user_{test_id}")
        gallery_title = f"Gallery - {test_id}"
        performer_name = f"Performer {test_id}"
    """
    worker_id = os.environ.get("PYTEST_XDIST_WORKER", "master")
    time_part = int(time.monotonic_ns()) % (2**20)  # ~1M unique values per worker
    return f"{worker_id}_{time_part}"


def get_worker_id() -> str:
    """Get pytest-xdist worker ID.

    Returns:
        str: Worker identifier like "gw0", "gw1", etc., or "master" for single-process runs

    Example:
        from tests.fixtures.utils.test_isolation import get_worker_id

        worker = get_worker_id()
        print(f"Running in worker: {worker}")
    """
    return os.environ.get("PYTEST_XDIST_WORKER", "master")
