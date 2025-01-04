from datetime import datetime


class StashTag:
    def __init__(
        self,
        id: str,
        name: str,
        description: str | None = None,
        ignore_auto_tag: bool = False,
        image_path: str | None = None,
        favorite: bool = False,
        created_at: datetime = datetime.now(),
        updated_at: datetime = datetime.now(),
    ):
        self.id = id
        self.name = name
        self.description = description
        self.aliases = []
        self.ignore_auto_tag = ignore_auto_tag
        self.image_path = image_path
        self.favorite = favorite
        self.created_at = created_at
        self.updated_at = updated_at
        self.parents = []
        self.children = []

    def scene_count(self, depth: int | None = None) -> int:
        # Implement logic to count scenes
        return 0

    def scene_marker_count(self, depth: int | None = None) -> int:
        # Implement logic to count scene markers
        return 0

    def image_count(self, depth: int | None = None) -> int:
        # Implement logic to count images
        return 0

    def gallery_count(self, depth: int | None = None) -> int:
        # Implement logic to count galleries
        return 0

    def performer_count(self, depth: int | None = None) -> int:
        # Implement logic to count performers
        return 0

    def studio_count(self, depth: int | None = None) -> int:
        # Implement logic to count studios
        return 0

    def group_count(self, depth: int | None = None) -> int:
        # Implement logic to count groups
        return 0

    def parent_count(self) -> int:
        return len(self.parents)

    def child_count(self) -> int:
        return len(self.children)
