"""Tests for stash.types.image module.

Tests image types including Image, ImageCreateInput, ImageUpdateInput and related types.
"""

from unittest.mock import PropertyMock, patch

import pytest
from strawberry import ID

from stash.types.image import (
    BulkImageUpdateInput,
    FindImagesResultType,
    Image,
    ImageDestroyInput,
    ImageFileType,
    ImagePathsType,
    ImagesDestroyInput,
    ImageUpdateInput,
)


@pytest.mark.unit
def test_image_file_type() -> None:
    """Test ImageFileType type."""
    assert hasattr(ImageFileType, "__strawberry_definition__")

    # Test field types
    fields = {
        field.name: field for field in ImageFileType.__strawberry_definition__.fields
    }
    expected_fields = ["mod_time", "size", "width", "height"]

    for field in expected_fields:
        assert field in fields, f"Field {field} not found in ImageFileType"


@pytest.mark.unit
def test_image_paths_type() -> None:
    """Test ImagePathsType type."""
    assert hasattr(ImagePathsType, "__strawberry_definition__")

    # Test field types
    fields = {
        field.name: field for field in ImagePathsType.__strawberry_definition__.fields
    }
    expected_fields = ["thumbnail", "preview", "image"]

    for field in expected_fields:
        assert field in fields, f"Field {field} not found in ImagePathsType"


@pytest.mark.unit
def test_image_update_input() -> None:
    """Test ImageUpdateInput input type."""
    assert hasattr(ImageUpdateInput, "__strawberry_definition__")

    # Test field types
    fields = {
        field.name: field for field in ImageUpdateInput.__strawberry_definition__.fields
    }
    expected_fields = [
        "id",
        "title",
        "code",
        "urls",
        "date",
        "details",
        "photographer",
        "rating100",
        "organized",
        "studio_id",
        "performer_ids",
        "tag_ids",
        "gallery_ids",
    ]

    for field in expected_fields:
        assert field in fields, f"Field {field} not found in ImageUpdateInput"


@pytest.mark.unit
def test_image() -> None:
    """Test Image type."""
    assert hasattr(Image, "__strawberry_definition__")

    # Test that it extends StashObject
    fields = {field.name: field for field in Image.__strawberry_definition__.fields}
    assert "id" in fields  # From StashObject

    # Test image-specific fields
    expected_fields = [
        "title",
        "code",
        "date",
        "details",
        "photographer",
        "studio",
        "urls",
        "organized",
        "visual_files",
        "paths",
        "galleries",
        "tags",
        "performers",
    ]

    for field in expected_fields:
        assert field in fields, f"Field {field} not found in Image"


@pytest.mark.unit
def test_image_class_variables() -> None:
    """Test Image class variables."""
    assert hasattr(Image, "__type_name__")
    assert Image.__type_name__ == "Image"

    assert hasattr(Image, "__update_input_type__")
    assert Image.__update_input_type__ == ImageUpdateInput

    assert hasattr(Image, "__tracked_fields__")
    # Test that key fields are tracked
    tracked_fields = Image.__tracked_fields__
    key_tracked = ["title", "studio", "performers", "tags", "galleries"]
    for field in key_tracked:
        assert field in tracked_fields, f"Field {field} not in tracked fields"


@pytest.mark.unit
def test_image_field_conversions() -> None:
    """Test Image field conversions."""
    assert hasattr(Image, "__field_conversions__")

    expected_conversions = {
        "title": str,
        "code": str,
        "urls": list,
        "details": str,
        "photographer": str,
    }

    for field, conversion in expected_conversions.items():
        if field in Image.__field_conversions__:
            assert Image.__field_conversions__[field] == conversion

    # Check that date field has a callable conversion function
    if "date" in Image.__field_conversions__:
        assert callable(Image.__field_conversions__["date"])


