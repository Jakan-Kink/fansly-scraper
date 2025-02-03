from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Protocol

from .stash_interface import StashInterface
from .types import BaseFileProtocol, ImageFileProtocol, VideoFileProtocol


class FileType(Enum):
    """Enum representing the type of file."""

    VIDEO = "VideoFile"
    IMAGE = "ImageFile"


@dataclass
class BaseFile(BaseFileProtocol):
    """Base class for all file types in Stash."""

    id: str
    path: str
    basename: str
    parent_folder_id: str
    mod_time: datetime = field(default_factory=datetime.now)
    size: int = 0
    zip_file_id: str | None = None
    fingerprints: list[str] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)

    # Define input field configurations
    _input_fields = {
        # Field name: (attribute name, default value, transform function, required)
        "path": ("path", None, None, True),  # Required field
        "basename": ("basename", None, None, True),  # Required field
        "parent_folder_id": ("parent_folder_id", None, None, True),  # Required field
        "mod_time": ("mod_time", None, lambda x: x.isoformat() if x else None, False),
        "size": ("size", 0, None, False),
        "zip_file_id": ("zip_file_id", None, None, False),
        "fingerprints": ("fingerprints", [], None, False),
    }

    def get_path(self) -> str:
        """Get the full path of the file."""
        return str(Path(self.path) / self.basename)

    def get_size(self) -> str:
        """Get the size of the file in human-readable format."""
        for unit in ["B", "KB", "MB", "GB", "TB"]:
            if self.size < 1024:
                return f"{self.size:.1f} {unit}"
            self.size /= 1024
        return f"{self.size:.1f} PB"

    def save(self, interface: StashInterface) -> None:
        """Save changes to this file in stash."""
        interface.update_file(self.to_update_input_dict())

    def to_dict(self) -> dict:
        """Convert the file object to a dictionary."""
        return {
            "id": self.id,
            "path": self.path,
            "basename": self.basename,
            "parent_folder_id": self.parent_folder_id,
            "mod_time": self.mod_time.isoformat() if self.mod_time else None,
            "size": self.size,
            "zip_file_id": self.zip_file_id,
            "fingerprints": self.fingerprints,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    def to_create_input_dict(self) -> dict:
        """Converts the file object into a dictionary matching the FileCreateInput GraphQL definition.

        Only includes fields that have non-default values to prevent unintended overwrites.
        Uses _input_fields configuration to determine what to include.
        Required fields (path, basename, parent_folder_id) are always included.
        """
        result = {}

        for field_name, (
            attr_name,
            default_value,
            transform_func,
            required,
        ) in self._input_fields.items():
            value = getattr(self, attr_name)

            # Skip None values for non-required fields
            if value is None and not required:
                continue

            # Skip if value equals default (but still include required fields)
            if not required and value == default_value:
                continue

            # For empty lists (but still include required fields)
            if not required and isinstance(default_value, list) and not value:
                continue

            # Special handling for numeric fields that could be 0
            if isinstance(value, (int, float)) or value is not None:
                result[field_name] = transform_func(value) if transform_func else value

        return result

    def to_update_input_dict(self) -> dict:
        """Converts the file object into a dictionary matching the FileUpdateInput GraphQL definition."""
        return {"id": self.id, **self.to_create_input_dict()}

    @classmethod
    def from_dict(cls, data: dict) -> BaseFile:
        """Create a BaseFile instance from a dictionary."""
        # Handle both GraphQL response format and direct dictionary format
        file_data = data.get("file", data)

        # Convert string dates to datetime objects
        mod_time = (
            datetime.fromisoformat(file_data["mod_time"])
            if file_data.get("mod_time")
            else None
        )
        created_at = (
            datetime.fromisoformat(file_data["created_at"])
            if file_data.get("created_at")
            else None
        )
        updated_at = (
            datetime.fromisoformat(file_data["updated_at"])
            if file_data.get("updated_at")
            else None
        )

        return cls(
            id=str(file_data.get("id", "")),
            path=file_data["path"],
            basename=file_data["basename"],
            parent_folder_id=file_data["parent_folder_id"],
            mod_time=mod_time or datetime.now(),
            size=file_data.get("size", 0),
            zip_file_id=file_data.get("zip_file_id"),
            fingerprints=list(file_data.get("fingerprints", [])),
            created_at=created_at or datetime.now(),
            updated_at=updated_at or datetime.now(),
        )


@dataclass
class ImageFile(BaseFile, ImageFileProtocol):
    """Represents an image file in Stash."""

    width: int = 0
    height: int = 0


@dataclass
class SceneFile(BaseFile, VideoFileProtocol):
    """Represents a scene (video) file in Stash."""

    format: str = ""
    width: int = 0
    height: int = 0
    duration: float = 0.0
    video_codec: str = ""
    audio_codec: str = ""
    frame_rate: float = 0.0
    bit_rate: int = 0


class VisualFileProtocol(Protocol):
    """Protocol for visual file types."""

    file: SceneFile | ImageFile
    file_type: FileType

    def get_path(self) -> str: ...
    def get_size(self) -> str: ...
    def to_dict(self) -> dict: ...
    def from_dict(self, data: dict) -> VisualFileProtocol: ...


@dataclass
class VisualFile:
    """Represents a visual file (image or video) in Stash."""

    file: SceneFile | ImageFile
    file_type: FileType

    def get_path(self) -> str:
        """Get the full path of the file."""
        return self.file.get_path()

    def get_size(self) -> str:
        """Get the size of the file in human-readable format."""
        return self.file.get_size()

    def to_dict(self) -> dict:
        """Convert the visual file object to a dictionary."""
        return {
            "file": self.file.to_dict(),
            "file_type": self.file_type.value,
        }

    @classmethod
    def from_dict(cls, data: dict) -> VisualFile:
        """Create a VisualFile instance from a dictionary."""
        # Handle both GraphQL response format and direct dictionary format
        if data.get("duration"):
            file = SceneFile.from_dict(data)
            type = FileType.VIDEO
        else:
            file = ImageFile.from_dict(data)
            type = FileType.IMAGE
        return cls(file, type)
