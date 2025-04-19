"""Test media filtering functionality."""

import contextlib
from contextlib import asynccontextmanager

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from config import FanslyConfig
from download.common import process_download_accessible_media
from download.downloadstate import DownloadState
from download.types import DownloadType
from media import MediaItem
from metadata.account import Account, AccountMedia
from metadata.base import Base
from metadata.media import Media


@pytest.fixture
def mock_create_dir(mocker, tmp_path):
    """Create a mock for set_create_directory_for_download."""

    async def mock_create_dir(*args, **kwargs):
        return tmp_path

    return mocker.patch(
        "pathio.pathio.set_create_directory_for_download", side_effect=mock_create_dir
    )


def create_media_info(
    media_id: int,
    preview_id: int | None = None,
    has_access: bool = True,
    url: str | None = None,
    mimetype: str = "video/mp4",
    preview_url: str | None = None,
    post_id: str | None = None,
    account_id: str = "555000000000000000",  # Anonymized test account ID
) -> dict:
    """Create a test media info dictionary with proper metadata."""
    # Create metadata for m3u8 files
    location_metadata = None
    if url and ".m3u8" in url:
        location_metadata = {
            "Policy": "test-policy",
            "Key-Pair-Id": "test-key-pair-id",
            "Signature": "test-signature",
        }

    media = {
        "id": str(media_id),  # API returns string IDs
        "previewId": str(preview_id) if preview_id else None,  # API returns string IDs
        "access": has_access,
        "accountId": account_id,
        "postId": post_id,  # Add post_id to media info
        "media": {
            "id": str(media_id),  # API returns string IDs
            "url": url,
            "mimetype": mimetype,
            "locations": (
                [
                    {
                        "location": url,
                        "locationId": str(media_id + 1),
                        "metadata": location_metadata,
                    }
                ]
                if url
                else []  # Add metadata if URL exists
            ),
            "createdAt": 1712345678,  # Anonymized test timestamp
            "variants": [],
            "height": 1080,
            "width": 1920,
        },
    }

    if preview_id:
        preview_metadata = None
        if preview_url and ".m3u8" in preview_url:
            preview_metadata = {
                "Policy": "test-policy",
                "Key-Pair-Id": "test-key-pair-id",
                "Signature": "test-signature",
            }

        media["preview"] = {
            "id": str(preview_id),  # API returns string IDs
            "url": preview_url,
            "mimetype": mimetype,
            "locations": (
                [
                    {
                        "location": preview_url,
                        "locationId": str(preview_id + 1),
                        "metadata": preview_metadata,
                    }
                ]
                if preview_url
                else []  # Add metadata if URL exists
            ),
            "createdAt": 1712345670,  # Slightly earlier test timestamp
            "variants": [],
            "height": 720,
            "width": 1280,
        }

    return media


@pytest.fixture
async def test_config_factory(tmp_path, mocker):
    """Create a test configuration with mocked database."""
    config = FanslyConfig(program_version="1.0.0")
    config.download_media_previews = True
    config.interactive = False
    config.download_directory = tmp_path

    # Create a mock query result that always indicates media is not yet downloaded
    mock_result = mocker.Mock()
    mock_result.scalars = lambda: mock_result
    mock_result.all = lambda: []  # No existing media
    mock_result.scalar_one_or_none = lambda: None  # No existing media

    # Create a mock Media object that always indicates not downloaded
    mock_media = mocker.Mock()
    mock_media.is_downloaded = False
    mock_media.id = None  # New media item

    # Create a mock session that returns our mock media
    mock_session = mocker.AsyncMock()
    mock_session.execute.return_value = mock_result
    mock_session.in_transaction.return_value = False

    # Mock get to return our mock media object
    mock_session.get = mocker.AsyncMock(return_value=mock_media)

    # Create async context manager for nested transactions
    nested_ctx = mocker.AsyncMock()
    nested_ctx.__aenter__.return_value = nested_ctx
    mock_session.begin_nested.return_value = nested_ctx

    # Create a proper async context manager for the database
    @asynccontextmanager
    async def mock_session_scope():
        try:
            yield mock_session
        finally:
            await mock_session.close()

    # Create a mock database with async session scope
    mock_db = mocker.AsyncMock()
    mock_db.async_session_scope = mock_session_scope

    config._database = mock_db
    return config


