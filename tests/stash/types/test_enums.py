"""Tests for stash.types.enums module.

Tests all enum definitions to ensure they have correct values,
proper inheritance, and Strawberry decoration.
"""

from enum import Enum

import pytest
import strawberry

from stash.types.enums import (
    BlobsStorageType,
    BulkUpdateIdMode,
    CircumisedEnum,
    CriterionModifier,
    FilterMode,
    GenderEnum,
    HashAlgorithm,
    IdentifyFieldStrategy,
    ImageLightboxDisplayMode,
    ImageLightboxScrollMode,
    ImportDuplicateEnum,
    ImportMissingRefEnum,
    OnMultipleMatch,
    OrientationEnum,
    PackageType,
    PreviewPreset,
    ResolutionEnum,
    SortDirectionEnum,
    StreamingResolutionEnum,
    SystemStatusEnum,
)


@pytest.mark.unit
def test_gender_enum() -> None:
    """Test GenderEnum values match schema."""
    assert issubclass(GenderEnum, str)
    assert issubclass(GenderEnum, Enum)
    assert GenderEnum.MALE.value == "MALE"
    assert GenderEnum.FEMALE.value == "FEMALE"
    assert GenderEnum.TRANSGENDER_MALE.value == "TRANSGENDER_MALE"
    assert GenderEnum.TRANSGENDER_FEMALE.value == "TRANSGENDER_FEMALE"
    assert GenderEnum.INTERSEX.value == "INTERSEX"
    assert GenderEnum.NON_BINARY.value == "NON_BINARY"


def test_circumcised_enum() -> None:
    """Test CircumisedEnum values match schema."""
    assert issubclass(CircumisedEnum, str)
    assert issubclass(CircumisedEnum, Enum)
    assert CircumisedEnum.CUT.value == "CUT"
    assert CircumisedEnum.UNCUT.value == "UNCUT"


def test_bulk_update_id_mode() -> None:
    """Test BulkUpdateIdMode values match schema."""
    assert issubclass(BulkUpdateIdMode, str)
    assert issubclass(BulkUpdateIdMode, Enum)
    assert BulkUpdateIdMode.SET.value == "SET"
    assert BulkUpdateIdMode.ADD.value == "ADD"
    assert BulkUpdateIdMode.REMOVE.value == "REMOVE"


def test_sort_direction_enum() -> None:
    """Test SortDirectionEnum values match schema."""
    assert issubclass(SortDirectionEnum, str)
    assert issubclass(SortDirectionEnum, Enum)
    assert SortDirectionEnum.ASC.value == "ASC"
    assert SortDirectionEnum.DESC.value == "DESC"


def test_resolution_enum() -> None:
    """Test ResolutionEnum values match schema."""
    assert issubclass(ResolutionEnum, str)
    assert issubclass(ResolutionEnum, Enum)
    assert ResolutionEnum.VERY_LOW.value == "VERY_LOW"  # 144p
    assert ResolutionEnum.LOW.value == "LOW"  # 240p
    assert ResolutionEnum.R360P.value == "R360P"  # 360p
    assert ResolutionEnum.STANDARD.value == "STANDARD"  # 480p
    assert ResolutionEnum.WEB_HD.value == "WEB_HD"  # 540p
    assert ResolutionEnum.STANDARD_HD.value == "STANDARD_HD"  # 720p
    assert ResolutionEnum.FULL_HD.value == "FULL_HD"  # 1080p
    assert ResolutionEnum.QUAD_HD.value == "QUAD_HD"  # 1440p
    assert ResolutionEnum.FOUR_K.value == "FOUR_K"  # 4K
    assert ResolutionEnum.FIVE_K.value == "FIVE_K"  # 5K
    assert ResolutionEnum.SIX_K.value == "SIX_K"  # 6K
    assert ResolutionEnum.SEVEN_K.value == "SEVEN_K"  # 7K
    assert ResolutionEnum.EIGHT_K.value == "EIGHT_K"  # 8K
    assert ResolutionEnum.HUGE.value == "HUGE"  # 8K+


def test_orientation_enum() -> None:
    """Test OrientationEnum values match schema."""
    assert issubclass(OrientationEnum, str)
    assert issubclass(OrientationEnum, Enum)
    assert OrientationEnum.LANDSCAPE.value == "LANDSCAPE"
    assert OrientationEnum.PORTRAIT.value == "PORTRAIT"
    assert OrientationEnum.SQUARE.value == "SQUARE"


