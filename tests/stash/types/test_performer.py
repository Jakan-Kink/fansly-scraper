"""Tests for stash.types.performer module.

Tests performer types including Performer, PerformerCreateInput, PerformerUpdateInput and related types.
"""

import os
import tempfile
from typing import get_type_hints
from unittest.mock import AsyncMock, Mock, PropertyMock, patch

import pytest
from strawberry import ID

from stash.types.enums import CircumisedEnum, GenderEnum
from stash.types.performer import (
    FindPerformersResultType,
    Performer,
    PerformerCreateInput,
    PerformerUpdateInput,
)


@pytest.mark.unit
class TestPerformerCreateInput:
    """Test the PerformerCreateInput type."""

    def test_strawberry_input_decoration(self):
        """Test that PerformerCreateInput is decorated as a strawberry input."""
        assert hasattr(PerformerCreateInput, "__strawberry_definition__")
        definition = PerformerCreateInput.__strawberry_definition__
        assert definition.is_input

    def test_field_annotations(self):
        """Test PerformerCreateInput field type annotations."""
        type_hints = get_type_hints(PerformerCreateInput)
        assert type_hints["name"] == str
        assert type_hints["disambiguation"] == str | None
        assert type_hints["url"] == str | None
        assert type_hints["urls"] == list[str] | None
        assert type_hints["gender"] == GenderEnum | None
        assert type_hints["birthdate"] == str | None
        assert type_hints["ethnicity"] == str | None

    def test_instantiation(self):
        """Test PerformerCreateInput instantiation."""
        performer_input = PerformerCreateInput(
            name="New Performer",
            gender=GenderEnum.FEMALE,
            birthdate="1990-01-01",
            ethnicity="Caucasian",
            height_cm=165,
        )
        assert performer_input.name == "New Performer"
        assert performer_input.gender == GenderEnum.FEMALE
        assert performer_input.birthdate == "1990-01-01"
        assert performer_input.ethnicity == "Caucasian"
        assert performer_input.height_cm == 165

    def test_performer_creation_scenario(self):
        """Test realistic performer creation scenario."""
        # Simulate creating a new performer with comprehensive details
        new_performer = PerformerCreateInput(
            name="Jane Doe",
            gender=GenderEnum.FEMALE,
            birthdate="1995-06-15",
            ethnicity="Mixed",
            country="United States",
            eye_color="Brown",
            height_cm=168,
            measurements="34B-24-36",
            alias_list=["Jane D", "J. Doe"],
            urls=["https://example.com/jane-doe"],
        )

        assert new_performer.name == "Jane Doe"
        assert new_performer.gender == GenderEnum.FEMALE
        assert new_performer.height_cm == 168
        assert new_performer.alias_list is not None
        assert "Jane D" in new_performer.alias_list
        assert new_performer.urls is not None
        assert len(new_performer.urls) == 1


@pytest.mark.unit
class TestPerformerUpdateInput:
    """Test the PerformerUpdateInput type."""

    def test_strawberry_input_decoration(self):
        """Test that PerformerUpdateInput is decorated as a strawberry input."""
        assert hasattr(PerformerUpdateInput, "__strawberry_definition__")
        definition = PerformerUpdateInput.__strawberry_definition__
        assert definition.is_input

    def test_field_annotations(self):
        """Test PerformerUpdateInput field type annotations."""
        type_hints = get_type_hints(PerformerUpdateInput)
        assert type_hints["id"] == ID
        assert type_hints["name"] == str | None
        assert type_hints["disambiguation"] == str | None
        assert type_hints["urls"] == list[str] | None
        assert type_hints["gender"] == GenderEnum | None

    def test_instantiation(self):
        """Test PerformerUpdateInput instantiation."""
        performer_input = PerformerUpdateInput(
            id=ID("123"),
            name="Updated Performer",
            gender=GenderEnum.MALE,
            ethnicity="Asian",
        )
        assert performer_input.id == ID("123")
        assert performer_input.name == "Updated Performer"
        assert performer_input.gender == GenderEnum.MALE
        assert performer_input.ethnicity == "Asian"

    def test_performer_update_scenario(self):
        """Test realistic performer update scenario."""
        # Simulate updating performer details
        update_input = PerformerUpdateInput(
            id=ID("performer-456"),
            height_cm=170,
            measurements="36C-26-38",
            career_length="2018-present",
            tattoos="Small rose on left shoulder",
            piercings="Ear piercings only",
        )

        assert update_input.id == ID("performer-456")
        assert update_input.height_cm == 170
        assert update_input.tattoos is not None
        assert "rose" in update_input.tattoos
        assert update_input.piercings is not None
        assert "Ear" in update_input.piercings


