"""Tests for common download functionality."""

from unittest.mock import MagicMock, patch

import pytest

from download.common import (
    check_page_duplicates,
    get_unique_media_ids,
    print_download_info,
    process_download_accessible_media,
)
from download.types import DownloadType
from errors import ApiError, DuplicateCountError, DuplicatePageError


@pytest.fixture
def info_object():
    """Create a test info object with media IDs."""
    return {
        "accountMedia": [{"id": "media1"}, {"id": "media2"}],
        "accountMediaBundles": [
            {"accountMediaIds": ["media2", "media3"]},
            {"accountMediaIds": ["media4", "media5"]},
        ],
    }


@pytest.fixture
def empty_info_object():
    """Create an empty info object."""
    return {"accountMedia": [], "accountMediaBundles": []}


@pytest.fixture
def media_infos():
    """Create test media info objects."""
    return [
        {
            "id": "media1",
            "contentType": "photo",
            "contentUrl": "http://example.com/photo1.jpg",
            "createdAt": "2025-01-01T00:00:00Z",
            "isPreview": False,
        },
        {
            "id": "media2",
            "contentType": "video",
            "contentUrl": "http://example.com/video1.mp4",
            "createdAt": "2025-01-01T00:00:00Z",
            "isPreview": True,
        },
    ]


@pytest.fixture
def mock_process_media():
    """Mock for process_media_info function."""
    with patch("download.common.process_media_info") as mock:
        yield mock


@pytest.fixture
def mock_parse_media():
    """Mock for parse_media_info function."""
    with patch("download.common.parse_media_info") as mock:
        mock.return_value = MagicMock(
            download_url="http://example.com/test.mp4", is_preview=False
        )
        yield mock


@pytest.fixture
def mock_download_media():
    """Mock for download_media function."""
    with patch("download.common.download_media") as mock:
        yield mock


@pytest.fixture
def mock_set_create_directory():
    """Mock for set_create_directory_for_download function."""
    with patch("download.common.set_create_directory_for_download") as mock:
        yield mock


@pytest.fixture
def mock_config_update(mock_config):
    """Update mock_config with additional required properties."""
    mock_config.interactive = False
    mock_config.DUPLICATE_THRESHOLD = 50
    return mock_config


def test_get_unique_media_ids_with_duplicates(info_object):
    """Test extracting unique media IDs from object with duplicates."""
    unique_ids = get_unique_media_ids(info_object)
    assert len(unique_ids) == 5
    assert set(unique_ids) == {"media1", "media2", "media3", "media4", "media5"}


def test_get_unique_media_ids_empty(empty_info_object):
    """Test extracting media IDs from empty object."""
    unique_ids = get_unique_media_ids(empty_info_object)
    assert len(unique_ids) == 0


def test_get_unique_media_ids_none_media():
    """Test handling None media items."""
    info_object = {"accountMedia": [None], "accountMediaBundles": []}
    with pytest.raises(ApiError):
        get_unique_media_ids(info_object)


@pytest.mark.asyncio
async def test_check_page_duplicates_disabled(mock_config, download_state):
    """Test that duplicate checking is skipped when disabled."""
    mock_config.use_pagination_duplication = False
    page_data = {"posts": [{"id": 1}]}

    # Should not raise any exceptions
    await check_page_duplicates(mock_config, page_data, "timeline")


@pytest.mark.asyncio
async def test_check_page_duplicates_empty_posts(mock_config, download_state):
    """Test handling of empty posts list."""
    page_data = {"posts": []}

    # Should not raise any exceptions
    await check_page_duplicates(mock_config, page_data, "timeline")


@pytest.mark.asyncio
async def test_check_page_duplicates_wall(mock_config, test_async_session):
    """Test duplicate checking for wall pages."""
    page_data = {"posts": [{"id": 1}]}

    # Should not raise exception for new post
    await check_page_duplicates(
        mock_config,
        page_data,
        "wall",
        page_id="wall1",
        cursor="123",
        session=test_async_session,
    )


@pytest.mark.asyncio
async def test_check_page_duplicates_all_existing(mock_config, test_async_session):
    """Test duplicate checking when all posts exist."""
    # Enable pagination duplication checking
    mock_config.use_pagination_duplication = True

    page_data = {"posts": [{"id": 1}]}

    class ExistingPostSession(test_async_session.__class__):
        async def execute(self, statement):
            class Result:
                def scalar_one_or_none(self):
                    return 1  # Simulates existing post

            return Result()

    session = ExistingPostSession()

    with pytest.raises(DuplicatePageError) as exc_info:
        await check_page_duplicates(
            mock_config, page_data, "timeline", cursor="123", session=session
        )

    assert exc_info.value.page_type == "timeline"
    assert exc_info.value.cursor == "123"


@pytest.mark.asyncio
async def test_process_download_accessible_media_basic(
    mock_config,
    download_state,
    media_infos,
    test_async_session,
    mock_process_media,
    mock_parse_media,
    mock_download_media,
    mock_set_create_directory,
):
    """Test basic media processing without previews."""
    mock_config.download_media_previews = False
    result = await process_download_accessible_media(
        mock_config, download_state, media_infos, session=test_async_session
    )

    assert result is True
    mock_process_media.assert_called_once()
    mock_parse_media.assert_called()
    mock_download_media.assert_called_once()
    mock_set_create_directory.assert_called_once_with(mock_config, download_state)
    assert download_state.pic_count == 0  # Counter is handled by mocked download_media