@pytest.fixture
def state(tmp_path):
    """Create a test download state."""
    state = DownloadState(
        creator_id="123",
        creator_name="test_creator",
        download_type=DownloadType.TIMELINE,
    )
    state.download_directory = tmp_path
    return state


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
                item.post_id = info.get("postId")  # Add post_id
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
                preview_item.post_id = info.get("postId")  # Add post_id
                mock_batch.media_items.append(preview_item)

        return mock_batch

    return mocker.patch("download.common.process_media_info", side_effect=mock_process)


@pytest.fixture
def mock_download(mocker):
    """Create a mock for download_media that properly records media IDs."""

    async def mock_download(config, state, media_items, session=None):
        # Process each media item
        for media_item in media_items:
            if media_item.download_url:
                # Use the correct ID based on whether it's a preview or primary media
                media_id = str(
                    media_item.preview_id
                    if media_item.is_preview and media_item.preview_id
                    else media_item.media_id
                )

                # Only add to state if we have a valid download URL
                if media_item.mimetype.startswith("video"):
                    state.recent_video_media_ids.add(media_id)
                elif media_item.mimetype.startswith("image"):
                    state.recent_photo_media_ids.add(media_id)

        # Return success
        return True

    return mocker.patch("download.media.download_media", side_effect=mock_download)


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

    return mocker.patch("media.parse_media_info", side_effect=mock_parse_media_info)


@pytest.fixture
def media_infos():
    """Create test media info dictionaries."""
    return [
        # Primary media (non-preview) with URL
        create_media_info(
            media_id=777000000000000001,  # Anonymized test media ID
            has_access=True,
            url="http://example.com/primary1.mp4",
            post_id="888000000000000001",  # Add post ID
        ),
        # Preview media with URL
        create_media_info(
            media_id=777000000000000002,  # Anonymized test media ID
            preview_id=777100000000000001,  # Anonymized test preview ID
            has_access=False,
            url=None,  # No URL for primary
            preview_url="http://example.com/preview1.mp4",  # But URL for preview
            post_id="888000000000000001",  # Add post ID
        ),
        # Primary media without URL (inaccessible)
        create_media_info(
            media_id=777000000000000003,  # Anonymized test media ID
            has_access=False,
            post_id="888000000000000002",  # Add post ID
        ),
        # Preview media without URL (inaccessible)
        create_media_info(
            media_id=777000000000000004,  # Anonymized test media ID
            preview_id=777100000000000002,  # Anonymized test preview ID
            has_access=False,
            post_id="888000000000000002",  # Add post ID
        ),
    ]


@pytest.fixture
def mock_process_media_download(mocker):
    """Create a mock for process_media_download that simulates database interaction."""

    async def mock_process_download(config, state, media_item):
        # Always return a new media record
        mock_media = mocker.Mock()
        mock_media.is_downloaded = False  # Never mark as downloaded
        mock_media.id = media_item.media_id
        mock_media.content_hash = None  # No existing hash
        return mock_media

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


@pytest.mark.asyncio
async def test_media_filtering_previews_enabled(
    mocker,
    test_config_factory,
    state,
    media_infos,
    tmp_path,
    mock_process_media_info,
    mock_download,
    mock_create_dir,
    mock_parse_media_info,
    mock_process_media_download,
    mock_process_media_bundles,  # Add new mock
):
    """Test media filtering when preview downloading is enabled."""
    # Mock input to avoid interactive prompts
    mocker.patch("builtins.input", return_value="")
    config = test_config_factory
    config.download_media_previews = True
    config.interactive = False

    # Process media
    await process_download_accessible_media(config, state, media_infos)

    # Verify both primary and preview media with URLs were included
    assert state.recent_video_media_ids == {
        "777000000000000001",  # Primary media with URL
        "777100000000000001",  # Preview with URL
    }

    # Verify mocks were called correctly
    assert mock_download.call_count == 2  # Called for both primary and preview
    assert mock_process_media_info.call_count == 1  # Called once for the batch
    assert mock_create_dir.call_count == 1  # Called once to set up directory


@pytest.mark.asyncio
async def test_media_filtering_previews_disabled(
    mocker,
    test_config_factory,
    state,
    media_infos,
    tmp_path,
    mock_process_media_info,
    mock_download,
    mock_create_dir,
    mock_parse_media_info,
):
    """Test media filtering when preview downloading is disabled."""
    # Mock input to avoid interactive prompts
    mocker.patch("builtins.input", return_value="")
    config = test_config_factory
    config.download_media_previews = False
    config.interactive = False

    # Reset the state
    state.recent_video_media_ids.clear()
    state.recent_photo_media_ids.clear()

    # Process media
    await process_download_accessible_media(config, state, media_infos)

    # Verify only primary media with URL was included
    assert state.recent_video_media_ids == {
        "777000000000000001"
    }  # Updated to use realistic ID

    # Verify mocks were called correctly
    assert mock_download.call_count == 1  # Called only for primary
    assert mock_process_media_info.call_count == 1  # Called once for the batch
    assert mock_create_dir.call_count == 1  # Called once to set up directory


