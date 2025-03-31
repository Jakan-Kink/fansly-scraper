"""Test media filtering functionality."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from config import FanslyConfig
from download.common import process_download_accessible_media
from download.downloadstate import DownloadState
from download.types import DownloadType
from media import MediaItem


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
    account_id: str = "123456789",
) -> dict:
    """Create a test media info dictionary with proper metadata."""
    media = {
        "id": media_id,
        "previewId": preview_id,
        "access": has_access,
        "accountId": account_id,
        "media": {
            "id": media_id,
            "url": url,
            "mimetype": mimetype,
            "locations": (
                [{"location": url, "locationId": media_id * 100}] if url else []
            ),
            "createdAt": 1234567890,  # Integer timestamp
            "variants": [],
            "height": 1080,
            "width": 1920,
        },
    }

    if preview_id:
        media["preview"] = {
            "id": preview_id,
            "url": preview_url,
            "mimetype": mimetype,
            "locations": (
                [{"location": preview_url, "locationId": preview_id * 100}]
                if preview_url
                else []
            ),
            "createdAt": 1234567890,  # Integer timestamp
            "variants": [],
            "height": 720,
            "width": 1280,
        }

    return media


@pytest.fixture
def test_config_factory(tmp_path, mocker):
    """Create a test configuration."""
    config = FanslyConfig(program_version="1.0.0")
    config.download_media_previews = True
    config.interactive = False
    config.download_directory = tmp_path

    # Create async context manager for database sessions
    class AsyncSessionContextManager:
        async def __aenter__(self):
            self.session = AsyncSession(
                bind=create_async_engine(
                    "sqlite+aiosqlite:///file:test_media_filtering?mode=memory&cache=shared&uri=true",
                    future=True,
                    connect_args={"check_same_thread": False},
                )
            )
            return self.session

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            await self.session.rollback()
            await self.session.close()

    # Mock database with async session support
    mock_db = mocker.Mock()
    mock_db.async_session_scope = AsyncSessionContextManager
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
    """Create a mock for process_media_info."""

    async def mock_process_media_info(config, batch_info):
        # Create a mock MediaBatch with required attributes
        mock_batch = mocker.Mock()
        mock_batch.is_downloaded = True
        mock_batch.media = []
        return mock_batch

    return mocker.patch(
        "metadata.process_media_info", side_effect=mock_process_media_info
    )


@pytest.fixture
def mock_download(mocker):
    """Create a mock for download_media that properly records media IDs."""

    async def mock_download(media_item, state, post_id):
        # Capture the media ID in the right set based on mimetype
        if media_item.mimetype.startswith("video"):
            if media_item.media_id:
                state.recent_video_media_ids.add(str(media_item.media_id))
            if media_item.preview_id:
                state.recent_video_media_ids.add(str(media_item.preview_id))
        elif media_item.mimetype.startswith("image"):
            if media_item.media_id:
                state.recent_photo_media_ids.add(str(media_item.media_id))
            if media_item.preview_id:
                state.recent_photo_media_ids.add(str(media_item.preview_id))
        return True

    return mocker.patch("download.media.download_media", side_effect=mock_download)


@pytest.fixture
def mock_parse_media_info(mocker):
    """Create a mock for parse_media_info."""

    def mock_parse_media_info(state, info, post_id):
        # Get URLs from locations if available
        media_url = None
        if info["media"]["locations"]:
            media_url = info["media"]["locations"][0]["location"]

        preview_url = None
        if info.get("preview") and info["preview"]["locations"]:
            preview_url = info["preview"]["locations"][0]["location"]

        # Create a MediaItem with the URLs
        item = MediaItem(
            media_id=info["id"],
            preview_id=info.get("previewId"),
            media_url=media_url,
            preview_url=preview_url,
            mimetype=info["media"]["mimetype"],
            post_id=post_id,
            has_access=info["access"],
        )
        return item

    return mocker.patch("media.parse_media_info", side_effect=mock_parse_media_info)


@pytest.fixture
def media_infos():
    """Create test media info dictionaries."""
    return [
        # Primary media (non-preview) with URL
        create_media_info(
            media_id=1,
            has_access=True,
            url="http://example.com/primary1.mp4",
        ),
        # Preview media with URL
        create_media_info(
            media_id=2,
            preview_id=5,
            has_access=False,
            url=None,  # No URL for primary
            preview_url="http://example.com/preview1.mp4",  # But URL for preview
        ),
        # Primary media without URL (inaccessible)
        create_media_info(
            media_id=3,
            has_access=False,
        ),
        # Preview media without URL (inaccessible)
        create_media_info(
            media_id=4,
            preview_id=104,
            has_access=False,
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
    mock_download,
    mock_create_dir,
    mock_parse_media_info,
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
    assert state.recent_video_media_ids == {"1", "5"}

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
    assert state.recent_video_media_ids == {"1"}

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
            media_id=1,
            has_access=False,
        ),
        # Preview media with URL
        create_media_info(
            media_id=2,
            preview_id=102,
            has_access=False,
            preview_url="http://example.com/preview1.mp4",
        ),
    ]

    # Test with previews enabled
    config.download_media_previews = True

    # Reset mock counters
    mock_download.reset_mock()
    mock_process_media_info.reset_mock()
    mock_create_dir.reset_mock()

    await process_download_accessible_media(config, state, media_infos)
    assert state.recent_video_media_ids == {"102"}  # Only preview should be included

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
            media_id=1,
            has_access=True,
            url="http://example.com/video1.mp4",
            mimetype="video/mp4",
        ),
        # Preview video
        create_media_info(
            media_id=2,
            preview_id=102,
            has_access=False,
            url=None,
            preview_url="http://example.com/preview1.mp4",
            mimetype="video/mp4",
        ),
        # Primary image
        create_media_info(
            media_id=3,
            has_access=True,
            url="http://example.com/image1.jpg",
            mimetype="image/jpeg",
        ),
        # Preview image
        create_media_info(
            media_id=4,
            preview_id=104,
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
    assert state.recent_video_media_ids == {"1", "102"}
    assert state.recent_photo_media_ids == {"3", "104"}

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
            media_id=1,
            preview_id=2,
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
            media_id=1,
            preview_id=2,
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
    assert state.recent_video_media_ids == {"1"}  # Only primary should be included

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
    assert state.recent_video_media_ids == {"1"}  # Only primary should be included

    # Verify mocks were called correctly
    assert mock_download.call_count == 1  # Called for primary only
    assert mock_process_media_info.call_count == 1  # Called once for the batch
    assert mock_create_dir.call_count == 1  # Called once to set up directory
