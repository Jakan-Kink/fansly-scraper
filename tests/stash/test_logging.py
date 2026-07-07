"""Tests for stash/logging.py module.

This module tests the logging utilities and error handling in stash logging.
"""

import logging
from pprint import pformat

import pytest

from stash.logging import debug_print


class TestDebugPrint:
    """Test debug_print() function."""

    @pytest.mark.parametrize(
        ("logger_name", "expected_name"),
        [
            pytest.param(None, None, id="root"),
            pytest.param("client", "client", id="client"),
            pytest.param("processing", "processing", id="processing"),
            pytest.param("custom_logger", "custom_logger", id="custom"),
        ],
    )
    def test_debug_print_routes_to_real_logger(
        self,
        caplog: pytest.LogCaptureFixture,
        logger_name: str | None,
        expected_name: str | None,
    ) -> None:
        """debug_print() emits a real DEBUG record bound to the right logger.

        pytest-loguru bridges loguru -> caplog, so the real logging path runs:
        - None             -> root stash_logger (no ``name`` bound in extra)
        - "client"         -> client_logger (extra["name"] == "client")
        - "processing"     -> processing_logger (extra["name"] == "processing")
        - any other string -> stash_logger.bind(name=...) (extra["name"] == it)
        """
        caplog.set_level(logging.DEBUG)
        test_obj = {"key": "value", "nested": {"data": [1, 2, 3]}}

        debug_print(test_obj, logger_name=logger_name)

        debug_records = [r for r in caplog.records if r.levelname == "DEBUG"]
        assert len(debug_records) == 1
        record = debug_records[0]

        # The real formatted payload is logged (pformat ran for real).
        assert record.getMessage() == pformat(test_obj, indent=2).strip()

        # The record carries the bound logger name in loguru's extra dict.
        # ``extra`` is injected onto the stdlib LogRecord by pytest-loguru and
        # is not part of LogRecord's static type, hence the getattr.
        extra: dict[str, object] = getattr(record, "extra")  # noqa: B009
        assert extra["logger"] == "stash"
        if expected_name is None:
            assert "name" not in extra
        else:
            assert extra["name"] == expected_name

    def test_debug_print_exception_handling(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """debug_print() falls back to stderr when formatting raises.

        No pformat mock: ``UnformattableObject.__repr__`` raises during the real
        ``pformat`` call, so the genuine ``except`` branch runs and prints to
        stderr.
        """

        class UnformattableObject:
            def __repr__(self) -> str:
                raise RuntimeError("Cannot format this object")

        debug_print(UnformattableObject())

        captured = capsys.readouterr()
        assert "Failed to log debug message" in captured.err

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
