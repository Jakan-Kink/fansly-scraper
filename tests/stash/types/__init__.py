"""Tests for stash.types.base module - Organized Test Suite

This package contains comprehensive tests for the stash.types.base module,
organized into focused, maintainable test files.

Test Organization:
- test_base_bulk_update_types.py: BulkUpdateStrings, BulkUpdateIds interface tests
- test_base_initialization.py: Object creation, field filtering, post-init logic
- test_base_change_tracking.py: Dirty state management, setattr behavior
- test_base_async_operations.py: GraphQL operations, database interactions
- test_base_field_processing.py: Field names, conversions, metadata handling
- test_base_relationship_processing.py: Single/list relationship processing logic
- test_base_input_conversion.py: Input conversion methods (largest logical group)
- test_base_hash_equality.py: Object comparison and hashing
- test_base_integration.py: Complete workflows, end-to-end scenarios
- test_base_targeted_coverage.py: Ultra-precise line coverage tests, edge cases

Coverage Target: 95%+ of stash/types/base.py
Total Tests: ~300+ test functions across all files

Usage:
    # Run all base tests
    pytest tests/stash/types/

    # Run specific test category
    pytest tests/stash/types/test_base_async_operations.py

    # Run with coverage
    pytest tests/stash/types/ --cov=stash/types/base --cov-report=term-missing
"""

# Import key test fixtures for easy access
from ...fixtures.stash_fixtures import (
    MockTag,
    TestStashCreateInput,
    TestStashObject,
    TestStashObjectNoCreate,
    TestStashObjectNoStrawberry,
    TestStashUpdateInput,
)


__all__ = [
    "MockTag",
    "TestStashCreateInput",
    "TestStashObject",
    "TestStashObjectNoCreate",
    "TestStashObjectNoStrawberry",
    "TestStashUpdateInput",
]
