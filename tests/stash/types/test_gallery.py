"""Tests for stash.types.gallery module."""

import pytest
import strawberry
from strawberry import ID

from stash.types.base import StashObject
from stash.types.enums import BulkUpdateIdMode
from stash.types.gallery import (  # Main gallery types; Gallery chapter types; Gallery input types; Gallery operation types; Bulk update types; Result types
    BulkGalleryUpdateInput,
    BulkUpdateIds,
    BulkUpdateStrings,
    FindGalleriesResultType,
    FindGalleryChaptersResultType,
    Gallery,
    GalleryAddInput,
    GalleryChapter,
    GalleryChapterCreateInput,
    GalleryChapterUpdateInput,
    GalleryCreateInput,
    GalleryDestroyInput,
    GalleryPathsType,
    GalleryRemoveInput,
    GalleryResetCoverInput,
    GallerySetCoverInput,
    GalleryUpdateInput,
)


@pytest.mark.unit
class TestGalleryPathsType:
    """Test GalleryPathsType class."""

    def test_strawberry_type_decoration(self):
        """Test that GalleryPathsType is decorated as strawberry type."""
        assert hasattr(GalleryPathsType, "__strawberry_definition__")
        assert not GalleryPathsType.__strawberry_definition__.is_input

    def test_field_types(self):
        """Test field type annotations."""
        annotations = GalleryPathsType.__annotations__
        assert annotations["cover"] == str
        assert annotations["preview"] == str

    def test_default_values(self):
        """Test default field values."""
        paths = GalleryPathsType()
        assert paths.cover == ""
        assert paths.preview == ""

    def test_create_default(self):
        """Test create_default class method."""
        paths = GalleryPathsType.create_default()
        assert isinstance(paths, GalleryPathsType)
        assert paths.cover == ""
        assert paths.preview == ""


@pytest.mark.unit
class TestGalleryChapter:
    """Test GalleryChapter class."""

    def test_strawberry_type_decoration(self):
        """Test that GalleryChapter is decorated as strawberry type."""
        assert hasattr(GalleryChapter, "__strawberry_definition__")
        assert not GalleryChapter.__strawberry_definition__.is_input

    def test_stash_object_inheritance(self):
        """Test that GalleryChapter inherits from StashObject."""
        assert issubclass(GalleryChapter, StashObject)

    def test_class_variables(self):
        """Test class variable values."""
        assert GalleryChapter.__type_name__ == "GalleryChapter"
        assert GalleryChapter.__update_input_type__ == GalleryChapterUpdateInput
        assert GalleryChapter.__create_input_type__ == GalleryChapterCreateInput

        # Test tracked fields
        expected_tracked = {"gallery", "title", "image_index"}
        assert GalleryChapter.__tracked_fields__ == expected_tracked

        # Test field conversions
        expected_conversions = {
            "title": str,
            "image_index": int,
        }
        assert GalleryChapter.__field_conversions__ == expected_conversions

        # Test relationships
        expected_relationships = {
            "gallery": ("gallery_id", False, None),
        }
        assert GalleryChapter.__relationships__ == expected_relationships

    def test_field_types(self):
        """Test field type annotations."""
        annotations = GalleryChapter.__annotations__
        assert annotations["title"] == str
        assert annotations["image_index"] == int


@pytest.mark.unit
class TestGalleryChapterInputs:
    """Test gallery chapter input types."""

    def test_gallery_chapter_create_input(self):
        """Test GalleryChapterCreateInput."""
        assert hasattr(GalleryChapterCreateInput, "__strawberry_definition__")
        assert GalleryChapterCreateInput.__strawberry_definition__.is_input

        annotations = GalleryChapterCreateInput.__annotations__
        assert annotations["gallery_id"] == ID
        assert annotations["title"] == str
        assert annotations["image_index"] == int

        # Test instantiation
        create_input = GalleryChapterCreateInput(
            gallery_id=ID("1"), title="Chapter 1", image_index=0
        )
        assert create_input.gallery_id == ID("1")
        assert create_input.title == "Chapter 1"
        assert create_input.image_index == 0

    def test_gallery_chapter_update_input(self):
        """Test GalleryChapterUpdateInput."""
        assert hasattr(GalleryChapterUpdateInput, "__strawberry_definition__")
        assert GalleryChapterUpdateInput.__strawberry_definition__.is_input

        annotations = GalleryChapterUpdateInput.__annotations__
        assert annotations["id"] == ID
        assert annotations["gallery_id"] == ID | None
        assert annotations["title"] == str | None
        assert annotations["image_index"] == int | None

        # Test instantiation
        update_input = GalleryChapterUpdateInput(id=ID("1"), title="Updated Chapter")
        assert update_input.id == ID("1")
        assert update_input.title == "Updated Chapter"
        assert update_input.gallery_id is None


