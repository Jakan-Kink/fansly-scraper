"""Test metadata types from stash/types/metadata.py."""

import types
from datetime import datetime
from typing import get_args, get_origin, get_type_hints

import pytest
from strawberry import ID

from stash.types.enums import (
    IdentifyFieldStrategy,
    ImportDuplicateEnum,
    ImportMissingRefEnum,
    PreviewPreset,
    SystemStatusEnum,
)
from stash.types.metadata import (  # Input types; Output types
    AnonymiseDatabaseInput,
    AutoTagMetadataInput,
    AutoTagMetadataOptions,
    BackupDatabaseInput,
    CleanGeneratedInput,
    CleanMetadataInput,
    CustomFieldsInput,
    ExportObjectsInput,
    ExportObjectTypeInput,
    GenerateMetadataInput,
    GenerateMetadataOptions,
    GeneratePreviewOptions,
    GeneratePreviewOptionsInput,
    IdentifyFieldOptions,
    IdentifyFieldOptionsInput,
    IdentifyMetadataOptions,
    IdentifyMetadataOptionsInput,
    ImportObjectsInput,
    MigrateInput,
    ScanMetaDataFilterInput,
    ScanMetadataInput,
    ScanMetadataOptions,
    SystemStatus,
)


@pytest.mark.unit
class TestMetadataInputTypes:
    """Test metadata input type decorations and field types."""

    def test_generate_preview_options_input_decoration(self):
        """Test GeneratePreviewOptionsInput has input decoration."""
        assert hasattr(GeneratePreviewOptionsInput, "__strawberry_definition__")
        assert GeneratePreviewOptionsInput.__strawberry_definition__.is_input

    def test_generate_preview_options_input_fields(self):
        """Test GeneratePreviewOptionsInput field types."""
        hints = get_type_hints(GeneratePreviewOptionsInput)

        # All fields should be optional
        assert hints["previewSegments"] == int | None
        assert hints["previewSegmentDuration"] == float | None
        assert hints["previewExcludeStart"] == str | None
        assert hints["previewExcludeEnd"] == str | None
        assert hints["previewPreset"] == PreviewPreset | None

    def test_generate_metadata_input_decoration(self):
        """Test GenerateMetadataInput has input decoration."""
        assert hasattr(GenerateMetadataInput, "__strawberry_definition__")
        assert GenerateMetadataInput.__strawberry_definition__.is_input

    def test_generate_metadata_input_fields(self):
        """Test GenerateMetadataInput field types."""
        hints = get_type_hints(GenerateMetadataInput)

        # Boolean flags
        assert hints["covers"] == bool
        assert hints["sprites"] == bool
        assert hints["previews"] == bool
        assert hints["imagePreviews"] == bool
        assert hints["markers"] == bool
        assert hints["markerImagePreviews"] == bool
        assert hints["markerScreenshots"] == bool
        assert hints["transcodes"] == bool
        assert hints["forceTranscodes"] == bool
        assert hints["phashes"] == bool
        assert hints["interactiveHeatmapsSpeeds"] == bool
        assert hints["imageThumbnails"] == bool
        assert hints["clipPreviews"] == bool
        assert hints["overwrite"] == bool

        # Optional fields
        assert hints["previewOptions"] == GeneratePreviewOptionsInput | None

        # List fields

        # Handle both old and new union type representations
        sceneIDs_origin = get_origin(hints["sceneIDs"])
        markerIDs_origin = get_origin(hints["markerIDs"])

        # Check if it's either the new UnionType or the old union
        assert sceneIDs_origin == types.UnionType or sceneIDs_origin == list | type(
            None
        )
        assert markerIDs_origin == types.UnionType or markerIDs_origin == list | type(
            None
        )

    def test_scan_metadata_filter_input_decoration(self):
        """Test ScanMetaDataFilterInput has input decoration."""
        assert hasattr(ScanMetaDataFilterInput, "__strawberry_definition__")
        assert ScanMetaDataFilterInput.__strawberry_definition__.is_input

    def test_scan_metadata_input_decoration(self):
        """Test ScanMetadataInput has input decoration."""
        assert hasattr(ScanMetadataInput, "__strawberry_definition__")
        assert ScanMetadataInput.__strawberry_definition__.is_input

    def test_scan_metadata_input_fields(self):
        """Test ScanMetadataInput field types."""
        hints = get_type_hints(ScanMetadataInput)

        # Required field
        assert get_origin(hints["paths"]) == list
        assert get_args(hints["paths"])[0] == str

        # Optional boolean fields
        assert hints["rescan"] == bool | None
        assert hints["scanGenerateCovers"] == bool | None
        assert hints["scanGeneratePreviews"] == bool | None
        assert hints["scanGenerateImagePreviews"] == bool | None
        assert hints["scanGenerateSprites"] == bool | None
        assert hints["scanGeneratePhashes"] == bool | None
        assert hints["scanGenerateThumbnails"] == bool | None
        assert hints["scanGenerateClipPreviews"] == bool | None

        # Filter field
        assert hints["filter"] == ScanMetaDataFilterInput | None

    def test_clean_metadata_input_decoration(self):
        """Test CleanMetadataInput has input decoration."""
        assert hasattr(CleanMetadataInput, "__strawberry_definition__")
        assert CleanMetadataInput.__strawberry_definition__.is_input

    def test_clean_generated_input_decoration(self):
        """Test CleanGeneratedInput has input decoration."""
        assert hasattr(CleanGeneratedInput, "__strawberry_definition__")
        assert CleanGeneratedInput.__strawberry_definition__.is_input

    def test_auto_tag_metadata_input_decoration(self):
        """Test AutoTagMetadataInput has input decoration."""
        assert hasattr(AutoTagMetadataInput, "__strawberry_definition__")
        assert AutoTagMetadataInput.__strawberry_definition__.is_input

    def test_identify_field_options_input_decoration(self):
        """Test IdentifyFieldOptionsInput has input decoration."""
        assert hasattr(IdentifyFieldOptionsInput, "__strawberry_definition__")
        assert IdentifyFieldOptionsInput.__strawberry_definition__.is_input

    def test_identify_field_options_input_fields(self):
        """Test IdentifyFieldOptionsInput field types."""
        hints = get_type_hints(IdentifyFieldOptionsInput)

        assert hints["field"] == str
        assert hints["strategy"] == IdentifyFieldStrategy
        assert hints["createMissing"] == bool | None

    def test_identify_metadata_options_input_decoration(self):
        """Test IdentifyMetadataOptionsInput has input decoration."""
        assert hasattr(IdentifyMetadataOptionsInput, "__strawberry_definition__")
        assert IdentifyMetadataOptionsInput.__strawberry_definition__.is_input

    def test_export_object_type_input_decoration(self):
        """Test ExportObjectTypeInput has input decoration."""
        assert hasattr(ExportObjectTypeInput, "__strawberry_definition__")
        assert ExportObjectTypeInput.__strawberry_definition__.is_input

    def test_export_objects_input_decoration(self):
        """Test ExportObjectsInput has input decoration."""
        assert hasattr(ExportObjectsInput, "__strawberry_definition__")
        assert ExportObjectsInput.__strawberry_definition__.is_input

    def test_import_objects_input_decoration(self):
        """Test ImportObjectsInput has input decoration."""
        assert hasattr(ImportObjectsInput, "__strawberry_definition__")
        assert ImportObjectsInput.__strawberry_definition__.is_input

    def test_import_objects_input_fields(self):
        """Test ImportObjectsInput field types."""
        hints = get_type_hints(ImportObjectsInput)

        assert hints["duplicateBehaviour"] == ImportDuplicateEnum
        assert hints["missingRefBehaviour"] == ImportMissingRefEnum

    def test_backup_database_input_decoration(self):
        """Test BackupDatabaseInput has input decoration."""
        assert hasattr(BackupDatabaseInput, "__strawberry_definition__")
        assert BackupDatabaseInput.__strawberry_definition__.is_input

    def test_anonymise_database_input_decoration(self):
        """Test AnonymiseDatabaseInput has input decoration."""
        assert hasattr(AnonymiseDatabaseInput, "__strawberry_definition__")
        assert AnonymiseDatabaseInput.__strawberry_definition__.is_input

    def test_migrate_input_decoration(self):
        """Test MigrateInput has input decoration."""
        assert hasattr(MigrateInput, "__strawberry_definition__")
        assert MigrateInput.__strawberry_definition__.is_input

    def test_custom_fields_input_decoration(self):
        """Test CustomFieldsInput has input decoration."""
        assert hasattr(CustomFieldsInput, "__strawberry_definition__")
        assert CustomFieldsInput.__strawberry_definition__.is_input


