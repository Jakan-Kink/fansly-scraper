from .stash_image_file import StashImageFile
from .stash_scene_file import StashSceneFile
from .visual_file_type import VisualFileType


class VisualFile:
    file: StashSceneFile | StashImageFile
    file_type: VisualFileType

    def __init__(
        self, file: StashSceneFile | StashImageFile, file_type: VisualFileType
    ) -> None:
        self.file = file
        self.file_type = file_type

    def get_path(self) -> str:
        return self.file.get_path()

    def get_size(self) -> int:
        return self.file.get_size()
