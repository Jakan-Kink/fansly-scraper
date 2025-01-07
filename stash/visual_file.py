from .types import (
    StashImageFileProtocol,
    StashSceneFileProtocol,
    VisualFileProtocol,
    VisualFileType,
)


class VisualFile(VisualFileProtocol):
    def __init__(
        self,
        file: StashSceneFileProtocol | StashImageFileProtocol,
        file_type: VisualFileType,
    ) -> None:
        VisualFileProtocol.__init__(self)
        self.file = file
        self.file_type = file_type

    def get_path(self) -> str:
        return self.file.get_path()

    def get_size(self) -> int:
        return self.file.get_size()

    @classmethod
    def from_dict(cls, data: dict) -> "VisualFile":
        """Create a VisualFile instance from a dictionary.

        Args:
            data: Dictionary containing visual file data

        Returns:
            A new VisualFile instance
        """
        from .stash_image_file import StashImageFile
        from .stash_scene_file import StashSceneFile

        file_data = data.get("visual_file", {})

        # Determine file type and create appropriate file object
        if "duration" in file_data:
            file = StashSceneFile.from_dict({"scene_file": file_data})
            file_type = VisualFileType.VIDEO
        else:
            file = StashImageFile.from_dict({"image_file": file_data})
            file_type = VisualFileType.IMAGE

        return cls(file=file, file_type=file_type)
