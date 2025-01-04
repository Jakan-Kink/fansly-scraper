from datetime import datetime, timezone

from .stash_context import StashQL


class StashBaseFile(StashQL):
    path: str
    basename: str
    parent_folder_id: str
    mod_time: datetime
    size: int
    zip_file_id: str | None
    fingerprints: list

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
        super().__init__(id=id, urls=[], created_at=created_at, updated_at=updated_at)
        self.path = path
        self.basename = basename
        self.parent_folder_id = parent_folder_id
        self.zip_file_id = zip_file_id
        self.mod_time = self.sanitize_datetime(mod_time)
        self.size = size
        self.fingerprints = []

    # def fingerprint(self, type: str) -> str:
    #     # Implement fingerprint logic
    #     return ""
