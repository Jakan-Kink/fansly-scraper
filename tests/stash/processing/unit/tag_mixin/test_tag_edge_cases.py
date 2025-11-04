"""Tests for edge cases in TagProcessingMixin."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from stash.types import FindTagsResultType
from tests.fixtures.metadata_factories import HashtagFactory
from tests.fixtures.stash_type_factories import TagFactory


@pytest.mark.asyncio
async def test_process_hashtags_to_tags_alias_match(tag_mixin):
    """Test finding a tag by alias when exact name match fails."""
    # Create real hashtag using factory
    hashtag = HashtagFactory.build(value="alias_name")

    # Mock tag name lookup to return no results
    name_result = FindTagsResultType(count=0, tags=[])

    # Mock alias lookup to return a match (GraphQL returns dict)
    tag_dict = {"id": "tag_123", "name": "Original Name"}
    alias_result = FindTagsResultType(count=1, tags=[tag_dict])

    # Set up mock find_tags to return different results for name vs alias search
    tag_mixin.context.client.find_tags = AsyncMock(
        side_effect=[name_result, alias_result]
    )

    # Process the hashtag
    tags = await tag_mixin._process_hashtags_to_tags([hashtag])

    assert len(tags) == 1
    assert tags[0].id == "tag_123"
    assert tags[0].name == "Original Name"

    # Verify both name and alias searches were attempted
    assert tag_mixin.context.client.find_tags.call_count == 2


@pytest.mark.asyncio
async def test_process_hashtags_to_tags_creation_error_exists(tag_mixin):
    """Test handling when client's create_tag returns existing tag.

    The client now handles "already exists" errors internally and returns
    the existing tag, so we just verify the tag is returned correctly.
    """
    # Create real hashtag using factory
    hashtag = HashtagFactory.build(value="test_tag")

    # Mock searches to return no results (tag not found by name or alias)
    empty_result = FindTagsResultType(count=0, tags=[])
    tag_mixin.context.client.find_tags = AsyncMock(return_value=empty_result)

    # Mock create_tag to return existing tag (client handles "already exists" internally)
    existing_tag = TagFactory.build(
        id="tag_123",
        name="test_tag",
    )
    tag_mixin.context.client.create_tag = AsyncMock(return_value=existing_tag)

    # Process the hashtag
    tags = await tag_mixin._process_hashtags_to_tags([hashtag])

    assert len(tags) == 1
    assert tags[0].id == "tag_123"
    assert tags[0].name == "test_tag"

    # Verify create_tag was called once (client handles error internally)
    assert tag_mixin.context.client.create_tag.call_count == 1


@pytest.mark.asyncio
async def test_process_hashtags_to_tags_creation_error_other(tag_mixin):
    """Test handling of tag creation with other errors."""
    # Create real hashtag using factory
    hashtag = HashtagFactory.build(value="test_tag")

    # Mock searches to return no results
    empty_result = FindTagsResultType(count=0, tags=[])
    tag_mixin.context.client.find_tags = AsyncMock(return_value=empty_result)

    # Mock tag creation to raise a different error
    tag_mixin.context.client.create_tag = AsyncMock(
        side_effect=Exception("Some other error")
    )

    # Process the hashtag and expect the error to be raised
    with pytest.raises(Exception) as exc_info:  # noqa: PT011 - message validated by assertion below
        await tag_mixin._process_hashtags_to_tags([hashtag])

    assert str(exc_info.value) == "Some other error"


@pytest.mark.asyncio
async def test_add_preview_tag_existing_tag(tag_mixin):
    """Test _add_preview_tag when tag is already present."""
    # Create mock file (Scene/Image from Stash API)
    mock_file = MagicMock()
    mock_file.id = "file_123"
    mock_file.tags = []

    # Create preview tag using factory
    preview_tag = TagFactory.build(
        id="preview_tag_123",
        name="Trailer",
    )

    # Mock tag search to return the preview tag
    tag_result = FindTagsResultType(count=1, tags=[preview_tag])
    tag_mixin.context.client.find_tags = AsyncMock(return_value=tag_result)

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
    # Create mock file (Scene/Image from Stash API)
    mock_file = MagicMock()
    mock_file.id = "file_123"
    mock_file.tags = []

    # Mock tag search to return no results
    empty_result = FindTagsResultType(count=0, tags=[])
    tag_mixin.context.client.find_tags = AsyncMock(return_value=empty_result)

    # Add the tag
    await tag_mixin._add_preview_tag(mock_file)

    # Verify no tag was added since none was found
    assert len(mock_file.tags) == 0
