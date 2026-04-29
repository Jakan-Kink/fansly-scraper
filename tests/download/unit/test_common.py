"""Tests for common download functionality."""

import logging
from unittest.mock import AsyncMock, patch

import pytest

from download.common import (
    check_page_duplicates,
    get_unique_media_ids,
    print_download_info,
    process_download_accessible_media,
)
from download.types import DownloadType
from errors import ApiError, DuplicateCountError, DuplicatePageError
from metadata.models import Account, Media, Post
from tests.fixtures.utils.test_isolation import snowflake_id


@pytest.fixture
def info_object():
    """Create a test info object with media IDs."""
    return {
        "accountMedia": [{"id": 100001}, {"id": 100002}],
        "accountMediaBundles": [
            {"accountMediaIds": [100002, 100003]},
            {"accountMediaIds": [100004, 100005]},
        ],
    }


@pytest.fixture
def empty_info_object():
    """Create an empty info object."""
    return {"accountMedia": [], "accountMediaBundles": []}


@pytest.fixture
def accessible_media():
    """Create test Media objects for process_download_accessible_media."""
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
def mock_download_media():
    """Mock for download_media function."""
    with patch("download.common.download_media", new_callable=AsyncMock) as mock:
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
    assert set(unique_ids) == {100001, 100002, 100003, 100004, 100005}


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

    # Should not raise any exceptions (early return)
    await check_page_duplicates(mock_config, page_data, "timeline")


@pytest.mark.asyncio
async def test_check_page_duplicates_empty_posts(mock_config, download_state):
    """Test handling of empty posts list."""
    page_data = {"posts": []}

    # Should not raise any exceptions
    await check_page_duplicates(mock_config, page_data, "timeline")


@pytest.mark.asyncio
async def test_check_page_duplicates_missing_posts_key(mock_config, download_state):
    """Test handling of page_data without posts key."""
    mock_config.use_pagination_duplication = True
    page_data = {"other_data": "value"}  # No "posts" key

    # Should not raise any exceptions (early return)
    await check_page_duplicates(mock_config, page_data, "timeline")


@pytest.mark.asyncio
async def test_check_page_duplicates_wall(mock_config, entity_store):
    """Test duplicate checking for wall pages with new (uncached) post."""
    mock_config.use_pagination_duplication = True
    post_id = snowflake_id()
    page_data = {"posts": [{"id": post_id}]}

    # Post not in store cache → no duplicate → should not raise
    await check_page_duplicates(
        mock_config,
        page_data,
        "wall",
        page_id="wall1",
        cursor="123",
    )


@pytest.mark.asyncio
async def test_check_page_duplicates_all_existing(mock_config, entity_store):
    """Test duplicate checking when all posts exist in store cache."""
    mock_config.use_pagination_duplication = True

    account_id = snowflake_id()
    post_id = snowflake_id()

    # Pre-populate the store so the post is in the identity map cache
    await entity_store.save(Account(id=account_id, username="dupetest"))
    await entity_store.save(Post(id=post_id, accountId=account_id))

    page_data = {"posts": [{"id": post_id}]}

    with (
        pytest.raises(DuplicatePageError) as exc_info,
        patch("download.common.asyncio.sleep", new_callable=AsyncMock),
    ):
        await check_page_duplicates(mock_config, page_data, "timeline", cursor="123")

    assert exc_info.value.page_type == "timeline"
    assert exc_info.value.cursor == "123"


@pytest.mark.asyncio
async def test_process_download_accessible_media_basic(
    mock_config,
    download_state,
    accessible_media,
    mock_download_media,
    mock_set_create_directory,
):
    """Test basic media processing."""
    result = await process_download_accessible_media(
        mock_config, download_state, accessible_media
    )

    assert result is True
    mock_download_media.assert_awaited_once_with(
        mock_config, download_state, accessible_media
    )
    mock_set_create_directory.assert_called_once_with(mock_config, download_state)


@pytest.mark.asyncio
async def test_process_download_accessible_media_messages(
    mock_config,
    download_state,
    accessible_media,
    mock_download_media,
    mock_set_create_directory,
):
    """Test message-specific duplicate threshold handling."""
    download_state.download_type = DownloadType.MESSAGES
    download_state.total_message_items = 100

    original_threshold = mock_config.DUPLICATE_THRESHOLD

    result = await process_download_accessible_media(
        mock_config, download_state, accessible_media
    )

    assert result is True
    assert original_threshold == mock_config.DUPLICATE_THRESHOLD  # Should be restored
    # Increased by accessible media count (2 items)
    assert download_state.total_message_items == 102


@pytest.mark.asyncio
async def test_process_download_accessible_media_wall(
    mock_config,
    download_state,
    accessible_media,
    mock_download_media,
    mock_set_create_directory,
):
    """Test wall-specific duplicate threshold handling."""
    download_state.download_type = DownloadType.WALL
    original_threshold = mock_config.DUPLICATE_THRESHOLD

    result = await process_download_accessible_media(
        mock_config, download_state, accessible_media
    )

    assert result is True
    mock_download_media.assert_awaited_once()
    mock_set_create_directory.assert_called_once_with(mock_config, download_state)
    assert original_threshold == mock_config.DUPLICATE_THRESHOLD  # Should be restored


@pytest.mark.asyncio
async def test_process_download_accessible_media_duplicate_error(
    mock_config_update,
    download_state,
    accessible_media,
    mock_download_media,
    mock_set_create_directory,
):
    """Test handling of DuplicateCountError during download."""
    download_state.download_type = DownloadType.TIMELINE

    mock_download_media.side_effect = DuplicateCountError(duplicate_count=5)

    result = await process_download_accessible_media(
        mock_config_update, download_state, accessible_media
    )

    assert result is False  # Should indicate to stop processing for timeline


@pytest.mark.asyncio
async def test_process_download_accessible_media_general_error(
    mock_config_update,
    download_state,
    accessible_media,
    mock_download_media,
    mock_set_create_directory,
):
    """Test handling of general errors during download."""
    mock_download_media.side_effect = Exception("Test error")

    result = await process_download_accessible_media(
        mock_config_update, download_state, accessible_media
    )

    assert result is True  # Should continue processing


def test_print_download_info(mock_config, caplog):
    """Test download info printing."""
    caplog.set_level(logging.INFO)
    mock_config.user_agent = (
        "Test User Agent String That Is Really Long For Testing Truncation"
    )
    mock_config.open_folder_when_finished = True
    mock_config.download_media_previews = True
    mock_config.interactive = False

    print_download_info(mock_config)

    info_messages = [r.getMessage() for r in caplog.records if r.levelname == "INFO"]
    warning_messages = [
        r.getMessage() for r in caplog.records if r.levelname == "WARNING"
    ]

    expected_user_agent = (
        f"Using user-agent: '{mock_config.user_agent[:28]} [...] "
        f"{mock_config.user_agent[-35:]}'"
    )
    assert expected_user_agent in info_messages
    assert (
        f"Open download folder when finished, is set to: '{mock_config.open_folder_when_finished}'"
        in info_messages
    )
    assert (
        f"Downloading files marked as preview, is set to: '{mock_config.download_media_previews}'"
        in info_messages
    )

    preview_warnings = [
        m for m in warning_messages if "Previews downloading is enabled" in m
    ]
    assert len(preview_warnings) == 1
    assert (
        preview_warnings[0]
        == "Previews downloading is enabled; repetitive and/or emoji spammed media might be downloaded!"
    )
