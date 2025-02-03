"""Configuration types from schema/types/config.graphql."""

from typing import Any, List, Optional

import strawberry
from strawberry import ID, lazy

from .enums import (
    BlobsStorageType,
    HashAlgorithm,
    ImageLightboxDisplayMode,
    ImageLightboxScrollMode,
    PreviewPreset,
    StreamingResolutionEnum,
)
from .metadata import (
    AutoTagMetadataInput,
    AutoTagMetadataOptions,
    GenerateMetadataInput,
    GenerateMetadataOptions,
    ScanMetadataInput,
    ScanMetadataOptions,
)


@strawberry.input
class SetupInput:
    """Input for initial setup."""

    config_location: str  # String!
    stashes: list["StashConfigInput"]  # [StashConfigInput!]!
    database_file: str  # String!
    generated_location: str  # String!
    cache_location: str  # String!
    store_blobs_in_database: bool  # Boolean!
    blobs_location: str  # String!


@strawberry.input
class ConfigGeneralInput:
    """Input for general configuration."""

    stashes: list["StashConfigInput"] | None = None  # [StashConfigInput!]
    database_path: str | None = None  # String
    backup_directory_path: str | None = None  # String
    generated_path: str | None = None  # String
    metadata_path: str | None = None  # String
    cache_path: str | None = None  # String
    blobs_path: str | None = None  # String
    blobs_storage: BlobsStorageType | None = None  # BlobsStorageType
    ffmpeg_path: str | None = None  # String
    ffprobe_path: str | None = None  # String
    calculate_md5: bool | None = None  # Boolean
    video_file_naming_algorithm: HashAlgorithm | None = None  # HashAlgorithm
    parallel_tasks: int | None = None  # Int
    preview_audio: bool | None = None  # Boolean
    preview_segments: int | None = None  # Int
    preview_segment_duration: float | None = None  # Float
    preview_exclude_start: str | None = None  # String
    preview_exclude_end: str | None = None  # String
    preview_preset: PreviewPreset | None = None  # PreviewPreset
    transcode_hardware_acceleration: bool | None = None  # Boolean
    max_transcode_size: StreamingResolutionEnum | None = None  # StreamingResolutionEnum
    max_streaming_transcode_size: StreamingResolutionEnum | None = (
        None  # StreamingResolutionEnum
    )
    transcode_input_args: list[str] | None = None  # [String!]
    transcode_output_args: list[str] | None = None  # [String!]
    live_transcode_input_args: list[str] | None = None  # [String!]
    live_transcode_output_args: list[str] | None = None  # [String!]
    draw_funscript_heatmap_range: bool | None = None  # Boolean
    write_image_thumbnails: bool | None = None  # Boolean
    create_image_clips_from_videos: bool | None = None  # Boolean
    username: str | None = None  # String
    password: str | None = None  # String
    max_session_age: int | None = None  # Int
    log_file: str | None = None  # String
    log_out: bool | None = None  # Boolean
    log_level: str | None = None  # String
    log_access: bool | None = None  # Boolean
    create_galleries_from_folders: bool | None = None  # Boolean
    gallery_cover_regex: str | None = None  # String
    video_extensions: list[str] | None = None  # [String!]
    image_extensions: list[str] | None = None  # [String!]
    gallery_extensions: list[str] | None = None  # [String!]
    excludes: list[str] | None = None  # [String!]
    image_excludes: list[str] | None = None  # [String!]
    custom_performer_image_location: str | None = None  # String