@pytest.mark.asyncio
async def test_media_filtering_only_previews_available(
    mocker,
    test_config_factory,
    state,
    tmp_path,
    mock_process_media_info,
    mock_download,
    mock_create_dir,
    mock_parse_media_info,
    mock_process_media_download,
    mock_process_media_bundles,  # Add new mock
):
    """Test media filtering when only preview media is available (e.g., unsubscribed)."""
    # Mock input to avoid interactive prompts
    mocker.patch("builtins.input", return_value="")
    config = test_config_factory
    config.interactive = False

    # Reset the state
    state.recent_video_media_ids.clear()
    state.recent_photo_media_ids.clear()

    # Create media items where only previews have URLs
    media_infos = [
        # Primary media without URL (inaccessible)
        create_media_info(
            media_id=777000000000000005,  # Anonymized test media ID
            has_access=False,
            post_id="888000000000000003",  # Add post ID
        ),
        # Preview media with URL
        create_media_info(
            media_id=777000000000000006,  # Anonymized test media ID
            preview_id=777100000000000003,  # Anonymized test preview ID
            has_access=False,
            preview_url="http://example.com/preview1.mp4",
            post_id="888000000000000003",  # Add post ID
        ),
    ]

    # Test with previews enabled
    config.download_media_previews = True

    # Reset mock counters
    mock_download.reset_mock()
    mock_process_media_info.reset_mock()
    mock_create_dir.reset_mock()

    await process_download_accessible_media(config, state, media_infos)
    assert state.recent_video_media_ids == {
        "777100000000000003"
    }  # Only preview should be included

    # Verify mocks were called correctly
    assert mock_download.call_count == 1  # Called for preview only
    assert mock_process_media_info.call_count == 1  # Called once for the batch
    assert mock_create_dir.call_count == 1  # Called once to set up directory

    # Reset state
    state.recent_video_media_ids.clear()

    # Test with previews disabled
    config.download_media_previews = False

    # Reset mock counters
    mock_download.reset_mock()
    mock_process_media_info.reset_mock()
    mock_create_dir.reset_mock()

    await process_download_accessible_media(config, state, media_infos)
    assert not state.recent_video_media_ids  # Should be empty

    # Verify mocks were called correctly
    assert mock_download.call_count == 0  # Nothing should be downloaded
    assert mock_process_media_info.call_count == 1  # Still called once for the batch
    assert mock_create_dir.call_count == 1  # Still called once to set up directory


@pytest.mark.asyncio
async def test_media_filtering_mixed_media_types(
    mocker,
    test_config_factory,
    state,
    tmp_path,
    mock_process_media_info,
    mock_download,
    mock_create_dir,
    mock_parse_media_info,
):
    """Test media filtering with mixed media types (images, videos)."""
    # Mock input to avoid interactive prompts
    mocker.patch("builtins.input", return_value="")
    config = test_config_factory
    config.download_media_previews = True
    config.interactive = False

    # Reset the state
    state.recent_video_media_ids.clear()
    state.recent_photo_media_ids.clear()

    # Create media items with different types
    media_infos = [
        # Primary video
        create_media_info(
            media_id=777000000000000007,
            has_access=True,
            url="http://example.com/video1.mp4",
            mimetype="video/mp4",
        ),
        # Preview video
        create_media_info(
            media_id=777000000000000008,
            preview_id=777100000000000004,
            has_access=False,
            url=None,
            preview_url="http://example.com/preview1.mp4",
            mimetype="video/mp4",
        ),
        # Primary image
        create_media_info(
            media_id=777000000000000009,
            has_access=True,
            url="http://example.com/image1.jpg",
            mimetype="image/jpeg",
        ),
        # Preview image
        create_media_info(
            media_id=777000000000000010,
            preview_id=777100000000000005,
            has_access=False,
            url=None,
            preview_url="http://example.com/preview1.jpg",
            mimetype="image/jpeg",
        ),
    ]

    # Reset mock counters
    mock_download.reset_mock()
    mock_process_media_info.reset_mock()
    mock_create_dir.reset_mock()

    # Process media
    await process_download_accessible_media(config, state, media_infos)

    # Verify media was correctly categorized
    assert state.recent_video_media_ids == {
        "777000000000000007",  # Primary video
        "777100000000000004",  # Preview video
    }
    assert state.recent_photo_media_ids == {
        "777000000000000009",  # Primary image
        "777100000000000005",  # Preview image
    }

    # Verify mocks were called correctly
    assert mock_download.call_count == 4  # Called for all media items
    assert mock_process_media_info.call_count == 1  # Called once for the batch
    assert mock_create_dir.call_count == 1  # Called once to set up directory


