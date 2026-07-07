"""Unit tests for pathio/__init__.py module."""

import pytest

import pathio


class TestPathioInit:
    """Tests for pathio/__init__.py lazy loading."""

    def test_lazy_load_ask_correct_dir(self):
        """Test lazy loading of ask_correct_dir function."""
        # Access ask_correct_dir through __getattr__
        ask_correct_dir = pathio.ask_correct_dir

        # Verify it's a callable (function)
        assert callable(ask_correct_dir)

    def test_invalid_attribute_raises_error(self):
        """Test that accessing invalid attribute raises AttributeError."""
        # Try to access an attribute that doesn't exist
        # A computed attr name keeps the deliberately-absent lookup dynamic:
        # mypy resolves a literal (and ruff B009 rewrites getattr-with-literal
        # back to attribute access), but neither touches a variable. The
        # AttributeError is the assertion.
        missing_attr = "nonexistent_function"
        with pytest.raises(
            AttributeError, match="has no attribute 'nonexistent_function'"
        ):
            getattr(pathio, missing_attr)
