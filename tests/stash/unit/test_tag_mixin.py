"""Unit tests for TagClientMixin."""

import asyncio
from datetime import datetime
from unittest.mock import AsyncMock, create_autospec, patch

import pytest

from stash import StashClient
from stash.types import FindTagsResultType, Tag, TagsMergeInput


@pytest.fixture
def stash_client() -> StashClient:
    """Create a mock StashClient for testing."""
    client = create_autospec(StashClient, instance=True)
    return client


@pytest.fixture
def mock_tag() -> Tag:
    """Create a mock tag for testing."""
    return Tag(
        id="123",
        name="Test Tag",
        description="Test tag description",
        aliases=["alias1", "alias2"],
        image_path="/path/to/image.jpg",
        parents=[
            Tag(
                id="456",
                name="Parent Tag",
            )
        ],
        children=[
            Tag(
                id="789",
                name="Child Tag",
            )
        ],
    )


@pytest.mark.asyncio
async def test_find_tag(stash_client: StashClient, mock_tag: Tag) -> None:
    """Test finding a tag by ID."""
    with patch.object(
        stash_client,
        "find_tag",
        new_callable=AsyncMock,
        return_value=mock_tag,
    ):
        # Find by ID
        tag = await stash_client.find_tag("123")
        assert tag is not None
        assert tag.id == mock_tag.id
        assert tag.name == mock_tag.name
        assert tag.description == mock_tag.description
        assert tag.aliases == mock_tag.aliases
        # favorite is not in the client model
        # scene_count is not in the client model
        assert len(tag.parents) == 1
        assert len(tag.children) == 1
        assert tag.parents[0].id == mock_tag.parents[0].id
        assert tag.children[0].id == mock_tag.children[0].id


@pytest.mark.asyncio
async def test_find_tags(stash_client: StashClient, mock_tag: Tag) -> None:
    """Test finding tags with filters."""
    mock_result = FindTagsResultType(
        count=1,
        tags=[mock_tag],
    )

    with patch.object(
        stash_client,
        "find_tags",
        new_callable=AsyncMock,
        return_value=mock_result,
    ):
        # Test with tag filter
        result = await stash_client.find_tags(
            tag_filter={
                "name": {"modifier": "EQUALS", "value": "Test Tag"},
                "description": {"modifier": "INCLUDES", "value": "test"},
                # favorite is not in the client model
            }
        )
        assert result.count == 1
        assert len(result.tags) == 1
        assert result.tags[0].id == mock_tag.id

        # Test with general filter
        result = await stash_client.find_tags(
            filter_={
                "page": 1,
                "per_page": 10,
                "sort": "name",
                "direction": "ASC",
            }
        )
        assert result.count == 1
        assert len(result.tags) == 1


@pytest.mark.asyncio
async def test_create_tag(stash_client: StashClient, mock_tag: Tag) -> None:
    """Test creating a tag."""
    with patch.object(
        stash_client,
        "create_tag",
        new_callable=AsyncMock,
        return_value=mock_tag,
    ):
        # Create with minimum fields
        tag = Tag(
            id="new",  # Will be replaced on save
            name="New Tag",
            aliases=[],
            parents=[],
            children=[],
        )
        created = await stash_client.create_tag(tag)
        assert created.id == mock_tag.id
        assert created.name == mock_tag.name

        # Create with all fields
        tag = mock_tag
        tag.id = "new"  # Force create
        created = await stash_client.create_tag(tag)
        assert created.id == mock_tag.id
        assert created.name == mock_tag.name
        assert created.description == mock_tag.description
        assert created.aliases == mock_tag.aliases
        # favorite is not in the client model
        assert len(created.parents) == 1
        assert len(created.children) == 1


@pytest.mark.asyncio
async def test_update_tag(stash_client: StashClient, mock_tag: Tag) -> None:
    """Test updating a tag."""
    with patch.object(
        stash_client,
        "update_tag",
        new_callable=AsyncMock,
        return_value=mock_tag,
    ):
        # Update single field
        tag = mock_tag
        tag.name = "Updated Name"
        updated = await stash_client.update_tag(tag)
        assert updated.id == mock_tag.id
        assert updated.name == mock_tag.name

        # Update multiple fields
        tag.description = "Updated description"
        # favorite is not in the client model
        tag.aliases = ["new_alias1", "new_alias2"]
        updated = await stash_client.update_tag(tag)
        assert updated.id == mock_tag.id
        assert updated.description == mock_tag.description
        # favorite is not in the client model
        assert updated.aliases == mock_tag.aliases

        # Update relationships
        new_parent = Tag(
            id="999",
            name="New Parent",
            aliases=[],
            parents=[],
            children=[],
        )
        tag.parents = [new_parent]
        updated = await stash_client.update_tag(tag)
        assert updated.id == mock_tag.id
        assert len(updated.parents) == 1
        assert updated.parents[0].id == new_parent.id


@pytest.mark.asyncio
async def test_merge_tags(stash_client: StashClient, mock_tag: Tag) -> None:
    """Test merging tags."""
    source_tags = [
        Tag(
            id="456",
            name="Source Tag 1",
            aliases=[],
            parents=[],
            children=[],
        ),
        Tag(
            id="789",
            name="Source Tag 2",
            aliases=[],
            parents=[],
            children=[],
        ),
    ]

    with patch.object(
        stash_client,
        "tags_merge",
        new_callable=AsyncMock,
        return_value=mock_tag,
    ):
        # Merge tags
        merged = await stash_client.tags_merge(
            source=[t.id for t in source_tags],
            destination=mock_tag.id,
        )
        assert merged.id == mock_tag.id
        assert merged.name == mock_tag.name
        # scene_count is not in the client model


@pytest.mark.asyncio
async def test_bulk_tag_update(stash_client: StashClient, mock_tag: Tag) -> None:
    """Test bulk updating tags."""
    tags = [mock_tag, mock_tag]  # Multiple tags with same data for testing
    with patch.object(
        stash_client,
        "bulk_tag_update",
        new_callable=AsyncMock,
        return_value=[mock_tag, mock_tag],
    ):
        # Update multiple tags
        updated = await stash_client.bulk_tag_update(
            ids=[t.id for t in tags],
            description="Bulk updated description",
            aliases=["bulk_alias1", "bulk_alias2"],
        )
        assert len(updated) == 2
        for tag in updated:
            assert tag.id == mock_tag.id
            assert tag.name == mock_tag.name
            assert tag.description == mock_tag.description
            assert tag.aliases == mock_tag.aliases


@pytest.mark.asyncio
async def test_tag_hierarchy(stash_client: StashClient, mock_tag: Tag) -> None:
    """Test tag hierarchy operations."""
    # Create test tags
    parent_tag = Tag(
        id="456",
        name="Parent Tag",
        aliases=[],
        parents=[],
        children=[],
    )
    child_tag = Tag(
        id="789",
        name="Child Tag",
        aliases=[],
        parents=[],
        children=[],
    )

    # Set up mock return value
    mock_tag.parents = [parent_tag]
    mock_tag.children = [child_tag]
    stash_client.update_tag = AsyncMock(return_value=mock_tag)

    # Update tag with parent and child
    tag = mock_tag
    updated = await stash_client.update_tag(tag)

    # Verify the result
    assert updated.id == mock_tag.id
    assert updated.name == mock_tag.name
    assert len(updated.parents) == 1
    assert len(updated.children) == 1
    assert updated.parents[0].id == parent_tag.id
    assert updated.children[0].id == child_tag.id
