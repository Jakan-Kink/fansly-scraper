"""Tests for helpers/__init__.py — lazy import __getattr__ for checkkey."""

import pytest


class TestHelpersLazyImport:
    """Lines 21-28: __getattr__ lazy loads guess_check_key, raises on unknown."""

    def test_guess_check_key_lazy_import(self):
        """Lines 24-27: accessing guess_check_key triggers lazy import."""
        import helpers

        func = helpers.guess_check_key
        assert callable(func)

    def test_unknown_attribute_raises(self):
        """Line 28: unknown name → AttributeError."""
        import helpers

        with pytest.raises(AttributeError, match="has no attribute"):
            _ = helpers.nonexistent_function
