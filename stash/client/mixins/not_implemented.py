"""Mixin for not implemented methods."""

from typing import Any

from ...types import (
    DLNAStatus,
    Gallery,
    Image,
    JobStatusUpdate,
    LatestVersion,
    Performer,
    Scene,
    SQLExecResult,
    SQLQueryResult,
    StashBoxValidationResult,
    Tag,
    Version,
)


class NotImplementedClientMixin:
    """Mixin for methods that are not yet implemented."""

    # File Operations
    async def move_files(self, input_data: dict[str, Any]) -> bool:
        """Move files to a new location."""
        raise NotImplementedError("File operations not implemented")

    async def delete_files(self, ids: list[str]) -> bool:
        """Delete files by ID."""
        raise NotImplementedError("File operations not implemented")

    async def file_set_fingerprints(self, input_data: dict[str, Any]) -> bool:
        """Set file fingerprints."""
        raise NotImplementedError("File operations not implemented")

    # Configuration
    async def configure_general(self, input_data: dict[str, Any]) -> dict[str, Any]:
        """Configure general settings."""
        raise NotImplementedError("Configuration not implemented")

    async def configure_interface(self, input_data: dict[str, Any]) -> dict[str, Any]:
        """Configure interface settings."""
        raise NotImplementedError("Configuration not implemented")

    async def configure_dlna(self, input_data: dict[str, Any]) -> dict[str, Any]:
        """Configure DLNA settings."""
        raise NotImplementedError("Configuration not implemented")

    async def configure_scraping(self, input_data: dict[str, Any]) -> dict[str, Any]:
        """Configure scraping settings."""
        raise NotImplementedError("Configuration not implemented")

    async def configure_defaults(self, input_data: dict[str, Any]) -> dict[str, Any]:
        """Configure default settings."""
        raise NotImplementedError("Configuration not implemented")

    async def configure_plugin(
        self, plugin_id: str, input_data: dict[str, Any]
    ) -> dict[str, Any]:
        """Configure plugin settings."""
        raise NotImplementedError("Configuration not implemented")

    async def configure_ui(
        self,
        input_data: dict[str, Any] | None = None,
        partial: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Configure UI settings."""
        raise NotImplementedError("Configuration not implemented")

    async def configure_ui_setting(self, key: str, value: Any) -> dict[str, Any]:
        """Configure a single UI setting."""
        raise NotImplementedError("Configuration not implemented")

    async def generate_api_key(self, input_data: dict[str, Any]) -> str:
        """Generate a new API key."""
        raise NotImplementedError("Configuration not implemented")

    # Import/Export
    async def export_objects(self, input_data: dict[str, Any]) -> str:
        """Export objects to a file."""
        raise NotImplementedError("Import/Export not implemented")

    async def import_objects(self, input_data: dict[str, Any]) -> str:
        """Import objects from a file."""
        raise NotImplementedError("Import/Export not implemented")

    async def metadata_import(self) -> str:
        """Import metadata."""
        raise NotImplementedError("Import/Export not implemented")

    async def metadata_export(self) -> str:
        """Export metadata."""
        raise NotImplementedError("Import/Export not implemented")

    async def metadata_auto_tag(self, input_data: dict[str, Any]) -> str:
        """Auto-tag metadata."""
        raise NotImplementedError("Import/Export not implemented")

    async def metadata_identify(self, input_data: dict[str, Any]) -> str:
        """Identify metadata."""
        raise NotImplementedError("Import/Export not implemented")

    async def metadata_clean(self, input_data: dict[str, Any]) -> str:
        """Clean metadata."""
        raise NotImplementedError("Import/Export not implemented")

    async def metadata_clean_generated(self, input_data: dict[str, Any]) -> str:
        """Clean generated files."""
        raise NotImplementedError("Import/Export not implemented")

    # Queries
    async def find_scene_by_hash(self, input_data: dict[str, Any]) -> Scene | None:
        """Find a scene by its hash."""
        raise NotImplementedError("Scene hash lookup not implemented")

    async def scene_streams(self, id: str) -> list[dict[str, Any]]:
        """Get scene stream endpoints."""
        raise NotImplementedError("Scene streams not implemented")

    async def marker_wall(self, q: str | None = None) -> list[dict[str, Any]]:
        """Get marker wall."""
        raise NotImplementedError("Marker wall not implemented")

    async def marker_strings(
        self, q: str | None = None, sort: str | None = None
    ) -> list[dict[str, Any]]:
        """Get marker strings."""
        raise NotImplementedError("Marker strings not implemented")

    async def stats(self) -> dict[str, Any]:
        """Get system stats."""
        raise NotImplementedError("Stats not implemented")

    async def logs(self) -> list[dict[str, Any]]:
        """Get system logs."""
        raise NotImplementedError("Logs not implemented")

    # Scene Mutations
    async def scene_destroy(self, input_data: dict[str, Any]) -> bool:
        """Delete a scene."""
        raise NotImplementedError("Scene deletion not implemented")

    async def scene_merge(self, input_data: dict[str, Any]) -> Scene:
        """Merge scenes."""
        raise NotImplementedError("Scene merging not implemented")

    async def scenes_destroy(self, ids: list[str]) -> bool:
        """Delete multiple scenes."""
        raise NotImplementedError("Scene deletion not implemented")

    async def scene_add_o(
        self, id: str, times: list[str] | None = None
    ) -> dict[str, Any]:
        """Add O-count to scene."""
        raise NotImplementedError("Scene O-count not implemented")

    async def scene_delete_o(
        self, id: str, times: list[str] | None = None
    ) -> dict[str, Any]:
        """Delete O-count from scene."""
        raise NotImplementedError("Scene O-count not implemented")

    async def scene_reset_o(self, id: str) -> int:
        """Reset scene O-count."""
        raise NotImplementedError("Scene O-count not implemented")

    async def scene_save_activity(
        self,
        id: str,
        resume_time: float | None = None,
        play_duration: float | None = None,
    ) -> bool:
        """Save scene activity."""
        raise NotImplementedError("Scene activity not implemented")

    async def scene_reset_activity(
        self, id: str, reset_resume: bool = False, reset_duration: bool = False
    ) -> bool:
        """Reset scene activity."""
        raise NotImplementedError("Scene activity not implemented")

    async def scene_add_play(
        self, id: str, times: list[str] | None = None
    ) -> dict[str, Any]:
        """Add play count to scene."""
        raise NotImplementedError("Scene play count not implemented")

    async def scene_delete_play(
        self, id: str, times: list[str] | None = None
    ) -> dict[str, Any]:
        """Delete play count from scene."""
        raise NotImplementedError("Scene play count not implemented")

    async def scene_reset_play_count(self, id: str) -> int:
        """Reset scene play count."""
        raise NotImplementedError("Scene play count not implemented")

    # Scene Marker Mutations
    async def scene_marker_create(self, input_data: dict[str, Any]) -> dict[str, Any]:
        """Create a scene marker."""
        raise NotImplementedError("Scene markers not implemented")

    async def scene_marker_update(self, input_data: dict[str, Any]) -> dict[str, Any]:
        """Update a scene marker."""
        raise NotImplementedError("Scene markers not implemented")

    async def scene_marker_destroy(self, id: str) -> bool:
        """Delete a scene marker."""
        raise NotImplementedError("Scene markers not implemented")

    async def scene_markers_destroy(self, ids: list[str]) -> bool:
        """Delete multiple scene markers."""
        raise NotImplementedError("Scene markers not implemented")

    async def scene_assign_file(self, input_data: dict[str, Any]) -> bool:
        """Assign a file to a scene."""
        raise NotImplementedError("Scene file assignment not implemented")

    # Image Mutations
    async def bulk_image_update(self, input_data: dict[str, Any]) -> list[Image]:
        """Update multiple images."""
        raise NotImplementedError("Bulk image update not implemented")

    async def images_destroy(self, input_data: dict[str, Any]) -> bool:
        """Delete multiple images."""
        raise NotImplementedError("Image deletion not implemented")

    async def images_update(self, input_data: list[dict[str, Any]]) -> list[Image]:
        """Update multiple images."""
        raise NotImplementedError("Image updates not implemented")

    async def image_increment_o(self, id: str) -> int:
        """Increment image O-count."""
        raise NotImplementedError("Image O-count not implemented")

    async def image_decrement_o(self, id: str) -> int:
        """Decrement image O-count."""
        raise NotImplementedError("Image O-count not implemented")

    async def image_reset_o(self, id: str) -> int:
        """Reset image O-count."""
        raise NotImplementedError("Image O-count not implemented")

    # Gallery Mutations
    async def bulk_gallery_update(self, input_data: dict[str, Any]) -> list[Gallery]:
        """Update multiple galleries."""
        raise NotImplementedError("Bulk gallery update not implemented")

    # Performer Mutations
    async def performer_destroy(self, input_data: dict[str, Any]) -> bool:
        """Delete a performer."""
        raise NotImplementedError("Performer deletion not implemented")

    async def performers_destroy(self, ids: list[str]) -> bool:
        """Delete multiple performers."""
        raise NotImplementedError("Performer deletion not implemented")

    async def bulk_performer_update(
        self, input_data: dict[str, Any]
    ) -> list[Performer]:
        """Update multiple performers."""
        raise NotImplementedError("Bulk performer update not implemented")

    # Studio Mutations
    async def studio_destroy(self, input_data: dict[str, Any]) -> bool:
        """Delete a studio."""
        raise NotImplementedError("Studio deletion not implemented")

    async def studios_destroy(self, ids: list[str]) -> bool:
        """Delete multiple studios."""
        raise NotImplementedError("Studio deletion not implemented")

    # Tag Mutations
    async def tag_destroy(self, input_data: dict[str, Any]) -> bool:
        """Delete a tag."""
        raise NotImplementedError("Tag deletion not implemented")

    async def tags_destroy(self, ids: list[str]) -> bool:
        """Delete multiple tags."""
        raise NotImplementedError("Tag deletion not implemented")

    # SQL Operations
    async def querySQL(self, sql: str, args: list[Any] | None = None) -> SQLQueryResult:
        """Execute SQL query."""
        raise NotImplementedError("SQL queries not implemented")

    async def execSQL(self, sql: str, args: list[Any] | None = None) -> SQLExecResult:
        """Execute SQL statement."""
        raise NotImplementedError("SQL execution not implemented")

    # Stash-box Operations
    async def validateStashBoxCredentials(
        self, input_data: dict[str, Any]
    ) -> StashBoxValidationResult:
        """Validate stash-box credentials."""
        raise NotImplementedError("Stash-box validation not implemented")

    # DLNA Operations
    async def dlnaStatus(self) -> DLNAStatus:
        """Get DLNA status."""
        raise NotImplementedError("DLNA not implemented")

    # Job Operations
    async def jobsSubscribe(self) -> JobStatusUpdate:
        """Subscribe to job updates."""
        raise NotImplementedError("Job subscription not implemented")

    # Version Operations
    async def version(self) -> Version:
        """Get version information."""
        raise NotImplementedError("Version info not implemented")

    async def latestversion(self) -> LatestVersion:
        """Get latest version information."""
        raise NotImplementedError("Latest version info not implemented")
