from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Optional, Protocol

from stashapi.stashapp import StashInterface

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
        interface.update_file(self.to_dict())

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
        file_data = data.get("file", data)

        # Determine file type and create appropriate file instance
        file_type = FileType(file_data.get("file_type", "VideoFile"))
        if file_type == FileType.VIDEO:
            file = SceneFile.from_dict(file_data["file"])
        else:
            file = ImageFile.from_dict(file_data["file"])

        return cls(
            file=file,
            file_type=file_type,
        )