@pytest.mark.unit
class TestMetadataOutputTypes:
    """Test metadata output type decorations and field types."""

    def test_generate_preview_options_decoration(self):
        """Test GeneratePreviewOptions has type decoration."""
        assert hasattr(GeneratePreviewOptions, "__strawberry_definition__")
        assert not GeneratePreviewOptions.__strawberry_definition__.is_input

    def test_generate_preview_options_fields(self):
        """Test GeneratePreviewOptions field types."""
        hints = get_type_hints(GeneratePreviewOptions)

        # All fields should be optional
        assert hints["previewSegments"] == int | None
        assert hints["previewSegmentDuration"] == float | None
        assert hints["previewExcludeStart"] == str | None
        assert hints["previewExcludeEnd"] == str | None
        assert hints["previewPreset"] == PreviewPreset | None

    def test_generate_metadata_options_decoration(self):
        """Test GenerateMetadataOptions has type decoration."""
        assert hasattr(GenerateMetadataOptions, "__strawberry_definition__")
        assert not GenerateMetadataOptions.__strawberry_definition__.is_input

    def test_scan_metadata_options_decoration(self):
        """Test ScanMetadataOptions has type decoration."""
        assert hasattr(ScanMetadataOptions, "__strawberry_definition__")
        assert not ScanMetadataOptions.__strawberry_definition__.is_input

    def test_scan_metadata_options_fields(self):
        """Test ScanMetadataOptions field types."""
        hints = get_type_hints(ScanMetadataOptions)

        # All fields should be required bool
        assert hints["rescan"] == bool
        assert hints["scanGenerateCovers"] == bool
        assert hints["scanGeneratePreviews"] == bool
        assert hints["scanGenerateImagePreviews"] == bool
        assert hints["scanGenerateSprites"] == bool
        assert hints["scanGeneratePhashes"] == bool
        assert hints["scanGenerateThumbnails"] == bool
        assert hints["scanGenerateClipPreviews"] == bool

    def test_auto_tag_metadata_options_decoration(self):
        """Test AutoTagMetadataOptions has type decoration."""
        assert hasattr(AutoTagMetadataOptions, "__strawberry_definition__")
        assert not AutoTagMetadataOptions.__strawberry_definition__.is_input

    def test_identify_field_options_decoration(self):
        """Test IdentifyFieldOptions has type decoration."""
        assert hasattr(IdentifyFieldOptions, "__strawberry_definition__")
        assert not IdentifyFieldOptions.__strawberry_definition__.is_input

    def test_identify_field_options_fields(self):
        """Test IdentifyFieldOptions field types."""
        hints = get_type_hints(IdentifyFieldOptions)

        assert hints["field"] == str
        assert hints["strategy"] == IdentifyFieldStrategy
        assert hints["createMissing"] == bool

    def test_identify_metadata_options_decoration(self):
        """Test IdentifyMetadataOptions has type decoration."""
        assert hasattr(IdentifyMetadataOptions, "__strawberry_definition__")
        assert not IdentifyMetadataOptions.__strawberry_definition__.is_input

    def test_system_status_decoration(self):
        """Test SystemStatus has type decoration."""
        assert hasattr(SystemStatus, "__strawberry_definition__")
        assert not SystemStatus.__strawberry_definition__.is_input

    def test_system_status_fields(self):
        """Test SystemStatus field types."""
        hints = get_type_hints(SystemStatus)

        # Optional fields
        assert hints["databaseSchema"] == int | None
        assert hints["databasePath"] == str | None
        assert hints["configPath"] == str | None
        assert hints["ffmpegPath"] == str | None
        assert hints["ffprobePath"] == str | None

        # Required fields
        assert hints["appSchema"] == int
        assert hints["status"] == SystemStatusEnum
        assert hints["os"] == str
        assert hints["workingDir"] == str
        assert hints["homeDir"] == str


