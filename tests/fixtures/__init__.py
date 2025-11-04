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
from .api_fixtures import (
    create_mock_response,
    fansly_api,
    fansly_api_factory,
    mock_http_session,
)
from .cleanup_fixtures import (
    cleanup_global_config_state,
    cleanup_http_sessions,
    cleanup_loguru_handlers,
    cleanup_mock_patches,
    cleanup_rich_progress_state,
    cleanup_unawaited_coroutines,
)
from .database_fixtures import (
    AwaitableAttrsMock,
    config,
    conversation_data,
    factory_async_session,
    factory_session,
    json_conversation_data,
    mock_account,
    safe_name,
    session,
    session_factory,
    session_sync,
    test_account,
    test_account_media,
    test_async_session,
    test_bundle,
    test_data_dir,
    test_database,
    test_database_sync,
    test_engine,
    test_media,
    test_message,
    test_post,
    test_sync_engine,
    test_wall,
    timeline_data,
    uuid_test_db_factory,
)
from .metadata_factories import (
    AccountFactory,
    AccountMediaBundleFactory,
    AccountMediaFactory,
    AttachmentFactory,
    BaseFactory,
    HashtagFactory,
    MediaFactory,
    MediaLocationFactory,
    MediaStoryStateFactory,
    MessageFactory,
    PostFactory,
    StoryFactory,
    StubTrackerFactory,
    TimelineStatsFactory,
    WallFactory,
    create_groups_from_messages,
    setup_accounts_and_groups,
)
from .metadata_factories import (
    GroupFactory as MetadataGroupFactory,  # Alias to avoid collision with Stash GroupFactory
)
from .stash_api_fixtures import (
    enable_scene_creation,
    mock_account,
    mock_client,
    mock_performer,
    mock_scene,
    mock_session,
    mock_studio,
    mock_transport,
    stash_cleanup_tracker,
    stash_client,
    stash_context,
    test_query,
)
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
from .stash_integration_fixtures import (
    base_mock_performer,
    base_mock_scene,
    base_mock_studio,
    fansly_network_studio,
    integration_mock_account,
    integration_mock_performer,
    integration_mock_scene,
    integration_mock_studio,
    mock_account_media,
    mock_attachment,
    mock_context,
    mock_gallery,
    mock_group,
    mock_image,
    mock_media,
    mock_media_bundle,
    mock_message,
    mock_messages,
    mock_permissions,
    mock_post,
    mock_posts,
    mock_stash_context,
    mock_state,
    mock_studio_finder,
    real_stash_processor,
    stash_mock_account,
    stash_processor,
)
from .stash_mixin_fixtures import (
    account_mixin,
    batch_mixin,
    content_mixin,
    gallery_mixin,
    gallery_mock_performer,
    gallery_mock_studio,
    media_mixin,
    mock_item,
    studio_mixin,
    tag_mixin,
)
from .stash_processing_fixtures import (
    AsyncResult,
    AsyncSessionContext,
    MockDatabase,
    mock_database,
    processing_mock_attachment,
    processing_mock_media,
    processing_mock_messages,
    processing_mock_multiple_messages,
    processing_mock_multiple_posts,
    processing_mock_posts,
    safe_image_create,
    safe_scene_create,
    safe_scene_marker_create,
    safe_studio_create,
    safe_tag_create,
    sanitize_model_data,
)
from .stash_type_factories import (  # Pytest fixtures for Stash types
    GalleryFactory,
    GroupFactory,
    ImageFactory,
    ImageFileFactory,
    PerformerFactory,
    SceneFactory,
    StudioFactory,
    TagFactory,
    VideoFileFactory,
    mock_gallery,
    mock_image,
    mock_image_file,
    mock_performer,
    mock_scene,
    mock_studio,
    mock_tag,
    mock_video_file,
)


FIXTURES_DIR = Path(__file__).parent

# Module-specific exports
mod_metadata_factories = [
    "AccountFactory",
    "AccountMediaFactory",
    "AccountMediaBundleFactory",
    "AttachmentFactory",
    "HashtagFactory",
    "MediaFactory",
    "MediaLocationFactory",
    "MediaStoryStateFactory",
    "MessageFactory",
    "MetadataGroupFactory",  # Renamed to avoid collision with Stash GroupFactory
    "PostFactory",
    "StoryFactory",
    "StubTrackerFactory",
    "TimelineStatsFactory",
    "WallFactory",
    "create_groups_from_messages",
    "setup_accounts_and_groups",
]

mod_stash_type_factories = [
    # Factories
    "PerformerFactory",
    "StudioFactory",
    "TagFactory",
    "SceneFactory",
    "GalleryFactory",
    "ImageFactory",
    "ImageFileFactory",
    "VideoFileFactory",
    "GroupFactory",  # Stash API GroupFactory (for Strawberry GraphQL types)
    # Pytest fixtures (real Strawberry type instances)
    "mock_performer",
    "mock_studio",
    "mock_tag",
    "mock_scene",
    "mock_gallery",
    "mock_image",
    "mock_image_file",
    "mock_video_file",
]

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

