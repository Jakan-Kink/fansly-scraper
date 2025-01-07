from datetime import datetime

from stashapi.stashapp import StashInterface

from .types import StashStudioProtocol


class StashStudio(StashStudioProtocol):
    @staticmethod
    def find(id: str, interface: StashInterface) -> "StashStudio":
        """Find a studio by ID.

        Args:
            id: The ID of the studio to find
            interface: StashInterface instance to use for querying

        Returns:
            StashStudio instance if found, None otherwise
        """
        data = interface.find_studio(id)
        return StashStudio.from_dict(data) if data else None

    @staticmethod
    def find_by_name(
        name: str, interface: StashInterface, create: bool = False
    ) -> "StashStudio":
        """Find a studio by name.

        Args:
            name: The name of the studio to find
            interface: StashInterface instance to use for querying
            create: If True, create the studio if it doesn't exist

        Returns:
            StashStudio instance if found, None otherwise
        """
        data = interface.find_studio(name, create=create)
        return StashStudio.from_dict(data) if data else None

    @staticmethod
    def find_all(
        interface: StashInterface, filter: dict = {"per_page": -1}, q: str = ""
    ) -> list["StashStudio"]:
        """Find all studios matching the filter/query.

        Args:
            interface: StashInterface instance to use for querying
            filter: Filter parameters for the query
            q: Query string to search for

        Returns:
            List of StashStudio instances matching the criteria
        """
        data = interface.find_studios(filter=filter, q=q)
        return [StashStudio.from_dict(s) for s in data]

    def save(self, interface: StashInterface) -> None:
        """Save changes to this studio in stash.

        Args:
            interface: StashInterface instance to use for updating
        """
        interface.update_studio(self.to_dict())

    def __init__(
        self,
        id: str,
        name: str,
        url: str | None = None,
        ignore_auto_tag: bool = False,
        image_path: str | None = None,
        rating100: int | None = None,
        favorite: bool = False,
        details: str | None = None,
        created_at: datetime = datetime.now(),
        updated_at: datetime = datetime.now(),
    ):
        StashStudioProtocol.__init__(
            self=self, id=id, urls=[], created_at=created_at, updated_at=updated_at
        )
        self.name = name
        self.url = url
        self.parent_studio = None
        self.child_studios = []
        self.aliases = []
        self.tags = []
        self.ignore_auto_tag = ignore_auto_tag
        self.image_path = image_path
        self.rating100 = rating100
        self.favorite = favorite
        self.details = details
        self.groups = []
        self.stash_ids = []

    def to_dict(self) -> dict:
        """Convert the studio object to a dictionary."""
        return {
            "id": self.id,
            "name": self.name,
            "url": self.url,
            "parent_studio": self.parent_studio.id if self.parent_studio else None,
            "child_studios": [studio.id for studio in self.child_studios],
            "aliases": self.aliases,
            "ignore_auto_tag": self.ignore_auto_tag,
            "image_path": self.image_path,
            "rating100": self.rating100,
            "favorite": self.favorite,
            "details": self.details,
            "stash_ids": self.stash_ids,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    def to_create_input_dict(self) -> dict:
        """Converts the StashStudio object into a dictionary matching the StudioCreateInput GraphQL definition."""
        return {
            "name": self.name,
            "url": self.url,
            "parent_id": self.parent_studio.id if self.parent_studio else None,
            "aliases": self.aliases,
            "ignore_auto_tag": self.ignore_auto_tag,
            "image": self.image_path,
            "rating100": self.rating100,
            "favorite": self.favorite,
            "details": self.details,
            "stash_ids": self.stash_ids,
        }

    def to_update_input_dict(self) -> dict:
        """Converts the StashStudio object into a dictionary matching the StudioUpdateInput GraphQL definition."""
        return {"id": self.id, **self.to_create_input_dict()}

    def stash_create(self, interface: StashInterface) -> dict:
        """Creates the studio in stash using the interface.

        Args:
            interface: StashInterface instance to use for creation

        Returns:
            dict: Response from stash containing the created studio data
        """
        return interface.create_studio(self.to_create_input_dict())

    @classmethod
    def from_dict(cls, data: dict) -> "StashStudio":
        """Create a StashStudio instance from a dictionary.

        Args:
            data: Dictionary containing studio data from GraphQL or other sources.

        Returns:
            A new StashStudio instance.
        """
        # Handle both GraphQL response format and direct dictionary format
        studio_data = data.get("studio", data)

        # Create the base studio object
        studio = cls(
            id=str(studio_data.get("id", "")),
            name=str(studio_data.get("name", "")),
            url=studio_data.get("url"),
            ignore_auto_tag=bool(studio_data.get("ignore_auto_tag", False)),
            image_path=studio_data.get("image_path"),
            rating100=studio_data.get("rating100"),
            favorite=bool(studio_data.get("favorite", False)),
            details=studio_data.get("details"),
            created_at=studio_data.get("created_at"),
            updated_at=studio_data.get("updated_at"),
        )

        # Handle parent_studio if present
        if "parent_studio" in studio_data and studio_data["parent_studio"]:
            studio.parent_studio = cls.from_dict(studio_data["parent_studio"])

        # Handle child_studios if present
        if "child_studios" in studio_data:
            studio.child_studios = [
                cls.from_dict(s) for s in studio_data["child_studios"]
            ]

        # Handle aliases if present
        if "aliases" in studio_data:
            studio.aliases = list(studio_data["aliases"])

        # Handle tags if present
        if "tags" in studio_data:
            from .stash_tag import StashTag

            studio.tags = [StashTag.from_dict(t) for t in studio_data["tags"]]

        # Handle groups if present
        if "groups" in studio_data:
            from .stash_group import StashGroup

            studio.groups = [StashGroup.from_dict(g) for g in studio_data["groups"]]

        # Handle stash_ids if present
        if "stash_ids" in studio_data:
            studio.stash_ids = list(studio_data["stash_ids"])

        return studio