@pytest.mark.unit
class TestMetadataInstantiation:
    """Test metadata type instantiation."""

    def test_generate_preview_options_input_instantiation(self):
        """Test GeneratePreviewOptionsInput can be instantiated."""
        options = GeneratePreviewOptionsInput()
        assert options.previewSegments is None
        assert options.previewSegmentDuration is None
        assert options.previewExcludeStart is None
        assert options.previewExcludeEnd is None
        assert options.previewPreset is None

        # Test with values
        options = GeneratePreviewOptionsInput(
            previewSegments=10,
            previewSegmentDuration=5.0,
            previewExcludeStart="00:00:10",
            previewExcludeEnd="00:00:10",
            previewPreset=PreviewPreset.ULTRAFAST,
        )
        assert options.previewSegments == 10
        assert options.previewSegmentDuration == 5.0
        assert options.previewExcludeStart == "00:00:10"
        assert options.previewExcludeEnd == "00:00:10"
        assert options.previewPreset == PreviewPreset.ULTRAFAST

    def test_generate_metadata_input_instantiation(self):
        """Test GenerateMetadataInput can be instantiated."""
        metadata = GenerateMetadataInput()
        assert metadata.covers is False
        assert metadata.sprites is False
        assert metadata.previews is False
        assert metadata.imagePreviews is False
        assert metadata.markers is False
        assert metadata.markerImagePreviews is False
        assert metadata.markerScreenshots is False
        assert metadata.transcodes is False
        assert metadata.forceTranscodes is False
        assert metadata.phashes is False
        assert metadata.interactiveHeatmapsSpeeds is False
        assert metadata.imageThumbnails is False
        assert metadata.clipPreviews is False
        assert metadata.overwrite is False
        assert metadata.previewOptions is None
        assert metadata.sceneIDs is None
        assert metadata.markerIDs is None

    def test_scan_metadata_filter_input_instantiation(self):
        """Test ScanMetaDataFilterInput can be instantiated."""
        filter_input = ScanMetaDataFilterInput()
        assert filter_input.minModTime is None

        # Test with datetime
        now = datetime.now()
        filter_input = ScanMetaDataFilterInput(minModTime=now)
        assert filter_input.minModTime == now

    def test_scan_metadata_input_instantiation(self):
        """Test ScanMetadataInput can be instantiated."""
        scan_input = ScanMetadataInput(paths=["/path/to/content"])
        assert scan_input.paths == ["/path/to/content"]
        assert scan_input.rescan is None
        assert scan_input.scanGenerateCovers is None
        assert scan_input.filter is None

    def test_clean_metadata_input_instantiation(self):
        """Test CleanMetadataInput can be instantiated."""
        clean_input = CleanMetadataInput(paths=["/path/to/clean"], dryRun=True)
        assert clean_input.paths == ["/path/to/clean"]
        assert clean_input.dryRun is True

    def test_clean_generated_input_instantiation(self):
        """Test CleanGeneratedInput can be instantiated."""
        clean_input = CleanGeneratedInput(dryRun=False)
        assert clean_input.dryRun is False
        assert clean_input.blobFiles is None
        assert clean_input.sprites is None
        assert clean_input.screenshots is None
        assert clean_input.transcodes is None
        assert clean_input.markers is None
        assert clean_input.imageThumbnails is None

    def test_auto_tag_metadata_input_instantiation(self):
        """Test AutoTagMetadataInput can be instantiated."""
        auto_tag = AutoTagMetadataInput()
        assert auto_tag.paths is None
        assert auto_tag.performers is None
        assert auto_tag.studios is None
        assert auto_tag.tags is None

        # Test with values
        auto_tag = AutoTagMetadataInput(
            paths=["/content"],
            performers=["*"],
            studios=["studio-1"],
            tags=["tag-1", "tag-2"],
        )
        assert auto_tag.paths == ["/content"]
        assert auto_tag.performers == ["*"]
        assert auto_tag.studios == ["studio-1"]
        assert auto_tag.tags == ["tag-1", "tag-2"]

    def test_identify_field_options_input_instantiation(self):
        """Test IdentifyFieldOptionsInput can be instantiated."""
        field_options = IdentifyFieldOptionsInput(
            field="title", strategy=IdentifyFieldStrategy.MERGE
        )
        assert field_options.field == "title"
        assert field_options.strategy == IdentifyFieldStrategy.MERGE
        assert field_options.createMissing is None

    def test_system_status_instantiation(self):
        """Test SystemStatus can be instantiated."""
        status = SystemStatus(
            appSchema=1,
            status=SystemStatusEnum.OK,
            os="linux",
            workingDir="/app",
            homeDir="/home/user",
        )
        assert status.appSchema == 1
        assert status.status == SystemStatusEnum.OK
        assert status.os == "linux"
        assert status.workingDir == "/app"
        assert status.homeDir == "/home/user"
        assert status.databaseSchema is None
        assert status.databasePath is None
        assert status.configPath is None
        assert status.ffmpegPath is None
        assert status.ffprobePath is None


