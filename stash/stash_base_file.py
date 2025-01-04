from datetime import datetime, timezone


class StashBaseFile:
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
    ):
        self.id = id
        self.path = path
        self.basename = basename
        self.parent_folder_id = parent_folder_id
        self.zip_file_id = zip_file_id
        self.mod_time = mod_time
        self.size = size
        self.created_at = created_at
        self.updated_at = updated_at
        self.fingerprints = []

    def fingerprint(self, type: str) -> str:
        # Implement fingerprint logic
        return ""
