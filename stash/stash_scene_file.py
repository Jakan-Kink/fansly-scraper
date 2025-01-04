from datetime import datetime

from .stash_base_file import StashBaseFile
from .visual_file import VisualFile


class StashSceneFile(StashBaseFile, VisualFile):
    format: str
    width: int
    height: int
    duration: float
    video_codec: str
    audio_codec: str
    frame_rate: float
    bit_rate: int

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
        format: str,
        width: int,
        height: int,
        duration: float,
        video_codec: str,
        audio_codec: str,
        frame_rate: float,
        bit_rate: int,
        created_at: datetime = datetime.now(),
        updated_at: datetime = datetime.now(),
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
        self.format = format
        self.width = width
        self.height = height
        self.duration = duration
        self.video_codec = video_codec
        self.audio_codec = audio_codec
        self.frame_rate = frame_rate
        self.bit_rate = bit_rate
