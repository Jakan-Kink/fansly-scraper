"""Tests for stash.types.files module.

Tests file types including BaseFile, VideoFile, ImageFile and related input types.
"""

from datetime import datetime

import pytest
from strawberry import ID

from stash.types.files import (
    BaseFile,
    FileSetFingerprintsInput,
    Fingerprint,
    Folder,
    GalleryFile,
    ImageFile,
    MoveFilesInput,
    SetFingerprintsInput,
    StashID,
    fingerprint_resolver,
    StashIDInput,
    VideoCaption,
    VideoFile,
    VisualFile,
)


@pytest.mark.unit
def test_set_fingerprints_input() -> None:
    """Test SetFingerprintsInput input type."""
    assert hasattr(SetFingerprintsInput, "__strawberry_definition__")

    # Test field types
    fields = {
        field.name: field
        for field in SetFingerprintsInput.__strawberry_definition__.fields
    }
    assert "type_" in fields  # Python attribute name (GraphQL name is "type")
    assert "value" in fields

    # Test instantiation
    fingerprint_input = SetFingerprintsInput(type_="MD5", value="abc123")
    assert fingerprint_input.type_ == "MD5"
    assert fingerprint_input.value == "abc123"


@pytest.mark.unit
def test_file_set_fingerprints_input() -> None:
    """Test FileSetFingerprintsInput input type."""
    assert hasattr(FileSetFingerprintsInput, "__strawberry_definition__")

    # Test field types
    fields = {
        field.name: field
        for field in FileSetFingerprintsInput.__strawberry_definition__.fields
    }
    assert "id" in fields
    assert "fingerprints" in fields

    # Test instantiation
    file_input = FileSetFingerprintsInput(
        id=ID("123"), fingerprints=[SetFingerprintsInput(type_="MD5", value="abc123")]
    )
    assert file_input.id == ID("123")
    assert len(file_input.fingerprints) == 1


@pytest.mark.unit
def test_move_files_input() -> None:
    """Test MoveFilesInput input type."""
    assert hasattr(MoveFilesInput, "__strawberry_definition__")

    # Test field types
    fields = {
        field.name: field for field in MoveFilesInput.__strawberry_definition__.fields
    }
    assert "ids" in fields
    assert "destination_folder" in fields
    assert "destination_folder_id" in fields
    assert "destination_basename" in fields

    # Test instantiation
    move_input = MoveFilesInput(ids=[ID("1"), ID("2")], destination_folder="/new/path")
    assert move_input.ids == [ID("1"), ID("2")]
    assert move_input.destination_folder == "/new/path"


@pytest.mark.unit
def test_fingerprint() -> None:
    """Test Fingerprint type."""
    assert hasattr(Fingerprint, "__strawberry_definition__")

    # Test field types
    fields = {
        field.name: field for field in Fingerprint.__strawberry_definition__.fields
    }
    assert "type_" in fields  # Python attribute name (GraphQL name is "type")
    assert "value" in fields

    # Test instantiation
    fingerprint = Fingerprint(type_="MD5", value="abc123")
    assert fingerprint.type_ == "MD5"
    assert fingerprint.value == "abc123"


@pytest.mark.unit
def test_base_file_interface() -> None:
    """Test BaseFile interface."""
    assert hasattr(BaseFile, "__strawberry_definition__")

    # Test that it's an interface that extends StashObject
    assert BaseFile.__strawberry_definition__.is_interface

    # Test required fields
    fields = {field.name: field for field in BaseFile.__strawberry_definition__.fields}
    expected_fields = [
        "path",
        "basename",
        "parent_folder_id",
        "mod_time",
        "size",
        "fingerprints",
    ]

    for field in expected_fields:
        assert field in fields, f"Field {field} not found in BaseFile"


@pytest.mark.unit
def test_image_file() -> None:
    """Test ImageFile type."""
    assert hasattr(ImageFile, "__strawberry_definition__")

    # Test that it implements BaseFile
    # ImageFile should have all BaseFile fields plus additional ones
    fields = {field.name: field for field in ImageFile.__strawberry_definition__.fields}
    base_fields = [
        "path",
        "basename",
        "parent_folder_id",
        "mod_time",
        "size",
        "fingerprints",
    ]

    for field in base_fields:
        assert field in fields, f"BaseFile field {field} not found in ImageFile"

    # Test image-specific fields
    image_fields = ["width", "height"]
    for field in image_fields:
        assert field in fields, f"Image field {field} not found in ImageFile"