@pytest.mark.unit
class TestPerformer:
    """Test the Performer type."""

    def test_strawberry_type_decoration(self):
        """Test that Performer is decorated as a strawberry type."""
        assert hasattr(Performer, "__strawberry_definition__")
        definition = Performer.__strawberry_definition__
        assert hasattr(definition, "name")

    def test_inheritance_from_stash_object(self):
        """Test that Performer inherits from StashObject."""
        from stash.types.base import StashObject

        assert issubclass(Performer, StashObject)

    def test_class_variables(self):
        """Test Performer class variables."""
        assert hasattr(Performer, "__type_name__")
        assert Performer.__type_name__ == "Performer"

        assert hasattr(Performer, "__tracked_fields__")
        assert isinstance(Performer.__tracked_fields__, set)

        assert hasattr(Performer, "__field_conversions__")
        assert isinstance(Performer.__field_conversions__, dict)

        assert hasattr(Performer, "__relationships__")
        assert isinstance(Performer.__relationships__, dict)

    def test_field_annotations(self):
        """Test Performer field type annotations."""
        # Skip forward reference annotations that cause import issues
        import dataclasses

        fields = {f.name: f for f in dataclasses.fields(Performer)}
        assert "name" in fields
        assert "disambiguation" in fields
        assert "gender" in fields
        assert "birthdate" in fields

    def test_instantiation(self):
        """Test Performer instantiation."""
        performer = Performer(
            id=ID("123"), name="Test Performer", gender=GenderEnum.FEMALE
        )
        assert performer.id == ID("123")
        assert performer.name == "Test Performer"
        assert performer.gender == GenderEnum.FEMALE

    def test_performer_with_relationships(self):
        """Test Performer instantiation with relationships."""
        # Create alias list ensuring it's always list[str], never None
        alias_list: list[str] = ["Alias1", "Alias2"]
        performer = Performer(
            id=ID("456"),
            name="Related Performer",
            alias_list=alias_list,
            urls=["https://example.com/performer"],
        )
        assert performer.name == "Related Performer"
        assert performer.alias_list is not None
        assert len(performer.alias_list) == 2
        assert "Alias1" in performer.alias_list

    def test_performer_profile_scenario(self):
        """Test realistic performer profile scenario."""
        # Simulate a complete performer profile
        performer = Performer(
            id=ID("performer-789"),
            name="Sarah Johnson",
            gender=GenderEnum.FEMALE,
            birthdate="1992-03-20",
            ethnicity="Caucasian",
            country="Canada",
            eye_color="Blue",
            height_cm=172,
            measurements="34D-25-36",
            alias_list=["Sarah J", "SJ"],
            urls=["https://example.com/sarah-johnson"],
        )

        assert performer.name == "Sarah Johnson"
        assert performer.gender == GenderEnum.FEMALE
        assert performer.height_cm == 172
        assert "Sarah J" in performer.alias_list


@pytest.mark.unit
class TestFindPerformersResultType:
    """Test the FindPerformersResultType type."""

    def test_strawberry_type_decoration(self):
        """Test that FindPerformersResultType is decorated as a strawberry type."""
        assert hasattr(FindPerformersResultType, "__strawberry_definition__")
        definition = FindPerformersResultType.__strawberry_definition__
        assert hasattr(definition, "name")

    def test_field_annotations(self):
        """Test FindPerformersResultType field type annotations."""
        type_hints = get_type_hints(FindPerformersResultType)
        assert type_hints["count"] == int
        assert type_hints["performers"] == list[Performer]

    def test_instantiation(self):
        """Test FindPerformersResultType instantiation."""
        performers = [
            Performer(id=ID("1"), name="Performer 1"),
            Performer(id=ID("2"), name="Performer 2"),
        ]
        result = FindPerformersResultType(count=2, performers=performers)
        assert result.count == 2
        assert len(result.performers) == 2
        assert result.performers[0].name == "Performer 1"

    def test_performer_search_scenario(self):
        """Test realistic performer search scenario."""
        # Simulate search results for performers
        search_results = FindPerformersResultType(
            count=25,
            performers=[
                Performer(
                    id=ID("p1"),
                    name="Alice Cooper",
                    gender=GenderEnum.FEMALE,
                ),
                Performer(
                    id=ID("p2"),
                    name="Bob Smith",
                    gender=GenderEnum.MALE,
                ),
                Performer(
                    id=ID("p3"),
                    name="Carol Johnson",
                    gender=GenderEnum.FEMALE,
                ),
            ],
        )

        assert search_results.count == 25
        assert len(search_results.performers) == 3

        female_performers = [
            p for p in search_results.performers if p.gender == GenderEnum.FEMALE
        ]
        assert len(female_performers) == 2


