"""Class to Represent Media Items"""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


@dataclass
class MediaItem:
    """Represents a media item published on Fansly
    eg. a picture or video.
    """

    # Regular media fields
    media_id: int = 0
    metadata: dict[str, Any] | None = None
    mimetype: str | None = None
    created_at: int = 0
    download_url: str | None = None
    file_extension: str | None = None

    # Preview fields
    preview_url: str | None = None
    preview_mimetype: str | None = None
    preview_extension: str | None = None
    is_preview: bool = False

    # Resolution info
    highest_variants_resolution: int = 0
    highest_variants_resolution_height: int = 0
    highest_variants_resolution_url: str | None = None

    # Legacy fields - kept for compatibility
    default_normal_id: int = 0
    default_normal_created_at: int = 0
    default_normal_locations: str | None = None
    default_normal_mimetype: str | None = None
    default_normal_height: int = 0

    def created_at_str(self) -> str:
        # Always use UTC for timestamps to ensure consistent filenames
        dt = datetime.fromtimestamp(self.created_at, tz=timezone.utc)
        return dt.strftime("%Y-%m-%d_at_%H-%M_UTC")

    def get_download_url_file_extension(self) -> str | None:
        if self.download_url:
            return self.download_url.split("/")[-1].split(".")[-1].split("?")[0]
        else:
            return None

    def get_file_name(self, for_preview: bool = False) -> str:
        """Get filename for either regular or preview content.

        Args:
            for_preview: If True, generate filename for preview content

        Returns:
            Filename with appropriate extension and id marker
        """
        id_marker = "preview_id" if for_preview else "id"
        extension = self.preview_extension if for_preview else self.file_extension

        if extension is None:
            if for_preview and self.preview_url:
                extension = self.preview_url.split("/")[-1].split(".")[-1].split("?")[0]
            elif not for_preview and self.download_url:
                extension = (
                    self.download_url.split("/")[-1].split(".")[-1].split("?")[0]
                )

        return f"{self.created_at_str()}_{id_marker}_{self.media_id}.{extension}"
