"""Pytest fixtures for download testing.

This module provides pytest fixtures for creating and managing download-related
objects in tests. These fixtures create REAL DownloadState instances instead
of using MagicMock.

Usage:
    def test_something(download_state):
        assert download_state.creator_name == "test_creator"
"""

from pathlib import Path

import pytest

from tests.fixtures.download.download_factories import DownloadStateFactory


@pytest.fixture
def download_state():
    """Create a real DownloadState for testing.

    Returns:
        DownloadState: Real download state instance with test defaults

    Example:
        def test_download(download_state):
            download_state.creator_name = "mycreator"
            assert download_state.creator_name == "mycreator"
    """
    state = DownloadStateFactory()
    state.creator_name = "test_creator"
    return state


@pytest.fixture
def test_downloads_dir(tmp_path):
    """Create a temporary downloads directory.

    Args:
        tmp_path: Pytest's temporary path fixture

    Returns:
        Path: Path to temporary downloads directory

    Example:
        def test_file_download(test_downloads_dir):
            file_path = test_downloads_dir / "test.mp4"
            file_path.write_text("test data")
            assert file_path.exists()
    """
    downloads_dir = tmp_path / "downloads"
    downloads_dir.mkdir()
    return downloads_dir


@pytest.fixture
def mock_download_dir(temp_config_dir):
    """Create a mock download directory for testing.

    Args:
        temp_config_dir: Temporary config directory fixture

    Returns:
        Path: Path to download directory within temp config dir

    Note:
        This fixture depends on temp_config_dir from core.config_fixtures
    """
    download_dir = temp_config_dir / "downloads"
    download_dir.mkdir()
    return download_dir


@pytest.fixture
def mock_metadata_dir(temp_config_dir):
    """Create a mock metadata directory for testing.

    Args:
        temp_config_dir: Temporary config directory fixture

    Returns:
        Path: Path to metadata directory within temp config dir

    Note:
        This fixture depends on temp_config_dir from core.config_fixtures
    """
    metadata_dir = temp_config_dir / "metadata"
    metadata_dir.mkdir()
    return metadata_dir


@pytest.fixture
def mock_temp_dir(temp_config_dir):
    """Create a mock temporary directory for testing.

    Args:
        temp_config_dir: Temporary config directory fixture

    Returns:
        Path: Path to temp directory within temp config dir

    Note:
        This fixture depends on temp_config_dir from core.config_fixtures
    """
    temp_dir = temp_config_dir / "temp"
    temp_dir.mkdir()
    return temp_dir


__all__ = [
    "download_state",
    "mock_download_dir",
    "mock_metadata_dir",
    "mock_temp_dir",
    "test_downloads_dir",
]
