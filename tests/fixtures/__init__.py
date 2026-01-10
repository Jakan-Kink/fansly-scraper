"""Fixture loading utilities for pytest tests.

This module provides utilities for loading and managing test fixtures
for the Fansly downloader application. All fixtures are organized in
a nested folder structure for better maintainability.

Fixture Organization:
- core/: Configuration and app-level fixtures
- download/: Download state and path fixtures
- api/: API client fixtures with respx for HTTP mocking
- metadata/: Database model factories and fixtures
- database/: Database connection and session fixtures
- stash/: Stash integration fixtures
- utils/: Cleanup and utility fixtures
"""

import json
from pathlib import Path
from typing import Any

# Import from nested modules
from .api import (
    create_mock_json_response,
    fansly_api,
    fansly_api_factory,
    fansly_api_with_respx,
    mock_fansly_account_response,
    mock_fansly_timeline_response,
)
from .core import (
    FanslyConfigFactory,
    complete_args,
    config_parser,
    mock_config_file,
    temp_config_dir,
    test_config,
    valid_api_config,
)
from .database import (
    config,
    config_with_database,
    conversation_data,
    factory_async_session,
    factory_session,
    json_conversation_data,
    mock_account,
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
from .download import (
    DownloadStateFactory,
    download_state,
    mock_download_dir,
    mock_download_media,
    mock_metadata_dir,
    mock_parse_media_info,
    mock_process_media_bundles,
    mock_process_media_download,
    mock_process_media_info,
    mock_temp_dir,
    test_downloads_dir,
)
from .metadata import (
    AccountFactory,
    AccountMediaBundleFactory,
    AccountMediaFactory,
    AttachmentFactory,
    BaseFactory,
    GroupFactory,
    HashtagFactory,
    MediaFactory,
    MediaLocationFactory,
    MediaStoryStateFactory,
    MessageFactory,
    MetadataGroupFactory,
    PostFactory,
    StoryFactory,
    StubTrackerFactory,
    TimelineStatsFactory,
    WallFactory,
    create_groups_from_messages,
    setup_accounts_and_groups,
    test_attachment,
    test_group,
    test_media_bundle,
    test_messages,
    test_posts,
)

# Note: Stash GroupFactory imported separately to avoid name collision
# Removed: mock_performer, mock_studio, mock_scene from stash_api_fixtures;
# (MagicMock duplicates - use real mock_performer/mock_studio/mock_scene from stash_type_factories);
# Removed: mock_client, mock_session, mock_transport from stash_api_fixtures;
# (Mocked internal GraphQL components - use respx to mock HTTP instead);
# Removed: integration_mock_performer, integration_mock_studio, integration_mock_scene;
# (MagicMock duplicates - use real factories from stash_type_factories);
# Removed: mock_stash_context, mock_context;
# (Mocked internal StashContext - use real stash_context or respx);
# Removed: mock_stash_client_with_responses, mock_stash_client_with_errors;
# (Mocked internal client.execute() - use respx to mock HTTP instead);
# Removed: stash_processor;
# (Used mock_stash_context - use real_stash_processor with respx instead)
from .stash import (
    GalleryFactory,
    ImageFactory,
    ImageFileFactory,
    JobFactory,
    PerformerFactory,
    SceneFactory,
    StudioFactory,
    TagFactory,
    VideoFileFactory,
    account_mixin,
    batch_mixin,
    content_mixin,
    create_find_performers_result,
    create_find_scenes_result,
    create_find_studios_result,
    create_find_tags_result,
    create_graphql_response,
    create_performer_dict,
    create_scene_dict,
    create_studio_dict,
    create_tag_create_result,
    create_tag_dict,
    enable_scene_creation,
    fansly_network_studio,
    gallery_mixin,
    gallery_mock_performer,
    gallery_mock_studio,
    media_mixin,
    message_media_generator,
    mock_gallery,  # From stash_type_factories (real factory)
    mock_image,  # From stash_type_factories (real factory)
    mock_image_file,  # From stash_type_factories (real factory)
    mock_item,
    mock_performer,  # From stash_type_factories (real factory)
    mock_scene,  # From stash_type_factories (real factory)
    mock_studio,  # From stash_type_factories (real factory)
    mock_tag,
    mock_video_file,  # From stash_type_factories (real factory)
    real_stash_processor,
    respx_stash_client,
    respx_stash_processor,
    stash_cleanup_tracker,
    stash_client,
    stash_context,
    studio_mixin,
    tag_mixin,
    test_state,
)
from .stash import GroupFactory as StashGroupFactory
from .utils import (
    cleanup_global_config_state,
    cleanup_http_sessions,
    cleanup_loguru_handlers,
    cleanup_mock_patches,
    cleanup_rich_progress_state,
    cleanup_unawaited_coroutines,
)


FIXTURES_DIR = Path(__file__).parent

# Module-specific exports (maintained for backward compatibility)
mod_core_factories = [
    "FanslyConfigFactory",
]

mod_core_fixtures = [
    "complete_args",
    "config_parser",
    "mock_config_file",
    "temp_config_dir",
    "test_config",
    "valid_api_config",
]

mod_download_factories = [
    "DownloadStateFactory",
]

mod_download_fixtures = [
    "download_state",
    "mock_download_dir",
    "mock_download_media",
    "mock_metadata_dir",
    "mock_parse_media_info",
    "mock_process_media_bundles",
    "mock_process_media_download",
    "mock_process_media_info",
    "mock_temp_dir",
    "test_downloads_dir",
]

mod_api_fixtures = [
    "create_mock_json_response",
    "fansly_api",
    "fansly_api_factory",
    "fansly_api_with_respx",
    "mock_fansly_account_response",
    "mock_fansly_timeline_response",
]

mod_metadata_factories = [
    "AccountFactory",
    "AccountMediaFactory",
    "AccountMediaBundleFactory",
    "AttachmentFactory",
    "BaseFactory",
    "HashtagFactory",
    "MediaFactory",
    "MediaLocationFactory",
    "MediaStoryStateFactory",
    "MessageFactory",
    "MetadataGroupFactory",
    "PostFactory",
    "StoryFactory",
    "StubTrackerFactory",
    "TimelineStatsFactory",
    "WallFactory",
    "create_groups_from_messages",
    "setup_accounts_and_groups",
]

mod_metadata_fixtures = [
    "test_account",
    "test_media",
    "test_group",
    "test_attachment",
    "test_post",
    "test_posts",
    "test_message",
    "test_messages",
    "test_account_media",
    "test_media_bundle",
]

mod_stash_type_factories = [
    # Factory classes
    "PerformerFactory",
    "StudioFactory",
    "TagFactory",
    "SceneFactory",
    "GalleryFactory",
    "ImageFactory",
    "ImageFileFactory",
    "VideoFileFactory",
    "JobFactory",
    "StashGroupFactory",
    # Fixtures that return REAL objects (not MagicMock)
    "mock_performer",  # From stash_type_factories - returns PerformerFactory()
    "mock_studio",  # From stash_type_factories - returns StudioFactory()
    "mock_tag",
    "mock_scene",  # From stash_type_factories - returns SceneFactory()
    "mock_gallery",
    "mock_image",
    "mock_image_file",  # From stash_type_factories - returns ImageFileFactory()
    "mock_video_file",  # From stash_type_factories - returns VideoFileFactory()
]

mod_stash_fixtures = [
    "create_find_performers_result",
    "create_find_scenes_result",
    "create_find_studios_result",
    "create_find_tags_result",
    "create_graphql_response",
    "create_performer_dict",
    "create_scene_dict",
    "create_studio_dict",
    "create_tag_create_result",
    "create_tag_dict",
    # Removed (Phase 6 cleanup - from stash_fixtures.py): reset_stash_field_names_cache,
    # MockTag, TestStashCreateInput, TestStashObject, TestStashObjectNoCreate,
    # TestStashObjectNoStrawberry, TestStashUpdateInput, bulk_update_ids_data,
    # bulk_update_strings_data, complex_relationship_data, edge_case_stash_data,
    # generate_graphql_response, generate_stash_object_data, large_stash_object_data,
    # mock_tags, test_stash_object, test_stash_object_new, test_stash_object_no_create,
    # test_stash_object_no_strawberry
    # Removed: "mock_stash_client_with_errors", "mock_stash_client_with_responses"
    # (Mocked internal client.execute() - use respx to mock HTTP instead)
]

mod_stash_mixin_fixtures = [
    "account_mixin",
    "batch_mixin",
    "content_mixin",
    "gallery_mixin",
    "media_mixin",
    "studio_mixin",
    "tag_mixin",
    "gallery_mock_performer",
    "gallery_mock_studio",
    "mock_item",
]

mod_database_fixtures = [
    "uuid_test_db_factory",
    "test_data_dir",
    "timeline_data",
    "json_conversation_data",
    "conversation_data",
    # "safe_name",  # Commented out - fixture is not currently defined
    "test_engine",
    "test_async_session",
    "config",
    "config_with_database",
    "test_sync_engine",
    "session_factory",
    "test_database_sync",
    "test_database",
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

mod_stash_processing_fixtures = [
    # REMOVED (Phase 6 cleanup): All safe_*_create and sanitize_model_data
    # These were never used - tests use real fixtures from stash_api_fixtures
]

mod_stash_api_fixtures = [
    "stash_context",
    "stash_client",
    "respx_stash_client",
    "enable_scene_creation",
    "stash_cleanup_tracker",
    # Removed: test_query (Phase 6 cleanup - never used)
    # Removed: mock_account, mock_performer, mock_studio, mock_scene
    # (MagicMock duplicates - use real factories instead)
    # Removed: mock_session, mock_transport, mock_client
    # (Mocked internal GraphQL components - use respx to mock HTTP instead)
]

mod_stash_integration_fixtures = [
    "fansly_network_studio",
    "test_state",  # was "mock_state" - already a real object, not a mock
    # Removed: mock_studio_finder, mock_permissions (deleted during stash-graphql-client migration)
    "message_media_generator",
    "real_stash_processor",  # Hits real Docker Stash (true integration tests)
    "respx_stash_processor",  # Has respx.mock wrapper (unit tests with mocked HTTP)
]

mod_cleanup_fixtures = [
    "cleanup_rich_progress_state",
    "cleanup_loguru_handlers",
    "cleanup_http_sessions",
    "cleanup_global_config_state",
    "cleanup_unawaited_coroutines",
    "cleanup_mock_patches",
]

mod_init = [
    "load_json_fixture",
    "save_json_fixture",
    "anonymize_response",
    "API_FIELD_MAPPINGS",
    "FIXTURES_DIR",
]

# Combined __all__ from all modules
__all__ = [  # noqa: PLE0604
    *mod_core_factories,
    *mod_core_fixtures,
    *mod_download_factories,
    *mod_download_fixtures,
    *mod_api_fixtures,
    *mod_metadata_factories,
    *mod_metadata_fixtures,
    *mod_stash_type_factories,
    *mod_stash_fixtures,
    *mod_stash_mixin_fixtures,
    *mod_database_fixtures,
    *mod_stash_processing_fixtures,
    *mod_stash_api_fixtures,
    *mod_stash_integration_fixtures,
    *mod_cleanup_fixtures,
    *mod_init,
]


def load_json_fixture(filename: str) -> dict[str, Any]:
    """Load a JSON fixture file.

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
    """Save data as a JSON fixture file.

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
    """Anonymize API response data while preserving structure.

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
