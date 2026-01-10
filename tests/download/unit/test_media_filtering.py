"""Test media filtering functionality."""

import pytest

from download.common import process_download_accessible_media
from download.downloadstate import DownloadState
from download.types import DownloadType


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
async def test_config_factory(tmp_path, uuid_test_db_factory):
    """Create a test configuration with real database."""
    config = uuid_test_db_factory
    config.download_media_previews = True
    config.interactive = False
    config.download_directory = tmp_path
    return config


@pytest.fixture
def state(tmp_path, test_config_factory):
    """Create a test download state."""
    state = DownloadState(
        creator_id="123",
        creator_name="test_creator",
        download_type=DownloadType.TIMELINE,
    )
    state.download_directory = tmp_path
    state.config = test_config_factory
    return state


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


@pytest.mark.asyncio
async def test_media_filtering_previews_enabled(
    mocker,
    test_config_factory,
    state,
    media_infos,
    tmp_path,
    mock_process_media_info,
    mock_download_media,
    mock_parse_media_info,
    mock_process_media_download,
    mock_process_media_bundles,
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
    assert mock_download_media.call_count == 1  # Called once with list of media items
    assert mock_process_media_info.call_count == 1  # Called once for the batch


@pytest.mark.asyncio
async def test_media_filtering_previews_disabled(
    mocker,
    test_config_factory,
    state,
    media_infos,
    tmp_path,
    mock_process_media_info,
    mock_download_media,
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
    assert mock_download_media.call_count == 1  # Called only for primary
    assert mock_process_media_info.call_count == 1  # Called once for the batch


@pytest.mark.asyncio
async def test_media_filtering_only_previews_available(
    mocker,
    test_config_factory,
    state,
    tmp_path,
    mock_process_media_info,
    mock_download_media,
    mock_parse_media_info,
    mock_process_media_download,
    mock_process_media_bundles,
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
    mock_download_media.reset_mock()
    mock_process_media_info.reset_mock()

    await process_download_accessible_media(config, state, media_infos)
    assert state.recent_video_media_ids == {
        "777100000000000003"
    }  # Only preview should be included

    # Verify mocks were called correctly
    assert mock_download_media.call_count == 1  # Called for preview only
    assert mock_process_media_info.call_count == 1  # Called once for the batch

    # Reset state
    state.recent_video_media_ids.clear()

    # Test with previews disabled
    config.download_media_previews = False

    # Reset mock counters
    mock_download_media.reset_mock()
    mock_process_media_info.reset_mock()

    await process_download_accessible_media(config, state, media_infos)
    assert not state.recent_video_media_ids  # Should be empty

    # Verify mocks were called correctly
    assert mock_download_media.call_count == 1  # Called with empty list
    assert mock_process_media_info.call_count == 1  # Still called once for the batch


@pytest.mark.asyncio
async def test_media_filtering_mixed_media_types(
    mocker,
    test_config_factory,
    state,
    tmp_path,
    mock_process_media_info,
    mock_download_media,
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
    mock_download_media.reset_mock()
    mock_process_media_info.reset_mock()

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
    assert (
        mock_download_media.call_count == 1
    )  # Called once with list of all media items
    assert mock_process_media_info.call_count == 1  # Called once for the batch


@pytest.mark.asyncio
async def test_media_filtering_both_inaccessible(
    mocker,
    test_config_factory,
    state,
    tmp_path,
    mock_process_media_info,
    mock_download_media,
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
    mock_download_media.reset_mock()
    mock_process_media_info.reset_mock()

    await process_download_accessible_media(config, state, media_infos)
    assert (
        not state.recent_video_media_ids
    )  # Should be empty since neither is accessible

    # Verify mocks were called correctly
    assert mock_download_media.call_count == 1  # Called with empty list
    assert mock_process_media_info.call_count == 1  # Called once for the batch

    # Test with previews disabled
    config.download_media_previews = False

    # Reset mock counters
    mock_download_media.reset_mock()
    mock_process_media_info.reset_mock()

    await process_download_accessible_media(config, state, media_infos)
    assert not state.recent_video_media_ids  # Should still be empty

    # Verify mocks were called correctly
    assert mock_download_media.call_count == 1  # Called with empty list
    assert mock_process_media_info.call_count == 1  # Called once for the batch


@pytest.mark.asyncio
async def test_media_filtering_primary_only_accessible(
    mocker,
    test_config_factory,
    state,
    tmp_path,
    mock_process_media_info,
    mock_download_media,
    mock_parse_media_info,
    mock_process_media_download,
    mock_process_media_bundles,
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
    mock_download_media.reset_mock()
    mock_process_media_info.reset_mock()

    await process_download_accessible_media(config, state, media_infos)
    assert state.recent_video_media_ids == {
        "777000000000000012"
    }  # Only primary should be included

    # Verify mocks were called correctly
    assert mock_download_media.call_count == 1  # Called for primary only
    assert mock_process_media_info.call_count == 1  # Called once for the batch

    # Reset state
    state.recent_video_media_ids.clear()

    # Test with previews disabled
    config.download_media_previews = False

    # Reset mock counters
    mock_download_media.reset_mock()
    mock_process_media_info.reset_mock()

    await process_download_accessible_media(config, state, media_infos)
    assert state.recent_video_media_ids == {
        "777000000000000012"
    }  # Only primary should be included

    # Verify mocks were called correctly
    assert mock_download_media.call_count == 1  # Called for primary only
    assert mock_process_media_info.call_count == 1  # Called once for the batch
