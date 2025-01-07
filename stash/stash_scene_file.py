from datetime import datetime

from .types import StashSceneFileProtocol


class StashSceneFile(StashSceneFileProtocol):

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
        StashSceneFileProtocol.__init__(
            self=self, id=id, created_at=created_at, updated_at=updated_at
        )
        self.path = path
        self.basename = basename
        self.parent_folder_id = parent_folder_id
        self.zip_file_id = zip_file_id
        self.mod_time = self.sanitize_datetime(mod_time)
        self.size = size
        self.format = format
        self.width = width
        self.height = height
        self.duration = duration
        self.video_codec = video_codec
        self.audio_codec = audio_codec
        self.frame_rate = frame_rate
        self.bit_rate = bit_rate

    @classmethod
    def from_dict(cls, data: dict) -> "StashSceneFile":
        """Create a StashSceneFile instance from a dictionary.

        Args:
            data: Dictionary containing scene file data

        Returns:
            A new StashSceneFile instance
        """
        file_data = data.get("scene_file", {})
        return cls(
            id=str(file_data.get("id", "")),
            path=file_data.get("path", ""),
            basename=file_data.get("basename", ""),
            parent_folder_id=file_data.get("parent_folder_id", ""),
            mod_time=file_data.get("mod_time", datetime.now()),
            size=file_data.get("size", 0),
            format=file_data.get("format", ""),
            width=file_data.get("width", 0),
            height=file_data.get("height", 0),
            duration=file_data.get("duration", 0.0),
            video_codec=file_data.get("video_codec", ""),
            audio_codec=file_data.get("audio_codec", ""),
            frame_rate=file_data.get("frame_rate", 0.0),
            bit_rate=file_data.get("bit_rate", 0),
            created_at=file_data.get("created_at", datetime.now()),
            updated_at=file_data.get("updated_at", datetime.now()),
            zip_file_id=file_data.get("zip_file_id"),
        )