@pytest.mark.unit
def test_image_relationships() -> None:
    """Test Image relationships."""
    assert hasattr(Image, "__relationships__")

    # Test key relationships exist
    expected_relationships = ["studio", "performers", "tags", "galleries"]

    for field in expected_relationships:
        assert field in Image.__relationships__, (
            f"Relationship {field} not found in Image"
        )

    # Test specific relationship mappings
    performers_mapping = Image.__relationships__["performers"]
    assert performers_mapping[0] == "performer_ids"  # target field
    assert performers_mapping[1] is True  # is_list

    studio_mapping = Image.__relationships__["studio"]
    assert studio_mapping[0] == "studio_id"  # target field
    assert studio_mapping[1] is False  # is_list


@pytest.mark.unit
def test_image_destroy_input() -> None:
    """Test ImageDestroyInput input type."""
    assert hasattr(ImageDestroyInput, "__strawberry_definition__")

    # Test field types
    fields = {
        field.name: field
        for field in ImageDestroyInput.__strawberry_definition__.fields
    }
    expected_fields = ["id", "delete_file", "delete_generated"]

    for field in expected_fields:
        assert field in fields, f"Field {field} not found in ImageDestroyInput"


@pytest.mark.unit
def test_images_destroy_input() -> None:
    """Test ImagesDestroyInput input type."""
    assert hasattr(ImagesDestroyInput, "__strawberry_definition__")

    # Test field types
    fields = {
        field.name: field
        for field in ImagesDestroyInput.__strawberry_definition__.fields
    }
    expected_fields = ["ids", "delete_file", "delete_generated"]

    for field in expected_fields:
        assert field in fields, f"Field {field} not found in ImagesDestroyInput"


@pytest.mark.unit
def test_bulk_image_update_input() -> None:
    """Test BulkImageUpdateInput input type."""
    assert hasattr(BulkImageUpdateInput, "__strawberry_definition__")

    # Test field types
    fields = {
        field.name: field
        for field in BulkImageUpdateInput.__strawberry_definition__.fields
    }
    expected_fields = [
        "ids",
        "rating100",
        "organized",
        "studio_id",
        "performer_ids",
        "tag_ids",
        "gallery_ids",
    ]

    for field in expected_fields:
        assert field in fields, f"Field {field} not found in BulkImageUpdateInput"


@pytest.mark.unit
def test_find_images_result_type() -> None:
    """Test FindImagesResultType result type."""
    assert hasattr(FindImagesResultType, "__strawberry_definition__")

    # Test field types
    fields = {
        field.name: field
        for field in FindImagesResultType.__strawberry_definition__.fields
    }
    expected_fields = ["count", "images"]

    for field in expected_fields:
        assert field in fields, f"Field {field} not found in FindImagesResultType"


@pytest.mark.unit
def test_image_instantiation() -> None:
    """Test Image instantiation."""
    image = Image(id=ID("123"), title="Test Image")

    assert image.id == ID("123")
    assert image.title == "Test Image"
    assert image.urls == []  # default factory
    assert image.visual_files == []  # default factory
    assert image.galleries == []  # default factory
    assert image.tags == []  # default factory
    assert image.performers == []  # default factory
    assert image.organized is False  # default value


@pytest.mark.unit
def test_image_update_input_instantiation() -> None:
    """Test ImageUpdateInput instantiation."""
    image_input = ImageUpdateInput(
        id=ID("123"), title="Updated Image", photographer="Test Photographer"
    )

    assert image_input.id == ID("123")
    assert image_input.title == "Updated Image"
    assert image_input.photographer == "Test Photographer"


@pytest.mark.unit
def test_image_file_type_instantiation() -> None:
    """Test ImageFileType instantiation."""
    from datetime import datetime

    file_type = ImageFileType(
        mod_time=datetime.now(), size=1024, width=1920, height=1080
    )

    assert isinstance(file_type.mod_time, datetime)
    assert file_type.size == 1024
    assert file_type.width == 1920
    assert file_type.height == 1080


@pytest.mark.unit
def test_image_paths_type_instantiation() -> None:
    """Test ImagePathsType instantiation."""
    paths = ImagePathsType()

    # All fields should be optional and default to None
    assert paths.thumbnail is None
    assert paths.preview is None
    assert paths.image is None


