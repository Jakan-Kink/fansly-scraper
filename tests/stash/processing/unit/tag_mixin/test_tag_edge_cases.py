"""Tests for edge cases in TagProcessingMixin."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.stash.processing.unit.media_mixin.async_mock_helper import (
    AccessibleAsyncMock,
    make_asyncmock_awaitable,
)


@pytest.mark.asyncio
async def test_process_hashtags_to_tags_alias_match(tag_mixin):
    """Test finding a tag by alias when exact name match fails."""
    # Mock tag name lookup to return no results
    mock_name_result = MagicMock()
    mock_name_result.count = 0
    mock_name_result.tags = []

    # Mock alias lookup to return a match
    mock_alias_result = MagicMock()
    mock_alias_result.count = 1
    mock_tag = {"id": "tag_123", "name": "Original Name"}
    mock_alias_result.tags = [mock_tag]

    # Set up mock find_tags to return different results for name vs alias search
    tag_mixin.context.client.find_tags = AsyncMock(
        side_effect=[mock_name_result, mock_alias_result]
    )
    make_asyncmock_awaitable(tag_mixin.context.client.find_tags)

    # Create mock hashtag
    hashtag = MagicMock()
    hashtag.value = "alias_name"

    # Process the hashtag
    tags = await tag_mixin._process_hashtags_to_tags([hashtag])

    assert len(tags) == 1
    assert tags[0].id == "tag_123"
    assert tags[0].name == "Original Name"

    # Verify both name and alias searches were attempted
    assert tag_mixin.context.client.find_tags.call_count == 2


@pytest.mark.asyncio
async def test_process_hashtags_to_tags_creation_error_exists(tag_mixin):
    """Test handling of tag creation when tag already exists."""
    # Mock searches to return no results
    mock_empty_result = MagicMock()
    mock_empty_result.count = 0
    mock_empty_result.tags = []

    # Mock tag creation to raise "already exists" error first, then succeed
    existing_tag = MagicMock()
    existing_tag.id = "tag_123"
    existing_tag.name = "test_tag"

    def mock_create_tag(*args, **kwargs):
        if mock_create_tag.first_call:
            mock_create_tag.first_call = False
            raise Exception("tag with name 'test_tag' already exists")
        return existing_tag

    mock_create_tag.first_call = True

    tag_mixin.context.client.find_tags = AsyncMock(return_value=mock_empty_result)
    tag_mixin.context.client.create_tag = AsyncMock(side_effect=mock_create_tag)
    make_asyncmock_awaitable(tag_mixin.context.client.find_tags)
    make_asyncmock_awaitable(tag_mixin.context.client.create_tag)

    # Create mock hashtag
    hashtag = MagicMock()
    hashtag.value = "test_tag"

    # Process the hashtag
    tags = await tag_mixin._process_hashtags_to_tags([hashtag])

    assert len(tags) == 1
    assert tags[0].id == "tag_123"
    assert tags[0].name == "test_tag"

    # Verify create_tag was called twice (first fails, second succeeds)
    assert tag_mixin.context.client.create_tag.call_count == 2


@pytest.mark.asyncio
async def test_process_hashtags_to_tags_creation_error_other(tag_mixin):
    """Test handling of tag creation with other errors."""
    # Mock searches to return no results
    mock_empty_result = MagicMock()
    mock_empty_result.count = 0
    mock_empty_result.tags = []

    # Mock tag creation to raise a different error
    tag_mixin.context.client.find_tags = AsyncMock(return_value=mock_empty_result)
    tag_mixin.context.client.create_tag = AsyncMock(
        side_effect=Exception("Some other error")
    )
    make_asyncmock_awaitable(tag_mixin.context.client.find_tags)
    make_asyncmock_awaitable(tag_mixin.context.client.create_tag)

    # Create mock hashtag
    hashtag = MagicMock()
    hashtag.value = "test_tag"

    # Process the hashtag and expect the error to be raised
    with pytest.raises(Exception) as exc_info:
        await tag_mixin._process_hashtags_to_tags([hashtag])

    assert str(exc_info.value) == "Some other error"


@pytest.mark.asyncio
async def test_add_preview_tag_existing_tag(tag_mixin):
    """Test _add_preview_tag when tag is already present."""
    # Create mock file
    mock_file = MagicMock()
    mock_file.id = "file_123"
    mock_file.tags = []

    # Create mock preview tag
    mock_preview_tag = MagicMock()
    mock_preview_tag.id = "preview_tag_123"
    mock_preview_tag.name = "Trailer"

    # Mock tag search to return the preview tag
    mock_tag_result = MagicMock()
    mock_tag_result.count = 1
    mock_tag_result.tags = [mock_preview_tag]

    tag_mixin.context.client.find_tags = AsyncMock(return_value=mock_tag_result)
    make_asyncmock_awaitable(tag_mixin.context.client.find_tags)

    # Add the tag
    await tag_mixin._add_preview_tag(mock_file)

    # Verify tag was added
    assert len(mock_file.tags) == 1
    assert mock_file.tags[0].id == "preview_tag_123"

    # Add the tag again
    await tag_mixin._add_preview_tag(mock_file)

    # Verify tag wasn't duplicated
    assert len(mock_file.tags) == 1
    assert mock_file.tags[0].id == "preview_tag_123"


@pytest.mark.asyncio
async def test_add_preview_tag_no_tag_found(tag_mixin):
    """Test _add_preview_tag when preview tag doesn't exist."""
    # Create mock file
    mock_file = MagicMock()
    mock_file.id = "file_123"
    mock_file.tags = []

    # Mock tag search to return no results
    mock_tag_result = MagicMock()
    mock_tag_result.count = 0
    mock_tag_result.tags = []

    tag_mixin.context.client.find_tags = AsyncMock(return_value=mock_tag_result)
    make_asyncmock_awaitable(tag_mixin.context.client.find_tags)

    # Add the tag
    await tag_mixin._add_preview_tag(mock_file)

    # Verify no tag was added since none was found
    assert len(mock_file.tags) == 0