mod_stash_mixin_fixtures = [
    # Mixin test classes
    "account_mixin",
    "batch_mixin",
    "content_mixin",
    "gallery_mixin",
    "media_mixin",
    "studio_mixin",
    "tag_mixin",
    # Gallery test fixture aliases (delegate to stash_type_factories fixtures)
    "gallery_mock_performer",
    "gallery_mock_studio",
    # Mock item for Stash unit tests
    "mock_item",
]

# Local utility functions
mod_init = [
    "load_json_fixture",
    "save_json_fixture",
    "anonymize_response",
    "API_FIELD_MAPPINGS",
    "FIXTURES_DIR",
]

# Fixture names from database_fixtures
mod_database_fixtures = [
    "AwaitableAttrsMock",
    "uuid_test_db_factory",
    "test_data_dir",
    "timeline_data",
    "json_conversation_data",
    "conversation_data",
    "safe_name",
    # "temp_db_path" - REMOVED: Legacy SQLite fixture, no longer used
    "test_engine",
    "test_async_session",
    "config",
    "test_sync_engine",
    "session_factory",
    "test_database_sync",
    "test_database",
    # "cleanup_database" - REMOVED: UUID-based isolation makes cleanup redundant
    "session",
    "session_sync",
    "test_account",
    "mock_account",
    "test_media",
    "test_account_media",
    "test_post",
    "test_wall",
    "test_message",
    "test_bundle",
    "factory_session",
    "factory_async_session",
]

# Fixture names from stash_processing_fixtures
mod_stash_processing_fixtures = [
    "sanitize_model_data",
    "safe_scene_marker_create",
    "safe_tag_create",
    "safe_studio_create",
    "safe_image_create",
    "safe_scene_create",
    "AsyncResult",
    "AsyncSessionContext",
    "MockDatabase",
    # "mixin",
    "mock_database",
    "processing_mock_posts",
    "processing_mock_messages",
    "processing_mock_media",
    "processing_mock_attachment",
    "processing_mock_multiple_posts",
    "processing_mock_multiple_messages",
]

# Fixture names from stash_api_fixtures
mod_stash_api_fixtures = [
    "stash_context",
    "stash_client",
    "enable_scene_creation",
    "stash_cleanup_tracker",
    "mock_session",
    "mock_transport",
    "mock_client",
    "test_query",
    "mock_account",
    "mock_performer",
    "mock_studio",
    "mock_scene",
]

# Fixture names from stash_integration_fixtures
mod_stash_integration_fixtures = [
    "stash_mock_account",
    "base_mock_performer",
    "base_mock_studio",
    "base_mock_scene",
    "fansly_network_studio",
    "mock_context",
    "mock_stash_context",
    "mock_state",
    "mock_studio_finder",
    "integration_mock_account",
    "integration_mock_performer",
    "integration_mock_studio",
    "integration_mock_scene",
    "mock_media",
    "mock_group",
    "mock_attachment",
    "mock_post",
    "mock_posts",
    "mock_message",
    "mock_messages",
    "mock_permissions",
    "mock_account_media",
    "mock_media_bundle",
    "mock_gallery",
    "mock_image",
    "stash_processor",
    "real_stash_processor",
]

# Fixture names from cleanup_fixtures
mod_cleanup_fixtures = [
    "cleanup_rich_progress_state",
    "cleanup_loguru_handlers",
    "cleanup_http_sessions",
    "cleanup_global_config_state",
    "cleanup_unawaited_coroutines",
    "cleanup_mock_patches",
]

# Fixture names from api_fixtures
mod_api_fixtures = [
    "mock_http_session",
    "fansly_api",
    "fansly_api_factory",
    "create_mock_response",
]

# Combined __all__ from all modules
__all__ = [  # noqa: PLE0604 - all mod_ lists contain only strings
    *mod_metadata_factories,
    *mod_stash_type_factories,
    *mod_stash_fixtures,
    *mod_stash_mixin_fixtures,
    *mod_database_fixtures,
    *mod_stash_processing_fixtures,
    *mod_stash_api_fixtures,
    *mod_stash_integration_fixtures,
    *mod_cleanup_fixtures,
    *mod_api_fixtures,
    *mod_init,
]


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

    with fixture_path.open(encoding="utf-8") as f:
        data = json.load(f)
        if not isinstance(data, dict):
            raise TypeError(
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

    with fixture_path.open("w", encoding="utf-8") as f:
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
        if isinstance(value, list):
            return [_anonymize_value(key, item) for item in value]
        if key in field_mappings and isinstance(value, str):
            pass
            # faker_method = getattr(fake, field_mappings[key], None)
            # if faker_method:
            #     return faker_method()
        return value

    result = _anonymize_value("", response_data)
    if not isinstance(result, dict):
        raise TypeError("Response data must be a dictionary at the top level")
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