@strawberry.type
class ConfigGeneralResult:
    """Result type for general configuration."""

    stashes: list["StashConfig"]  # [StashConfig!]!
    database_path: str  # String!
    backup_directory_path: str  # String!
    generated_path: str  # String!
    metadata_path: str  # String!
    config_file_path: str  # String!
    cache_path: str  # String!
    blobs_path: str  # String!
    blobs_storage: BlobsStorageType  # BlobsStorageType!
    ffmpeg_path: str  # String!
    ffprobe_path: str  # String!
    calculate_md5: bool  # Boolean!
    video_file_naming_algorithm: HashAlgorithm  # HashAlgorithm!
    parallel_tasks: int  # Int!
    preview_audio: bool  # Boolean!
    preview_segments: int  # Int!
    preview_segment_duration: float  # Float!
    preview_exclude_start: str  # String!
    preview_exclude_end: str  # String!
    preview_preset: PreviewPreset  # PreviewPreset!
    transcode_hardware_acceleration: bool  # Boolean!
    max_transcode_size: StreamingResolutionEnum | None = None  # StreamingResolutionEnum
    max_streaming_transcode_size: StreamingResolutionEnum | None = (
        None  # StreamingResolutionEnum
    )
    transcode_input_args: list[str]  # [String!]!
    transcode_output_args: list[str]  # [String!]!
    live_transcode_input_args: list[str]  # [String!]!
    live_transcode_output_args: list[str]  # [String!]!
    draw_funscript_heatmap_range: bool  # Boolean!
    write_image_thumbnails: bool  # Boolean!
    create_image_clips_from_videos: bool  # Boolean!
    api_key: str  # String!
    username: str  # String!
    password: str  # String!
    max_session_age: int  # Int!
    log_file: str | None = None  # String
    log_out: bool  # Boolean!
    log_level: str  # String!
    log_access: bool  # Boolean!
    video_extensions: list[str]  # [String!]!
    image_extensions: list[str]  # [String!]!
    gallery_extensions: list[str]  # [String!]!
    create_galleries_from_folders: bool  # Boolean!
    gallery_cover_regex: str  # String!
    excludes: list[str]  # [String!]!
    image_excludes: list[str]  # [String!]!
    custom_performer_image_location: str | None = None  # String


@strawberry.input
class ConfigDisableDropdownCreateInput:
    """Input for disabling dropdown create."""

    performer: bool | None = None  # Boolean
    tag: bool | None = None  # Boolean
    studio: bool | None = None  # Boolean
    movie: bool | None = None  # Boolean


@strawberry.input
class ConfigImageLightboxInput:
    """Input for image lightbox configuration."""

    slideshowDelay: int | None = None  # Int
    displayMode: ImageLightboxDisplayMode | None = None  # ImageLightboxDisplayMode
    scaleUp: bool | None = None  # Boolean
    resetZoomOnNav: bool | None = None  # Boolean
    scrollMode: ImageLightboxScrollMode | None = None  # ImageLightboxScrollMode
    scrollAttemptsBeforeChange: int | None = None  # Int


@strawberry.type
class ConfigImageLightboxResult:
    """Result type for image lightbox configuration."""

    slideshowDelay: int | None = None  # Int
    displayMode: ImageLightboxDisplayMode | None = None  # ImageLightboxDisplayMode
    scaleUp: bool | None = None  # Boolean
    resetZoomOnNav: bool | None = None  # Boolean
    scrollMode: ImageLightboxScrollMode | None = None  # ImageLightboxScrollMode
    scrollAttemptsBeforeChange: int  # Int!


@strawberry.input
class ConfigInterfaceInput:
    """Input for interface configuration."""

    menu_items: list[str] | None = None  # [String!]
    sound_on_preview: bool | None = None  # Boolean
    wall_show_title: bool | None = None  # Boolean
    wall_playback: str | None = None  # String
    show_scrubber: bool | None = None  # Boolean
    maximum_loop_duration: int | None = None  # Int
    autostart_video: bool | None = None  # Boolean
    autostart_video_on_play_selected: bool | None = None  # Boolean
    continue_playlist_default: bool | None = None  # Boolean
    show_studio_as_text: bool | None = None  # Boolean
    css: str | None = None  # String
    css_enabled: bool | None = None  # Boolean
    javascript: str | None = None  # String
    javascript_enabled: bool | None = None  # Boolean
    custom_locales: str | None = None  # String
    custom_locales_enabled: bool | None = None  # Boolean
    language: str | None = None  # String
    image_lightbox: ConfigImageLightboxInput | None = None  # ConfigImageLightboxInput
    disable_dropdown_create: ConfigDisableDropdownCreateInput | None = (
        None  # ConfigDisableDropdownCreateInput
    )
    handy_key: str | None = None  # String
    funscript_offset: int | None = None  # Int
    use_stash_hosted_funscript: bool | None = None  # Boolean
    no_browser: bool | None = None  # Boolean
    notifications_enabled: bool | None = None  # Boolean


@strawberry.type
class ConfigDisableDropdownCreate:
    """Result type for disable dropdown create."""

    performer: bool  # Boolean!
    tag: bool  # Boolean!
    studio: bool  # Boolean!
    movie: bool  # Boolean!