@pytest.mark.unit
class TestMetadataWorkflows:
    """Test metadata workflow scenarios."""

    def test_complete_scan_workflow(self):
        """Test complete scan metadata workflow."""
        # Create filter for recent files only
        filter_input = ScanMetaDataFilterInput(minModTime=datetime(2024, 1, 1))

        # Create comprehensive scan input
        scan_input = ScanMetadataInput(
            paths=["/content/videos", "/content/images"],
            rescan=False,
            scanGenerateCovers=True,
            scanGeneratePreviews=True,
            scanGenerateImagePreviews=True,
            scanGenerateSprites=True,
            scanGeneratePhashes=True,
            scanGenerateThumbnails=True,
            scanGenerateClipPreviews=True,
            filter=filter_input,
        )

        assert len(scan_input.paths) == 2
        assert scan_input.rescan is False
        assert scan_input.scanGenerateCovers is True
        assert scan_input.filter is not None
        assert scan_input.filter.minModTime is not None
        assert scan_input.filter.minModTime.year == 2024

    def test_generate_metadata_workflow(self):
        """Test generate metadata workflow."""
        # Create preview options
        preview_options = GeneratePreviewOptionsInput(
            previewSegments=20,
            previewSegmentDuration=3.0,
            previewExcludeStart="00:00:30",
            previewExcludeEnd="00:00:30",
            previewPreset=PreviewPreset.SLOW,
        )

        # Create metadata generation input
        metadata_input = GenerateMetadataInput(
            covers=True,
            sprites=True,
            previews=True,
            imagePreviews=True,
            previewOptions=preview_options,
            markers=True,
            markerImagePreviews=True,
            markerScreenshots=True,
            transcodes=False,
            forceTranscodes=False,
            phashes=True,
            interactiveHeatmapsSpeeds=True,
            imageThumbnails=True,
            clipPreviews=True,
            sceneIDs=[ID("scene-1"), ID("scene-2")],
            overwrite=False,
        )

        assert metadata_input.covers is True
        assert metadata_input.previewOptions is not None
        assert metadata_input.previewOptions.previewSegments == 20
        assert metadata_input.previewOptions.previewPreset == PreviewPreset.SLOW
        assert metadata_input.sceneIDs is not None
        assert len(metadata_input.sceneIDs) == 2

    def test_identify_metadata_workflow(self):
        """Test identify metadata workflow."""
        # Create field options for different fields
        title_options = IdentifyFieldOptionsInput(
            field="title", strategy=IdentifyFieldStrategy.MERGE, createMissing=False
        )

        performer_options = IdentifyFieldOptionsInput(
            field="performers",
            strategy=IdentifyFieldStrategy.OVERWRITE,
            createMissing=True,
        )

        # Create identify options
        identify_options = IdentifyMetadataOptionsInput(
            fieldOptions=[title_options, performer_options],
            setCoverImage=True,
            setOrganized=True,
            includeMalePerformers=True,
            skipMultipleMatches=False,
            skipMultipleMatchTag="multiple-matches",
            skipSingleNamePerformers=True,
            skipSingleNamePerformerTag="single-name",
        )

        assert identify_options.fieldOptions is not None
        assert len(identify_options.fieldOptions) == 2
        assert identify_options.fieldOptions[0].field == "title"
        assert (
            identify_options.fieldOptions[1].strategy == IdentifyFieldStrategy.OVERWRITE
        )
        assert identify_options.setCoverImage is True
        assert identify_options.skipMultipleMatchTag == "multiple-matches"

    def test_export_import_workflow(self):
        """Test export/import workflow."""
        # Create export input for specific objects
        scenes_export = ExportObjectTypeInput(
            ids=["scene-1", "scene-2", "scene-3"], all=False
        )

        performers_export = ExportObjectTypeInput(all=True)

        export_input = ExportObjectsInput(
            scenes=scenes_export,
            performers=performers_export,
            studios=None,
            tags=None,
            groups=None,
            galleries=None,
            includeDependencies=True,
        )

        assert export_input.scenes is not None
        assert export_input.scenes.all is False
        assert export_input.scenes.ids is not None
        assert len(export_input.scenes.ids) == 3
        assert export_input.performers is not None
        assert export_input.performers.all is True
        assert export_input.includeDependencies is True

        # Create import input
        import_input = ImportObjectsInput(
            file="dummy_file",  # Would be actual upload in real usage
            duplicateBehaviour=ImportDuplicateEnum.IGNORE,
            missingRefBehaviour=ImportMissingRefEnum.FAIL,
        )

        assert import_input.duplicateBehaviour == ImportDuplicateEnum.IGNORE
        assert import_input.missingRefBehaviour == ImportMissingRefEnum.FAIL

    def test_maintenance_workflow(self):
        """Test maintenance workflow with clean and backup."""
        # Clean metadata for specific paths
        clean_input = CleanMetadataInput(
            paths=["/old/content", "/removed/content"],
            dryRun=True,  # Safe dry run first
        )

        # Clean generated files
        clean_generated = CleanGeneratedInput(
            blobFiles=True,
            sprites=True,
            screenshots=True,
            transcodes=False,  # Keep transcodes
            markers=True,
            imageThumbnails=True,
            dryRun=False,  # Actually clean
        )

        # Backup database
        backup_input = BackupDatabaseInput(download=True)

        # Anonymise database
        anonymise_input = AnonymiseDatabaseInput(download=False)

        assert clean_input.dryRun is True
        assert clean_generated.dryRun is False
        assert clean_generated.transcodes is False  # Preserved
        assert backup_input.download is True
        assert anonymise_input.download is False

    def test_system_status_scenarios(self):
        """Test different system status scenarios."""
        # Healthy system
        healthy_status = SystemStatus(
            databaseSchema=20,
            databasePath="/app/data/stash.db",
            configPath="/app/config/config.yml",
            appSchema=20,
            status=SystemStatusEnum.OK,
            os="linux",
            workingDir="/app",
            homeDir="/home/stash",
            ffmpegPath="/usr/bin/ffmpeg",
            ffprobePath="/usr/bin/ffprobe",
        )

        # System needing migration
        migration_status = SystemStatus(
            databaseSchema=18,
            appSchema=20,
            status=SystemStatusEnum.NEEDS_MIGRATION,
            os="windows",
            workingDir="C:\\stash",
            homeDir="C:\\Users\\stash",
        )

        # Setup needed
        setup_status = SystemStatus(
            appSchema=20,
            status=SystemStatusEnum.SETUP,
            os="darwin",
            workingDir="/Applications/Stash",
            homeDir="/Users/stash",
        )

        assert healthy_status.status == SystemStatusEnum.OK
        assert healthy_status.databaseSchema is not None
        assert healthy_status.databaseSchema == healthy_status.appSchema
        assert migration_status.status == SystemStatusEnum.NEEDS_MIGRATION
        assert migration_status.databaseSchema is not None
        assert migration_status.databaseSchema < migration_status.appSchema
        assert setup_status.status == SystemStatusEnum.SETUP
        assert setup_status.databaseSchema is None
