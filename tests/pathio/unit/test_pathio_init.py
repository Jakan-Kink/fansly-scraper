"""Unit tests for pathio/__init__.py module."""

import pytest


class TestPathioInit:
    """Tests for pathio/__init__.py lazy loading."""

    def test_lazy_load_ask_correct_dir(self):
        """Test lazy loading of ask_correct_dir function."""
        # Import at module level to test lazy loading
        import pathio

        # Access ask_correct_dir through __getattr__
        ask_correct_dir = pathio.ask_correct_dir

        # Verify it's a callable (function)
        assert callable(ask_correct_dir)

    def test_invalid_attribute_raises_error(self):
        """Test that accessing invalid attribute raises AttributeError."""
        import pathio

        # Try to access an attribute that doesn't exist
        with pytest.raises(
            AttributeError, match="has no attribute 'nonexistent_function'"
        ):
            _ = pathio.nonexistent_function
