"""Tests for stash.types.logging module."""

from datetime import datetime
from enum import Enum

import pytest

from stash.types.logging import LogEntry, LogLevel


@pytest.mark.unit
class TestLogLevel:
    """Test LogLevel enum."""

    def test_strawberry_enum_decoration(self):
        """Test that LogLevel is decorated as strawberry enum."""
        assert issubclass(LogLevel, str)
        assert issubclass(LogLevel, Enum)

    def test_enum_values(self):
        """Test enum values."""
        assert LogLevel.TRACE == "Trace"
        assert LogLevel.DEBUG == "Debug"  # type: ignore[unreachable]
        assert LogLevel.INFO == "Info"
        assert LogLevel.PROGRESS == "Progress"
        assert LogLevel.WARNING == "Warning"
        assert LogLevel.ERROR == "Error"

    def test_enum_count(self):
        """Test expected number of log levels."""
        levels = list(LogLevel)
        assert len(levels) == 6

    def test_log_level_hierarchy(self):
        """Test that all expected log levels are present."""
        expected_levels = ["Trace", "Debug", "Info", "Progress", "Warning", "Error"]
        actual_levels = [level.value for level in LogLevel]
        assert set(actual_levels) == set(expected_levels)


@pytest.mark.unit
class TestLogEntry:
    """Test LogEntry class."""

    def test_strawberry_type_decoration(self):
        """Test that LogEntry is decorated as strawberry type."""
        assert hasattr(LogEntry, "__strawberry_definition__")
        assert not LogEntry.__strawberry_definition__.is_input

    def test_field_types(self):
        """Test field type annotations."""
        annotations = LogEntry.__annotations__
        assert annotations["time"] == datetime
        assert annotations["level"] == LogLevel
        assert annotations["message"] == str

    def test_instantiation(self):
        """Test LogEntry can be instantiated."""
        now = datetime.now()
        entry = LogEntry(time=now, level=LogLevel.INFO, message="Test log message")

        assert entry.time == now
        assert entry.level == LogLevel.INFO
        assert entry.message == "Test log message"

    def test_all_log_levels(self):
        """Test LogEntry with all log levels."""
        now = datetime.now()

        for level in LogLevel:
            entry = LogEntry(
                time=now, level=level, message=f"Test message for {level.value} level"
            )
            assert entry.level == level
            assert entry.message == f"Test message for {level.value} level"

    def test_different_timestamps(self):
        """Test LogEntry with different timestamps."""
        # Test with current time
        now = datetime.now()
        entry1 = LogEntry(time=now, level=LogLevel.DEBUG, message="Current time entry")
        assert entry1.time == now

        # Test with specific timestamp
        specific_time = datetime(2023, 1, 1, 12, 0, 0)
        entry2 = LogEntry(
            time=specific_time, level=LogLevel.ERROR, message="Specific time entry"
        )
        assert entry2.time == specific_time

    def test_message_content(self):
        """Test LogEntry with various message content."""
        now = datetime.now()

        # Test empty message
        entry1 = LogEntry(time=now, level=LogLevel.INFO, message="")
        assert entry1.message == ""

        # Test multiline message
        multiline_msg = "Line 1\nLine 2\nLine 3"
        entry2 = LogEntry(time=now, level=LogLevel.WARNING, message=multiline_msg)
        assert entry2.message == multiline_msg

        # Test message with special characters
        special_msg = "Message with special chars: àáâãäå æç èéêë ìíîï"
        entry3 = LogEntry(time=now, level=LogLevel.TRACE, message=special_msg)
        assert entry3.message == special_msg


@pytest.mark.unit
class TestLoggingScenarios:
    """Test realistic logging scenarios."""

    def test_error_logging(self):
        """Test error log entry."""
        error_time = datetime.now()
        error_entry = LogEntry(
            time=error_time,
            level=LogLevel.ERROR,
            message="Database connection failed: Connection timeout after 30 seconds",
        )

        assert error_entry.level == LogLevel.ERROR
        assert "Database connection failed" in error_entry.message
        assert error_entry.time == error_time

    def test_progress_logging(self):
        """Test progress log entry."""
        progress_time = datetime.now()
        progress_entry = LogEntry(
            time=progress_time,
            level=LogLevel.PROGRESS,
            message="Processing files: 150/500 (30%)",
        )

        assert progress_entry.level == LogLevel.PROGRESS
        assert "30%" in progress_entry.message
        assert progress_entry.time == progress_time

    def test_debug_logging(self):
        """Test debug log entry."""
        debug_time = datetime.now()
        debug_entry = LogEntry(
            time=debug_time,
            level=LogLevel.DEBUG,
            message="Cache hit for key: user_session_12345",
        )

        assert debug_entry.level == LogLevel.DEBUG
        assert "Cache hit" in debug_entry.message
        assert debug_entry.time == debug_time

    def test_warning_logging(self):
        """Test warning log entry."""
        warning_time = datetime.now()
        warning_entry = LogEntry(
            time=warning_time,
            level=LogLevel.WARNING,
            message="Deprecated API endpoint used: /api/v1/users - please migrate to /api/v2/users",
        )

        assert warning_entry.level == LogLevel.WARNING
        assert "Deprecated" in warning_entry.message
        assert warning_entry.time == warning_time

    def test_trace_logging(self):
        """Test trace log entry."""
        trace_time = datetime.now()
        trace_entry = LogEntry(
            time=trace_time,
            level=LogLevel.TRACE,
            message="Function entered: calculate_hash(file_path='/tmp/video.mp4', algorithm='sha256')",
        )

        assert trace_entry.level == LogLevel.TRACE
        assert "Function entered" in trace_entry.message
        assert trace_entry.time == trace_time

    def test_info_logging(self):
        """Test info log entry."""
        info_time = datetime.now()
        info_entry = LogEntry(
            time=info_time,
            level=LogLevel.INFO,
            message="Scan completed successfully: 42 scenes processed, 3 new files found",
        )

        assert info_entry.level == LogLevel.INFO
        assert "Scan completed" in info_entry.message
        assert info_entry.time == info_time

    def test_log_sequence(self):
        """Test a sequence of log entries."""
        base_time = datetime(2023, 6, 1, 10, 0, 0)

        # Create a sequence of log entries
        entries = []

        # Start operation
        entries.append(
            LogEntry(
                time=base_time,
                level=LogLevel.INFO,
                message="Starting file processing operation",
            )
        )

        # Debug information
        entries.append(
            LogEntry(
                time=base_time.replace(second=1),
                level=LogLevel.DEBUG,
                message="Loading configuration from config.yaml",
            )
        )

        # Progress update
        entries.append(
            LogEntry(
                time=base_time.replace(second=30),
                level=LogLevel.PROGRESS,
                message="Processing: 50/100 files (50%)",
            )
        )

        # Warning
        entries.append(
            LogEntry(
                time=base_time.replace(second=45),
                level=LogLevel.WARNING,
                message="Skipping corrupted file: video_123.mp4",
            )
        )

        # Completion
        entries.append(
            LogEntry(
                time=base_time.replace(minute=1),
                level=LogLevel.INFO,
                message="File processing completed successfully",
            )
        )

        # Verify sequence
        assert len(entries) == 5
        assert entries[0].level == LogLevel.INFO
        assert entries[1].level == LogLevel.DEBUG
        assert entries[2].level == LogLevel.PROGRESS
        assert entries[3].level == LogLevel.WARNING
        assert entries[4].level == LogLevel.INFO

        # Verify timestamps are in order
        for i in range(1, len(entries)):
            assert entries[i].time >= entries[i - 1].time
