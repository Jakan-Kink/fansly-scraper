"""Pytest fixtures for download testing.

This module provides pytest fixtures for creating and managing download-related
objects in tests. These fixtures create REAL DownloadState instances instead
of using MagicMock.

Usage:
    def test_something(download_state):
        assert download_state.creator_name == "test_creator"
"""

import pytest

from media import MediaItem
from tests.fixtures.download.download_factories import DownloadStateFactory
from tests.fixtures.metadata import MediaFactory


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


@pytest.fixture
def mock_process_media_info(mocker):
    """Create a mock for media.process_media_info that avoids database interaction."""

    async def mock_process(config, batch_info, session=None):
        # Simulate batch processing without database interaction
        if isinstance(batch_info, dict) and "batch" in batch_info:
            batch = batch_info["batch"]
        else:
            batch = [batch_info]

        # Return a mock batch object that simulates database processing
        mock_batch = mocker.Mock()
        mock_batch.is_downloaded = False
        mock_batch.media_items = []

        for info in batch:
            item = MediaItem()
            if info.get("media", {}).get("locations"):
                item.download_url = info["media"]["locations"][0]["location"]
                item.media_id = int(info["media"]["id"])
                item.mimetype = info["media"]["mimetype"]
                item.metadata = info["media"]["locations"][0].get("metadata")
                item.post_id = info.get("postId")
                mock_batch.media_items.append(item)

            if (
                info.get("preview", {}).get("locations")
                and info.get("previewId")
                and config.download_media_previews
            ):
                preview_item = MediaItem()
                preview_item.is_preview = True
                preview_item.preview_id = int(info["previewId"])
                preview_item.media_id = int(info["media"]["id"])
                preview_item.download_url = info["preview"]["locations"][0]["location"]
                preview_item.mimetype = info["preview"]["mimetype"]
                preview_item.metadata = info["preview"]["locations"][0].get("metadata")
                preview_item.post_id = info.get("postId")
                mock_batch.media_items.append(preview_item)

        return mock_batch

    return mocker.patch("download.common.process_media_info", side_effect=mock_process)


@pytest.fixture
def mock_download_media(mocker):
    """Create a mock for download_media that properly records media IDs."""

    async def mock_download(config, state, media_items, session=None):
        # Process each media item
        print(f"\n*** mock_download called with {len(media_items)} items")
        for media_item in media_items:
            print(
                f"  Item: is_preview={media_item.is_preview}, preview_id={getattr(media_item, 'preview_id', None)}, media_id={media_item.media_id}, url={media_item.download_url}"
            )
            if media_item.download_url:
                # Use the correct ID based on whether it's a preview or primary media
                media_id = str(
                    media_item.preview_id
                    if media_item.is_preview and media_item.preview_id
                    else media_item.media_id
                )
                print(f"  -> Adding ID: {media_id}")

                # Only add to state if we have a valid download URL
                if media_item.mimetype.startswith("video"):
                    state.recent_video_media_ids.add(media_id)
                elif media_item.mimetype.startswith("image"):
                    state.recent_photo_media_ids.add(media_id)

        # Return success
        return True

    return mocker.patch("download.common.download_media", side_effect=mock_download)


@pytest.fixture
def mock_parse_media_info(mocker):
    """Create a mock for parse_media_info."""

    def mock_parse_media_info(state, info, post_id):
        # Create MediaItem with the correct attributes from the actual class
        item = MediaItem()

        # Set primary media properties
        if "media" in info:
            item.media_id = int(info["media"]["id"])
            item.mimetype = info["media"]["mimetype"]
            item.created_at = info["media"].get("createdAt", 0)
            if info["media"].get("locations"):
                item.download_url = info["media"]["locations"][0]["location"]
                item.metadata = info["media"]["locations"][0].get("metadata")
                item.file_extension = item.get_download_url_file_extension()

        # Check if this is a preview
        item.is_preview = info.get("previewId") is not None

        # Fix rare bug, of free/paid content being counted as preview
        if item.is_preview and info.get("access", False):
            item.is_preview = False

        # Handle preview content
        if info.get("previewId") and "preview" in info:
            preview_id = int(info["previewId"])
            item.preview_id = preview_id
            item.default_normal_id = preview_id

            # Only use preview content if:
            # 1. Preview downloading is enabled
            # 2. Preview content exists
            # 3. Primary content isn't available
            if (
                state.config.download_media_previews
                and "preview" in info
                and info["preview"].get("locations")
                and not item.download_url
            ):
                item.is_preview = True
                item.mimetype = info["preview"]["mimetype"]
                item.download_url = info["preview"]["locations"][0]["location"]
                item.metadata = info["preview"]["locations"][0].get("metadata")
                item.file_extension = item.get_download_url_file_extension()

        # Add post_id to item
        item.post_id = info.get("postId") or post_id

        return item

    return mocker.patch(
        "download.common.parse_media_info", side_effect=mock_parse_media_info
    )


@pytest.fixture
def mock_process_media_download(mocker):
    """Create a mock for process_media_download that simulates database interaction."""

    async def mock_process_download(config, state, media_item):
        # Always return a new media record using MediaFactory
        media = MediaFactory.build(
            id=media_item.media_id,
            is_downloaded=False,
            content_hash=None,
        )
        return media

    return mocker.patch(
        "metadata.process_media_download", side_effect=mock_process_download
    )


@pytest.fixture
def mock_process_media_bundles(mocker):
    """Create a mock for process_media_bundles that simulates bundle processing."""

    async def mock_bundles(config, account_id, media_bundles, session=None):
        # Just pass through without marking anything as downloaded
        return media_bundles

    return mocker.patch(
        "metadata.account.process_media_bundles", side_effect=mock_bundles
    )


__all__ = [
    "download_state",
    "mock_download_dir",
    "mock_download_media",
    "mock_metadata_dir",
    "mock_parse_media_info",
    "mock_process_media_bundles",
    "mock_process_media_download",
    "mock_process_media_info",
    "mock_temp_dir",
    "test_downloads_dir",
]