@pytest.mark.unit
class TestPerformerTypeIntegration:
    """Test integration between different performer types."""

    def test_create_and_result_type_compatibility(self):
        """Test that created performers work with result types."""
        # Create input
        create_input = PerformerCreateInput(
            name="Integration Test", gender=GenderEnum.NON_BINARY
        )

        # Simulate created performer
        created_performer = Performer(
            id=ID("new-performer"),
            name=create_input.name,
            gender=create_input.gender,
        )

        # Use in result type
        result = FindPerformersResultType(count=1, performers=[created_performer])

        assert result.performers[0].name == "Integration Test"
        assert result.performers[0].gender == GenderEnum.NON_BINARY

    def test_update_input_validation(self):
        """Test update input validation scenarios."""
        # Test minimal update (only ID required)
        minimal_update = PerformerUpdateInput(id=ID("test-123"))
        assert minimal_update.id == ID("test-123")
        assert minimal_update.name is None

        # Test comprehensive update
        full_update = PerformerUpdateInput(
            id=ID("test-456"),
            name="Updated Name",
            gender=GenderEnum.TRANSGENDER_FEMALE,
            birthdate="1988-12-25",
            ethnicity="Hispanic",
            height_cm=175,
            circumcised=CircumisedEnum.CUT,
        )

        assert full_update.id == ID("test-456")
        assert full_update.name == "Updated Name"
        assert full_update.gender == GenderEnum.TRANSGENDER_FEMALE
        assert full_update.circumcised == CircumisedEnum.CUT

    def test_performer_data_workflow(self):
        """Test complete performer data workflow."""
        # 1. Create new performer
        create_input = PerformerCreateInput(
            name="Workflow Test",
            gender=GenderEnum.MALE,
            birthdate="1990-05-10",
            ethnicity="African American",
            height_cm=180,
            alias_list=["WT", "WorkflowT"],
        )

        # 2. Simulate performer creation
        assert create_input.alias_list is not None
        performer = Performer(
            id=ID("workflow-123"),
            name=create_input.name,
            gender=create_input.gender,
            birthdate=create_input.birthdate,
            ethnicity=create_input.ethnicity,
            height_cm=create_input.height_cm,
            alias_list=create_input.alias_list,
        )

        # 3. Update performer
        update_input = PerformerUpdateInput(
            id=ID(str(performer.id)), rating100=90, career_length="2015-present"
        )

        # 4. Use in search results
        search_result = FindPerformersResultType(count=1, performers=[performer])

        # Verify workflow
        assert search_result.performers[0].id == ID("workflow-123")
        assert search_result.performers[0].name == "Workflow Test"
        assert search_result.performers[0].gender == GenderEnum.MALE
        assert "WT" in search_result.performers[0].alias_list
        # Verify update input was created correctly
        assert update_input.career_length == "2015-present"
        assert update_input.rating100 == 90


@pytest.mark.unit
async def test_performer_update_avatar_method() -> None:
    """Test Performer.update_avatar method updates performer image."""

    # Create a test performer
    performer = Performer(id=ID("perf1"), name="Test Performer")

    # Create a mock client
    mock_client = Mock()
    mock_client.update_performer_image = AsyncMock(return_value=performer)

    # Create a temporary image file for testing
    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as temp_file:
        temp_file.write(b"fake image data")
        temp_path = temp_file.name

    try:
        # Test successful avatar update
        result = await performer.update_avatar(mock_client, temp_path)

        # Verify client method was called
        assert mock_client.update_performer_image.called
        call_args = mock_client.update_performer_image.call_args
        assert call_args[0][0] == performer  # First arg is performer
        assert call_args[0][1].startswith(
            "data:image/jpeg;base64,"
        )  # Second arg is base64 image

        # Verify result
        assert result == performer

    finally:
        # Clean up temp file
        os.unlink(temp_path)


@pytest.mark.unit
async def test_performer_update_avatar_file_not_found() -> None:
    """Test Performer.update_avatar raises when image file doesn't exist."""
    from unittest.mock import Mock

    performer = Performer(id=ID("perf2"), name="Test Performer")
    mock_client = Mock()

    # Test with non-existent file
    with pytest.raises(FileNotFoundError) as excinfo:
        await performer.update_avatar(mock_client, "/nonexistent/path.jpg")
    assert "not found" in str(excinfo.value)


@pytest.mark.unit
async def test_performer_update_avatar_read_error() -> None:
    """Test Performer.update_avatar handles file read errors."""

    performer = Performer(id=ID("perf3"), name="Test Performer")
    mock_client = Mock()
    mock_client.update_performer_image = AsyncMock(
        side_effect=Exception("Client error")
    )

    # Create a temporary file
    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as temp_file:
        temp_file.write(b"fake image data")
        temp_path = temp_file.name

    try:
        # Test error handling
        with pytest.raises(ValueError) as excinfo:
            await performer.update_avatar(mock_client, temp_path)
        assert "Failed to update avatar" in str(excinfo.value)

    finally:
        os.unlink(temp_path)