@pytest.mark.unit
def test_criterion_modifier() -> None:
    """Test CriterionModifier values match schema."""
    assert issubclass(CriterionModifier, str)
    assert issubclass(CriterionModifier, Enum)
    assert CriterionModifier.EQUALS.value == "EQUALS"
    assert CriterionModifier.NOT_EQUALS.value == "NOT_EQUALS"
    assert CriterionModifier.GREATER_THAN.value == "GREATER_THAN"
    assert CriterionModifier.LESS_THAN.value == "LESS_THAN"
    assert CriterionModifier.IS_NULL.value == "IS_NULL"
    assert CriterionModifier.NOT_NULL.value == "NOT_NULL"
    assert CriterionModifier.INCLUDES_ALL.value == "INCLUDES_ALL"
    assert CriterionModifier.INCLUDES.value == "INCLUDES"
    assert CriterionModifier.EXCLUDES.value == "EXCLUDES"
    assert CriterionModifier.MATCHES_REGEX.value == "MATCHES_REGEX"
    assert CriterionModifier.NOT_MATCHES_REGEX.value == "NOT_MATCHES_REGEX"
    assert CriterionModifier.BETWEEN.value == "BETWEEN"
    assert CriterionModifier.NOT_BETWEEN.value == "NOT_BETWEEN"


def test_filter_mode() -> None:
    """Test FilterMode values match schema."""
    assert issubclass(FilterMode, str)
    assert issubclass(FilterMode, Enum)
    assert FilterMode.SCENES.value == "SCENES"
    assert FilterMode.PERFORMERS.value == "PERFORMERS"
    assert FilterMode.STUDIOS.value == "STUDIOS"
    assert FilterMode.GALLERIES.value == "GALLERIES"
    assert FilterMode.SCENE_MARKERS.value == "SCENE_MARKERS"
    assert FilterMode.MOVIES.value == "MOVIES"
    assert FilterMode.GROUPS.value == "GROUPS"
    assert FilterMode.TAGS.value == "TAGS"
    assert FilterMode.IMAGES.value == "IMAGES"


def test_streaming_resolution_enum() -> None:
    """Test StreamingResolutionEnum values match schema."""
    assert issubclass(StreamingResolutionEnum, str)
    assert issubclass(StreamingResolutionEnum, Enum)
    assert StreamingResolutionEnum.LOW.value == "LOW"  # 240p
    assert StreamingResolutionEnum.STANDARD.value == "STANDARD"  # 480p
    assert StreamingResolutionEnum.STANDARD_HD.value == "STANDARD_HD"  # 720p
    assert StreamingResolutionEnum.FULL_HD.value == "FULL_HD"  # 1080p
    assert StreamingResolutionEnum.FOUR_K.value == "FOUR_K"  # 4k
    assert StreamingResolutionEnum.ORIGINAL.value == "ORIGINAL"  # Original


def test_preview_preset() -> None:
    """Test PreviewPreset values match schema."""
    assert issubclass(PreviewPreset, str)
    assert issubclass(PreviewPreset, Enum)
    assert PreviewPreset.ULTRAFAST.value == "ultrafast"
    assert PreviewPreset.VERYFAST.value == "veryfast"
    assert PreviewPreset.FAST.value == "fast"
    assert PreviewPreset.MEDIUM.value == "medium"
    assert PreviewPreset.SLOW.value == "slow"
    assert PreviewPreset.SLOWER.value == "slower"
    assert PreviewPreset.VERYSLOW.value == "veryslow"


def test_hash_algorithm() -> None:
    """Test HashAlgorithm values match schema."""
    assert issubclass(HashAlgorithm, str)
    assert issubclass(HashAlgorithm, Enum)
    assert HashAlgorithm.MD5.value == "MD5"
    assert HashAlgorithm.OSHASH.value == "OSHASH"


def test_blobs_storage_type() -> None:
    """Test BlobsStorageType values match schema."""
    assert issubclass(BlobsStorageType, str)
    assert issubclass(BlobsStorageType, Enum)
    assert BlobsStorageType.DATABASE.value == "DATABASE"
    assert BlobsStorageType.FILESYSTEM.value == "FILESYSTEM"


