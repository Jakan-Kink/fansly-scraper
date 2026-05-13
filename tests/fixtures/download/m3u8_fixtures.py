"""Fixtures for download/m3u8.py integration tests.

Provides a ``MagicMock(spec=FanslyConfig)`` wrapping a real ``FanslyApi``
so production code can call ``config.get_api()`` and receive an api
whose HTTP layer is interceptable by respx.
"""

from unittest.mock import MagicMock

import pytest

from config.fanslyconfig import FanslyConfig


@pytest.fixture
def m3u8_mock_config(fansly_api_factory):
    """FanslyConfig MagicMock whose ``get_api()`` returns a real FanslyApi.

    Each test gets its own pre-bootstrap api instance; respx routes
    registered in the test intercept HTTP at the edge.
    """
    config = MagicMock(spec=FanslyConfig)
    api = fansly_api_factory()
    config.get_api.side_effect = lambda: api
    return config