@pytest.mark.unit
def test_video_file() -> None:
    """Test VideoFile type."""
    assert hasattr(VideoFile, "__strawberry_definition__")

    # Test that it implements BaseFile
    fields = {field.name: field for field in VideoFile.__strawberry_definition__.fields}
    base_fields = [
        "path",
        "basename",
        "parent_folder_id",
        "mod_time",
        "size",
        "fingerprints",
    ]

    for field in base_fields:
        assert field in fields, f"BaseFile field {field} not found in VideoFile"

    # Test video-specific fields
    video_fields = [
        "duration",
        "video_codec",
        "audio_codec",
        "width",
        "height",
        "frame_rate",
        "bit_rate",
    ]
    for field in video_fields:
        assert field in fields, f"Video field {field} not found in VideoFile"


@pytest.mark.unit
def test_visual_file_union() -> None:
    """Test VisualFile union type."""
    # Test union properties with proper attribute checks for mypy compatibility
    if hasattr(VisualFile, "graphql_name"):
        assert VisualFile.graphql_name == "VisualFile"

    assert hasattr(VisualFile, "types")

    # Test union members
    if hasattr(VisualFile, "types"):
        union_types = {getattr(t, "__name__", str(t)) for t in VisualFile.types}
        expected_types = {"VideoFile", "ImageFile"}
        assert union_types == expected_types

    # Note: Cannot subclass unions in tests - they are typing special forms
    # Unions don't have __strawberry_definition__ like regular types


@pytest.mark.unit
def test_gallery_file() -> None:
    """Test GalleryFile type."""
    assert hasattr(GalleryFile, "__strawberry_definition__")

    # Test that it implements BaseFile
    fields = {
        field.name: field for field in GalleryFile.__strawberry_definition__.fields
    }
    base_fields = [
        "path",
        "basename",
        "parent_folder_id",
        "mod_time",
        "size",
        "fingerprints",
    ]

    for field in base_fields:
        assert field in fields, f"BaseFile field {field} not found in GalleryFile"


@pytest.mark.unit
def test_stash_id_input() -> None:
    """Test StashIDInput input type."""
    assert hasattr(StashIDInput, "__strawberry_definition__")

    # Test field types
    fields = {
        field.name: field for field in StashIDInput.__strawberry_definition__.fields
    }
    assert "endpoint" in fields
    assert "stash_id" in fields

    # Test instantiation
    stash_input = StashIDInput(endpoint="https://stashdb.org", stash_id="abc123")
    assert stash_input.endpoint == "https://stashdb.org"
    assert stash_input.stash_id == "abc123"


@pytest.mark.unit
def test_stash_id() -> None:
    """Test StashID type."""
    assert hasattr(StashID, "__strawberry_definition__")

    # Test field types
    fields = {field.name: field for field in StashID.__strawberry_definition__.fields}
    assert "endpoint" in fields
    assert "stash_id" in fields

    # Test instantiation
    stash_id = StashID(endpoint="https://stashdb.org", stash_id="abc123")
    assert stash_id.endpoint == "https://stashdb.org"
    assert stash_id.stash_id == "abc123"


@pytest.mark.unit
def test_video_caption() -> None:
    """Test VideoCaption type."""
    assert hasattr(VideoCaption, "__strawberry_definition__")

    # Test field types
    fields = {
        field.name: field for field in VideoCaption.__strawberry_definition__.fields
    }
    assert "language_code" in fields
    assert "caption_type" in fields

    # Test instantiation
    caption = VideoCaption(language_code="en", caption_type="srt")
    assert caption.language_code == "en"
    assert caption.caption_type == "srt"


@pytest.mark.unit
def test_folder() -> None:
    """Test Folder type."""
    assert hasattr(Folder, "__strawberry_definition__")

    # Test that it extends StashObject
    fields = {field.name: field for field in Folder.__strawberry_definition__.fields}
    assert "id" in fields  # From StashObject

    # Test folder-specific fields
    folder_fields = ["path", "parent_folder_id", "zip_file_id", "mod_time"]
    for field in folder_fields:
        assert field in fields, f"Folder field {field} not found"


@pytest.mark.unit
def test_strawberry_decorations() -> None:
    """Test that all types are properly decorated with strawberry."""
    types_to_test = [
        SetFingerprintsInput,
        FileSetFingerprintsInput,
        MoveFilesInput,
        Fingerprint,
        BaseFile,
        ImageFile,
        VideoFile,
        GalleryFile,
        StashIDInput,
        StashID,
        VideoCaption,
        Folder,
    ]

    for type_class in types_to_test:
        assert hasattr(type_class, "__strawberry_definition__"), (
            f"{type_class.__name__} missing strawberry definition"
        )


@pytest.mark.unit
def test_base_file_methods() -> None:
    """Test BaseFile interface methods."""
    # Test that BaseFile has expected methods
    expected_methods = ["to_input"]
    expected_fields = ["fingerprint"]

    for method in expected_methods:
        assert hasattr(BaseFile, method), f"Method {method} not found on BaseFile"

    for field in expected_fields:
        assert hasattr(BaseFile, field), f"Field {field} not found on BaseFile"


