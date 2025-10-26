"""Integration tests for StashProcessing.

This module imports all the integration tests to ensure they are discovered by pytest.
"""

from tests.stash.processing.integration.test_content_processing import (
    TestContentProcessingIntegration,
)
from tests.stash.processing.integration.test_full_workflow.test_integration import (
    TestFullWorkflowIntegration,
)
from tests.stash.processing.integration.test_media_processing import (
    TestMediaProcessingIntegration,
)
from tests.stash.processing.integration.test_stash_processing import (
    TestStashProcessingIntegration,
)


# Import and run all integration tests when this module is imported
__all__ = [
    "TestContentProcessingIntegration",
    "TestFullWorkflowIntegration",
    "TestMediaProcessingIntegration",
    "TestStashProcessingIntegration",
]