@pytest.mark.unit
class TestGallery:
    """Test Gallery class."""

    def test_strawberry_type_decoration(self):
        """Test that Gallery is decorated as strawberry type."""
        assert hasattr(Gallery, "__strawberry_definition__")
        assert not Gallery.__strawberry_definition__.is_input

    def test_stash_object_inheritance(self):
        """Test that Gallery inherits from StashObject."""
        assert issubclass(Gallery, StashObject)

    def test_class_variables(self):
        """Test class variable values."""
        assert Gallery.__type_name__ == "Gallery"
        assert Gallery.__update_input_type__ == GalleryUpdateInput
        assert Gallery.__create_input_type__ == GalleryCreateInput

        # Test tracked fields
        expected_tracked = {
            "title",
            "code",
            "date",
            "details",
            "photographer",
            "rating100",
            "url",
            "urls",
            "organized",
            "files",
            "chapters",
            "scenes",
            "tags",
            "performers",
            "studio",
        }
        assert Gallery.__tracked_fields__ == expected_tracked

        # Test field conversions
        assert "title" in Gallery.__field_conversions__
        assert "code" in Gallery.__field_conversions__
        assert "urls" in Gallery.__field_conversions__
        assert "rating100" in Gallery.__field_conversions__
        assert "organized" in Gallery.__field_conversions__
        assert "date" in Gallery.__field_conversions__

        # Test relationships
        expected_relationships = {
            "studio": ("studio_id", False, None),
            "performers": ("performer_ids", True, None),
            "tags": ("tag_ids", True, None),
            "scenes": ("scene_ids", True, None),
        }
        assert Gallery.__relationships__ == expected_relationships

    def test_field_types(self):
        """Test field type annotations."""
        annotations = Gallery.__annotations__
        assert annotations["title"] == str | None
        assert annotations["code"] == str | None
        assert annotations["date"] == str | None
        assert annotations["details"] == str | None
        assert annotations["photographer"] == str | None
        assert annotations["rating100"] == int | None
        assert annotations["organized"] == bool
        assert annotations["image_count"] == int
        assert annotations["url"] == str | None

    def test_default_values(self):
        """Test default field values."""
        gallery = Gallery(id="test-gallery-id")
        assert gallery.organized is False
        assert gallery.image_count == 0
        assert isinstance(gallery.urls, list)
        assert len(gallery.urls) == 0
        assert isinstance(gallery.files, list)
        assert isinstance(gallery.chapters, list)
        assert isinstance(gallery.scenes, list)
        assert isinstance(gallery.tags, list)
        assert isinstance(gallery.performers, list)
        assert isinstance(gallery.paths, GalleryPathsType)

    def test_field_conversions(self):
        """Test field conversion functions."""
        conversions = Gallery.__field_conversions__

        # Test string conversions
        assert conversions["title"]("test") == "test"
        assert conversions["code"]("ABC123") == "ABC123"

        # Test int conversion
        assert conversions["rating100"](85) == 85

        # Test bool conversion
        assert conversions["organized"](True) is True

        # Test list conversion
        assert conversions["urls"](["url1", "url2"]) == ["url1", "url2"]