def test_image_lightbox_display_mode() -> None:
    """Test ImageLightboxDisplayMode values match schema."""
    assert issubclass(ImageLightboxDisplayMode, str)
    assert issubclass(ImageLightboxDisplayMode, Enum)
    assert ImageLightboxDisplayMode.ORIGINAL.value == "ORIGINAL"
    assert ImageLightboxDisplayMode.FIT_XY.value == "FIT_XY"
    assert ImageLightboxDisplayMode.FIT_X.value == "FIT_X"


def test_image_lightbox_scroll_mode() -> None:
    """Test ImageLightboxScrollMode values match schema."""
    assert issubclass(ImageLightboxScrollMode, str)
    assert issubclass(ImageLightboxScrollMode, Enum)
    assert ImageLightboxScrollMode.ZOOM.value == "ZOOM"
    assert ImageLightboxScrollMode.PAN_Y.value == "PAN_Y"


def test_identify_field_strategy() -> None:
    """Test IdentifyFieldStrategy values match schema."""
    assert issubclass(IdentifyFieldStrategy, str)
    assert issubclass(IdentifyFieldStrategy, Enum)
    assert IdentifyFieldStrategy.IGNORE.value == "IGNORE"
    assert IdentifyFieldStrategy.MERGE.value == "MERGE"
    assert IdentifyFieldStrategy.OVERWRITE.value == "OVERWRITE"


def test_import_duplicate_enum() -> None:
    """Test ImportDuplicateEnum values match schema."""
    assert issubclass(ImportDuplicateEnum, str)
    assert issubclass(ImportDuplicateEnum, Enum)
    assert ImportDuplicateEnum.IGNORE.value == "IGNORE"
    assert ImportDuplicateEnum.OVERWRITE.value == "OVERWRITE"
    assert ImportDuplicateEnum.FAIL.value == "FAIL"


def test_import_missing_ref_enum() -> None:
    """Test ImportMissingRefEnum values match schema."""
    assert issubclass(ImportMissingRefEnum, str)
    assert issubclass(ImportMissingRefEnum, Enum)
    assert ImportMissingRefEnum.IGNORE.value == "IGNORE"
    assert ImportMissingRefEnum.FAIL.value == "FAIL"
    assert ImportMissingRefEnum.CREATE.value == "CREATE"


def test_system_status_enum() -> None:
    """Test SystemStatusEnum values match schema."""
    assert issubclass(SystemStatusEnum, str)
    assert issubclass(SystemStatusEnum, Enum)
    assert SystemStatusEnum.SETUP.value == "SETUP"
    assert SystemStatusEnum.NEEDS_MIGRATION.value == "NEEDS_MIGRATION"
    assert SystemStatusEnum.OK.value == "OK"


def test_package_type() -> None:
    """Test PackageType values match schema."""
    assert issubclass(PackageType, str)
    assert issubclass(PackageType, Enum)
    assert PackageType.SCRAPER.value == "Scraper"
    assert PackageType.PLUGIN.value == "Plugin"


def test_on_multiple_match() -> None:
    """Test OnMultipleMatch values."""
    assert OnMultipleMatch.RETURN_NONE.value == 0
    assert OnMultipleMatch.RETURN_LIST.value == 1
    assert OnMultipleMatch.RETURN_FIRST.value == 2


def test_enum_values_match_schema() -> None:
    """Test that all enum values match the schema."""
    # Test that all enums are properly decorated with @strawberry.enum
    enums = [
        BlobsStorageType,
        BulkUpdateIdMode,
        CircumisedEnum,
        CriterionModifier,
        FilterMode,
        GenderEnum,
        HashAlgorithm,
        IdentifyFieldStrategy,
        ImageLightboxDisplayMode,
        ImageLightboxScrollMode,
        ImportDuplicateEnum,
        ImportMissingRefEnum,
        OrientationEnum,
        PackageType,
        PreviewPreset,
        ResolutionEnum,
        SortDirectionEnum,
        StreamingResolutionEnum,
        SystemStatusEnum,
    ]

    for enum in enums:
        assert issubclass(enum, str)  # All enums should inherit from str
        assert issubclass(enum, Enum)  # All enums should be Enum subclasses
