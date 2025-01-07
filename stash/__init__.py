""" Integration with Stash-App """

from stashapi.stashapp import StashInterface

from .group_description import StashGroupDescription
from .image_paths_type import ImagePathsType
from .stash_base_file import StashBaseFile
from .stash_context import StashContext, StashQL
from .stash_gallery import StashGallery, gallery_fragment
from .stash_group import StashGroup
from .stash_image import StashImage, image_fragment
from .stash_image_file import StashImageFile
from .stash_performer import StashPerformer, performer_fragment
from .stash_scene import StashScene, scene_fragment
from .stash_scene_file import StashSceneFile
from .stash_studio import StashStudio
from .stash_tag import StashTag
from .studio_relationship import StashStudioRelationship
from .tag_relationship import StashTagRelationship
from .types import (
    StashBaseFileProtocol,
    StashGalleryProtocol,
    StashGroupDescriptionProtocol,
    StashGroupProtocol,
    StashImageFileProtocol,
    StashImageProtocol,
    StashPerformerProtocol,
    StashSceneFileProtocol,
    StashSceneProtocol,
    StashStudioProtocol,
    StashStudioRelationshipProtocol,
    StashTagProtocol,
    StashTagRelationshipProtocol,
    VisualFileProtocol,
    VisualFileType,
)
from .visual_file import VisualFile

__all__ = [
    # Fragments
    "image_fragment",
    "gallery_fragment",
    "performer_fragment",
    "scene_fragment",
    # Core classes
    "StashContext",
    "StashQL",
    "StashInterface",
    # Main classes (with their instance and static methods)
    "StashScene",  # find, find_all, save, stash_create, update_batch, create_batch
    "StashImage",  # find, find_all, save, stash_create, update_batch
    "StashGallery",  # find, find_all, save, stash_create, update_batch
    "StashPerformer",  # find, find_by_name, find_all, save, stash_create
    "StashGroup",  # find, save
    "StashStudio",  # find, find_by_name, find_all, save, stash_create
    "StashTag",  # find, find_by_name, find_all, save, stash_create, delete
    # File classes
    "StashImageFile",
    "StashSceneFile",
    "StashBaseFile",
    "VisualFile",
    # Relationship classes
    "StashGroupDescription",
    "StashTagRelationship",
    "StashStudioRelationship",
    # Types
    "VisualFileType",
    "ImagePathsType",
    # Protocols
    "StashGroupDescriptionProtocol",
    "StashBaseFileProtocol",
    "StashGalleryProtocol",
    "StashGroupProtocol",
    "StashImageProtocol",
    "StashImageFileProtocol",
    "StashPerformerProtocol",
    "StashSceneProtocol",
    "StashSceneFileProtocol",
    "StashStudioProtocol",
    "StashTagProtocol",
    "StashStudioRelationshipProtocol",
    "StashTagRelationshipProtocol",
    "VisualFileProtocol",
]
