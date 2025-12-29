"""Fixtures for testing Stash GraphQL types and interactions.

This module provides test fixtures for the Stash integration components.

All test fixtures previously in this file were removed as dead code during
migration to stash-graphql-client library (Phase 6 cleanup):
- TestStashObject, TestStashCreateInput, TestStashUpdateInput (used by deleted tests)
- MockTag, mock_tags (used by deleted tests)
- bulk_update_* fixtures (used by deleted tests)
- reset_stash_field_names_cache (for old Strawberry __field_names__ cache - library uses Pydantic)
"""

__all__: list[str] = []
