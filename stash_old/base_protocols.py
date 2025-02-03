"""Base protocols for Stash types."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Protocol, TypeVar, runtime_checkable

from stashapi.stash_types import Gender

from .stash_interface import StashInterface

T = TypeVar("T", bound="StashBaseProtocol")


class VisualFileType(Enum):
    """Type of visual file."""

    VIDEO = "VideoFile"
    IMAGE = "ImageFile"


@runtime_checkable
class StashQLProtocol(Protocol):
    """Protocol defining the base interface for Stash types."""

    id: str
    created_at: datetime
    updated_at: datetime

    def to_dict(self) -> dict: ...
    @classmethod
    def from_dict(cls: type[T], data: dict) -> T: ...
    @staticmethod
    def sanitize_datetime(value: str | datetime | None) -> datetime | None: ...


@runtime_checkable
class StashBaseProtocol(StashQLProtocol, Protocol):
    """Base protocol for all Stash types."""

    def save(self, interface: StashInterface) -> None: ...
    @staticmethod
    def find(id: str, interface: StashInterface) -> T | None: ...


@runtime_checkable
class StashContentProtocol(StashBaseProtocol, Protocol):
    """Protocol for content types (scenes, images, galleries)."""

    id: str
    title: str | None
    code: str | None
    details: str | None
    date: datetime | None
    rating100: int | None
    organized: bool
    urls: list[str]
    studio: StashStudioProtocol | None
    tags: list[StashTagProtocol]
    performers: list[StashPerformerProtocol]
    created_at: datetime
    updated_at: datetime


@runtime_checkable
class BaseFileProtocol(StashBaseProtocol, Protocol):
    """Protocol for base file types."""

    path: str
    basename: str
    parent_folder_id: str | None
    mod_time: datetime
    size: int
    zip_file_id: str | None
    fingerprints: list[str]


@runtime_checkable
class ImageFileProtocol(BaseFileProtocol, Protocol):
    """Protocol for image file types."""

    width: int
    height: int


@runtime_checkable
class VideoFileProtocol(ImageFileProtocol, Protocol):
    """Protocol for video file types."""

    duration: float
    video_codec: str
    audio_codec: str
    frame_rate: float
    bit_rate: int


@runtime_checkable
class StashPerformerProtocol(StashBaseProtocol, Protocol):
    """Protocol for performer types."""

    id: str
    name: str
    disambiguation: str | None
    gender: str | None
    birthdate: str | None
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
    death_date: str | None
    hair_color: str | None
    weight: int | None
    custom_fields: dict[str, str]
    created_at: datetime
    updated_at: datetime


@runtime_checkable
class StashStudioProtocol(StashBaseProtocol, Protocol):
    """Protocol for studio types."""

    id: str
    name: str
    url: str | None
    parent_studio: StashStudioProtocol | None
    child_studios: list[StashStudioProtocol]
    aliases: list[str]
    ignore_auto_tag: bool
    image_path: str | None
    rating100: int | None
    favorite: bool
    details: str | None
    created_at: datetime
    updated_at: datetime
    scene_count: int
    image_count: int
    gallery_count: int
    performer_count: int
    group_count: int
    stash_ids: list[str]
    groups: list[str]


@runtime_checkable
class StashTagProtocol(StashBaseProtocol, Protocol):
    """Protocol for tag types."""

    id: str
    name: str
    description: str | None
    aliases: list[str]
    ignore_auto_tag: bool
    created_at: datetime
    updated_at: datetime
    favorite: bool
    image_path: str | None
    scene_count: int
    scene_marker_count: int
    image_count: int
    gallery_count: int
    performer_count: int
    studio_count: int
    group_count: int
    parents: list[StashTagProtocol]
    children: list[StashTagProtocol]
    parent_count: int
    child_count: int


@runtime_checkable
class StashSceneProtocol(StashContentProtocol, Protocol):
    """Protocol for scene types."""

    director: str | None
    o_counter: int | None
    interactive: bool
    interactive_speed: int | None
    captions: list[str]
    last_played_at: datetime | None
    resume_time: float | None
    play_duration: float | None
    play_count: int | None
    play_history: list[datetime]
    o_history: list[datetime]


@runtime_checkable
class StashImageProtocol(StashContentProtocol, Protocol):
    """Protocol for image types."""

    photographer: str | None
    o_counter: int | None


@runtime_checkable
class StashGalleryProtocol(StashContentProtocol, Protocol):
    """Protocol for gallery types."""

    photographer: str | None
    o_counter: int | None
    image_count: int
    scenes: list[StashSceneProtocol]


@runtime_checkable
class StashGroupProtocol(StashBaseProtocol, Protocol):
    """Protocol for group types."""

    id: str
    name: str
    aliases: str | None
    duration: int | None
    date: str | None
    rating100: int | None
    director: str | None
    synopsis: str | None
    front_image_path: str | None
    back_image_path: str | None
    created_at: datetime
    updated_at: datetime
    studio: StashStudioProtocol | None
    scenes: list[StashSceneProtocol]
    performers: list[StashPerformerProtocol]
    galleries: list[StashGalleryProtocol]
    images: list[StashImageProtocol]


@runtime_checkable
class StashGroupDescriptionProtocol(Protocol):
    """Protocol for group description types."""

    containing_group: StashGroupProtocol
    sub_group: StashGroupProtocol
    description: str


@runtime_checkable
class VisualFileProtocol(Protocol):
    """Protocol for visual file types."""

    file: VideoFileProtocol | ImageFileProtocol
    file_type: VisualFileType

    def get_path(self) -> str: ...
    def get_size(self) -> str: ...
    def to_dict(self) -> dict: ...
    @classmethod
    def from_dict(cls: type[T], data: dict) -> T: ...
