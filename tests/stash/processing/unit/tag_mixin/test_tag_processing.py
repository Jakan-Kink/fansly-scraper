"""Tests for tag processing methods in TagProcessingMixin."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.stash.processing.unit.media_mixin.async_mock_helper import (
    AccessibleAsyncMock,
    make_asyncmock_awaitable,
)


@pytest.fixture
def mock_stash_tag():
    """Create a mock Stash tag."""
    tag = MagicMock()
    tag.id = "tag_123"
    tag.name = "test_tag"
    tag.description = "Test tag description"
    tag.__type_name__ = "Tag"
    tag.is_dirty = MagicMock(return_value=False)
    return tag


@pytest.mark.asyncio
async def test_find_existing_tag_by_name(tag_mixin):
    """Test finding an existing tag by name."""
    # Setup mocks
    mock_tag = MagicMock()
    mock_tag.id = "tag_123"
    mock_tag.name = "test_tag"

    mock_result = MagicMock()
    mock_result.count = 1
    mock_result.tags = [mock_tag]

    tag_mixin.context.client.find_tags = AsyncMock(return_value=mock_result)
    make_asyncmock_awaitable(tag_mixin.context.client.find_tags)

    # Test with exact name match
    tag = await tag_mixin._find_existing_tag("test_tag")
    assert tag == mock_tag
    tag_mixin.context.client.find_tags.assert_called_once()

    # Reset mock
    tag_mixin.context.client.find_tags.reset_mock()

    # Test with case insensitive match
    tag = await tag_mixin._find_existing_tag("TEST_TAG")
    assert tag == mock_tag
    tag_mixin.context.client.find_tags.assert_called_once()


@pytest.mark.asyncio
async def test_find_existing_tag_not_found(tag_mixin):
    """Test finding a tag that doesn't exist."""
    # Setup mock to return no tags
    mock_result = MagicMock()
    mock_result.count = 0
    mock_result.tags = []

    tag_mixin.context.client.find_tags = AsyncMock(return_value=mock_result)
    make_asyncmock_awaitable(tag_mixin.context.client.find_tags)

    # Test with a tag that doesn't exist
    tag = await tag_mixin._find_existing_tag("nonexistent_tag")
    assert tag is None
    tag_mixin.context.client.find_tags.assert_called_once()


@pytest.mark.asyncio
async def test_get_tag_for_name_existing(tag_mixin, mock_stash_tag):
    """Test getting a tag for a name that already exists."""
    # Setup mock to find an existing tag
    tag_mixin._find_existing_tag = AsyncMock(return_value=mock_stash_tag)
    make_asyncmock_awaitable(tag_mixin._find_existing_tag)

    # Test getting an existing tag
    tag = await tag_mixin._get_tag_for_name("test_tag")
    assert tag == mock_stash_tag
    tag_mixin._find_existing_tag.assert_called_once_with("test_tag")

    # Verify the tag wasn't created
    tag_mixin.context.client.create_tag.assert_not_called()


@pytest.mark.asyncio
async def test_get_tag_for_name_new(tag_mixin):
    """Test getting a tag for a name that doesn't exist yet."""
    # Setup mock to not find the tag
    tag_mixin._find_existing_tag = AsyncMock(return_value=None)
    make_asyncmock_awaitable(tag_mixin._find_existing_tag)

    # Setup mock to create a new tag
    new_tag = MagicMock()
    new_tag.id = "new_tag_123"
    new_tag.name = "new_tag"

    tag_mixin.context.client.create_tag = AsyncMock(return_value=new_tag)
    make_asyncmock_awaitable(tag_mixin.context.client.create_tag)

    # Test creating a new tag
    tag = await tag_mixin._get_tag_for_name("new_tag")
    assert tag == new_tag
    tag_mixin._find_existing_tag.assert_called_once_with("new_tag")
    tag_mixin.context.client.create_tag.assert_called_once_with(name="new_tag")


@pytest.mark.asyncio
async def test_process_hashtags_to_tags_empty(tag_mixin):
    """Test processing an empty list of hashtags."""
    hashtags = []
    stashable = MagicMock()

    await tag_mixin._process_hashtags_to_tags(hashtags, stashable)

    # Verify no tags were added
    stashable.add_tag.assert_not_called()


