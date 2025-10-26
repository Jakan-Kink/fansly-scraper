"""Metadata types from schema/types/metadata.graphql."""

from datetime import datetime
from typing import TYPE_CHECKING, Any

import strawberry
from strawberry import ID

if TYPE_CHECKING:
    pass
else:
    pass

from .enums import (
    IdentifyFieldStrategy,
    ImportDuplicateEnum,
    ImportMissingRefEnum,
    PreviewPreset,
    SystemStatusEnum,
)


@strawberry.input
class GeneratePreviewOptionsInput:
    """Input for preview generation options from schema/types/metadata.graphql."""

    previewSegments: int | None = None  # Int (Number of segments in a preview file)
    previewSegmentDuration: float | None = (
        None  # Float (Preview segment duration, in seconds)
    )
    previewExcludeStart: str | None = (
        None  # String (Duration of start of video to exclude when generating previews)
    )
    previewExcludeEnd: str | None = (
        None  # String (Duration of end of video to exclude when generating previews)
    )
    previewPreset: PreviewPreset | None = (
        None  # PreviewPreset (Preset when generating preview)
    )


@strawberry.input
class GenerateMetadataInput:
    """Input for metadata generation from schema/types/metadata.graphql."""

    covers: bool = False  # Boolean
    sprites: bool = False  # Boolean
    previews: bool = False  # Boolean
    imagePreviews: bool = False  # Boolean
    previewOptions: GeneratePreviewOptionsInput | None = (
        None  # GeneratePreviewOptionsInput
    )
    markers: bool = False  # Boolean
    markerImagePreviews: bool = False  # Boolean
    markerScreenshots: bool = False  # Boolean
    transcodes: bool = False  # Boolean
    forceTranscodes: bool = False  # Boolean
    phashes: bool = False  # Boolean
    interactiveHeatmapsSpeeds: bool = False  # Boolean
    imageThumbnails: bool = False  # Boolean
    clipPreviews: bool = False  # Boolean
    sceneIDs: list[ID] | None = None  # [ID!] (scene ids to generate for)
    markerIDs: list[ID] | None = None  # [ID!] (marker ids to generate for)
    overwrite: bool = False  # Boolean (overwrite existing media)


@strawberry.type
class GeneratePreviewOptions:
    """Preview generation options from schema/types/metadata.graphql."""

    previewSegments: int | None = None  # Int (Number of segments in a preview file)
    previewSegmentDuration: float | None = (
        None  # Float (Preview segment duration, in seconds)
    )
    previewExcludeStart: str | None = (
        None  # String (Duration of start of video to exclude when generating previews)
    )
    previewExcludeEnd: str | None = (
        None  # String (Duration of end of video to exclude when generating previews)
    )
    previewPreset: PreviewPreset | None = (
        None  # PreviewPreset (Preset when generating preview)
    )


@strawberry.type
class GenerateMetadataOptions:
    """Metadata generation options from schema/types/metadata.graphql."""

    covers: bool | None = None  # Boolean
    sprites: bool | None = None  # Boolean
    previews: bool | None = None  # Boolean
    imagePreviews: bool | None = None  # Boolean
    previewOptions: GeneratePreviewOptions | None = None  # GeneratePreviewOptions
    markers: bool | None = None  # Boolean
    markerImagePreviews: bool | None = None  # Boolean
    markerScreenshots: bool | None = None  # Boolean
    transcodes: bool | None = None  # Boolean
    phashes: bool | None = None  # Boolean
    interactiveHeatmapsSpeeds: bool | None = None  # Boolean
    imageThumbnails: bool | None = None  # Boolean
    clipPreviews: bool | None = None  # Boolean


@strawberry.input
class ScanMetaDataFilterInput:
    """Filter options for meta data scanning from schema/types/metadata.graphql."""

    minModTime: datetime | None = (
        None  # Timestamp (If set, files with a modification time before this time point are ignored by the scan)
    )


