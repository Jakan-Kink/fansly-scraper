"""Stash API client library for Python.

This module provides a high-level interface to the Stash GraphQL API using dataclasses.
"""

from stashapi.stashapp import StashInterface

# Protocol definitions
# Core functionality
from .base_protocols import (
    BaseFileProtocol,
    ImageFileProtocol,
    StashBaseProtocol,
    StashContentProtocol,
    StashGalleryProtocol,
    StashGroupDescriptionProtocol,
    StashGroupProtocol,
    StashImageProtocol,
    StashPerformerProtocol,
    StashQLProtocol,
    StashSceneProtocol,
    StashStudioProtocol,
    StashTagProtocol,
    VideoFileProtocol,
    VisualFileProtocol,
    VisualFileType,
)

# Dataclass implementations
from .file import BaseFile, FileType, ImageFile, SceneFile, VisualFile
from .gallery import Gallery, GalleryChapter
from .group import Group, GroupDescription
from .image import Image, ImagePathsType
from .performer import Performer
from .processing import StashProcessing
from .scene import (
    Scene,
    SceneFileType,
    ScenePathsType,
    SceneStreamEndpoint,
    VideoCaption,
)
from .stash_context import StashContext, StashQL  # Core classes for Stash interaction
from .studio import Studio
from .tag import Tag

__all__ = [
    # Core functionality
    "StashContext",
    "StashQL",
    "StashInterface",
    "StashQLProtocol",
    "StashProcessing",
    # Dataclass implementations
    "BaseFile",
    "FileType",
    "Gallery",
    "GalleryChapter",
    "Group",
    "GroupDescription",
    "Image",
    "ImageFile",
    "ImagePathsType",
    "Performer",
    "Scene",
    "SceneFile",
    "SceneFileType",
    "ScenePathsType",
    "SceneStreamEndpoint",
    "Studio",
    "Tag",
    "VideoCaption",
    "VisualFile",
    # Protocol definitions
    "BaseFileProtocol",
    "ImageFileProtocol",
    "StashBaseProtocol",
    "StashContentProtocol",
    "StashGalleryProtocol",
    "StashGroupDescriptionProtocol",
    "StashGroupProtocol",
    "StashImageProtocol",
    "StashPerformerProtocol",
    "StashSceneProtocol",
    "StashStudioProtocol",
    "StashTagProtocol",
    "VideoFileProtocol",
    "VisualFileProtocol",
    "VisualFileType",
]
