"""Tests for tag processing methods in TagProcessingMixin."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from stash.types import FindTagsResultType
from tests.fixtures.metadata_factories import HashtagFactory
from tests.fixtures.stash_type_factories import TagFactory


@pytest.fixture
def mock_stash_tag():
    """Create a mock Stash tag using factory."""
    return TagFactory.build(
        id="tag_123",
        name="test_tag",
    )


@pytest.mark.asyncio
async def test_process_hashtags_to_tags_empty(tag_mixin):
    """Test processing an empty list of hashtags."""
    hashtags = []

    tags = await tag_mixin._process_hashtags_to_tags(hashtags)

    # Verify no tags were returned
    assert tags == []
    # Verify no API calls were made
    tag_mixin.context.client.find_tags.assert_not_called()


@pytest.mark.asyncio
async def test_process_hashtags_to_tags_single(tag_mixin):
    """Test processing a single hashtag to tag."""
    # Create hashtag using factory
    hashtag = HashtagFactory.build(value="testTag")

    # GraphQL API returns dicts, which production code converts to Tag objects
    tag_dict = {
        "id": "tag_123",
        "name": "testtag",
        "aliases": [],
        "parents": [],
        "children": [],
        "description": None,
        "image_path": None,
    }

    # Setup mock to find existing tag using real FindTagsResultType
    result = FindTagsResultType(count=1, tags=[tag_dict])
    tag_mixin.context.client.find_tags = AsyncMock(return_value=result)

    # Test processing hashtag
    tags = await tag_mixin._process_hashtags_to_tags([hashtag])

    # Verify tag was returned
    assert len(tags) == 1
    assert tags[0].id == "tag_123"
    assert tags[0].name == "testtag"

    # Verify tag was searched with lowercase name
    tag_mixin.context.client.find_tags.assert_called_once()
    call_args = tag_mixin.context.client.find_tags.call_args
    assert call_args[1]["tag_filter"]["name"]["value"] == "testtag"


@pytest.mark.asyncio
async def test_process_hashtags_to_tags_not_found_creates_new(tag_mixin):
    """Test processing a hashtag that doesn't exist creates a new tag."""
    # Create hashtag using factory
    hashtag = HashtagFactory.build(value="newTag")

    # Setup mock to not find existing tag (both name and alias searches return empty)
    empty_result = FindTagsResultType(count=0, tags=[])

    # Setup mock to create new tag using factory
    new_tag = TagFactory.build(
        id="new_tag_123",
        name="newtag",
    )

    tag_mixin.context.client.find_tags = AsyncMock(return_value=empty_result)
    tag_mixin.context.client.create_tag = AsyncMock(return_value=new_tag)

    # Test processing hashtag
    tags = await tag_mixin._process_hashtags_to_tags([hashtag])

    # Verify tag was created and returned
    assert len(tags) == 1
    assert tags[0].id == "new_tag_123"
    assert tags[0].name == "newtag"

    # Verify tag creation was called - should be called with a Tag object with id="new"
    tag_mixin.context.client.create_tag.assert_called_once()
    call_args = tag_mixin.context.client.create_tag.call_args[0][0]
    assert call_args.name == "newtag"


@pytest.mark.asyncio
async def test_process_hashtags_to_tags_multiple(tag_mixin):
    """Test processing multiple hashtags."""
    # Create hashtags using factory
    hashtag1 = HashtagFactory.build(value="tag1")
    hashtag2 = HashtagFactory.build(value="tag2")

    # GraphQL API returns dicts, which production code converts to Tag objects
    tag1_dict = {
        "id": "tag_1",
        "name": "tag1",
        "aliases": [],
        "parents": [],
        "children": [],
        "description": None,
        "image_path": None,
    }
    tag2_dict = {
        "id": "tag_2",
        "name": "tag2",
        "aliases": [],
        "parents": [],
        "children": [],
        "description": None,
        "image_path": None,
    }

    # Setup mock to find both tags
    def mock_find_tags(tag_filter=None):
        tag_name = tag_filter["name"]["value"]
        if tag_name == "tag1":
            return FindTagsResultType(count=1, tags=[tag1_dict])
        if tag_name == "tag2":
            return FindTagsResultType(count=1, tags=[tag2_dict])
        return FindTagsResultType(count=0, tags=[])

    tag_mixin.context.client.find_tags = AsyncMock(side_effect=mock_find_tags)

    # Test processing hashtags
    tags = await tag_mixin._process_hashtags_to_tags([hashtag1, hashtag2])

    # Verify both tags were returned
    assert len(tags) == 2
    assert tags[0].id == "tag_1"
    assert tags[0].name == "tag1"
    assert tags[1].id == "tag_2"
    assert tags[1].name == "tag2"

    # Verify both tags were searched
    assert tag_mixin.context.client.find_tags.call_count == 2


@pytest.mark.asyncio
async def test_add_preview_tag_not_found(tag_mixin):
    """Test add_preview_tag when Trailer tag doesn't exist."""
    # Create mock Scene with empty tags
    scene = MagicMock()
    scene.id = "scene_123"
    scene.tags = []

    # Setup mock to not find Trailer tag
    empty_result = FindTagsResultType(count=0, tags=[])
    tag_mixin.context.client.find_tags = AsyncMock(return_value=empty_result)

    # Test with scene
    await tag_mixin._add_preview_tag(scene)

    # Verify the Trailer tag was searched for with query
    tag_mixin.context.client.find_tags.assert_called_once()
    call_args = tag_mixin.context.client.find_tags.call_args
    assert call_args[1]["q"] == "Trailer"

    # Verify no tag was added since not found
    assert scene.tags == []


@pytest.mark.asyncio
async def test_add_preview_tag_found_adds_tag(tag_mixin):
    """Test add_preview_tag when Trailer tag exists and is added."""
    # Create mock Scene with empty tags
    scene = MagicMock()
    scene.id = "scene_123"
    scene.tags = []

    # Setup mock to find Trailer tag using factory
    trailer_tag = TagFactory.build(id="trailer_tag_123", name="Trailer")
    result = FindTagsResultType(count=1, tags=[trailer_tag])
    tag_mixin.context.client.find_tags = AsyncMock(return_value=result)

    # Test with scene
    await tag_mixin._add_preview_tag(scene)

    # Verify the Trailer tag was searched for
    tag_mixin.context.client.find_tags.assert_called_once_with(q="Trailer")

    # Verify the tag was added to scene
    assert len(scene.tags) == 1
    assert scene.tags[0] == trailer_tag


@pytest.mark.asyncio
async def test_add_preview_tag_already_has_tag(tag_mixin):
    """Test add_preview_tag when scene already has the Trailer tag."""
    # Create Trailer tag using factory
    trailer_tag = TagFactory.build(id="trailer_tag_123", name="Trailer")

    # Create mock Scene with Trailer tag already added
    scene = MagicMock()
    scene.id = "scene_123"
    scene.tags = [trailer_tag]

    # Setup mock to find Trailer tag
    result = FindTagsResultType(count=1, tags=[trailer_tag])
    tag_mixin.context.client.find_tags = AsyncMock(return_value=result)

    # Test with scene
    await tag_mixin._add_preview_tag(scene)

    # Verify the Trailer tag was searched for
    tag_mixin.context.client.find_tags.assert_called_once_with(q="Trailer")

    # Verify the tag was NOT added again (still only one)
    assert len(scene.tags) == 1
    assert scene.tags[0] == trailer_tag
