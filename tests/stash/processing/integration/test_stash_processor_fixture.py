"""Simple test file to directly test the real_stash_processor fixture."""


class TestStashProcessorFixture:
    """Simple tests to verify the real_stash_processor fixture works."""

    def test_stash_processor_exists(self, real_stash_processor):
        """Test that the real_stash_processor fixture is available and properly initialized."""
        # Simple verification that the processor exists and is properly initialized
        assert real_stash_processor is not None
        assert hasattr(real_stash_processor, "process_creator")
        assert hasattr(real_stash_processor, "process_creator_posts")
        assert hasattr(real_stash_processor, "process_creator_messages")
