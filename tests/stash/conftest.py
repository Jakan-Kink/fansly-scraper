"""Test configuration and fixtures for Stash tests."""

import asyncio
import logging
from collections.abc import AsyncGenerator, Generator

import pytest
import pytest_asyncio

from stash import StashClient, StashContext


@pytest.fixture(scope="session")
def event_loop() -> Generator[asyncio.AbstractEventLoop, None, None]:
    """Create an event loop for the test session."""
    policy = asyncio.get_event_loop_policy()
    loop = policy.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture
async def stash_context() -> AsyncGenerator[StashContext, None]:
    """Create a StashContext for testing."""
    context = StashContext(
        conn={
            "Scheme": "http",
            "Host": "localhost",
            "Port": 9999,
            "ApiKey": "test_api_key",
            "Logger": logging.getLogger("stash.test"),
        },
        verify_ssl=False,
    )
    yield context
    await context.close()


@pytest_asyncio.fixture
async def stash_client(
    stash_context: StashContext,
) -> AsyncGenerator[StashClient, None]:
    """Create a StashClient for testing."""
    yield stash_context.client
