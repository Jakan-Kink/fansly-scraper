"""Configuration for API tests."""

import pytest


@pytest.fixture
def api_fixture():
    """Example fixture for API tests."""
    return {"test": "data"}
