"""Unit tests for TagClientMixin."""

from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest

from stash import StashClient
from stash.types import FindTagsResultType, Tag, TagsMergeInput


@pytest.fixture
def mock_tag() -> Tag:
    """Create a mock tag for testing."""
    return Tag(
        id="123",
        name="Test Tag",
        description="Test tag description",
        aliases=["alias1", "alias2"],
        ignore_auto_tag=False,
        created_at=datetime.now(),
        updated_at=datetime.now(),
        favorite=True,
        image_path="/path/to/image.jpg",
        scene_count=10,
        scene_marker_count=5,
        image_count=20,
        gallery_count=3,
        performer_count=2,
        parents=[
            Tag(
                id="456",
                name="Parent Tag",
                created_at=datetime.now(),
                updated_at=datetime.now(),
            )
        ],
        children=[
            Tag(
                id="789",
                name="Child Tag",
                created_at=datetime.now(),
                updated_at=datetime.now(),
            )
        ],
    )


@pytest.mark.asyncio
async def test_find_tag(stash_client: StashClient, mock_tag: Tag) -> None:
    """Test finding a tag by ID."""
    with patch.object(
        stash_client,
        "execute",
        new_callable=AsyncMock,
        return_value={"findTag": mock_tag.__dict__},
    ):
        # Find by ID
        tag = await stash_client.find_tag("123")
        assert tag is not None
        assert tag.id == mock_tag.id
        assert tag.name == mock_tag.name
        assert tag.description == mock_tag.description
        assert tag.aliases == mock_tag.aliases
        assert tag.favorite == mock_tag.favorite
        assert tag.scene_count == mock_tag.scene_count
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
        "execute",
        new_callable=AsyncMock,
        return_value={"findTags": mock_result.__dict__},
    ):
        # Test with tag filter
        result = await stash_client.find_tags(
            tag_filter={
                "name": {"modifier": "EQUALS", "value": "Test Tag"},
                "description": {"modifier": "INCLUDES", "value": "test"},
                "favorite": True,
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
        "execute",
        new_callable=AsyncMock,
        return_value={"tagCreate": mock_tag.__dict__},
    ):
        # Create with minimum fields
        tag = Tag(
            name="New Tag",
            created_at=datetime.now(),
            updated_at=datetime.now(),
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
        assert created.favorite == mock_tag.favorite
        assert len(created.parents) == 1
        assert len(created.children) == 1


@pytest.mark.asyncio
async def test_update_tag(stash_client: StashClient, mock_tag: Tag) -> None:
    """Test updating a tag."""
    with patch.object(
        stash_client,
        "execute",
        new_callable=AsyncMock,
        return_value={"tagUpdate": mock_tag.__dict__},
    ):
        # Update single field
        tag = mock_tag
        tag.name = "Updated Name"
        updated = await stash_client.update_tag(tag)
        assert updated.id == mock_tag.id
        assert updated.name == mock_tag.name

        # Update multiple fields
        tag.description = "Updated description"
        tag.favorite = False
        tag.aliases = ["new_alias1", "new_alias2"]
        updated = await stash_client.update_tag(tag)
        assert updated.id == mock_tag.id
        assert updated.description == mock_tag.description
        assert updated.favorite == mock_tag.favorite
        assert updated.aliases == mock_tag.aliases

        # Update relationships
        new_parent = Tag(
            id="999",
            name="New Parent",
            created_at=datetime.now(),
            updated_at=datetime.now(),
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
            created_at=datetime.now(),
            updated_at=datetime.now(),
        ),
        Tag(
            id="789",
            name="Source Tag 2",
            created_at=datetime.now(),
            updated_at=datetime.now(),
        ),
    ]

    with patch.object(
        stash_client,
        "execute",
        new_callable=AsyncMock,
        return_value={"tagsMerge": mock_tag.__dict__},
    ):
        # Merge tags
        merged = await stash_client.tags_merge(
            source=[t.id for t in source_tags],
            destination=mock_tag.id,
        )
        assert merged.id == mock_tag.id
        assert merged.name == mock_tag.name
        # Scene count should be sum of all tags
        assert merged.scene_count == mock_tag.scene_count


@pytest.mark.asyncio
async def test_bulk_tag_update(stash_client: StashClient, mock_tag: Tag) -> None:
    """Test bulk updating tags."""
    tags = [mock_tag, mock_tag]  # Multiple tags with same data for testing
    with patch.object(
        stash_client,
        "execute",
        new_callable=AsyncMock,
        return_value={"bulkTagUpdate": [t.__dict__ for t in tags]},
    ):
        # Update multiple tags
        updated = await stash_client.bulk_tag_update(
            ids=[t.id for t in tags],
            description="Bulk updated description",
            aliases=["bulk_alias1", "bulk_alias2"],
            ignore_auto_tag=True,
            favorite=True,
        )
        assert len(updated) == len(tags)
        for tag in updated:
            assert tag.id in [t.id for t in tags]
            assert tag.description == "Bulk updated description"
            assert tag.aliases == ["bulk_alias1", "bulk_alias2"]
            assert tag.ignore_auto_tag is True
            assert tag.favorite is True


@pytest.mark.asyncio
async def test_tag_hierarchy(stash_client: StashClient, mock_tag: Tag) -> None:
    """Test tag hierarchy operations."""
    parent_tag = Tag(
        id="456",
        name="Parent Tag",
        created_at=datetime.now(),
        updated_at=datetime.now(),
    )
    child_tag = Tag(
        id="789",
        name="Child Tag",
        created_at=datetime.now(),
        updated_at=datetime.now(),
    )

    with patch.object(
        stash_client,
        "execute",
        new_callable=AsyncMock,
        return_value={"tagUpdate": mock_tag.__dict__},
    ):
        # Set parent
        tag = mock_tag
        tag.parents = [parent_tag]
        updated = await stash_client.update_tag(tag)
        assert updated.id == mock_tag.id
        assert len(updated.parents) == 1
        assert updated.parents[0].id == parent_tag.id

        # Set child
        tag.children = [child_tag]
        updated = await stash_client.update_tag(tag)
        assert updated.id == mock_tag.id
        assert len(updated.children) == 1
        assert updated.children[0].id == child_tag.id

        # Set both
        tag.parents = [parent_tag]
        tag.children = [child_tag]
        updated = await stash_client.update_tag(tag)
        assert updated.id == mock_tag.id
        assert len(updated.parents) == 1
        assert len(updated.children) == 1
        assert updated.parents[0].id == parent_tag.id
        assert updated.children[0].id == child_tag.id