@pytest.mark.asyncio
async def test_process_download_accessible_media_with_previews(
    mock_config,
    download_state,
    media_infos,
    test_async_session,
    mock_process_media,
    mock_parse_media,
    mock_download_media,
    mock_set_create_directory,
):
    """Test media processing with previews enabled."""
    mock_config.download_media_previews = True
    result = await process_download_accessible_media(
        mock_config, download_state, media_infos, session=test_async_session
    )

    assert result is True
    mock_process_media.assert_called_once()
    mock_parse_media.assert_called()
    mock_download_media.assert_called_once()
    mock_set_create_directory.assert_called_once_with(mock_config, download_state)
    assert download_state.pic_count == 0
    assert download_state.vid_count == 0  # Counter is handled by mocked download_media


@pytest.mark.asyncio
async def test_process_download_accessible_media_messages(
    mock_config,
    download_state,
    media_infos,
    test_async_session,
    mock_process_media,
    mock_parse_media,
    mock_download_media,
    mock_set_create_directory,
):
    """Test message-specific duplicate threshold handling."""
    download_state.download_type = DownloadType.MESSAGES
    download_state.total_message_items = 100

    # Original threshold should be preserved after processing
    original_threshold = mock_config.DUPLICATE_THRESHOLD

    result = await process_download_accessible_media(
        mock_config, download_state, media_infos, session=test_async_session
    )

    assert result is True
    assert original_threshold == mock_config.DUPLICATE_THRESHOLD  # Should be restored
    assert (
        download_state.total_message_items == 102
    )  # Increased by accessible media count


@pytest.mark.asyncio
async def test_process_download_accessible_media_wall(
    mock_config,
    download_state,
    media_infos,
    test_async_session,
    mock_process_media,
    mock_parse_media,
    mock_download_media,
    mock_set_create_directory,
):
    """Test wall-specific duplicate threshold handling."""
    download_state.download_type = DownloadType.WALL
    original_threshold = mock_config.DUPLICATE_THRESHOLD

    result = await process_download_accessible_media(
        mock_config, download_state, media_infos, session=test_async_session
    )

    assert result is True
    mock_process_media.assert_called_once()
    mock_parse_media.assert_called()
    mock_download_media.assert_called_once()
    mock_set_create_directory.assert_called_once_with(mock_config, download_state)
    assert original_threshold == mock_config.DUPLICATE_THRESHOLD  # Should be restored


@pytest.mark.asyncio
async def test_process_download_accessible_media_duplicate_error(
    mock_config_update,
    download_state,
    media_infos,
    test_async_session,
    mock_process_media,
    mock_parse_media,
    mock_download_media,
    mock_set_create_directory,
):
    """Test handling of DuplicateCountError during download."""
    download_state.download_type = DownloadType.TIMELINE

    # Mock download_media to raise DuplicateCountError with required duplicate_count
    mock_download_media.side_effect = DuplicateCountError(duplicate_count=5)

    result = await process_download_accessible_media(
        mock_config_update, download_state, media_infos, session=test_async_session
    )

    assert result is False  # Should indicate to stop processing for timeline


@pytest.mark.asyncio
async def test_process_download_accessible_media_general_error(
    mock_config_update,
    download_state,
    media_infos,
    test_async_session,
    mock_process_media,
    mock_parse_media,
    mock_download_media,
    mock_set_create_directory,
):
    """Test handling of general errors during download."""
    mock_download_media.side_effect = Exception("Test error")
    # Should not raise exception but print error and continue
    result = await process_download_accessible_media(
        mock_config_update, download_state, media_infos, session=test_async_session
    )

    assert result is True  # Should continue processing


def test_print_download_info(mock_config):
    """Test download info printing."""
    mock_config.user_agent = (
        "Test User Agent String That Is Really Long For Testing Truncation"
    )
    mock_config.open_folder_when_finished = True
    mock_config.download_media_previews = True
    mock_config.interactive = False

    # Since we're using loguru for logging, we need to patch the print functions
    with (
        patch("download.common.print_info") as mock_print_info,
        patch("download.common.print_warning") as mock_print_warning,
    ):
        print_download_info(mock_config)

        # Check that print_info was called with expected messages
        mock_print_info.assert_any_call(
            f"Using user-agent: '{mock_config.user_agent[:28]} [...] {mock_config.user_agent[-35:]}'"
        )
        mock_print_info.assert_any_call(
            f"Open download folder when finished, is set to: '{mock_config.open_folder_when_finished}'"
        )
        mock_print_info.assert_any_call(
            f"Downloading files marked as preview, is set to: '{mock_config.download_media_previews}'"
        )

        # Check that print_warning was called for preview warning
        mock_print_warning.assert_called_once_with(
            "Previews downloading is enabled; repetitive and/or emoji spammed media might be downloaded!"
        )