@strawberry.input
class ScanMetadataInput:
    """Input for metadata scanning from schema/types/metadata.graphql."""

    paths: list[str]  # [String!]
    rescan: bool | None = (
        None  # Boolean (Forces a rescan on files even if modification time is unchanged)
    )
    scanGenerateCovers: bool | None = None  # Boolean (Generate covers during scan)
    scanGeneratePreviews: bool | None = None  # Boolean (Generate previews during scan)
    scanGenerateImagePreviews: bool | None = (
        None  # Boolean (Generate image previews during scan)
    )
    scanGenerateSprites: bool | None = None  # Boolean (Generate sprites during scan)
    scanGeneratePhashes: bool | None = None  # Boolean (Generate phashes during scan)
    scanGenerateThumbnails: bool | None = (
        None  # Boolean (Generate image thumbnails during scan)
    )
    scanGenerateClipPreviews: bool | None = (
        None  # Boolean (Generate image clip previews during scan)
    )
    filter: ScanMetaDataFilterInput | None = (
        None  # ScanMetaDataFilterInput (Filter options for the scan)
    )


@strawberry.type
class ScanMetadataOptions:
    """Metadata scan options from schema/types/metadata.graphql."""

    rescan: bool  # Boolean! (Forces a rescan on files even if modification time is unchanged)
    scanGenerateCovers: bool  # Boolean! (Generate covers during scan)
    scanGeneratePreviews: bool  # Boolean! (Generate previews during scan)
    scanGenerateImagePreviews: bool  # Boolean! (Generate image previews during scan)
    scanGenerateSprites: bool  # Boolean! (Generate sprites during scan)
    scanGeneratePhashes: bool  # Boolean! (Generate phashes during scan)
    scanGenerateThumbnails: bool  # Boolean! (Generate image thumbnails during scan)
    scanGenerateClipPreviews: (
        bool  # Boolean! (Generate image clip previews during scan)
    )


@strawberry.input
class CleanMetadataInput:
    """Input for metadata cleaning from schema/types/metadata.graphql."""

    paths: list[str]  # [String!]
    dryRun: bool  # Boolean! (Do a dry run. Don't delete any files)


@strawberry.input
class CleanGeneratedInput:
    """Input for cleaning generated files from schema/types/metadata.graphql."""

    blobFiles: bool | None = None  # Boolean (Clean blob files without blob entries)
    sprites: bool | None = (
        None  # Boolean (Clean sprite and vtt files without scene entries)
    )
    screenshots: bool | None = (
        None  # Boolean (Clean preview files without scene entries)
    )
    transcodes: bool | None = (
        None  # Boolean (Clean scene transcodes without scene entries)
    )
    markers: bool | None = None  # Boolean (Clean marker files without marker entries)
    imageThumbnails: bool | None = (
        None  # Boolean (Clean image thumbnails/clips without image entries)
    )
    dryRun: bool  # Boolean (Do a dry run. Don't delete any files)


@strawberry.input
class AutoTagMetadataInput:
    """Input for auto-tagging metadata from schema/types/metadata.graphql."""

    paths: list[str] | None = None  # [String!] (Paths to tag, null for all files)
    performers: list[str] | None = (
        None  # [String!] (IDs of performers to tag files with, or "*" for all)
    )
    studios: list[str] | None = (
        None  # [String!] (IDs of studios to tag files with, or "*" for all)
    )
    tags: list[str] | None = (
        None  # [String!] (IDs of tags to tag files with, or "*" for all)
    )


@strawberry.type
class AutoTagMetadataOptions:
    """Auto-tag metadata options from schema/types/metadata.graphql."""

    performers: list[str] | None = (
        None  # [String!] (IDs of performers to tag files with, or "*" for all)
    )
    studios: list[str] | None = (
        None  # [String!] (IDs of studios to tag files with, or "*" for all)
    )
    tags: list[str] | None = (
        None  # [String!] (IDs of tags to tag files with, or "*" for all)
    )


@strawberry.input
class IdentifyFieldOptionsInput:
    """Input for identify field options from schema/types/metadata.graphql."""

    field: str  # String!
    strategy: IdentifyFieldStrategy  # IdentifyFieldStrategy!
    createMissing: bool | None = (
        None  # Boolean (creates missing objects if needed - only applicable for performers, tags and studios)
    )


@strawberry.input
class IdentifyMetadataOptionsInput:
    """Input for identify metadata options from schema/types/metadata.graphql."""

    fieldOptions: list[IdentifyFieldOptionsInput] | None = (
        None  # [IdentifyFieldOptionsInput!] (any fields missing from here are defaulted to MERGE and createMissing false)
    )
    setCoverImage: bool | None = None  # Boolean (defaults to true if not provided)
    setOrganized: bool | None = None  # Boolean
    includeMalePerformers: bool | None = (
        None  # Boolean (defaults to true if not provided)
    )
    skipMultipleMatches: bool | None = (
        None  # Boolean (defaults to true if not provided)
    )
    skipMultipleMatchTag: str | None = (
        None  # String (tag to tag skipped multiple matches with)
    )
    skipSingleNamePerformers: bool | None = (
        None  # Boolean (defaults to true if not provided)
    )
    skipSingleNamePerformerTag: str | None = (
        None  # String (tag to tag skipped single name performers with)
    )


