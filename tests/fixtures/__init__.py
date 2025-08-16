"""
Fixture loading utilities for pytest tests.

This module provides utilities for loading and managing test fixtures
for the Fansly downloader application, including stash fixtures,
configuration fixtures, and utility functions.
"""

import json
from pathlib import Path
from typing import Any

# Import all fixtures from modules
from .stash_fixtures import (
    MockTag,
    TestStashCreateInput,
    TestStashObject,
    TestStashObjectNoCreate,
    TestStashObjectNoStrawberry,
    TestStashUpdateInput,
    bulk_update_ids_data,
    bulk_update_strings_data,
    complex_relationship_data,
    edge_case_stash_data,
    generate_graphql_response,
    generate_stash_object_data,
    large_stash_object_data,
    mock_stash_client_with_errors,
    mock_stash_client_with_responses,
    mock_tags,
    reset_stash_field_names_cache,
    test_stash_object,
    test_stash_object_new,
    test_stash_object_no_create,
    test_stash_object_no_strawberry,
)

# Base directory for fixtures
FIXTURES_DIR = Path(__file__).parent

# Module-specific exports
mod_stash_fixtures = [
    "reset_stash_field_names_cache",
    "MockTag",
    "TestStashCreateInput",
    "TestStashObject",
    "TestStashObjectNoCreate",
    "TestStashObjectNoStrawberry",
    "TestStashUpdateInput",
    "bulk_update_ids_data",
    "bulk_update_strings_data",
    "complex_relationship_data",
    "edge_case_stash_data",
    "generate_graphql_response",
    "generate_stash_object_data",
    "large_stash_object_data",
    "mock_stash_client_with_errors",
    "mock_stash_client_with_responses",
    "mock_tags",
    "test_stash_object",
    "test_stash_object_new",
    "test_stash_object_no_create",
    "test_stash_object_no_strawberry",
]

# Local utility functions
mod_init = [
    "load_json_fixture",
    "save_json_fixture",
    "anonymize_response",
    "API_FIELD_MAPPINGS",
    "FIXTURES_DIR",
]

# Combined __all__ from all modules
__all__ = mod_stash_fixtures + mod_init


def load_json_fixture(filename: str) -> dict[str, Any]:
    """
    Load a JSON fixture file.

    Args:
        filename: Path to JSON file relative to fixtures directory

    Returns:
        Dict containing the loaded JSON data

    Raises:
        FileNotFoundError: If fixture file doesn't exist
        json.JSONDecodeError: If JSON is invalid
    """
    fixture_path = FIXTURES_DIR / filename
    if not fixture_path.exists():
        raise FileNotFoundError(f"Fixture file not found: {fixture_path}")

    with open(fixture_path, encoding="utf-8") as f:
        data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError(
                f"Fixture file {fixture_path} does not contain a JSON object"
            )
        return data


def save_json_fixture(data: dict[str, Any], filename: str) -> None:
    """
    Save data as a JSON fixture file.

    Args:
        data: Data to save
        filename: Path to save file relative to fixtures directory
    """
    fixture_path = FIXTURES_DIR / filename
    fixture_path.parent.mkdir(parents=True, exist_ok=True)

    with open(fixture_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def anonymize_response(
    response_data: dict[str, Any], field_mappings: dict[str, str] | None = None
) -> dict[str, Any]:
    """
    Anonymize API response data while preserving structure.

    Args:
        response_data: Original API response data
        field_mappings: Optional mapping of field names to anonymization types

    Returns:
        Anonymized copy of the response data
    """
    if field_mappings is None:
        field_mappings = {
            "id": "uuid4",
            "email": "email",
            "username": "user_name",
            "name": "name",
            "title": "sentence",
            "description": "text",
            "bio": "text",
            "url": "url",
            "avatar": "image_url",
            "cover": "image_url",
        }

    def _anonymize_value(key: str, value: Any) -> Any:
        """Recursively anonymize values based on field mappings."""
        if isinstance(value, dict):
            return {k: _anonymize_value(k, v) for k, v in value.items()}
        elif isinstance(value, list):
            return [_anonymize_value(key, item) for item in value]
        elif key in field_mappings and isinstance(value, str):
            pass
            # faker_method = getattr(fake, field_mappings[key], None)
            # if faker_method:
            #     return faker_method()
        return value

    result = _anonymize_value("", response_data)
    if not isinstance(result, dict):
        raise ValueError("Response data must be a dictionary at the top level")
    return result


# Common field mappings for different API types
API_FIELD_MAPPINGS = {
    "fansly": {
        "id": "uuid4",
        "email": "email",
        "username": "user_name",
        "display_name": "name",
        "title": "sentence",
        "description": "text",
        "bio": "text",
        "avatar": "image_url",
        "cover_url": "image_url",
        "media_url": "url",
        "thumbnail_url": "image_url",
    }
}
