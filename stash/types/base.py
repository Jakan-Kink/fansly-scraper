"""Base types for Stash models.

Note: While this is not a schema interface, it represents a common pattern
in the schema where many types have an id field. This includes core types
like Scene, Gallery, Performer, etc., and file types like VideoFile,
ImageFile, etc.

We use this interface to provide common functionality for these types, even
though the schema doesn't explicitly define an interface for them.

Note: created_at and updated_at are handled by Stash internally and not
included in this interface.
"""

from datetime import datetime
from typing import TYPE_CHECKING, Any, ClassVar, Type, TypeVar

import strawberry
from strawberry import ID
from strawberry.types import Info

from .enums import BulkUpdateIdMode

if TYPE_CHECKING:
    from ..client import StashClient

T = TypeVar("T", bound="StashObject")


@strawberry.input
class BulkUpdateStrings:
    """Input for bulk string updates."""

    values: list[str]  # [String!]!
    mode: BulkUpdateIdMode  # BulkUpdateIdMode!


@strawberry.input
class BulkUpdateIds:
    """Input for bulk ID updates."""

    ids: list[ID]  # [ID!]!
    mode: BulkUpdateIdMode  # BulkUpdateIdMode!


@strawberry.interface
class StashObject:
    """Base interface for our Stash model implementations.

    While this is not a schema interface, it represents a common pattern in the
    schema where many types have id, created_at, and updated_at fields. We use
    this interface to provide common functionality for these types.

    Common fields (matching schema pattern):
    - id: Unique identifier (ID!)
    Note: created_at and updated_at are handled by Stash internally

    Common functionality provided:
    - find_by_id: Find object by ID
    - save: Save object to Stash
    - to_input: Convert to GraphQL input type
    """

    # GraphQL type name (e.g., "Scene", "Performer")
    __type_name__: ClassVar[str]

    # Fields to include in queries
    __field_names__: ClassVar[set[str]]

    id: str  # Only required field - Stash handles created_at/updated_at internally

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

        # Get input data
        try:
            # Get input data
            input_data = self.to_input()
            # If it's a coroutine, await it
            if hasattr(input_data, "__await__"):
                input_data = await input_data

            # Ensure input_data is a plain dict
            if not isinstance(input_data, dict):
                raise ValueError(
                    f"to_input() must return a dict, got {type(input_data)}"
                )

            # Ensure all values are JSON serializable
            for key, value in list(
                input_data.items()
            ):  # Use list to allow modification during iteration
                if hasattr(value, "__await__"):
                    print(f"Found coroutine in {key}: {value}")
                    input_data[key] = await value
                elif isinstance(value, (list, tuple)):
                    # Check for coroutines in lists/tuples
                    new_value = []
                    for item in value:
                        if hasattr(item, "__await__"):
                            print(f"Found coroutine in {key} list: {item}")
                            new_value.append(await item)
                        else:
                            new_value.append(item)
                    input_data[key] = new_value

            result = await client.execute(
                mutation,
                {"input": input_data},
            )
        except Exception as e:
            raise ValueError(f"Failed to save {self.__type_name__}: {e}") from e

        # Update ID if this was a create
        if not hasattr(self, "id") or self.id == "new":
            result_key = f"{self.__type_name__.lower()}Create"
            if result_key not in result:
                print(f"DEBUG: Missing '{result_key}' in result: {result}")
                raise ValueError(f"Missing '{result_key}' in response")
            self.id = result[result_key]["id"]

    async def to_input(self) -> dict[str, Any]:
        """Convert to GraphQL input type.

        Returns:
            Dictionary of input fields
        """
        raise NotImplementedError("Subclasses must implement to_input()")
