from datetime import datetime, timezone

from .types import StashBaseFileProtocol


class StashBaseFile(StashBaseFileProtocol):
    def __init__(
        self,
        id: str,
        path: str,
        basename: str,
        parent_folder_id: str,
        mod_time: datetime,
        size: int,
        created_at: datetime = datetime.now(tz=timezone.utc),
        updated_at: datetime = datetime.now(tz=timezone.utc),
        zip_file_id: str | None = None,
    ) -> None:
        StashBaseFileProtocol.__init__(self=self, id=id, urls=[])
        self.path = path
        self.basename = basename
        self.parent_folder_id = parent_folder_id
        self.zip_file_id = zip_file_id
        self.mod_time = self.sanitize_datetime(mod_time)
        self.created_at = self.sanitize_datetime(created_at)
        self.updated_at = self.sanitize_datetime(updated_at)
        self.size = size
        self.fingerprints = []

    @classmethod
    def from_dict(cls, data: dict) -> "StashBaseFile":
        """Create a StashBaseFile instance from a dictionary.

        Args:
            data: Dictionary containing base file data

        Returns:
            A new StashBaseFile instance
        """
        file_data = data.get("base_file", {})
        return cls(
            id=str(file_data.get("id", "")),
            path=file_data.get("path", ""),
            basename=file_data.get("basename", ""),
            parent_folder_id=file_data.get("parent_folder_id", ""),
            mod_time=file_data.get("mod_time", datetime.now(tz=timezone.utc)),
            size=file_data.get("size", 0),
            created_at=file_data.get("created_at", datetime.now(tz=timezone.utc)),
            updated_at=file_data.get("updated_at", datetime.now(tz=timezone.utc)),
            zip_file_id=file_data.get("zip_file_id"),
        )
