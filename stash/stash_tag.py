from datetime import datetime

from stashapi.stashapp import StashInterface

from .stash_context import StashQL
from .types import StashTagProtocol


class StashTag(StashTagProtocol):
    @staticmethod
    def find(id: str, interface: StashInterface) -> "StashTag":
        """Find a tag by ID.

        Args:
            id: The ID of the tag to find
            interface: StashInterface instance to use for querying

        Returns:
            StashTag instance if found, None otherwise
        """
        data = interface.find_tag(id)
        return StashTag.from_dict(data) if data else None

    @staticmethod
    def find_by_name(
        name: str, interface: StashInterface, create: bool = False
    ) -> "StashTag":
        """Find a tag by name.

        Args:
            name: The name of the tag to find
            interface: StashInterface instance to use for querying
            create: If True, create the tag if it doesn't exist

        Returns:
            StashTag instance if found, None otherwise
        """
        data = interface.find_tag(name, create=create)
        return StashTag.from_dict(data) if data else None

    @staticmethod
    def find_all(
        interface: StashInterface, filter: dict = {"per_page": -1}, q: str = ""
    ) -> list["StashTag"]:
        """Find all tags matching the filter/query.

        Args:
            interface: StashInterface instance to use for querying
            filter: Filter parameters for the query
            q: Query string to search for

        Returns:
            List of StashTag instances matching the criteria
        """
        data = interface.find_tags(filter=filter, q=q)
        return [StashTag.from_dict(t) for t in data]

    def save(self, interface: StashInterface) -> None:
        """Save changes to this tag in stash.

        Args:
            interface: StashInterface instance to use for updating
        """
        interface.update_tag(self.to_dict())

    def delete(self, interface: StashInterface) -> None:
        """Delete this tag from stash.

        Args:
            interface: StashInterface instance to use for deletion
        """
        interface.destroy_tag(self.id)

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
        StashTagProtocol.__init__(
            self=self, id=id, created_at=created_at, updated_at=updated_at
        )
        self.name = name
        self.description = description
        self.aliases = []
        self.ignore_auto_tag = ignore_auto_tag
        self.image_path = image_path
        self.favorite = favorite
        self.parents = []
        self.children = []

    def to_dict(self) -> dict:
        ql_dict = StashQL.to_dict(self)
        tag_dict = {
            "name": self.name,
            "description": self.description,
            "ignore_auto_tag": self.ignore_auto_tag,
            "image_path": self.image_path,
            "favorite": self.favorite,
            "parents": [tag.id for tag in self.parents],
            "children": [tag.id for tag in self.children],
        }
        return {**ql_dict, **tag_dict}

    def to_create_input_dict(self) -> dict:
        """Converts the StashTag object into a dictionary matching the TagCreateInput GraphQL definition."""
        return {
            "name": self.name,
            "description": self.description,
            "aliases": self.aliases,
            "ignore_auto_tag": self.ignore_auto_tag,
            "image": self.image_path,
            "parent_ids": [tag.id for tag in self.parents],
            "child_ids": [tag.id for tag in self.children],
        }

    def to_update_input_dict(self) -> dict:
        """Converts the StashTag object into a dictionary matching the TagUpdateInput GraphQL definition."""
        return {"id": self.id, **self.to_create_input_dict()}

    def stash_create(self, interface: StashInterface) -> dict:
        """Creates the tag in stash using the interface.

        Args:
            interface: StashInterface instance to use for creation

        Returns:
            dict: Response from stash containing the created tag data
        """
        return interface.create_tag(self.to_create_input_dict())

    @classmethod
    def from_dict(cls, data: dict) -> "StashTag":
        """Create a StashTag instance from a dictionary.

        Args:
            data: Dictionary containing tag data from GraphQL or other sources.

        Returns:
            A new StashTag instance.
        """
        # Handle both GraphQL response format and direct dictionary format
        tag_data = data.get("tag", data)

        # Create the base tag object
        tag = cls(
            id=str(tag_data.get("id", "")),
            name=str(tag_data.get("name", "")),
            description=tag_data.get("description"),
            ignore_auto_tag=bool(tag_data.get("ignore_auto_tag", False)),
            image_path=tag_data.get("image_path"),
            favorite=bool(tag_data.get("favorite", False)),
            created_at=tag_data.get("created_at"),
            updated_at=tag_data.get("updated_at"),
        )

        # Handle aliases if present
        if "aliases" in tag_data:
            tag.aliases = list(tag_data["aliases"])

        # Handle parents if present
        if "parents" in tag_data:
            tag.parents = [cls.from_dict(t) for t in tag_data["parents"]]

        # Handle children if present
        if "children" in tag_data:
            tag.children = [cls.from_dict(t) for t in tag_data["children"]]

        return tag
