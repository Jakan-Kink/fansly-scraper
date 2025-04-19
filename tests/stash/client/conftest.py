"""Test configuration and fixtures for Stash client tests."""

from unittest.mock import AsyncMock, MagicMock

import pytest

# Import common fixtures from parent conftest.py
# These fixtures provide the core functionality for interacting with Stash
from ..conftest import mock_client  # Mock GraphQL client
from ..conftest import mock_session  # Mock SQLAlchemy session
from ..conftest import mock_transport  # Mock GraphQL transport
from ..conftest import stash_client  # StashClient for making API calls
from ..conftest import stash_context  # StashContext for connecting to Stash server
from ..conftest import (
    test_query,  # Core Stash connection fixtures; Mock objects for testing without a real server; Sample GraphQL query
)

# Add client-specific fixtures below


@pytest.fixture
def mock_client_mixin():
    """Create a mock client mixin for testing StashClient mixins.

    This fixture provides a mock implementation of a client mixin with common
    methods used by various StashClient mixins (like PerformerMixin, SceneMixin, etc.)
    mocked for testing. This allows testing mixin functionality without requiring
    a full client implementation.

    The mock includes essential methods that most mixins would use:
    - execute: For making GraphQL requests
    - find_by_id: For retrieving a single object by ID
    - find_all: For retrieving multiple objects

    Returns:
        MagicMock: A mock client mixin with common methods configured as AsyncMocks
    """
    mixin = MagicMock()
    mixin.execute = AsyncMock()
    mixin.find_by_id = AsyncMock()
    mixin.find_all = AsyncMock()
    return mixin
