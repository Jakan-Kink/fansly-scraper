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

from __future__ import annotations

import copy
import inspect
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, ClassVar, TypeVar

import strawberry
from strawberry import ID

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

    # Input type for updates (e.g., SceneUpdateInput, PerformerUpdateInput)
    __update_input_type__: ClassVar[type[Any]]

    # Input type for creation (e.g., SceneCreateInput, PerformerCreateInput)
    # Optional - if not set, the type doesn't support creation
    __create_input_type__: ClassVar[type[Any] | None] = None

    # Fields to include in queries
    __field_names__: ClassVar[set[str]]

    # Fields to track for changes
    __tracked_fields__: ClassVar[set[str]] = set()

    # Relationship mappings for converting to input types
    __relationships__: ClassVar[
        dict[str, tuple[str, bool, Callable[[Any], Any] | None]]
    ] = {}

    # Field conversion functions
    __field_conversions__: ClassVar[dict[str, Callable[[Any], Any]]] = {}

    # Note: __original_values__ and __is_dirty__ are initialized in __post_init__
    # They are not declared as class attributes to avoid Strawberry treating them as dataclass fields

    id: str  # Only required field - Stash handles created_at/updated_at internally

    @classmethod
    def _filter_init_args(cls, kwargs: dict[str, Any]) -> dict[str, Any]:
        """Filter out unknown fields from __init__ kwargs.

        Args:
            kwargs: Dictionary of keyword arguments

        Returns:
            Dictionary with only valid fields for this class
        """
        # Try to get fields from strawberry definition, fall back to all kwargs if not available
        try:
            valid_fields = {
                field.name
                for field in cls.__strawberry_definition__.fields  # type: ignore[attr-defined]
            }
            return {k: v for k, v in kwargs.items() if k in valid_fields}
        except AttributeError:
            # Fallback if strawberry definition is not available
            return kwargs

    def __init__(self, **kwargs: Any) -> None:
        """Initialize object with filtered keyword arguments.

        Note: We don't call mark_clean() here because strawberry hasn't initialized
        the fields yet. That happens in __post_init__.
        """
        filtered_kwargs = self._filter_init_args(kwargs)
        super().__init__(**filtered_kwargs)

    def __post_init__(self) -> None:
        """Initialize object and store original values after dataclass init.

        This is called by strawberry after all fields are initialized, so it's
        the right place to mark the object as clean and store original values.
        """
        # Initialize internal tracking fields (not part of GraphQL schema)
        self.__original_values__: dict[str, Any] = {}
        self.__is_dirty__: bool = False

        # Mark the object as clean and set up tracking
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

            # Set dirty attribute for better debugging
            if not hasattr(self, "_dirty_attrs"):
                self._dirty_attrs = set()
            self._dirty_attrs.add(name)

            # Also update original values to not include this field
            # This ensures to_input_dirty will include this field
            if (
                hasattr(self, "__original_values__")
                and name in self.__original_values__
            ):
                del self.__original_values__[name]

    def is_dirty(self) -> bool:
        """Check if object has unsaved changes.

        Returns:
            True if object has unsaved changes
        """
        return self.__is_dirty__

    def mark_clean(self) -> None:
        """Mark object as having no unsaved changes."""
        self.__is_dirty__ = False
        # Clear the dirty attributes tracking
        if hasattr(self, "_dirty_attrs"):
            self._dirty_attrs = set()

        # Store deep copies of current values of tracked fields to detect changes in lists
        self.__original_values__ = {}
        for field in self.__tracked_fields__:
            if hasattr(self, field):
                value = getattr(self, field)
                # Make deep copy of lists to detect mutations
                if isinstance(value, list):
                    self.__original_values__[field] = copy.deepcopy(value)
                else:
                    self.__original_values__[field] = value

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
            # Try to get all fields from Strawberry type definition
            try:
                fields = cls.__strawberry_definition__.fields  # type: ignore[attr-defined]
                cls.__field_names__ = {
                    field.name for field in fields if not field.is_subscription
                }
            except AttributeError:
                # Fallback if strawberry definition is not available
                cls.__field_names__ = {"id"}  # At minimum, include id field
        return cls.__field_names__

    @classmethod
    async def find_by_id(
        cls: type[T],
        client: StashClient,
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

    async def save(self, client: StashClient) -> None:
        """Save object to Stash.

        Args:
            client: StashClient instance

        Raises:
            ValueError: If save fails
        """
        # Skip save if object is not dirty and not new
        if not self.is_dirty() and hasattr(self, "id") and self.id != "new":
            return

        # Get input data
        try:
            input_data = await self.to_input()
            # Ensure input_data is a plain dict
            if not isinstance(input_data, dict):
                raise TypeError(
                    f"to_input() must return a dict, got {type(input_data)}"
                )

            # For existing objects, if only ID is present, no actual changes to save
            if (
                hasattr(self, "id")
                and self.id != "new"
                and set(input_data.keys()) <= {"id"}
            ):
                log.debug(f"No changes to save for {self.__type_name__} {self.id}")
                self.mark_clean()  # Mark as clean since there are no changes
                return

            is_update = hasattr(self, "id") and self.id != "new"
            operation = "Update" if is_update else "Create"
            type_name = self.__type_name__

            # Generate consistent camelCase operation key
            operation_key = f"{type_name[0].lower()}{type_name[1:]}{operation}"
            mutation = f"""
                mutation {operation}{type_name}($input: {type_name}{operation}Input!) {{
                    {operation_key}(input: $input) {{
                        id
                    }}
                }}
            """

            result = await client.execute(mutation, {"input": input_data})

            # Extract the result using the same camelCase key
            if operation_key not in result:
                raise ValueError(f"Missing '{operation_key}' in response: {result}")

            operation_result = result[operation_key]
            if operation_result is None:
                raise ValueError(f"{operation} operation returned None")

            # Update ID for new objects
            if not is_update:
                self.id = operation_result["id"]

            # Mark object as clean after successful save
            self.mark_clean()

        except Exception as e:
            raise ValueError(f"Failed to save {self.__type_name__}: {e}") from e

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
        self, value: Any, transform: Callable[[Any], Any] | None
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
        if transform is not None:
            # Check if transform is async or sync
            if inspect.iscoroutinefunction(transform):
                result = await transform(value)
            else:
                result = transform(value)
            # Ensure we return str | None as declared
            return str(result) if result is not None else None
        return None

    async def _process_list_relationship(
        self, value: list[Any], transform: Callable[[Any], Any] | None
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
            if transform is not None:
                # Check if transform is async or sync
                if inspect.iscoroutinefunction(transform):
                    transformed = await transform(item)
                else:
                    transformed = transform(item)
                if transformed:
                    # Ensure we append a string to the list
                    items.append(str(transformed))
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
        data: dict[str, Any] = {}

        for rel_field in fields_to_process:
            # Skip if field is not a relationship or doesn't exist
            if rel_field not in self.__relationships__ or not hasattr(self, rel_field):
                continue

            # Get relationship mapping
            mapping = self.__relationships__[rel_field]
            target_field, is_list = mapping[:2]
            # Use explicit transform if provided and not None, otherwise use default _get_id
            transform = (
                mapping[2]
                if len(mapping) >= 3 and mapping[2] is not None
                else self._get_id
            )

            # Get and process value
            value = getattr(self, rel_field)
            if is_list:
                items = await self._process_list_relationship(value, transform)
                if items:
                    data[target_field] = items
            else:
                transformed = await self._process_single_relationship(value, transform)
                if transformed:
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
                        converter = self.__field_conversions__[field]
                        if converter is not None and callable(converter):
                            converted = converter(value)
                            if converted is not None:
                                data[field] = converted
                    except (ValueError, TypeError, ArithmeticError):
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

        Raises:
            ValueError: If creation is not supported and object has no ID
        """
        # Process all fields
        data = await self._process_fields(set(self.__field_conversions__.keys()))

        # Process all relationships
        rel_data = await self._process_relationships(set(self.__relationships__.keys()))
        data.update(rel_data)

        # Determine if this is a create or update operation
        is_new = not hasattr(self, "id") or self.id == "new"

        # If this is a create operation but creation isn't supported, raise an error
        if is_new and not self.__create_input_type__:
            raise ValueError(
                f"{self.__type_name__} objects cannot be created, only updated"
            )

        # Use the appropriate input type
        input_type = (
            self.__create_input_type__ if is_new else self.__update_input_type__
        )
        if input_type is None:
            if is_new:
                raise ValueError(
                    f"{self.__type_name__} objects cannot be created, only updated"
                )
            raise NotImplementedError("__update_input_type__ cannot be None")

        input_obj = input_type(**data)

        # Convert to dict and filter out None values and internal fields
        return {
            k: v
            for k, v in vars(input_obj).items()
            if not k.startswith("_") and v is not None and k != "client_mutation_id"
        }

    async def _to_input_dirty(self) -> dict[str, Any]:
        """Convert only dirty fields to input type.

        Returns:
            Dictionary of dirty input fields plus ID
        """
        if (
            not hasattr(self, "__update_input_type__")
            or self.__update_input_type__ is None
        ):
            raise NotImplementedError("Subclass must define __update_input_type__")

        # Start with ID which is always required for updates
        data = {"id": self.id}

        # Get set of dirty fields (fields whose values have changed)
        dirty_fields = set()

        for field in self.__tracked_fields__:
            if not hasattr(self, field):
                continue

            current_value = getattr(self, field)

            # Field was added after creation
            if field not in self.__original_values__:
                dirty_fields.add(field)
                continue

            original_value = self.__original_values__[field]

            # Handle list comparison more carefully - check if content has changed
            if isinstance(current_value, list) and isinstance(original_value, list):
                # Lists may have different object instances but same content
                # For objects with __dict__, compare their dictionaries
                if len(current_value) != len(original_value):
                    dirty_fields.add(field)
                    continue

                # Check each item in the lists
                for curr, orig in zip(current_value, original_value, strict=True):
                    # If items are dictionaries, compare their content
                    if hasattr(curr, "__dict__") and hasattr(orig, "__dict__"):
                        if curr.__dict__ != orig.__dict__:
                            dirty_fields.add(field)
                            break
                    # For simple types or objects with equality defined
                    elif curr != orig:
                        dirty_fields.add(field)
                        break
            # For non-list fields, simple comparison is enough
            elif current_value != original_value:
                dirty_fields.add(field)

        # Process dirty regular fields
        field_data = await self._process_fields(dirty_fields)
        data.update(field_data)

        # Process dirty relationships
        rel_data = await self._process_relationships(dirty_fields)
        data.update(rel_data)

        # Convert to update input and dict
        update_input_type = self.__update_input_type__
        if update_input_type is None:
            raise NotImplementedError("__update_input_type__ cannot be None")

        input_obj = update_input_type(**data)
        return {
            k: v
            for k, v in vars(input_obj).items()
            if not k.startswith("_") and v is not None and k != "client_mutation_id"
        }

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
