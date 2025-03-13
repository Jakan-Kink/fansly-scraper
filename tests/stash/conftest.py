"""Test configuration and fixtures for Stash tests."""

import asyncio
import contextlib
import logging
from collections.abc import AsyncGenerator, Generator
from unittest.mock import AsyncMock, MagicMock

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


@pytest.fixture
def stash_client() -> StashClient:
    """Create a mock StashClient for testing."""
    # Create a mock client
    mock_client = MagicMock(spec=StashClient)
    mock_client._initialized = True
    mock_client.client = MagicMock()
    mock_client.client.headers = {}

    # Mock gql.Client async methods
    mock_client.client.__aenter__ = AsyncMock(return_value=mock_client.client)
    mock_client.client.connect_async = AsyncMock(return_value=mock_client.client)
    mock_client.client.fetch_schema = AsyncMock()

    # Mock transports
    mock_client.http_transport = MagicMock()
    mock_client.http_transport.connect = AsyncMock()
    mock_client.http_transport.close = AsyncMock()

    mock_client.ws_transport = MagicMock()
    mock_client.ws_transport.connect = AsyncMock()
    mock_client.ws_transport.close = AsyncMock()

    # Create a mock async context manager for subscriptions
    class MockAsyncContextManager:
        def __init__(self, exception_message="Failed to connect"):
            self.exception_message = exception_message

        async def __aenter__(self):
            # Return a mock async iterator
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            return False

        def __aiter__(self):
            return self

        async def __anext__(self):
            # Raise the expected exception
            raise Exception(self.exception_message)

    # Set up subscription methods to return the mock context manager
    mock_client.subscribe_to_jobs.return_value = MockAsyncContextManager()
    mock_client.subscribe_to_logs.return_value = MockAsyncContextManager()
    mock_client.subscribe_to_scan_complete.return_value = MockAsyncContextManager()

    # Set up other async methods
    mock_client.initialize = AsyncMock()
    mock_client.close = AsyncMock()
    mock_client.wait_for_job_with_updates = AsyncMock(return_value=True)

    # Set up mock responses for create methods
    mock_client.execute = AsyncMock()
    mock_client.execute.side_effect = lambda query, variables=None: (
        {"performerCreate": {"id": "123", "name": "Test Account"}}
        if "performerCreate" in query
        else (
            {
                "galleryCreate": {
                    "id": "123",
                    "title": "Test Gallery",
                    "details": "Test gallery details",
                    "date": "2024-01-01",
                    "urls": ["https://example.com/gallery"],
                    "photographer": "Test Photographer",
                    "rating100": 85,
                    "organized": True,
                    "image_count": 10,
                    "studio": {"id": "456", "name": "Test Studio"},
                    "performers": [{"id": "789", "name": "Test Performer"}],
                    "tags": [{"id": "012", "name": "Test Tag"}],
                }
            }
            if "galleryCreate" in query
            else {}
        )
    )

    # Return the mock client
    return mock_client
