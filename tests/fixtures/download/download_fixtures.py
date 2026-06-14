"""General download object fixtures."""

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from download.globalstate import GlobalState
from download.types import DownloadType
from metadata.models import Media
from tests.fixtures.download.download_factories import DownloadStateFactory
from tests.fixtures.utils.test_isolation import snowflake_id


@pytest.fixture
def global_state():
    """Create a test global state."""
    return GlobalState()


@pytest.fixture
def timeline_download_state():
    """A DownloadState for a timeline download (creator set, TIMELINE type)."""
    return DownloadStateFactory(
        creator_name="test_creator",
        download_type=DownloadType.TIMELINE,
    )


@pytest.fixture
def notset_download_state():
    """A DownloadState with creator + base_path set but download_type NOTSET."""
    return DownloadStateFactory(
        creator_name="test_creator",
        download_type=DownloadType.NOTSET,
        base_path=Path("/test/path"),
    )


@pytest.fixture
def filtered_media_list():
    """Media objects that already passed filtering — download URLs set."""
    account_id = snowflake_id()
    return [
        Media(
            id=snowflake_id(),
            accountId=account_id,
            mimetype="video/mp4",
            download_url="http://example.com/video1.mp4",
        ),
        Media(
            id=snowflake_id(),
            accountId=account_id,
            mimetype="image/jpeg",
            download_url="http://example.com/image1.jpg",
        ),
    ]


@pytest.fixture
def accessible_media():
    """Media objects for process_download_accessible_media (one preview, one not)."""
    account_id = snowflake_id()
    return [
        Media(
            id=snowflake_id(),
            accountId=account_id,
            mimetype="image/jpeg",
            download_url="http://example.com/photo1.jpg",
            is_preview=False,
        ),
        Media(
            id=snowflake_id(),
            accountId=account_id,
            mimetype="video/mp4",
            download_url="http://example.com/video1.mp4",
            is_preview=True,
        ),
    ]


@pytest.fixture
def synthetic_timeline_page():
    """Timeline-like page data: three synthetic posts from one account."""
    account_id = snowflake_id()
    return {
        "posts": [
            {"id": snowflake_id(), "accountId": account_id},
            {"id": snowflake_id(), "accountId": account_id},
            {"id": snowflake_id(), "accountId": account_id},
        ],
    }


@pytest.fixture
def mock_download_media():
    """Mock for the download_media function."""
    with patch("download.common.download_media", new_callable=AsyncMock) as mock:
        yield mock


@pytest.fixture
def mock_set_create_directory():
    """Mock for the set_create_directory_for_download function."""
    with patch("download.common.set_create_directory_for_download") as mock:
        yield mock


__all__ = [
    "accessible_media",
    "filtered_media_list",
    "global_state",
    "mock_download_media",
    "mock_set_create_directory",
    "notset_download_state",
    "synthetic_timeline_page",
    "timeline_download_state",
]
