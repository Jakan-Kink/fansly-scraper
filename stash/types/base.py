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

from ..logging import client_logger as log
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
    - is_dirty: Check if object has unsaved changes
    - mark_clean: Mark object as having no unsaved changes
    - mark_dirty: Mark object as having unsaved changes
    """

    # GraphQL type name (e.g., "Scene", "Performer")
    __type_name__: ClassVar[str]

    # Fields to include in queries
    __field_names__: ClassVar[set[str]]

    # Fields to track for changes
    __tracked_fields__: ClassVar[set[str]] = set()

    # Original values for tracked fields
    __original_values__: dict[str, Any] = strawberry.field(default_factory=dict)

    # Dirty flag
    __is_dirty__: bool = strawberry.field(default=False)

    id: str  # Only required field - Stash handles created_at/updated_at internally

    def __post_init__(self) -> None:
        """Initialize object and store original values after dataclass init."""
        self.mark_clean()

    def __setattr__(self, name: str, value: Any) -> None:
        """Track changes to fields.

        Args:
            name: Field name
            value: New value
        """
        # Get the current value if it exists
        old_value = getattr(self, name, None) if hasattr(self, name) else None

        # Set the new value
        super().__setattr__(name, value)

        # If this is a tracked field and the value changed, mark as dirty
        if name in self.__tracked_fields__ and old_value != value:
            self.__is_dirty__ = True

    def is_dirty(self) -> bool:
        """Check if object has unsaved changes.

        Returns:
            True if object has unsaved changes
        """
        return self.__is_dirty__

    def mark_clean(self) -> None:
        """Mark object as having no unsaved changes."""
        self.__is_dirty__ = False
        # Store current values of tracked fields
        self.__original_values__ = {
            field: getattr(self, field)
            for field in self.__tracked_fields__
            if hasattr(self, field)
        }

    def mark_dirty(self) -> None:
        """Mark object as having unsaved changes."""
        self.__is_dirty__ = True

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
        # Skip save if object is not dirty and not new
        if not self.is_dirty() and hasattr(self, "id") and self.id != "new":
            return

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

        # Mark object as clean after successful save
        self.mark_clean()

    @staticmethod
    async def _get_id(obj: Any) -> str | None:
        """Get ID from object or dict.

        Args:
            obj: Object to get ID from

        Returns:
            ID if found, None otherwise
        """
        if isinstance(obj, dict):
            return obj.get("id")
        if hasattr(obj, "awaitable_attrs"):
            await obj.awaitable_attrs.id
        return getattr(obj, "id", None)

    async def _process_single_relationship(
        self, value: Any, transform: callable
    ) -> str | None:
        """Process a single relationship.

        Args:
            value: Value to transform
            transform: Transform function to apply

        Returns:
            Transformed value if successful, None otherwise
        """
        if not value:
            return None
        return await transform(value)

    async def _process_list_relationship(
        self, value: list[Any], transform: callable
    ) -> list[str]:
        """Process a list relationship.

        Args:
            value: List of values to transform
            transform: Transform function to apply

        Returns:
            List of transformed values
        """
        if not value:
            return []

        items = []
        for item in value:
            if transformed := await transform(item):
                items.append(transformed)
        return items

    async def _process_relationships(
        self, fields_to_process: set[str]
    ) -> dict[str, Any]:
        """Process relationships according to their mappings.

        Args:
            fields_to_process: Set of field names to process

        Returns:
            Dictionary of processed relationships
        """
        data = {}

        for rel_field in fields_to_process:
            # Skip if field is not a relationship or doesn't exist
            if rel_field not in self.__relationships__ or not hasattr(self, rel_field):
                continue

            # Get relationship mapping
            mapping = self.__relationships__[rel_field]
            target_field, is_list = mapping[:2]
            transform = mapping[2] if len(mapping) == 3 else self._get_id

            # Get and process value
            value = getattr(self, rel_field)
            if is_list:
                items = await self._process_list_relationship(value, transform)
                if items:
                    data[target_field] = items
            else:
                if transformed := await self._process_single_relationship(
                    value, transform
                ):
                    data[target_field] = transformed

        return data

    async def _process_fields(self, fields_to_process: set[str]) -> dict[str, Any]:
        """Process fields according to their converters.

        Args:
            fields_to_process: Set of field names to process

        Returns:
            Dictionary of processed fields
        """
        data = {}
        for field in fields_to_process:
            if field not in self.__field_conversions__:
                continue

            if hasattr(self, field):
                value = getattr(self, field)
                if value is not None:
                    try:
                        converted = self.__field_conversions__[field](value)
                        if converted is not None:
                            data[field] = converted
                    except (ValueError, TypeError):
                        pass

        return data

    async def to_input(self) -> dict[str, Any]:
        """Convert to GraphQL input type.

        Returns:
            Dictionary of input fields that have changed (are dirty) plus required fields.
            For new objects (id="new"), all fields are included.
        """
        # For new objects, include all fields
        is_new = not hasattr(self, "id") or self.id == "new"
        if is_new:
            return_obj = await self._to_input_all()
        else:
            # For existing objects, only include dirty fields plus ID
            return_obj = await self._to_input_dirty()
        log.debug(f"Converted {self.__type_name__} to input: {return_obj}")
        return return_obj

    async def _to_input_all(self) -> dict[str, Any]:
        """Convert all fields to input type.

        Returns:
            Dictionary of all input fields
        """
        raise NotImplementedError("Subclasses must implement _to_input_all()")

    async def _to_input_dirty(self) -> dict[str, Any]:
        """Convert only dirty fields to input type.

        Returns:
            Dictionary of dirty input fields plus ID
        """
        raise NotImplementedError("Subclasses must implement _to_input_dirty()")

    def __hash__(self) -> int:
        """Make object hashable based on type and ID.

        Returns:
            Hash of (type_name, id)
        """
        return hash((self.__type_name__, self.id))

    def __eq__(self, other: object) -> bool:
        """Compare objects based on type and ID.

        Args:
            other: Object to compare with

        Returns:
            True if objects are equal
        """
        if not isinstance(other, StashObject):
            return NotImplemented
        return (self.__type_name__, self.id) == (other.__type_name__, other.id)
