"""Unit tests for PerformerClientMixin."""

from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest

from stash import StashClient
from stash.types import FindPerformersResultType, Performer, Tag


@pytest.fixture
def mock_performer() -> Performer:
    """Create a mock performer for testing."""
    return Performer(
        id="123",
        name="Test Performer",
        gender="FEMALE",
        url="https://example.com/performer",
        urls=["https://example.com/performer", "https://example.com/performer2"],
        birthdate="1990-01-01",
        ethnicity="CAUCASIAN",
        country="US",
        eye_color="BLUE",
        height_cm=170,
        measurements="34-24-36",
        fake_tits="NO",
        career_length="2020-",
        tattoos="None",
        piercings="None",
        alias_list=["Alias 1", "Alias 2"],
        favorite=True,
        rating100=85,
        details="Test performer details",
        created_at=datetime.now(),
        updated_at=datetime.now(),
        tags=[
            Tag(
                id="456",
                name="Tag1",
                description="Test tag",
                created_at=datetime.now(),
                updated_at=datetime.now(),
            )
        ],
    )


@pytest.mark.asyncio
async def test_find_performer(
    stash_client: StashClient, mock_performer: Performer
) -> None:
    """Test finding a performer by ID."""
    with patch.object(
        stash_client,
        "execute",
        new_callable=AsyncMock,
        return_value={"findPerformer": mock_performer.__dict__},
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
        assert performer.rating100 == mock_performer.rating100
        assert len(performer.tags) == 1
        assert performer.tags[0].id == mock_performer.tags[0].id

        # Find by name
        performer = await stash_client.find_performer("Test Performer")
        assert performer is not None
        assert performer.id == mock_performer.id
        assert performer.name == mock_performer.name


@pytest.mark.asyncio
async def test_find_performers(
    stash_client: StashClient, mock_performer: Performer
) -> None:
    """Test finding performers with filters."""
    mock_result = FindPerformersResultType(
        count=1,
        performers=[mock_performer],
    )

    with patch.object(
        stash_client,
        "execute",
        new_callable=AsyncMock,
        return_value={"findPerformers": mock_result.__dict__},
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
    with patch.object(
        stash_client,
        "execute",
        new_callable=AsyncMock,
        return_value={"performerCreate": mock_performer.__dict__},
    ):
        # Create with minimum fields
        performer = Performer(
            name="New Performer",
            gender="FEMALE",
            created_at=datetime.now(),
            updated_at=datetime.now(),
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
        assert created.rating100 == mock_performer.rating100
        assert len(created.tags) == 1


@pytest.mark.asyncio
async def test_update_performer(
    stash_client: StashClient, mock_performer: Performer
) -> None:
    """Test updating a performer."""
    with patch.object(
        stash_client,
        "execute",
        new_callable=AsyncMock,
        return_value={"performerUpdate": mock_performer.__dict__},
    ):
        # Update single field
        performer = mock_performer
        performer.name = "Updated Name"
        updated = await stash_client.update_performer(performer)
        assert updated.id == mock_performer.id
        assert updated.name == mock_performer.name

        # Update multiple fields
        performer.details = "Updated details"
        performer.favorite = False
        performer.rating100 = 90
        updated = await stash_client.update_performer(performer)
        assert updated.id == mock_performer.id
        assert updated.details == mock_performer.details
        assert updated.favorite == mock_performer.favorite
        assert updated.rating100 == mock_performer.rating100

        # Update relationships
        new_tag = Tag(
            id="789",
            name="NewTag",
            description="New test tag",
            created_at=datetime.now(),
            updated_at=datetime.now(),
        )
        performer.tags.append(new_tag)
        updated = await stash_client.update_performer(performer)
        assert updated.id == mock_performer.id
        assert len(updated.tags) == len(performer.tags)


@pytest.mark.asyncio
async def test_update_performer_avatar(
    stash_client: StashClient, mock_performer: Performer
) -> None:
    """Test updating a performer's avatar."""
    mock_path = "/path/to/avatar.jpg"
    with patch.object(
        stash_client,
        "execute",
        new_callable=AsyncMock,
        return_value={
            "performerUpdate": {**mock_performer.__dict__, "image_path": mock_path}
        },
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
    from metadata import Account

    # Create mock account
    account = Account(
        id=123,
        username="test_account",
        displayName="Test Account",
        about="Test account bio",
        location="US",
        createdAt=datetime.now(),
        updatedAt=datetime.now(),
    )

    with patch.object(
        stash_client,
        "execute",
        new_callable=AsyncMock,
        return_value={"performerCreate": mock_performer.__dict__},
    ):
        # Convert account to performer
        performer = await Performer.from_account(account)
        assert performer.name == account.displayName
        assert performer.details == account.about
        assert performer.country == account.location

        # Create in Stash
        created = await performer.save(stash_client)
        assert created.id == mock_performer.id
        assert created.name == mock_performer.name
