"""Simple test file to directly test the stash_processor fixture."""

import pytest


class TestStashProcessorFixture:
    """Simple tests to verify the stash_processor fixture works."""

    def test_stash_processor_exists(self, stash_processor):
        """Test that the stash_processor fixture is available and properly initialized."""
        # Simple verification that the processor exists and is properly initialized
        assert stash_processor is not None
        assert hasattr(stash_processor, "process_creator")
        assert hasattr(stash_processor, "scan_to_stash")
        assert hasattr(stash_processor, "process_creator_posts")
        assert hasattr(stash_processor, "process_creator_messages")
