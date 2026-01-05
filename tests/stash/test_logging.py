"""Tests for stash/logging.py module.

This module tests the logging utilities and error handling in stash logging.
"""

from unittest.mock import MagicMock, patch

from stash.logging import client_logger, debug_print, processing_logger


class TestLoggers:
    """Test logger instances."""

    def test_client_logger_exists(self) -> None:
        """Test that client_logger is properly initialized."""
        assert client_logger is not None
        # Verify it's a loguru logger
        assert hasattr(client_logger, "debug")
        assert hasattr(client_logger, "info")

    def test_processing_logger_exists(self) -> None:
        """Test that processing_logger is properly initialized."""
        assert processing_logger is not None
        # Verify it's a loguru logger
        assert hasattr(processing_logger, "debug")
        assert hasattr(processing_logger, "info")


class TestDebugPrint:
    """Test debug_print() function."""

    def test_debug_print_with_no_logger_name(self) -> None:
        """Test debug_print() with no logger_name (uses root stash logger)."""
        test_obj = {"key": "value", "nested": {"data": [1, 2, 3]}}

        # Should not raise exception
        debug_print(test_obj)

    def test_debug_print_with_client_logger(self) -> None:
        """Test debug_print() with logger_name='client' (line 35-36)."""
        test_obj = {"message": "client operation"}

        # Mock the client_logger to verify it's called
        with patch("stash.logging.client_logger") as mock_client:
            debug_print(test_obj, logger_name="client")
            mock_client.debug.assert_called_once()

    def test_debug_print_with_processing_logger(self) -> None:
        """Test debug_print() with logger_name='processing' (line 37-38)."""
        test_obj = {"status": "processing data"}

        # Mock the processing_logger to verify it's called
        with patch("stash.logging.processing_logger") as mock_processing:
            debug_print(test_obj, logger_name="processing")
            mock_processing.debug.assert_called_once()

    def test_debug_print_with_custom_logger_name(self) -> None:
        """Test debug_print() with custom logger_name (line 39-41)."""
        test_obj = {"custom": "data"}

        # Mock stash_logger.bind to verify custom logger is created
        with patch("stash.logging.stash_logger") as mock_stash:
            mock_bound = MagicMock()
            mock_stash.bind.return_value = mock_bound

            debug_print(test_obj, logger_name="custom_logger")

            # Verify bind was called with custom name
            mock_stash.bind.assert_called_once_with(name="custom_logger")
            # Verify debug was called on the bound logger
            mock_bound.debug.assert_called_once()

    def test_debug_print_exception_handling(self) -> None:
        """Test debug_print() exception handler (lines 45-46)."""

        # Create an object that will raise exception during pformat
        class UnformattableObject:
            def __repr__(self) -> str:
                raise RuntimeError("Cannot format this object")

        # Mock pformat to raise exception
        with (
            patch("stash.logging.pformat", side_effect=RuntimeError("Format error")),
            patch("sys.stderr") as mock_stderr,
        ):
            debug_print(UnformattableObject())
            # Verify error was printed (stderr.write is called by print())
            assert mock_stderr.write.called

    def test_debug_print_with_various_object_types(self) -> None:
        """Test debug_print() handles various object types correctly."""
        test_cases = [
            {"dict": "object"},
            ["list", "of", "items"],
            ("tuple", "data"),
            "simple string",
            12345,
            None,
        ]

        for test_obj in test_cases:
            # Should not raise exception for any type
            debug_print(test_obj)

    def test_debug_print_with_large_object(self) -> None:
        """Test debug_print() handles large objects."""
        large_obj = {f"key_{i}": f"value_{i}" for i in range(1000)}

        # Should not raise exception even for large objects
        debug_print(large_obj)
