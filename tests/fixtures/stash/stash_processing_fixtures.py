"""Re-export stash API fixtures for convenience.

REMOVED (Phase 6 cleanup): All safe_*_create wrapper functions (lines 42-78)
- safe_scene_marker_create, safe_tag_create, safe_studio_create, safe_image_create, safe_scene_create
- sanitize_model_data helper function
- Class method monkey-patching (SceneMarker.safe_create, etc.)

These were never used in any test. Tests import the real sanitize_model_data from
stash_graphql_client.client.utils instead, and use real factories from stash_type_factories.py.

NOTE: AwaitableAttrsMock-based fixtures have been removed as they were masking
real async relationship loading issues. Use real database fixtures from
tests/fixtures/metadata_fixtures.py instead.

REMOVED: AsyncMock monkey-patching has been removed. Tests should NOT mock
internal async methods. Use @respx.mock to intercept HTTP calls at the edge instead.
"""

# Import fixtures from stash_api_fixtures
from tests.fixtures.stash.stash_api_fixtures import (
    stash_cleanup_tracker,
    stash_client,
    stash_context,
    test_query,
)


__all__ = [
    # Re-exported from stash_api_fixtures for convenience
    "stash_cleanup_tracker",
    "stash_client",
    "stash_context",
    "test_query",
]
