from datetime import datetime, timezone

from .stash_base_file import StashBaseFile
from .visual_file import VisualFile


class StashImageFile(StashBaseFile, VisualFile):
    width: int
    height: int

    def get_path(self) -> str:
        return self.path

    def get_size(self) -> int:
        return self.size

    def __init__(
        self,
        id: str,
        path: str,
        basename: str,
        parent_folder_id: str,
        mod_time: datetime,
        size: int,
        width: int,
        height: int,
        created_at: datetime = datetime.now(tz=timezone.utc),
        updated_at: datetime = datetime.now(tz=timezone.utc),
        zip_file_id: str | None = None,
    ) -> None:
        super().__init__(
            id,
            path,
            basename,
            parent_folder_id,
            mod_time,
            size,
            created_at,
            updated_at,
            zip_file_id,
        )
        self.width = width
        self.height = height