@pytest.mark.asyncio
async def test_process_hashtags_to_tags(tag_mixin):
    """Test processing hashtags to tags."""
    # Create mock hashtags
    hashtag1 = AccessibleAsyncMock()
    hashtag1.name = "tag1"
    hashtag2 = AccessibleAsyncMock()
    hashtag2.name = "tag2"
    hashtags = [hashtag1, hashtag2]

    # Create mock stashable object
    stashable = MagicMock()
    stashable.add_tag = MagicMock()

    # Setup mock tag creation
    tag1 = MagicMock()
    tag1.id = "tag_1"
    tag1.name = "tag1"
    tag2 = MagicMock()
    tag2.id = "tag_2"
    tag2.name = "tag2"

    # Setup mock to return the tags
    tag_mixin._get_tag_for_name = AsyncMock(side_effect=[tag1, tag2])
    make_asyncmock_awaitable(tag_mixin._get_tag_for_name)

    # Test processing hashtags
    await tag_mixin._process_hashtags_to_tags(hashtags, stashable)

    # Verify tags were added to the stashable object
    assert tag_mixin._get_tag_for_name.call_count == 2
    stashable.add_tag.assert_any_call(tag1)
    stashable.add_tag.assert_any_call(tag2)


@pytest.mark.asyncio
async def test_add_preview_tag_not_preview(tag_mixin):
    """Test add_preview_tag with a non-preview item."""
    # Create mock stashable object
    stashable = MagicMock()
    stashable.add_tag = MagicMock()

    # Test with non-preview item
    await tag_mixin._add_preview_tag(stashable, False)

    # Verify no tag was added
    stashable.add_tag.assert_not_called()
    tag_mixin._get_tag_for_name.assert_not_called()


@pytest.mark.asyncio
async def test_add_preview_tag_is_preview(tag_mixin):
    """Test add_preview_tag with a preview item."""
    # Create mock stashable object
    stashable = MagicMock()
    stashable.add_tag = MagicMock()

    # Create mock preview tag
    preview_tag = MagicMock()
    preview_tag.id = "preview_tag"
    preview_tag.name = "Preview"

    # Setup mock to return the preview tag
    tag_mixin._get_tag_for_name = AsyncMock(return_value=preview_tag)
    make_asyncmock_awaitable(tag_mixin._get_tag_for_name)

    # Test with preview item
    await tag_mixin._add_preview_tag(stashable, True)

    # Verify the tag was added
    tag_mixin._get_tag_for_name.assert_called_once_with("Preview")
    stashable.add_tag.assert_called_once_with(preview_tag)


@pytest.mark.asyncio
async def test_get_tags_for_content_empty(tag_mixin):
    """Test get_tags_for_content with empty content."""
    # Test with empty content
    tags = await tag_mixin.get_tags_for_content("")
    assert tags == []

    # Test with None content
    tags = await tag_mixin.get_tags_for_content(None)
    assert tags == []


@pytest.mark.asyncio
async def test_get_tags_for_content_with_hashtags(tag_mixin):
    """Test get_tags_for_content with content containing hashtags."""
    # Setup mock tags
    tag1 = MagicMock()
    tag1.id = "tag_1"
    tag1.name = "tag1"
    tag2 = MagicMock()
    tag2.id = "tag_2"
    tag2.name = "tag2"

    # Setup mock to return tags
    tag_mixin._get_tag_for_name = AsyncMock(side_effect=[tag1, tag2])
    make_asyncmock_awaitable(tag_mixin._get_tag_for_name)

    # Test with content containing hashtags
    content = "This is a post with #tag1 and #tag2 hashtags."
    tags = await tag_mixin.get_tags_for_content(content)

    # Verify tags were created
    assert len(tags) == 2
    assert tags[0] == tag1
    assert tags[1] == tag2
    assert tag_mixin._get_tag_for_name.call_count == 2
    tag_mixin._get_tag_for_name.assert_any_call("tag1")
    tag_mixin._get_tag_for_name.assert_any_call("tag2")


@pytest.mark.asyncio
async def test_get_tags_for_content_duplicate_hashtags(tag_mixin):
    """Test get_tags_for_content with duplicate hashtags."""
    # Setup mock tag
    tag = MagicMock()
    tag.id = "tag_1"
    tag.name = "tag1"

    # Setup mock to return tag
    tag_mixin._get_tag_for_name = AsyncMock(return_value=tag)
    make_asyncmock_awaitable(tag_mixin._get_tag_for_name)

    # Test with content containing duplicate hashtags
    content = "This is a post with #tag1 and #tag1 duplicate hashtags."
    tags = await tag_mixin.get_tags_for_content(content)

    # Verify tag was only created once
    assert len(tags) == 1
    assert tags[0] == tag
    tag_mixin._get_tag_for_name.assert_called_once_with("tag1")
