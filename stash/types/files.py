"""File types from schema/types/file.graphql."""

from datetime import datetime
from typing import Any

import strawberry
from strawberry import ID, lazy

from .base import StashObject


def fingerprint_resolver(parent: "BaseFile", type: str) -> str:
    """Resolver for fingerprint field.

    Args:
        parent: The BaseFile instance (automatically passed by strawberry)
        type: The fingerprint type to look for

    Returns:
        The fingerprint value for the given type, or empty string if not found.
        This matches the GraphQL schema which defines the return type as String! (non-nullable).
    """
    for fp in parent.fingerprints:
        if fp.type_ == type:
            return fp.value
    return ""  # Return empty string instead of None to match GraphQL schema


@strawberry.input
class SetFingerprintsInput:
    """Input for setting fingerprints."""

    type_: str = strawberry.field(
        name="type"
    )  # String! - aliased to avoid built-in conflict
    value: str | None = None  # String


@strawberry.input
class FileSetFingerprintsInput:
    """Input for setting file fingerprints."""

    id: ID  # ID!
    fingerprints: list[SetFingerprintsInput]  # [SetFingerprintsInput!]!


@strawberry.input
class MoveFilesInput:
    """Input for moving files."""

    ids: list[ID]  # [ID!]!
    destination_folder: str | None = None  # String
    destination_folder_id: ID | None = None  # ID
    destination_basename: str | None = None  # String


@strawberry.type
class Fingerprint:
    """Fingerprint type from schema/types/file.graphql."""

    type_: str = strawberry.field(
        name="type"
    )  # String! - aliased to avoid built-in conflict
    value: str  # String!


@strawberry.interface
class BaseFile(StashObject):
    """Base interface for all file types from schema/types/file.graphql.

    Note: Inherits from StashObject since it has id, created_at, and updated_at
    fields in the schema, matching the common pattern."""

    __type_name__ = "BaseFile"

    # Required fields
    path: str  # String!
    basename: str  # String!
    parent_folder_id: ID  # ID!
    zip_file_id: ID | None = None  # ID
    mod_time: datetime  # Time!
    size: int  # Int64!
    fingerprints: list[Fingerprint]  # [Fingerprint!]!

    # Field with resolver for fingerprint lookup
    fingerprint: str = strawberry.field(resolver=fingerprint_resolver)

    async def to_input(self) -> dict[str, Any]:
        """Convert to GraphQL input.

        Returns:
            Dictionary of input fields for move or set fingerprints operations.
        """
        # Files don't have create/update operations, only move and set fingerprints
        if hasattr(self, "id"):
            # For move operation - return dict with proper field names
            return {
                "ids": [self.id],
                "destination_folder": None,  # Must be set by caller
                "destination_folder_id": None,  # Must be set by caller
                "destination_basename": self.basename,
            }
        else:
            raise ValueError("File must have an ID")


@strawberry.type
class ImageFile(BaseFile):
    """Image file type from schema/types/file.graphql.

    Implements BaseFile and inherits StashObject through it."""

    __type_name__ = "ImageFile"

    # Required fields
    width: int  # Int!
    height: int  # Int!


@strawberry.type
class VideoFile(BaseFile):
    """Video file type from schema/types/file.graphql.

    Implements BaseFile and inherits StashObject through it."""

    __type_name__ = "VideoFile"

    # Required fields
    format: str  # String!
    width: int  # Int!
    height: int  # Int!
    duration: float  # Float!
    video_codec: str  # String!
    audio_codec: str  # String!
    frame_rate: float  # Float!  # frame_rate in schema
    bit_rate: int  # Int!  # bit_rate in schema


VisualFile = strawberry.union("VisualFile", (VideoFile, ImageFile))


@strawberry.type
class GalleryFile(BaseFile):
    """Gallery file type from schema/types/file.graphql.

    Implements BaseFile with no additional fields and inherits StashObject through it.
    """

    __type_name__ = "GalleryFile"


@strawberry.type
class ScenePathsType:
    """Scene paths type from schema/types/scene.graphql."""

    screenshot: str | None = None  # String (Resolver)
    preview: str | None = None  # String (Resolver)
    stream: str | None = None  # String (Resolver)
    webp: str | None = None  # String (Resolver)
    vtt: str | None = None  # String (Resolver)
    sprite: str | None = None  # String (Resolver)
    funscript: str | None = None  # String (Resolver)
    interactive_heatmap: str | None = None  # String (Resolver)
    caption: str | None = None  # String (Resolver)


@strawberry.input
class StashIDInput:
    """Input for StashID from schema/types/stash-box.graphql."""

    endpoint: str  # String!
    stash_id: str  # String!


@strawberry.type
class StashID:
    """StashID type from schema/types/stash-box.graphql."""

    endpoint: str  # String!
    stash_id: str  # String!


@strawberry.type
class VideoCaption:
    """Video caption type from schema/types/scene.graphql."""

    language_code: str  # String!
    caption_type: str  # String!


@strawberry.type
class Folder(StashObject):
    """Folder type from schema/types/file.graphql.

    Note: Inherits from StashObject since it has id, created_at, and updated_at
    fields in the schema, matching the common pattern."""

    __type_name__ = "Folder"

    # Required fields
    path: str  # String!
    parent_folder_id: ID | None = None  # ID
    zip_file_id: ID | None = None  # ID
    mod_time: datetime  # Time!

    async def to_input(self) -> dict[str, Any]:
        """Convert to GraphQL input.

        Returns:
            Dictionary of input fields for move operation.
        """
        # Folders don't have create/update operations, only move
        if hasattr(self, "id"):
            # For move operation - return dict with proper field names
            return {
                "ids": [self.id],
                "destination_folder": None,  # Must be set by caller
                "destination_folder_id": None,  # Must be set by caller
                "destination_basename": None,  # Not applicable for folders
            }
        else:
            raise ValueError("Folder must have an ID")