@pytest.mark.unit
class TestGalleryInputs:
    """Test gallery input types."""

    def test_gallery_create_input(self):
        """Test GalleryCreateInput."""
        assert hasattr(GalleryCreateInput, "__strawberry_definition__")
        assert GalleryCreateInput.__strawberry_definition__.is_input

        annotations = GalleryCreateInput.__annotations__
        assert annotations["title"] == str
        assert annotations["code"] == str | None
        assert annotations["url"] == str | None
        assert annotations["urls"] == list[str] | None
        assert annotations["date"] == str | None
        assert annotations["details"] == str | None
        assert annotations["photographer"] == str | None
        assert annotations["rating100"] == int | None
        assert annotations["organized"] == bool | None
        assert annotations["scene_ids"] == list[ID] | None
        assert annotations["studio_id"] == ID | None
        assert annotations["tag_ids"] == list[ID] | None
        assert annotations["performer_ids"] == list[ID] | None

        # Test instantiation
        create_input = GalleryCreateInput(title="Test Gallery")
        assert create_input.title == "Test Gallery"

    def test_gallery_update_input(self):
        """Test GalleryUpdateInput."""
        assert hasattr(GalleryUpdateInput, "__strawberry_definition__")
        assert GalleryUpdateInput.__strawberry_definition__.is_input

        annotations = GalleryUpdateInput.__annotations__
        assert annotations["id"] == ID
        assert annotations["client_mutation_id"] == str | None
        assert annotations["title"] == str | None
        assert annotations["primary_file_id"] == ID | None

        # Test instantiation
        update_input = GalleryUpdateInput(id=ID("1"), title="Updated Gallery")
        assert update_input.id == ID("1")
        assert update_input.title == "Updated Gallery"


@pytest.mark.unit
class TestGalleryOperationInputs:
    """Test gallery operation input types."""

    def test_gallery_add_input(self):
        """Test GalleryAddInput."""
        assert hasattr(GalleryAddInput, "__strawberry_definition__")
        assert GalleryAddInput.__strawberry_definition__.is_input

        annotations = GalleryAddInput.__annotations__
        assert annotations["gallery_id"] == ID
        assert annotations["image_ids"] == list[ID]

        # Test instantiation
        add_input = GalleryAddInput(gallery_id=ID("1"), image_ids=[ID("2"), ID("3")])
        assert add_input.gallery_id == ID("1")
        assert len(add_input.image_ids) == 2

    def test_gallery_remove_input(self):
        """Test GalleryRemoveInput."""
        assert hasattr(GalleryRemoveInput, "__strawberry_definition__")
        assert GalleryRemoveInput.__strawberry_definition__.is_input

        annotations = GalleryRemoveInput.__annotations__
        assert annotations["gallery_id"] == ID
        assert annotations["image_ids"] == list[ID]

    def test_gallery_set_cover_input(self):
        """Test GallerySetCoverInput."""
        assert hasattr(GallerySetCoverInput, "__strawberry_definition__")
        assert GallerySetCoverInput.__strawberry_definition__.is_input

        annotations = GallerySetCoverInput.__annotations__
        assert annotations["gallery_id"] == ID
        assert annotations["cover_image_id"] == ID

        # Test instantiation
        set_cover_input = GallerySetCoverInput(
            gallery_id=ID("1"), cover_image_id=ID("2")
        )
        assert set_cover_input.gallery_id == ID("1")
        assert set_cover_input.cover_image_id == ID("2")

    def test_gallery_reset_cover_input(self):
        """Test GalleryResetCoverInput."""
        assert hasattr(GalleryResetCoverInput, "__strawberry_definition__")
        assert GalleryResetCoverInput.__strawberry_definition__.is_input

        annotations = GalleryResetCoverInput.__annotations__
        assert annotations["gallery_id"] == ID

        # Test instantiation
        reset_cover_input = GalleryResetCoverInput(gallery_id=ID("1"))
        assert reset_cover_input.gallery_id == ID("1")

    def test_gallery_destroy_input(self):
        """Test GalleryDestroyInput."""
        assert hasattr(GalleryDestroyInput, "__strawberry_definition__")
        assert GalleryDestroyInput.__strawberry_definition__.is_input

        annotations = GalleryDestroyInput.__annotations__
        assert annotations["ids"] == list[ID]
        assert annotations["delete_file"] == bool | None
        assert annotations["delete_generated"] == bool | None

        # Test instantiation
        destroy_input = GalleryDestroyInput(ids=[ID("1"), ID("2")], delete_file=True)
        assert len(destroy_input.ids) == 2
        assert destroy_input.delete_file is True


