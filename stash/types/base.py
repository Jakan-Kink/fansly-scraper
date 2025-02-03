"""Base types for Stash models.

Note: While this is not a schema interface, it represents a common pattern
in the schema where many types have id, created_at, and updated_at fields.
This includes core types like Scene, Gallery, Performer, etc., and file types
like VideoFile, ImageFile, etc.

We use this interface to provide common functionality for these types, even
though the schema doesn't explicitly define an interface for them.
"""

from datetime import datetime
from typing import TYPE_CHECKING, Any, ClassVar, Type, TypeVar

import strawberry
from strawberry.types import Info

if TYPE_CHECKING:
    from ..client import StashClient

T = TypeVar("T", bound="StashObject")


@strawberry.interface
class StashObject:
    """Base interface for our Stash model implementations.

    While this is not a schema interface, it represents a common pattern in the
    schema where many types have id, created_at, and updated_at fields. We use
    this interface to provide common functionality for these types.

    Common fields (matching schema pattern):
    - id: Unique identifier (ID!)
    - created_at: Creation timestamp (Time!)
    - updated_at: Last update timestamp (Time!)

    Common functionality provided:
    - find_by_id: Find object by ID
    - save: Save object to Stash
    - to_input: Convert to GraphQL input type
    """

    # GraphQL type name (e.g., "Scene", "Performer")
    __type_name__: ClassVar[str]

    # Fields to include in queries
    __field_names__: ClassVar[set[str]]

    id: str
    created_at: datetime
    updated_at: datetime

    @classmethod
    def _get_field_names(cls) -> set[str]:
        """Get field names from Strawberry type definition.

        Returns:
            Set of field names to include in queries
        """
        if not hasattr(cls, "__field_names__"):
            # Get all fields from Strawberry type definition
            fields = strawberry.RESOLVER_PREFIX + cls.__strawberry_definition__.fields
            cls.__field_names__ = {
                field.name for field in fields if not field.is_subscription
            }
        return cls.__field_names__

    @classmethod
    async def find_by_id(
        cls: type[T],
        client: "StashClient",
        id: str,
    ) -> T | None:
        """Find object by ID.

        Args:
            client: StashClient instance
            id: Object ID

        Returns:
            Object instance if found, None otherwise
        """
        # Build query using fields from type definition
        fields = " ".join(cls._get_field_names())
        query = f"""
            query Find{cls.__type_name__}($id: ID!) {{
                find{cls.__type_name__}(id: $id) {{
                    {fields}
                }}
            }}
        """
        try:
            result = await client.execute(query, {"id": id})
            data = result[f"find{cls.__type_name__}"]
            return cls(**data) if data else None
        except Exception:
            return None

    async def save(self, client: "StashClient") -> None:
        """Save object to Stash.

        Args:
            client: StashClient instance

        Raises:
            ValueError: If save fails
        """
        if hasattr(self, "id") and self.id != "new":
            # Update existing
            mutation = f"""
                mutation Update{self.__type_name__}($input: {self.__type_name__}UpdateInput!) {{
                    {self.__type_name__.lower()}Update(input: $input) {{
                        id
                    }}
                }}
            """
        else:
            # Create new
            mutation = f"""
                mutation Create{self.__type_name__}($input: {self.__type_name__}CreateInput!) {{
                    {self.__type_name__.lower()}Create(input: $input) {{
                        id
                    }}
                }}
            """

        result = await client.execute(
            mutation,
            {"input": self.to_input()},
        )

        # Update ID if this was a create
        if not hasattr(self, "id") or self.id == "new":
            self.id = result[f"{self.__type_name__.lower()}Create"]["id"]

    def to_input(self) -> dict[str, Any]:
        """Convert to GraphQL input type.

        Returns:
            Dictionary of input fields
        """
        raise NotImplementedError("Subclasses must implement to_input()")