@pytest.mark.unit
def test_performer_from_dict_method() -> None:
    """Test Performer.from_dict method creates performer from dictionary."""

    # Test with minimal data
    data = {"id": "performer1", "name": "Test Performer"}
    performer = Performer.from_dict(data)

    assert performer.id == "performer1"
    assert performer.name == "Test Performer"


@pytest.mark.unit
def test_performer_from_dict_with_stash_ids() -> None:
    """Test Performer.from_dict handles stash_ids conversion."""
    from stash.types.files import StashID

    stash_data = [{"endpoint": "https://stashdb.org", "stash_id": "abc123"}]
    data = {"id": "performer2", "name": "Test Performer 2", "stash_ids": stash_data}

    performer = Performer.from_dict(data)

    assert performer.id == "performer2"
    assert len(performer.stash_ids) == 1
    assert isinstance(performer.stash_ids[0], StashID)
    assert performer.stash_ids[0].endpoint == "https://stashdb.org"
    assert performer.stash_ids[0].stash_id == "abc123"


@pytest.mark.unit
def test_performer_from_dict_strawberry_definition_fallback() -> None:
    """Test Performer.from_dict when strawberry definition access fails."""

    data = {"id": "performer3", "name": "Test Performer 3", "gender": "FEMALE"}

    # Mock the strawberry definition property to raise AttributeError
    with patch.object(
        Performer, "__strawberry_definition__", new_callable=PropertyMock
    ) as mock_def:
        mock_def.side_effect = AttributeError("Definition not available")

        # This should trigger the except AttributeError fallback
        performer = Performer.from_dict(data)

    # Should use fallback behavior - use unfiltered data
    assert performer.id == "performer3"
    assert performer.name == "Test Performer 3"
    # Verify that the AttributeError fallback path was actually taken
    mock_def.assert_called()


@pytest.mark.unit
def test_performer_from_account_method() -> None:
    """Test Performer.from_account method creates performer from account."""
    from unittest.mock import Mock

    # Create mock account with full data
    mock_account = Mock()
    mock_account.display_name = "Display Name"
    mock_account.username = "username"
    mock_account.screen_name = "Screen Name"
    mock_account.bio = "Test bio"

    performer = Performer.from_account(mock_account)

    # Verify basic fields
    assert performer.id == "new"
    assert performer.name == "Display Name"  # Should use display_name first
    assert performer.alias_list == [
        "username"
    ]  # Username as alias since display_name different
    assert performer.urls == ["https://fansly.com/username/posts"]
    assert performer.details == "Test bio"
    assert performer.country == ""

    # Verify required lists are initialized
    assert performer.tags == []
    assert performer.scenes == []
    assert performer.groups == []
    assert performer.stash_ids == []


@pytest.mark.unit
def test_performer_from_account_fallback_names() -> None:
    """Test Performer.from_account handles missing name fields."""
    from unittest.mock import Mock

    # Test with only username
    mock_account = Mock()
    mock_account.display_name = None
    mock_account.username = "username_only"
    mock_account.screen_name = None
    mock_account.bio = None

    performer = Performer.from_account(mock_account)

    assert performer.name == "username_only"
    assert performer.alias_list == []  # No alias since using username as name
    assert performer.details == ""  # Empty string for None bio

    # Test with only screen_name
    mock_account2 = Mock()
    mock_account2.display_name = None
    mock_account2.username = None
    mock_account2.screen_name = "screen_only"
    mock_account2.bio = "Bio text"

    performer2 = Performer.from_account(mock_account2)

    assert performer2.name == "screen_only"
    assert performer2.urls == []  # No URL without username
    assert performer2.details == "Bio text"

    # Test with all names None - should fallback to "Unknown"
    mock_account3 = Mock()
    mock_account3.display_name = None
    mock_account3.username = None
    mock_account3.screen_name = None
    mock_account3.bio = None

    performer3 = Performer.from_account(mock_account3)

    assert performer3.name == "Unknown"
    assert performer3.alias_list == []
    assert performer3.urls == []


@pytest.mark.unit
def test_performer_from_account_alias_case_sensitivity() -> None:
    """Test Performer.from_account handles alias case sensitivity correctly."""
    from unittest.mock import Mock

    # Test case where display_name and username are same (case-insensitive)
    mock_account = Mock()
    mock_account.display_name = "SameUser"
    mock_account.username = "sameuser"  # Different case
    mock_account.screen_name = None
    mock_account.bio = None

    performer = Performer.from_account(mock_account)

    # Should not add username as alias since it's same as display_name (case-insensitive)
    assert performer.name == "SameUser"
    assert performer.alias_list == []
