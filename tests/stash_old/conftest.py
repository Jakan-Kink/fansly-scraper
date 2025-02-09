"""Shared test fixtures for stash module tests."""

from datetime import datetime, timezone

import pytest
from stashapi.stashapp import StashInterface


@pytest.fixture
def mock_stash_interface(mocker):
    """Mock StashInterface for testing."""
    mock = mocker.Mock(spec=StashInterface)
    return mock


@pytest.fixture
def utc_now():
    """Get current UTC datetime for testing."""
    return datetime.now(timezone.utc)
