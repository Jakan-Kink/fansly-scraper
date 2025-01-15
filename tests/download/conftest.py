from pathlib import Path
from unittest.mock import MagicMock

import pytest

from config import FanslyConfig
from download.core import DownloadState


@pytest.fixture
def mock_config():
    config = MagicMock(spec=FanslyConfig)
    config.download_path = Path("/test/download/path")
    config.program_version = "0.0.0-test"
    return config


@pytest.fixture
def download_state():
    state = DownloadState()
    state.creator_name = "test_creator"
    return state