@pytest.mark.unit
def test_base_file_fingerprint_resolver() -> None:
    """Test BaseFile fingerprint resolver logic."""
    # Create a test file with fingerprints
    fingerprints = [
        Fingerprint(type_="MD5", value="abc123"),
        Fingerprint(type_="SHA1", value="def456"),
        Fingerprint(type_="PHASH", value="789ghi"),
    ]

    # Create a concrete BaseFile implementation for testing
    test_file = ImageFile(
        id=ID("test1"),
        path="/test/image.jpg",
        basename="image.jpg",
        parent_folder_id=ID("parent1"),
        mod_time=datetime.now(),
        size=1024,
        fingerprints=fingerprints,
        width=1920,
        height=1080,
    )

    # Test the resolver function directly
    assert fingerprint_resolver(test_file, "MD5") == "abc123"
    assert fingerprint_resolver(test_file, "SHA1") == "def456"
    assert fingerprint_resolver(test_file, "PHASH") == "789ghi"

    # Test finding non-existent fingerprint type (should return empty string per GraphQL schema)
    assert fingerprint_resolver(test_file, "NONEXISTENT") == ""


@pytest.mark.unit
async def test_base_file_to_input_method() -> None:
    """Test BaseFile.to_input method converts to GraphQL input."""
    # Create a test file
    test_file = VideoFile(
        id=ID("video1"),
        path="/test/video.mp4",
        basename="video.mp4",
        parent_folder_id=ID("parent1"),
        mod_time=datetime.now(),
        size=2048,
        fingerprints=[],
        format="mp4",
        width=1920,
        height=1080,
        duration=120.5,
        video_codec="h264",
        audio_codec="aac",
        frame_rate=29.97,
        bit_rate=5000000,
    )

    # Test to_input method
    input_data = await test_file.to_input()

    # Verify the structure matches MoveFilesInput expectations
    assert "ids" in input_data
    assert input_data["ids"] == [ID("video1")]
    assert "destination_folder" in input_data
    assert input_data["destination_folder"] is None  # Must be set by caller
    assert "destination_folder_id" in input_data
    assert input_data["destination_folder_id"] is None  # Must be set by caller
    assert "destination_basename" in input_data
    assert input_data["destination_basename"] == "video.mp4"


@pytest.mark.unit
async def test_base_file_to_input_no_id_raises() -> None:
    """Test BaseFile.to_input raises when file has no ID."""
    # Create a file without ID by using delattr to remove it after creation
    test_file = ImageFile(
        id=ID("temp"),  # Temporary ID that we'll remove
        path="/test/no_id.jpg",
        basename="no_id.jpg",
        parent_folder_id=ID("parent1"),
        mod_time=datetime.now(),
        size=1024,
        fingerprints=[],
        width=1920,
        height=1080,
    )

    # Remove the ID attribute to simulate a file without ID
    delattr(test_file, "id")

    # Should raise ValueError for missing ID
    with pytest.raises(ValueError) as excinfo:
        await test_file.to_input()
    assert "ID" in str(excinfo.value)


@pytest.mark.unit
async def test_folder_to_input_method() -> None:
    """Test Folder.to_input method converts to GraphQL input."""
    # Create a test folder
    test_folder = Folder(
        id=ID("folder1"),
        path="/test/folder",
        parent_folder_id=ID("parent1"),
        mod_time=datetime.now(),
    )

    # Test to_input method
    input_data = await test_folder.to_input()

    # Verify the structure matches MoveFilesInput expectations for folders
    assert "ids" in input_data
    assert input_data["ids"] == [ID("folder1")]
    assert "destination_folder" in input_data
    assert input_data["destination_folder"] is None  # Must be set by caller
    assert "destination_folder_id" in input_data
    assert input_data["destination_folder_id"] is None  # Must be set by caller
    assert "destination_basename" in input_data
    assert input_data["destination_basename"] is None  # Not applicable for folders


@pytest.mark.unit
async def test_folder_to_input_no_id_raises() -> None:
    """Test Folder.to_input raises when folder has no ID."""
    # Create a folder without ID by using delattr to remove it after creation
    test_folder = Folder(
        id=ID("temp"),  # Temporary ID that we'll remove
        path="/test/no_id_folder",
        parent_folder_id=ID("parent1"),
        mod_time=datetime.now(),
    )

    # Remove the ID attribute to simulate a folder without ID
    delattr(test_folder, "id")

    # Should raise ValueError for missing ID
    with pytest.raises(ValueError) as excinfo:
        await test_folder.to_input()
    assert "ID" in str(excinfo.value)