@pytest.mark.unit
class TestBulkUpdateTypes:
    """Test bulk update input types."""

    def test_bulk_update_strings(self):
        """Test BulkUpdateStrings."""
        assert hasattr(BulkUpdateStrings, "__strawberry_definition__")
        assert BulkUpdateStrings.__strawberry_definition__.is_input

        annotations = BulkUpdateStrings.__annotations__
        assert annotations["values"] == list[str]
        assert annotations["mode"] == BulkUpdateIdMode

        # Test instantiation
        bulk_strings = BulkUpdateStrings(
            values=["url1", "url2"], mode=BulkUpdateIdMode.SET
        )
        assert len(bulk_strings.values) == 2
        assert bulk_strings.mode == BulkUpdateIdMode.SET

    def test_bulk_update_ids(self):
        """Test BulkUpdateIds."""
        assert hasattr(BulkUpdateIds, "__strawberry_definition__")
        assert BulkUpdateIds.__strawberry_definition__.is_input

        annotations = BulkUpdateIds.__annotations__
        assert annotations["ids"] == list[ID]
        assert annotations["mode"] == BulkUpdateIdMode

        # Test instantiation
        bulk_ids = BulkUpdateIds(ids=[ID("1"), ID("2")], mode=BulkUpdateIdMode.ADD)
        assert len(bulk_ids.ids) == 2
        assert bulk_ids.mode == BulkUpdateIdMode.ADD

    def test_bulk_gallery_update_input(self):
        """Test BulkGalleryUpdateInput."""
        assert hasattr(BulkGalleryUpdateInput, "__strawberry_definition__")
        assert BulkGalleryUpdateInput.__strawberry_definition__.is_input

        annotations = BulkGalleryUpdateInput.__annotations__
        assert annotations["ids"] == list[ID]
        assert annotations["client_mutation_id"] == str | None
        assert annotations["code"] == str | None
        assert annotations["url"] == str | None
        assert annotations["urls"] == BulkUpdateStrings | None
        assert annotations["date"] == str | None
        assert annotations["details"] == str | None
        assert annotations["photographer"] == str | None
        assert annotations["rating100"] == int | None
        assert annotations["organized"] == bool | None
        assert annotations["scene_ids"] == BulkUpdateIds | None
        assert annotations["studio_id"] == ID | None
        assert annotations["tag_ids"] == BulkUpdateIds | None
        assert annotations["performer_ids"] == BulkUpdateIds | None

        # Test instantiation
        bulk_update = BulkGalleryUpdateInput(ids=[ID("1"), ID("2")], rating100=85)
        assert len(bulk_update.ids) == 2
        assert bulk_update.rating100 == 85


@pytest.mark.unit
class TestResultTypes:
    """Test result types."""

    def test_find_galleries_result_type(self):
        """Test FindGalleriesResultType."""
        assert hasattr(FindGalleriesResultType, "__strawberry_definition__")
        assert not FindGalleriesResultType.__strawberry_definition__.is_input

        annotations = FindGalleriesResultType.__annotations__
        assert annotations["count"] == int
        assert annotations["galleries"] == list[Gallery]

        # Test instantiation
        result = FindGalleriesResultType(count=2, galleries=[])
        assert result.count == 2
        assert isinstance(result.galleries, list)

    def test_find_gallery_chapters_result_type(self):
        """Test FindGalleryChaptersResultType."""
        assert hasattr(FindGalleryChaptersResultType, "__strawberry_definition__")
        assert not FindGalleryChaptersResultType.__strawberry_definition__.is_input

        annotations = FindGalleryChaptersResultType.__annotations__
        assert annotations["count"] == int
        assert annotations["chapters"] == list[GalleryChapter]

        # Test instantiation
        result = FindGalleryChaptersResultType(count=3, chapters=[])
        assert result.count == 3
        assert isinstance(result.chapters, list)