@strawberry.type
class IdentifyFieldOptions:
    """Identify field options from schema/types/metadata.graphql."""

    field: str  # String!
    strategy: IdentifyFieldStrategy  # IdentifyFieldStrategy!
    createMissing: bool  # Boolean (creates missing objects if needed - only applicable for performers, tags and studios)


@strawberry.type
class IdentifyMetadataOptions:
    """Identify metadata options from schema/types/metadata.graphql."""

    fieldOptions: list[IdentifyFieldOptions] | None = (
        None  # [IdentifyFieldOptions!] (any fields missing from here are defaulted to MERGE and createMissing false)
    )
    setCoverImage: bool | None = None  # Boolean (defaults to true if not provided)
    setOrganized: bool | None = None  # Boolean
    includeMalePerformers: bool | None = (
        None  # Boolean (defaults to true if not provided)
    )
    skipMultipleMatches: bool | None = (
        None  # Boolean (defaults to true if not provided)
    )
    skipMultipleMatchTag: str | None = (
        None  # String (tag to tag skipped multiple matches with)
    )
    skipSingleNamePerformers: bool | None = (
        None  # Boolean (defaults to true if not provided)
    )
    skipSingleNamePerformerTag: str | None = (
        None  # String (tag to tag skipped single name performers with)
    )


@strawberry.input
class ExportObjectTypeInput:
    """Input for export object type from schema/types/metadata.graphql."""

    ids: list[str] | None = None  # [String!]
    all: bool | None = None  # Boolean


@strawberry.input
class ExportObjectsInput:
    """Input for exporting objects from schema/types/metadata.graphql."""

    scenes: ExportObjectTypeInput | None = None  # ExportObjectTypeInput
    images: ExportObjectTypeInput | None = None  # ExportObjectTypeInput
    studios: ExportObjectTypeInput | None = None  # ExportObjectTypeInput
    performers: ExportObjectTypeInput | None = None  # ExportObjectTypeInput
    tags: ExportObjectTypeInput | None = None  # ExportObjectTypeInput
    groups: ExportObjectTypeInput | None = None  # ExportObjectTypeInput
    movies: ExportObjectTypeInput | None = (
        None  # ExportObjectTypeInput @deprecated(reason: "Use groups instead")
    )
    galleries: ExportObjectTypeInput | None = None  # ExportObjectTypeInput
    includeDependencies: bool | None = None  # Boolean


@strawberry.input
class ImportObjectsInput:
    """Input for importing objects from schema/types/metadata.graphql."""

    file: Any  # Upload!
    duplicateBehaviour: ImportDuplicateEnum  # ImportDuplicateEnum!
    missingRefBehaviour: ImportMissingRefEnum  # ImportMissingRefEnum!


@strawberry.input
class BackupDatabaseInput:
    """Input for database backup from schema/types/metadata.graphql."""

    download: bool | None = None  # Boolean


@strawberry.input
class AnonymiseDatabaseInput:
    """Input for database anonymisation from schema/types/metadata.graphql."""

    download: bool | None = None  # Boolean


@strawberry.type
class SystemStatus:
    """System status type from schema/types/metadata.graphql."""

    databaseSchema: int | None = None  # Int
    databasePath: str | None = None  # String
    configPath: str | None = None  # String
    appSchema: int  # Int!
    status: SystemStatusEnum  # SystemStatusEnum!
    os: str  # String!
    workingDir: str  # String!
    homeDir: str  # String!
    ffmpegPath: str | None = None  # String
    ffprobePath: str | None = None  # String


@strawberry.input
class MigrateInput:
    """Input for migration from schema/types/metadata.graphql."""

    backupPath: str  # String!


@strawberry.input
class CustomFieldsInput:
    """Input for custom fields from schema/types/metadata.graphql."""

    full: dict[str, Any] | None = (
        None  # Map (If populated, the entire custom fields map will be replaced with this value)
    )
    partial: dict[str, Any] | None = (
        None  # Map (If populated, only the keys in this map will be updated)
    )