@strawberry.type
class ConfigInterfaceResult:
    """Result type for interface configuration."""

    menu_items: list[str] | None = None  # [String!]
    sound_on_preview: bool | None = None  # Boolean
    wall_show_title: bool | None = None  # Boolean
    wall_playback: str | None = None  # String
    show_scrubber: bool | None = None  # Boolean
    maximum_loop_duration: int | None = None  # Int
    no_browser: bool | None = None  # Boolean
    notifications_enabled: bool | None = None  # Boolean
    autostart_video: bool | None = None  # Boolean
    autostart_video_on_play_selected: bool | None = None  # Boolean
    continue_playlist_default: bool | None = None  # Boolean
    show_studio_as_text: bool | None = None  # Boolean
    css: str | None = None  # String
    css_enabled: bool | None = None  # Boolean
    javascript: str | None = None  # String
    javascript_enabled: bool | None = None  # Boolean
    custom_locales: str | None = None  # String
    custom_locales_enabled: bool | None = None  # Boolean
    language: str | None = None  # String
    image_lightbox: ConfigImageLightboxResult  # ConfigImageLightboxResult!
    disable_dropdown_create: ConfigDisableDropdownCreate  # ConfigDisableDropdownCreate!
    handy_key: str | None = None  # String
    funscript_offset: int | None = None  # Int
    use_stash_hosted_funscript: bool | None = None  # Boolean


@strawberry.input
class ConfigDLNAInput:
    """Input for DLNA configuration."""

    server_name: str | None = None  # String
    enabled: bool | None = None  # Boolean
    port: int | None = None  # Int
    whitelisted_ips: list[str] | None = None  # [String!]
    interfaces: list[str] | None = None  # [String!]
    video_sort_order: str | None = None  # String


@strawberry.type
class ConfigDLNAResult:
    """Result type for DLNA configuration."""

    server_name: str  # String!
    enabled: bool  # Boolean!
    port: int  # Int!
    whitelisted_ips: list[str]  # [String!]!
    interfaces: list[str]  # [String!]!
    video_sort_order: str  # String!


@strawberry.type
class ConfigDefaultSettingsResult:
    """Result type for default settings configuration."""

    scan: ScanMetadataOptions  # ScanMetadataOptions
    autoTag: AutoTagMetadataOptions  # AutoTagMetadataOptions
    generate: GenerateMetadataOptions  # GenerateMetadataOptions
    deleteFile: (
        bool  # Boolean (If true, delete file checkbox will be checked by default)
    )
    deleteGenerated: bool  # Boolean (If true, delete generated supporting files checkbox will be checked by default)


@strawberry.input
class ConfigDefaultSettingsInput:
    """Input for default settings configuration."""

    scan: ScanMetadataInput | None = None  # ScanMetadataInput
    autoTag: AutoTagMetadataInput | None = None  # AutoTagMetadataInput
    generate: GenerateMetadataInput | None = None  # GenerateMetadataInput
    deleteFile: bool | None = None  # Boolean
    deleteGenerated: bool | None = None  # Boolean


@strawberry.type
class ConfigResult:
    """Result type for all configuration."""

    general: ConfigGeneralResult  # ConfigGeneralResult!
    interface: ConfigInterfaceResult  # ConfigInterfaceResult!
    dlna: ConfigDLNAResult  # ConfigDLNAResult!
    defaults: ConfigDefaultSettingsResult  # ConfigDefaultSettingsResult!
    ui: dict[str, Any]  # Map!

    @strawberry.field
    def plugins(self, include: list[ID] | None = None) -> dict[str, dict[str, Any]]:
        """Get plugin configuration.

        Args:
            include: Optional list of plugin IDs to include

        Returns:
            Plugin configuration map
        """
        # TODO: Implement plugin filtering
        return {}


@strawberry.type
class Directory:
    """Directory structure of a path."""

    path: str  # String!
    parent: str | None = None  # String
    directories: list[str]  # [String!]!


@strawberry.input
class StashConfigInput:
    """Input for stash configuration."""

    path: str  # String!
    excludeVideo: bool  # Boolean!
    excludeImage: bool  # Boolean!


@strawberry.type
class StashConfig:
    """Result type for stash configuration."""

    path: str  # String!
    excludeVideo: bool  # Boolean!
    excludeImage: bool  # Boolean!


@strawberry.input
class GenerateAPIKeyInput:
    """Input for generating API key."""

    clear: bool | None = None  # Boolean
