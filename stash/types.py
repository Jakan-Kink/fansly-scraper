from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Protocol, runtime_checkable

from stashapi.stash_types import Gender
from stashapi.stashapp import StashInterface

from .base_protocols import StashQLProtocol
from .image_paths_type import ImagePathsType


@runtime_checkable
class StashBaseProtocol(StashQLProtocol, Protocol):
    """Base protocol for all Stash types."""

    def save(self, interface: StashInterface) -> None: ...
    def to_dict(self) -> dict: ...
    def from_dict(self, data: dict) -> StashBaseProtocol: ...
    @staticmethod
    def find(id: str, interface: StashInterface) -> StashBaseProtocol: ...


@runtime_checkable
class StashBaseFileProtocol(StashBaseProtocol, Protocol):
    """Protocol for file-based Stash types."""

    path: str
    basename: str
    parent_folder_id: str
    mod_time: datetime
    size: int
    zip_file_id: str | None
    fingerprints: list


@runtime_checkable
class StashGalleryProtocol(StashBaseProtocol, Protocol):
    """Protocol for gallery types."""

    title: str | None
    code: str | None
    urls: list[str]
    date: datetime | str | None
    details: str | None
    photographer: str | None
    rating100: int | None
    organized: bool
    files: list[VisualFileProtocol, StashGalleryProtocol]
    folder: None
    chapters: list
    scenes: list[StashSceneProtocol]
    studio: None
    image_count: int
    tags: list
    performers: list
    cover: None
    paths: None


@runtime_checkable
class StashGroupDescriptionProtocol(Protocol):
    """Protocol for group description types."""

    containing_group: StashGroupProtocol
    sub_group: StashGroupProtocol
    description: str


@runtime_checkable
class StashGroupProtocol(StashBaseProtocol, Protocol):
    """Protocol for group types."""

    name: str
    aliases: str | None
    duration: int | None
    date: str | None
    rating100: int | None
    director: str | None
    synopsis: str | None
    urls: list[str]
    front_image_path: str | None
    back_image_path: str | None
    studio: None
    tags: list[StashTagProtocol]
    containing_groups: list[StashGroupDescriptionProtocol]
    sub_groups: list[StashGroupDescriptionProtocol]
    scenes: list[StashSceneProtocol]
    performers: list[StashPerformerProtocol]
    galleries: list[StashGalleryProtocol]
    images: list[StashImageProtocol]


@runtime_checkable
class StashImageFileProtocol(StashBaseFileProtocol, Protocol):
    """Protocol for image file types."""

    width: int
    height: int

    def get_path(self) -> str: ...
    def get_size(self) -> str: ...


@runtime_checkable
class StashImageProtocol(StashBaseProtocol, Protocol):
    """Protocol for image types."""

    title: str | None
    code: str | None
    rating100: int | None
    date: datetime | str | None
    details: str | None
    photographer: str | None
    o_counter: int | None
    organized: bool
    visual_files: list[VisualFileProtocol]
    paths: list[ImagePathsType] | None
    galleries: list[StashGalleryProtocol]
    studio: StashStudioProtocol | None
    tags: list[StashTagProtocol]
    performers: list[StashPerformerProtocol]


@runtime_checkable
class StashPerformerProtocol(StashBaseProtocol, Protocol):
    """Protocol for performer types."""

    name: str
    disambiguation: str | None
    urls: list[str]
    gender: Gender | None
    birthdate: datetime | None
    ethnicity: str | None
    country: str | None
    eye_color: str | None
    height_cm: int | None
    measurements: str | None
    fake_tits: str | None
    penis_length: float | None
    circumcised: str | None
    career_length: str | None
    tattoos: str | None
    piercings: str | None
    favorite: bool
    ignore_auto_tag: bool
    image_path: str | None
    o_counter: int | None
    rating100: int | None
    details: str | None
    death_date: datetime | None
    hair_color: str | None
    weight: int | None
    scenes: list[StashSceneProtocol]
    stash_ids: list[str]
    groups: list[StashGroupProtocol]
    custom_fields: dict[str, str]
    tags: list[StashTagProtocol]


@runtime_checkable
class StashSceneFileProtocol(StashBaseFileProtocol, Protocol):
    """Protocol for scene file types."""

    format: str
    width: int
    height: int
    duration: float
    video_codec: str
    audio_codec: str
    frame_rate: float
    bit_rate: int

    def get_path(self) -> str: ...
    def get_size(self) -> str: ...


@runtime_checkable
class StashSceneProtocol(StashBaseProtocol, Protocol):
    """Protocol for scene types."""

    title: str | None
    code: str | None
    details: str | None
    director: str | None
    urls: list[str]
    date: datetime | None
    rating100: int | None
    organized: bool
    o_counter: int | None
    interactive: bool
    interactive_speed: int | None
    files: list[str]
    paths: None
    scene_markers: list[str]
    galleries: list[StashGalleryProtocol]
    studio: StashStudioProtocol | None
    groups: list[StashGroupProtocol]
    tags: list[StashTagProtocol]
    performers: list[StashPerformerProtocol]
    stash_ids: list[str]
    sceneStreams: list[str]


@runtime_checkable
class StashStudioProtocol(StashBaseProtocol, Protocol):
    """Protocol for studio types."""

    name: str
    url: str | None
    parent_studio: StashStudioProtocol | None
    child_studios: list[StashStudioProtocol]
    aliases: list[str]
    tags: list[StashTagProtocol]
    ignore_auto_tag: bool
    image_path: str | None
    rating100: int | None
    favorite: bool
    details: str | None
    groups: list[StashGroupProtocol]
    stash_ids: list[str]

    def save(self, interface: StashInterface) -> None: ...
    @staticmethod
    def find(id: str, interface: StashInterface) -> StashStudioProtocol: ...


@runtime_checkable
class StashStudioRelationshipProtocol(Protocol):
    """Protocol for studio relationship types."""

    parent_studio: StashStudioProtocol
    child_studio: StashStudioProtocol


@runtime_checkable
class StashTagProtocol(StashBaseProtocol, Protocol):
    """Protocol for tag types."""

    name: str
    description: str | None
    aliases: list[str]
    ignore_auto_tag: bool
    image_path: str | None
    favorite: bool
    parents: list[StashTagProtocol]
    children: list[StashTagProtocol]


@runtime_checkable
class StashTagRelationshipProtocol(Protocol):
    """Protocol for tag relationship types."""

    parent_tag: StashTagProtocol
    child_tag: StashTagProtocol


@runtime_checkable
class VisualFileProtocol(Protocol):
    """Protocol for visual file types."""

    file: StashSceneFileProtocol | StashImageFileProtocol
    file_type: VisualFileType

    def get_path(self) -> str: ...
    def get_size(self) -> str: ...
    def to_dict(self) -> dict: ...
    def from_dict(self, data: dict) -> VisualFileProtocol: ...


class VisualFileType(Enum):
    VIDEO = "VideoFile"
    IMAGE = "ImageFile"