@pytest.mark.unit
def test_strawberry_decorations() -> None:
    """Test that all types are properly decorated with strawberry."""
    types_to_test = [
        ImageFileType,
        ImagePathsType,
        ImageUpdateInput,
        Image,
        ImageDestroyInput,
        ImagesDestroyInput,
        BulkImageUpdateInput,
        FindImagesResultType,
    ]

    for type_class in types_to_test:
        assert hasattr(type_class, "__strawberry_definition__"), (
            f"{type_class.__name__} missing strawberry definition"
        )


@pytest.mark.unit
def test_image_inheritance() -> None:
    """Test that Image properly inherits from StashObject."""

    # Test that Image follows the StashObject interface pattern
    assert hasattr(Image, "__type_name__")
    assert hasattr(Image, "__tracked_fields__")
    assert hasattr(Image, "__field_conversions__")
    assert hasattr(Image, "__relationships__")


@pytest.mark.unit
def test_image_from_dict_method() -> None:
    """Test that Image has from_dict class method."""
    assert hasattr(Image, "from_dict")
    assert callable(getattr(Image, "from_dict"))


@pytest.mark.unit
def test_image_from_dict_missing_id_raises() -> None:
    """Test that Image.from_dict raises when ID is missing."""
    with pytest.raises(ValueError) as excinfo:
        Image.from_dict({})
    assert "ID field" in str(excinfo.value)


@pytest.mark.unit
def test_image_from_dict_with_minimal_data() -> None:
    """Test that Image.from_dict works with minimal data."""
    data = {"id": "image1"}
    image = Image.from_dict(data)
    assert image.id == "image1"
    # Default lists should be empty
    assert isinstance(image.visual_files, list) and image.visual_files == []
    assert isinstance(image.galleries, list) and image.galleries == []


@pytest.mark.unit
def test_image_from_dict_with_deprecated_files() -> None:
    """Test that Image.from_dict handles deprecated 'files' field."""
    from stash.types.files import ImageFile

    # Mock file data that would be in the deprecated 'files' field
    file_data = {
        "id": "file1",
        "path": "/path/to/image.jpg",
        "basename": "image.jpg",
        "parent_folder_id": "folder1",
        "mod_time": "2023-01-01T00:00:00Z",
        "size": 1024,
        "fingerprints": [],
        "width": 1920,
        "height": 1080,
    }

    data = {"id": "image2", "files": [file_data]}

    # Mock ImageFile since that's what the code now calls
    def mock_image_file_constructor(**kwargs):
        # Return ImageFile since we have width/height but no duration
        return ImageFile(**kwargs)

    with patch(
        "stash.types.image.ImageFile", side_effect=mock_image_file_constructor
    ) as mock_image_file:
        # Call the actual method (now works because bug is fixed!)
        image = Image.from_dict(data)

        # Verify the deprecated 'files' branch was hit (lines 150-151)
        assert image.id == "image2"
        assert len(image.visual_files) == 1
        assert mock_image_file.called
        # Verify it's the right type
        assert isinstance(image.visual_files[0], ImageFile)


@pytest.mark.unit
def test_image_from_dict_with_visual_files() -> None:
    """Test that Image.from_dict handles visual_files field."""
    from stash.types.files import ImageFile

    file_data = {
        "id": "file2",
        "path": "/path/to/image2.jpg",
        "basename": "image2.jpg",
        "parent_folder_id": "folder2",
        "mod_time": "2023-01-01T00:00:00Z",
        "size": 2048,
        "fingerprints": [],
        "width": 1280,
        "height": 720,
    }

    data = {"id": "image3", "visual_files": [file_data]}

    # Mock ImageFile since that's what the code now calls
    def mock_image_file_constructor(**kwargs):
        # Return ImageFile since we have width/height but no duration
        return ImageFile(**kwargs)

    with patch(
        "stash.types.image.ImageFile", side_effect=mock_image_file_constructor
    ) as mock_image_file:
        # Call the actual method (not mocked)
        image = Image.from_dict(data)

        # Verify the visual_files branch was hit
        assert image.id == "image3"
        assert len(image.visual_files) == 1
        assert mock_image_file.called
        # Verify it's the right type
        assert isinstance(image.visual_files[0], ImageFile)


