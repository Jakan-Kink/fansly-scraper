"""Unit tests for PerformerClientMixin.

These tests use respx to mock GraphQL HTTP responses at the edge,
testing the real StashClient methods with mocked HTTP layer.
"""

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from stash import StashClient
from stash.types import Tag
from stash.types.performer import FindPerformersResultType, Performer
from tests.fixtures.metadata.metadata_factories import AccountFactory


@pytest.mark.asyncio
async def test_find_performer(stash_client: StashClient, mock_performer) -> None:
    """Test finding a performer by ID."""
    # Use existing mock_performer fixture from stash_type_factories
    # Mock the find_performer method directly
    with patch.object(
        stash_client,
        "find_performer",
        new_callable=AsyncMock,
        return_value=mock_performer,
    ):
        # Find by ID
        performer = await stash_client.find_performer("123")
        assert performer is not None
        assert performer.id == mock_performer.id
        assert performer.name == mock_performer.name
        assert performer.gender == mock_performer.gender
        assert performer.birthdate == mock_performer.birthdate
        assert performer.measurements == mock_performer.measurements
        assert performer.alias_list == mock_performer.alias_list
        # rating100 is not in the client model
        assert len(performer.tags) == 1
        assert performer.tags[0].id == mock_performer.tags[0].id

        # Find by name
        performer = await stash_client.find_performer("Test Performer")
        assert performer is not None
        assert performer.id == mock_performer.id
        assert performer.name == mock_performer.name


@pytest.mark.asyncio
async def test_find_performers(
    stash_client: StashClient, stash_cleanup_tracker, mock_performer: Performer
) -> None:
    """Test finding performers with filters."""
    mock_result = FindPerformersResultType(
        count=1,
        performers=[mock_performer],
    )

    # Mock the find_performers method directly
    with patch.object(
        stash_client,
        "find_performers",
        new_callable=AsyncMock,
        return_value=mock_result,
    ):
        # Test with performer filter
        result = await stash_client.find_performers(
            performer_filter={
                "name": {"modifier": "EQUALS", "value": "Test Performer"},
                "gender": "FEMALE",
                "favorite": True,
            }
        )
        assert result.count == 1
        assert len(result.performers) == 1
        assert result.performers[0].id == mock_performer.id

        # Test with general filter
        result = await stash_client.find_performers(
            filter_={
                "page": 1,
                "per_page": 10,
                "sort": "name",
                "direction": "ASC",
            }
        )
        assert result.count == 1
        assert len(result.performers) == 1


@pytest.mark.asyncio
async def test_create_performer(
    stash_client: StashClient, mock_performer: Performer
) -> None:
    """Test creating a performer."""
    # Mock the create_performer method directly
    with patch.object(
        stash_client,
        "create_performer",
        new_callable=AsyncMock,
        return_value=mock_performer,
    ):
        # Create with minimum fields
        performer = Performer(
            id="new",  # Required for initialization
            name="New Performer",
            gender="FEMALE",
            groups=[],  # Required relationship
            tags=[],  # Required relationship
            scenes=[],  # Required relationship
            stash_ids=[],  # Required relationship
        )
        created = await stash_client.create_performer(performer)
        assert created.id == mock_performer.id
        assert created.name == mock_performer.name

        # Create with all fields
        performer = mock_performer
        performer.id = "new"  # Force create
        created = await stash_client.create_performer(performer)
        assert created.id == mock_performer.id
        assert created.name == mock_performer.name
        assert created.gender == mock_performer.gender
        assert created.birthdate == mock_performer.birthdate
        assert created.measurements == mock_performer.measurements
        assert created.alias_list == mock_performer.alias_list
        # rating100 is not in the client model
        assert len(created.tags) == 1


@pytest.mark.asyncio
async def test_update_performer(
    stash_client: StashClient, stash_cleanup_tracker, mock_performer: Performer
) -> None:
    """Test updating a performer."""
    # Mock the update_performer method directly
    with patch.object(
        stash_client,
        "update_performer",
        new_callable=AsyncMock,
        return_value=mock_performer,
    ):
        # Update single field
        performer = mock_performer
        performer.name = "Updated Name"
        updated = await stash_client.update_performer(performer)
        assert updated.id == mock_performer.id
        assert updated.name == mock_performer.name

        # Update multiple fields
        performer.details = "Updated details"
        # favorite and rating100 are not in the client model
        updated = await stash_client.update_performer(performer)
        assert updated.id == mock_performer.id
        assert updated.details == mock_performer.details
        # favorite and rating100 are not in the client model

        # Update relationships
        new_tag = Tag(
            id="789",
            name="NewTag",
            description="New test tag",
        )
        performer.tags.append(new_tag)
        updated = await stash_client.update_performer(performer)
        assert updated.id == mock_performer.id
        assert len(updated.tags) == len(performer.tags)


@pytest.mark.asyncio
async def test_update_performer_avatar(
    stash_client: StashClient, mock_performer: Performer, tmp_path: Path
) -> None:
    """Test updating a performer's avatar."""
    mock_path = tmp_path / "avatar.jpg"
    # Create a temporary file for testing
    mock_path.write_text("test")

    # Create a modified performer with image_path set
    updated_performer = Performer(
        id=mock_performer.id,
        name=mock_performer.name,
        gender=mock_performer.gender,
        image_path=str(mock_path),
        groups=[],  # Required relationship
        tags=[],  # Required relationship
        scenes=[],  # Required relationship
        stash_ids=[],  # Required relationship
    )

    # Mock the update_performer_image method directly
    with patch.object(
        stash_client,
        "update_performer_image",
        new_callable=AsyncMock,
        return_value=updated_performer,
    ):
        # Update avatar
        performer = mock_performer
        updated = await performer.update_avatar(stash_client, mock_path)
        assert updated.id == mock_performer.id
        assert updated.image_path == mock_path


@pytest.mark.asyncio
async def test_performer_from_account(
    stash_client: StashClient, mock_performer: Performer
) -> None:
    """Test creating a performer from an account."""
    # Use AccountFactory instead of local fixture
    account = AccountFactory.build(
        id=123,
        username="test_account",
        displayName="Test Account",
        about="Test account bio",
        location="US",
    )

    # Mock the create_performer method directly
    with (
        patch.object(
            stash_client,
            "create_performer",
            new_callable=AsyncMock,
            return_value=mock_performer,
        ),
        patch.object(
            Performer,
            "save",
            new_callable=AsyncMock,
            return_value=mock_performer,
        ),
    ):
        # Convert account to performer
        performer = Performer.from_account(account)
        assert performer.name == account.displayName
        assert performer.details == account.about
        assert performer.country == account.location

        # Create in Stash
        created = await performer.save(stash_client)
        assert created.id == mock_performer.id
        assert created.name == mock_performer.name