@pytest.mark.asyncio
async def test_media_filtering_both_inaccessible(
    mocker,
    test_config_factory,
    state,
    tmp_path,
    mock_process_media_info,
    mock_download,
    mock_create_dir,
    mock_parse_media_info,
):
    """Test media filtering when both primary and preview media are inaccessible."""
    mocker.patch("builtins.input", return_value="")
    config = test_config_factory
    config.interactive = False

    # Reset the state
    state.recent_video_media_ids.clear()
    state.recent_photo_media_ids.clear()

    # Create media items where neither primary nor preview have URLs
    media_infos = [
        create_media_info(
            media_id=777000000000000011,  # Anonymized test media ID
            preview_id=777100000000000006,  # Anonymized test preview ID
            has_access=False,
        ),
    ]

    # Test with previews enabled
    config.download_media_previews = True

    # Reset mock counters
    mock_download.reset_mock()
    mock_process_media_info.reset_mock()
    mock_create_dir.reset_mock()

    await process_download_accessible_media(config, state, media_infos)
    assert (
        not state.recent_video_media_ids
    )  # Should be empty since neither is accessible

    # Verify mocks were called correctly
    assert mock_download.call_count == 0  # Nothing should be downloaded
    assert mock_process_media_info.call_count == 1  # Called once for the batch
    assert mock_create_dir.call_count == 1  # Still called once to set up directory

    # Test with previews disabled
    config.download_media_previews = False

    # Reset mock counters
    mock_download.reset_mock()
    mock_process_media_info.reset_mock()
    mock_create_dir.reset_mock()

    await process_download_accessible_media(config, state, media_infos)
    assert not state.recent_video_media_ids  # Should still be empty

    # Verify mocks were called correctly
    assert mock_download.call_count == 0  # Nothing should be downloaded
    assert mock_process_media_info.call_count == 1  # Called once for the batch
    assert mock_create_dir.call_count == 1  # Still called once to set up directory


@pytest.mark.asyncio
async def test_media_filtering_primary_only_accessible(
    mocker,
    test_config_factory,
    state,
    tmp_path,
    mock_process_media_info,
    mock_download,
    mock_create_dir,
    mock_parse_media_info,
    mock_process_media_download,
    mock_process_media_bundles,  # Add new mock
):
    """Test media filtering when primary media is accessible but preview is not."""
    mocker.patch("builtins.input", return_value="")
    config = test_config_factory
    config.interactive = False

    # Reset the state
    state.recent_video_media_ids.clear()
    state.recent_photo_media_ids.clear()

    # Create media items where primary has URL but preview doesn't
    media_infos = [
        create_media_info(
            media_id=777000000000000012,  # Anonymized test media ID
            preview_id=777100000000000007,  # Anonymized test preview ID
            has_access=True,
            url="http://example.com/primary1.mp4",
        ),
    ]

    # Test with previews enabled
    config.download_media_previews = True

    # Reset mock counters
    mock_download.reset_mock()
    mock_process_media_info.reset_mock()
    mock_create_dir.reset_mock()

    await process_download_accessible_media(config, state, media_infos)
    assert state.recent_video_media_ids == {
        "777000000000000012"
    }  # Only primary should be included

    # Verify mocks were called correctly
    assert mock_download.call_count == 1  # Called for primary only
    assert mock_process_media_info.call_count == 1  # Called once for the batch
    assert mock_create_dir.call_count == 1  # Called once to set up directory

    # Reset state
    state.recent_video_media_ids.clear()

    # Test with previews disabled
    config.download_media_previews = False

    # Reset mock counters
    mock_download.reset_mock()
    mock_process_media_info.reset_mock()
    mock_create_dir.reset_mock()

    await process_download_accessible_media(config, state, media_infos)
    assert state.recent_video_media_ids == {
        "777000000000000012"
    }  # Only primary should be included

    # Verify mocks were called correctly
    assert mock_download.call_count == 1  # Called for primary only
    assert mock_process_media_info.call_count == 1  # Called once for the batch
    assert mock_create_dir.call_count == 1  # Called once to set up directory