@pytest.mark.unit
def test_image_from_dict_strawberry_definition_fallback() -> None:
    """Test Image.from_dict when strawberry definition access fails."""

    # Use only valid Image fields for the fallback test
    data = {"id": "image4", "title": "Test Image", "organized": True}

    # Mock the strawberry definition property to raise AttributeError
    with patch.object(
        Image, "__strawberry_definition__", new_callable=PropertyMock
    ) as mock_def:
        mock_def.side_effect = AttributeError("Definition not available")

        # This should trigger the except AttributeError fallback
        image = Image.from_dict(data)

    # Should use fallback behavior - use unfiltered data (but only valid fields)
    assert image.id == "image4"
    assert image.title == "Test Image"
    assert image.organized is True
    # Verify that the AttributeError fallback path was actually taken
    mock_def.assert_called()


@pytest.mark.unit
def test_image_from_dict_actual_implementation() -> None:
    """Test Image.from_dict method actually works with proper data."""

    # Test data that should work with the actual implementation
    data = {
        "id": "image5",
        "title": "Actual Test Image",
        "organized": False,
        "urls": ["https://example.com/image.jpg"],
        "photographer": "Test Photographer",
    }

    # Call the actual method (not mocked)
    image = Image.from_dict(data)

    # Verify the image was created correctly
    assert image.id == "image5"
    assert image.title == "Actual Test Image"
    assert image.organized is False
    assert image.urls == ["https://example.com/image.jpg"]
    assert image.photographer == "Test Photographer"
    assert image.visual_files == []  # Should be empty when no files provided


@pytest.mark.unit
def test_image_from_dict_field_filtering() -> None:
    """Test Image.from_dict filters unknown fields correctly."""
    # Data with both valid and invalid fields
    data = {
        "id": "image6",
        "title": "Filtered Test",
        "unknown_field": "should_be_filtered",
        "another_unknown": 12345,
        "organized": True,
    }

    # Call the actual method
    image = Image.from_dict(data)

    # Valid fields should be set
    assert image.id == "image6"
    assert image.title == "Filtered Test"
    assert image.organized is True

    # Unknown fields should not be present
    assert not hasattr(image, "unknown_field")
    assert not hasattr(image, "another_unknown")


@pytest.mark.unit
def test_image_from_dict_files_removal_logic() -> None:
    """Test Image.from_dict handles 'files' field correctly."""
    from stash.types.files import ImageFile

    file_data = {
        "id": "file3",
        "path": "/path/to/image3.jpg",
        "basename": "image3.jpg",
        "parent_folder_id": "folder3",
        "mod_time": "2023-01-01T00:00:00Z",
        "size": 1024,
        "fingerprints": [],
        "width": 800,
        "height": 600,
    }

    # Create data dict with both files and valid fields
    data = {"id": "image7", "files": [file_data], "title": "Test"}

    # Mock ImageFile since that's what the code now calls
    def mock_image_file_constructor(**kwargs):
        # Return ImageFile since we have width/height but no duration
        return ImageFile(**kwargs)

    with patch(
        "stash.types.image.ImageFile", side_effect=mock_image_file_constructor
    ) as mock_image_file:
        # Call the actual method (now works because bug is fixed!)
        image = Image.from_dict(data)

        # Verify file processing occurred
        assert image.id == "image7"
        assert len(image.visual_files) == 1
        assert mock_image_file.called

        # Original data should be unchanged
        assert "files" in data  # Original data unchanged
        assert image.title == "Test"  # Other fields processed correctly
        # Verify it's the right type
        assert isinstance(image.visual_files[0], ImageFile)
