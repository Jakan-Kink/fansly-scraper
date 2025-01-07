from datetime import datetime, timezone

from .types import StashImageFileProtocol


class StashImageFile(StashImageFileProtocol):
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
        StashImageFileProtocol.__init__(
            self, id=id, created_at=created_at, updated_at=updated_at
        )
        self.path = path
        self.basename = basename
        self.parent_folder_id = parent_folder_id
        self.zip_file_id = zip_file_id
        self.mod_time = self.sanitize_datetime(mod_time)
        self.size = size
        self.width = width
        self.height = height

    @classmethod
    def from_dict(cls, data: dict) -> "StashImageFile":
        """Create a StashImageFile instance from a dictionary.

        Args:
            data: Dictionary containing image file data

        Returns:
            A new StashImageFile instance
        """
        file_data = data.get("image_file", {})
        return cls(
            id=str(file_data.get("id", "")),
            path=file_data.get("path", ""),
            basename=file_data.get("basename", ""),
            parent_folder_id=file_data.get("parent_folder_id", ""),
            mod_time=file_data.get("mod_time", datetime.now(tz=timezone.utc)),
            size=file_data.get("size", 0),
            width=file_data.get("width", 0),
            height=file_data.get("height", 0),
            created_at=file_data.get("created_at", datetime.now(tz=timezone.utc)),
            updated_at=file_data.get("updated_at", datetime.now(tz=timezone.utc)),
            zip_file_id=file_data.get("zip_file_id"),
        )
