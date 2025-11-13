"""Stash fixtures for testing Stash integration."""

from .stash_api_fixtures import (
    enable_scene_creation,
    # Removed: mock_account, mock_performer, mock_studio, mock_scene
    # (MagicMock duplicates - use real factories from stash_type_factories)
    # Removed: mock_client, mock_session, mock_transport
    # (Mocked internal GraphQL components - use respx to mock HTTP instead)
    stash_cleanup_tracker,
    stash_client,
    stash_context,
    test_query,
)
from .stash_graphql_fixtures import (
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
    # Removed: mock_stash_client_with_errors, mock_stash_client_with_responses
    # (Mocked internal client.execute() - use respx to mock HTTP instead)
    mock_tags,
    reset_stash_field_names_cache,
    test_stash_object,
    test_stash_object_new,
    test_stash_object_no_create,
    test_stash_object_no_strawberry,
)
from .stash_integration_fixtures import (
    fansly_network_studio,
    mock_permissions,
    mock_studio_finder,
    real_stash_processor,
    test_state,
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
    safe_image_create,
    safe_scene_create,
    safe_scene_marker_create,
    safe_studio_create,
    safe_tag_create,
    sanitize_model_data,
)
from .stash_type_factories import (
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

__all__ = [
    # API fixtures
    "enable_scene_creation",
    # Removed: mock_account, mock_performer, mock_studio, mock_scene
    # (MagicMock duplicates - use real factories instead)
    # Removed: mock_client, mock_session, mock_transport
    # (Mocked internal GraphQL components - use respx to mock HTTP instead)
    "stash_cleanup_tracker",
    "stash_client",
    "stash_context",
    "test_query",
    # GraphQL response helpers for respx mocking
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
    # Stash fixtures
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
    # Removed: "mock_stash_client_with_errors", "mock_stash_client_with_responses"
    # (Mocked internal client.execute() - use respx to mock HTTP instead)
    "mock_tags",
    "reset_stash_field_names_cache",
    "test_stash_object",
    "test_stash_object_new",
    "test_stash_object_no_create",
    "test_stash_object_no_strawberry",
    # Integration fixtures
    "fansly_network_studio",
    "mock_permissions",
    "mock_studio_finder",
    "real_stash_processor",
    "test_state",
    # Mixin fixtures
    "account_mixin",
    "batch_mixin",
    "content_mixin",
    "gallery_mixin",
    "gallery_mock_performer",
    "gallery_mock_studio",
    "media_mixin",
    "mock_item",
    "studio_mixin",
    "tag_mixin",
    # Processing fixtures
    "safe_image_create",
    "safe_scene_create",
    "safe_scene_marker_create",
    "safe_studio_create",
    "safe_tag_create",
    "sanitize_model_data",
    # Type factories
    "GalleryFactory",
    "GroupFactory",
    "ImageFactory",
    "ImageFileFactory",
    "PerformerFactory",
    "SceneFactory",
    "StudioFactory",
    "TagFactory",
    "VideoFileFactory",
    "mock_gallery",
    "mock_image",
    "mock_image_file",
    "mock_performer",
    "mock_scene",
    "mock_studio",
    "mock_tag",
    "mock_video_file",
]
